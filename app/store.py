import json
import sqlite3
import time
from pathlib import Path

from app.models import Job, SearchInput


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS searches (
                id INTEGER PRIMARY KEY, config TEXT NOT NULL, initialized INTEGER DEFAULT 0,
                next_check REAL DEFAULT 0, last_check REAL, last_success REAL,
                last_duration_ms REAL, last_count INTEGER DEFAULT 0, last_new INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0, error TEXT
            );
            CREATE TABLE IF NOT EXISTS jobs (
                key TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL,
                first_seen REAL NOT NULL, last_seen REAL NOT NULL,
                status TEXT NOT NULL, attempts INTEGER DEFAULT 0,
                next_attempt REAL DEFAULT 0, sent_at REAL, delivery_ms REAL, error TEXT
            );
            CREATE TABLE IF NOT EXISTS search_jobs (
                search_id INTEGER REFERENCES searches(id) ON DELETE CASCADE,
                job_key TEXT REFERENCES jobs(key), PRIMARY KEY(search_id, job_key)
            );
            CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status, next_attempt);
            CREATE TABLE IF NOT EXISTS source_state (
                source TEXT PRIMARY KEY, next_request REAL DEFAULT 0
            );
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def searches(self):
        result = []
        for row in self.db.execute("SELECT * FROM searches ORDER BY id"):
            data = dict(row)
            data.update(json.loads(data.pop("config")))
            data["initialized"] = bool(data["initialized"])
            result.append(data)
        return result

    def search(self, search_id):
        return next((x for x in self.searches() if x["id"] == search_id), None)

    def add_search(self, config: SearchInput):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO searches(config) VALUES (?)", (config.model_dump_json(),)
            )
        return self.search(cursor.lastrowid)

    def update_search(self, search_id, config: SearchInput):
        old = self.search(search_id)
        if not old:
            return None
        # A changed search scope starts a fresh baseline; pause/resume preserves it.
        fields = ["source", "keywords", "location", "contract", "experience", "exclude_keywords"]
        changed = any(old[k] != config.model_dump()[k] for k in fields)
        with self.db:
            self.db.execute(
                "UPDATE searches SET config=?, initialized=?, next_check=0, failures=0, error=NULL "
                "WHERE id=?",
                (config.model_dump_json(), 0 if changed else old["initialized"], search_id),
            )
        return self.search(search_id)

    def delete_search(self, search_id):
        with self.db:
            return self.db.execute("DELETE FROM searches WHERE id=?", (search_id,)).rowcount > 0

    def request_check(self, search_id):
        with self.db:
            self.db.execute("UPDATE searches SET next_check=0 WHERE id=?", (search_id,))

    def source_ready_at(self, source):
        row = self.db.execute(
            "SELECT next_request FROM source_state WHERE source=?", (source,)
        ).fetchone()
        return row[0] if row else 0

    def set_source_ready(self, source, timestamp):
        with self.db:
            self.db.execute(
                "INSERT INTO source_state(source,next_request) VALUES (?,?) "
                "ON CONFLICT(source) DO UPDATE SET next_request=excluded.next_request",
                (source, timestamp),
            )

    def record_scan(self, search_id, config: SearchInput, jobs: list[Job], duration_ms: float):
        current = self.search(search_id)
        # Ignore a result if its search was deleted, paused or changed while the request ran.
        if not current or not current["enabled"]:
            return 0
        if SearchInput.model_validate(current) != config:
            return 0
        now = time.time()
        new_count = 0
        with self.db:
            for job in jobs:
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO jobs(key,source,payload,first_seen,last_seen,status) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        job.key,
                        job.source,
                        json.dumps(job.to_dict(), ensure_ascii=False),
                        now,
                        now,
                        "pending" if current["initialized"] else "baseline",
                    ),
                )
                if cursor.rowcount and current["initialized"]:
                    new_count += 1
                self.db.execute(
                    "UPDATE jobs SET last_seen=?,payload=? WHERE key=?",
                    (now, json.dumps(job.to_dict(), ensure_ascii=False), job.key),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO search_jobs VALUES (?,?)", (search_id, job.key)
                )
            self.db.execute(
                "UPDATE searches SET initialized=1,last_check=?,last_success=?,next_check=?,"
                "last_duration_ms=?,last_count=?,last_new=?,failures=0,error=NULL WHERE id=?",
                (
                    now,
                    now,
                    now + config.interval_seconds,
                    duration_ms,
                    len(jobs),
                    new_count,
                    search_id,
                ),
            )
        return new_count

    def record_failure(self, search_id, error, retry_after=None):
        current = self.search(search_id)
        if not current:
            return 60
        failures = current["failures"] + 1
        delay = max(
            retry_after or 0, min(3600, current["interval_seconds"] * 2 ** min(failures, 6))
        )
        with self.db:
            self.db.execute(
                "UPDATE searches SET last_check=?,next_check=?,failures=?,error=? WHERE id=?",
                (time.time(), time.time() + delay, failures, error, search_id),
            )
        return delay

    @staticmethod
    def decode_job(row):
        data = dict(row)
        data.update(json.loads(data.pop("payload")))
        return data

    def jobs(self, limit=100, source=None, status=None, query=None):
        clauses, params = [], []
        if source:
            clauses.append("source=?")
            params.append(source)
        if status:
            clauses.append("status=?")
            params.append(status)
        if query:
            clauses.append("payload LIKE ?")
            params.append("%" + query + "%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.execute(
            "SELECT * FROM jobs" + where + " ORDER BY first_seen DESC,key LIMIT ?", [*params, limit]
        )
        return [self.decode_job(row) for row in rows]

    def next_delivery(self):
        row = self.db.execute(
            "SELECT * FROM jobs WHERE status='pending' AND next_attempt<=? "
            "ORDER BY first_seen,key LIMIT 1",
            (time.time(),),
        ).fetchone()
        return self.decode_job(row) if row else None

    def delivery_sent(self, key, duration_ms):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status='sent',sent_at=?,delivery_ms=?,error=NULL WHERE key=?",
                (time.time(), duration_ms, key),
            )

    def delivery_failed(self, key, message, delay, permanent=False):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET status=?,attempts=attempts+1,error=?,next_attempt=? WHERE key=?",
                ("failed" if permanent else "pending", message, time.time() + delay, key),
            )

    def retry_failed(self):
        with self.db:
            self.db.execute("UPDATE jobs SET status='pending',next_attempt=0 WHERE status='failed'")

    def summary(self):
        counts = {
            row[0]: row[1]
            for row in self.db.execute("SELECT status,count(*) FROM jobs GROUP BY status")
        }
        latency = self.db.execute(
            "SELECT avg((sent_at-first_seen)*1000) FROM jobs WHERE status='sent'"
        ).fetchone()[0]
        return {
            "jobs": sum(counts.values()),
            "baseline": counts.get("baseline", 0),
            "pending": counts.get("pending", 0),
            "sent": counts.get("sent", 0),
            "failed": counts.get("failed", 0),
            "average_detection_to_delivery_ms": latency,
        }
