import json
import math
import secrets
import sqlite3
import statistics
import time
from pathlib import Path

from app.auth import hash_password, session_hash, verify_password
from app.models import Job, SearchInput, normalize


class Store:
    def __init__(self, path: str, bootstrap_password="", telegram_token="", telegram_chat_id=""):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("normalize_company", 1, normalize, deterministic=True)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
                active INTEGER NOT NULL DEFAULT 1, telegram_token TEXT NOT NULL DEFAULT '',
                telegram_chat_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS banned_recruiters (
                name TEXT NOT NULL, normalized TEXT PRIMARY KEY, created_at REAL NOT NULL
            );
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
            CREATE TABLE IF NOT EXISTS search_sources (
                search_id INTEGER REFERENCES searches(id) ON DELETE CASCADE,
                source TEXT NOT NULL, initialized INTEGER DEFAULT 0,
                next_check REAL DEFAULT 0, last_check REAL, last_success REAL,
                last_duration_ms REAL, last_count INTEGER DEFAULT 0, last_new INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0, error TEXT, PRIMARY KEY(search_id, source)
            );
            CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status, next_attempt);
            CREATE TABLE IF NOT EXISTS source_state (
                source TEXT PRIMARY KEY, next_request REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS user_bans (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL, normalized TEXT NOT NULL, created_at REAL NOT NULL,
                PRIMARY KEY(user_id, normalized)
            );
            CREATE TABLE IF NOT EXISTS user_job_state (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                job_key TEXT NOT NULL REFERENCES jobs(key) ON DELETE CASCADE,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL DEFAULT 0, sent_at REAL, delivery_ms REAL,
                error TEXT, applied INTEGER NOT NULL DEFAULT 0,
                detected_at REAL NOT NULL DEFAULT 0, last_seen REAL NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id, job_key)
            );
        """)
        if not self.db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            # The pre-multi-user access token becomes the initial admin password.
            # If local development has no token, localhost API access uses this admin implicitly.
            password = bootstrap_password or "admin@"
            with self.db:
                self.db.execute(
                    "INSERT INTO users(username,password_hash,role,telegram_token,"
                    "telegram_chat_id,created_at) "
                    "VALUES ('admin',?,'admin',?,?,?)",
                    (hash_password(password), telegram_token, telegram_chat_id, time.time()),
                )
        if "user_id" not in {row[1] for row in self.db.execute("PRAGMA table_info(searches)")}:
            self.db.execute("ALTER TABLE searches ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        # Move legacy shared recruiter bans into the initial administrator's private list.
        self.db.execute(
            "INSERT OR IGNORE INTO user_bans(user_id,name,normalized,created_at) "
            "SELECT 1,name,normalized,created_at FROM banned_recruiters"
        )
        self.db.commit()
        if "applied" not in {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}:
            self.db.execute("ALTER TABLE jobs ADD COLUMN applied INTEGER NOT NULL DEFAULT 0")
        user_state_columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(user_job_state)")
        }
        if "detected_at" not in user_state_columns:
            self.db.execute(
                "ALTER TABLE user_job_state ADD COLUMN detected_at REAL NOT NULL DEFAULT 0"
            )
        if "last_seen" not in user_state_columns:
            self.db.execute(
                "ALTER TABLE user_job_state ADD COLUMN last_seen REAL NOT NULL DEFAULT 0"
            )
        # Preserve legacy alert state and application markers for the initial administrator.
        self.db.execute(
            "INSERT OR IGNORE INTO user_job_state "
            "(user_id,job_key,status,attempts,next_attempt,sent_at,delivery_ms,error,applied,"
            "detected_at,last_seen) "
            "SELECT 1,key,status,attempts,next_attempt,sent_at,delivery_ms,error,"
            "COALESCE(applied,0),first_seen,last_seen FROM jobs"
        )
        self.db.execute(
            "UPDATE user_job_state SET "
            "detected_at=(SELECT first_seen FROM jobs WHERE key=job_key), "
            "last_seen=(SELECT last_seen FROM jobs WHERE key=job_key) "
            "WHERE detected_at=0 OR last_seen=0"
        )
        self.db.commit()
        # Upgrade existing single-platform searches without losing health or job history.
        with self.db:
            for row in self.db.execute("SELECT * FROM searches").fetchall():
                config = SearchInput.model_validate(json.loads(row["config"]))
                for source in config.sources:
                    self.db.execute(
                        "INSERT OR IGNORE INTO search_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            row["id"],
                            source,
                            row["initialized"],
                            row["next_check"],
                            row["last_check"],
                            row["last_success"],
                            row["last_duration_ms"],
                            row["last_count"],
                            row["last_new"],
                            row["failures"],
                            row["error"],
                        ),
                    )
                self.db.execute(
                    "UPDATE searches SET config=? WHERE id=?", (config.model_dump_json(), row["id"])
                )

    def create_session(self, user_id):
        token = secrets.token_urlsafe(36)
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            self.db.execute(
                "INSERT INTO sessions(token_hash,user_id,expires_at) VALUES (?,?,?)",
                (session_hash(token), user_id, now + 30 * 86400),
            )
        return token

    def authenticate(self, username, password):
        row = self.db.execute(
            "SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", (username.strip(),)
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        return dict(row)

    def session_user(self, token):
        if not token:
            return None
        row = self.db.execute(
            "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id "
            "WHERE token_hash=? AND sessions.expires_at>? AND users.active=1",
            (session_hash(token), time.time()),
        ).fetchone()
        return dict(row) if row else None

    def revoke_session(self, token):
        with self.db:
            self.db.execute("DELETE FROM sessions WHERE token_hash=?", (session_hash(token),))

    def user(self, user_id):
        row = self.db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def users(self):
        return [
            {
                "id": row["id"],
                "username": row["username"],
                "role": row["role"],
                "active": bool(row["active"]),
            }
            for row in self.db.execute("SELECT id,username,role,active FROM users ORDER BY id")
        ]

    def add_user(self, username, password):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)",
                (username.strip(), hash_password(password), time.time()),
            )
        return self.user(cursor.lastrowid)

    def set_user_active(self, user_id, active):
        row = self.db.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["role"] == "admin" and not active:
            active_admins = self.db.execute(
                "SELECT count(*) FROM users WHERE role='admin' AND active=1"
            ).fetchone()[0]
            if active_admins <= 1:
                return False
        with self.db:
            updated = bool(
                self.db.execute(
                    "UPDATE users SET active=? WHERE id=?", (int(active), user_id)
                ).rowcount
            )
            if updated and not active:
                self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            return updated

    def change_password(self, user_id, current_password, new_password):
        row = self.db.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or not verify_password(current_password, row[0]):
            return False
        with self.db:
            self.db.execute(
                "UPDATE users SET password_hash=? WHERE id=?",
                (hash_password(new_password), user_id),
            )
            self.db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        return True

    def telegram_settings(self, user_id):
        row = self.db.execute(
            "SELECT telegram_token,telegram_chat_id FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not row:
            return {"configured": False, "chat_id": ""}
        return {
            "configured": bool(row["telegram_token"] and row["telegram_chat_id"]),
            "chat_id": row["telegram_chat_id"],
        }

    def save_telegram_settings(self, user_id, bot_token, chat_id):
        current = self.user(user_id)
        if not current:
            return False
        token = bot_token.strip() or current["telegram_token"]
        chat = chat_id.strip()
        with self.db:
            self.db.execute(
                "UPDATE users SET telegram_token=?,telegram_chat_id=? WHERE id=?",
                (token, chat, user_id),
            )
        return True

    def clear_telegram_settings(self, user_id):
        with self.db:
            self.db.execute(
                "UPDATE users SET telegram_token='',telegram_chat_id='' WHERE id=?", (user_id,)
            )

    def banned_recruiters(self, user_id=1):
        return [
            dict(row)
            for row in self.db.execute(
                "SELECT name,normalized,created_at FROM user_bans WHERE user_id=? ORDER BY name",
                (user_id,),
            )
        ]

    def is_banned(self, company, user_id=1):
        return bool(
            self.db.execute(
                "SELECT 1 FROM user_bans WHERE user_id=? AND normalized=?",
                (user_id, normalize(company)),
            ).fetchone()
        )

    def ban_recruiter(self, name, user_id=1):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO user_bans VALUES (?,?,?,?)",
                (user_id, name.strip(), normalize(name), time.time()),
            )
        return self.banned_recruiters(user_id)

    def unban_recruiter(self, name, user_id=1):
        with self.db:
            return bool(
                self.db.execute(
                    "DELETE FROM user_bans WHERE user_id=? AND normalized=?",
                    (user_id, normalize(name)),
                ).rowcount
            )

    def close(self):
        self.db.close()

    def searches(self, user_id=1):
        result = []
        for row in self.db.execute(
            "SELECT * FROM searches WHERE user_id=? ORDER BY id", (user_id,)
        ):
            data = {"id": row["id"], "user_id": row["user_id"], **json.loads(row["config"])}
            states = [
                dict(x)
                for x in self.db.execute(
                    "SELECT * FROM search_sources WHERE search_id=? ORDER BY source", (row["id"],)
                )
            ]
            for item in states:
                item.pop("search_id")
                item["initialized"] = bool(item["initialized"])
            data["source_statuses"] = states
            data["initialized"] = all(x["initialized"] for x in states)
            for key in ("last_count", "last_new", "failures"):
                data[key] = sum(x[key] for x in states)
            for key in ("last_check", "last_success", "last_duration_ms"):
                values = [x[key] for x in states if x[key] is not None]
                data[key] = max(values) if values else None
            data["next_check"] = min((x["next_check"] for x in states), default=0)
            data["error"] = (
                "; ".join(f"{x['source']}: {x['error']}" for x in states if x["error"]) or None
            )
            result.append(data)
        return result

    def searches_for_source(self, source, user_id=None):
        result = []
        searches = (
            [
                search
                for user in self.db.execute("SELECT id FROM users")
                for search in self.searches(user[0])
            ]
            if user_id is None
            else self.searches(user_id)
        )
        for search in searches:
            for status in search["source_statuses"]:
                if status["source"] == source:
                    result.append({**search, **status})
        return result

    def search(self, search_id, user_id=None):
        searches = (
            [
                search
                for user in self.db.execute("SELECT id FROM users")
                for search in self.searches(user[0])
            ]
            if user_id is None
            else self.searches(user_id)
        )
        return next((x for x in searches if x["id"] == search_id), None)

    def add_search(self, config: SearchInput, user_id=1):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO searches(config,user_id) VALUES (?,?)",
                (config.model_dump_json(), user_id),
            )
            search_id = cursor.lastrowid
            for source in config.sources:
                self.db.execute(
                    "INSERT INTO search_sources(search_id,source) VALUES (?,?)", (search_id, source)
                )
        return self.search(search_id, user_id)

    def update_search(self, search_id, config: SearchInput, user_id=1):
        old = self.search(search_id, user_id)
        if not old:
            return None
        fields = ["keywords", "location", "contract", "experience", "exclude_keywords"]
        changed = any(old[k] != config.model_dump()[k] for k in fields)
        with self.db:
            self.db.execute(
                "UPDATE searches SET config=? WHERE id=? AND user_id=?",
                (config.model_dump_json(), search_id, user_id),
            )
            for source in old["sources"]:
                if source not in config.sources:
                    self.db.execute(
                        "DELETE FROM search_sources WHERE search_id=? AND source=?",
                        (search_id, source),
                    )
            for source in config.sources:
                self.db.execute(
                    "INSERT OR IGNORE INTO search_sources(search_id,source) VALUES (?,?)",
                    (search_id, source),
                )
            self.db.execute(
                "UPDATE search_sources SET initialized=CASE WHEN ? THEN 0 ELSE initialized END,"
                "next_check=0,failures=0,error=NULL WHERE search_id=?",
                (changed, search_id),
            )
        return self.search(search_id, user_id)

    def delete_search(self, search_id, user_id=1):
        with self.db:
            return (
                self.db.execute(
                    "DELETE FROM searches WHERE id=? AND user_id=?", (search_id, user_id)
                ).rowcount
                > 0
            )

    def request_check(self, search_id, user_id=1):
        with self.db:
            self.db.execute(
                "UPDATE search_sources SET next_check=0 WHERE search_id IN "
                "(SELECT id FROM searches WHERE id=? AND user_id=?)",
                (search_id, user_id),
            )

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
        if (
            config.source not in current["sources"]
            or SearchInput.model_validate(current).for_source(config.source) != config
        ):
            return 0
        jobs = [job for job in jobs if not self.is_banned(job.company, current["user_id"])]
        now = time.time()
        new_count = 0
        with self.db:
            for job in jobs:
                self.db.execute(
                    "INSERT OR IGNORE INTO jobs(key,source,payload,first_seen,last_seen,status) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        job.key,
                        job.source,
                        json.dumps(job.to_dict(), ensure_ascii=False),
                        now,
                        now,
                        "pending",
                    ),
                )
                # Alert and application state belongs to a user, even when the listing
                # itself was already found by another user's search.
                state_cursor = self.db.execute(
                    "INSERT OR IGNORE INTO user_job_state "
                    "(user_id,job_key,status,detected_at,last_seen) VALUES (?,?,?,?,?)",
                    (current["user_id"], job.key, "pending", now, now),
                )
                if state_cursor.rowcount:
                    new_count += 1
                else:
                    self.db.execute(
                        "UPDATE user_job_state SET last_seen=? WHERE user_id=? AND job_key=?",
                        (now, current["user_id"], job.key),
                    )
                self.db.execute(
                    "UPDATE jobs SET last_seen=?,payload=? WHERE key=?",
                    (now, json.dumps(job.to_dict(), ensure_ascii=False), job.key),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO search_jobs VALUES (?,?)", (search_id, job.key)
                )
            self.db.execute(
                "UPDATE search_sources SET initialized=1,last_check=?,last_success=?,next_check=?,"
                "last_duration_ms=?,last_count=?,last_new=?,failures=0,error=NULL "
                "WHERE search_id=? AND source=?",
                (
                    now,
                    now,
                    now + config.interval_seconds,
                    duration_ms,
                    len(jobs),
                    new_count,
                    search_id,
                    config.source,
                ),
            )
        return new_count

    def record_failure(self, search_id, error, retry_after=None, source=None):
        parent = self.search(search_id)
        if parent and source is None:
            source = parent["sources"][0]
        current = next((x for x in self.searches_for_source(source) if x["id"] == search_id), None)
        if not current:
            return 60
        failures = current["failures"] + 1
        delay = max(
            retry_after or 0, min(3600, current["interval_seconds"] * 2 ** min(failures, 6))
        )
        with self.db:
            self.db.execute(
                "UPDATE search_sources SET last_check=?,next_check=?,failures=?,error=? "
                "WHERE search_id=? AND source=?",
                (time.time(), time.time() + delay, failures, error, search_id, source),
            )
        return delay

    @staticmethod
    def decode_job(row):
        data = dict(row)
        data.update(json.loads(data.pop("payload")))
        data["applied"] = bool(data["applied"])
        return data

    def jobs(self, limit=100, source=None, status=None, query=None, user_id=1):
        clauses, params = (
            [
                "NOT EXISTS (SELECT 1 FROM user_bans b WHERE b.user_id=? "
                "AND b.normalized=normalize_company(json_extract(jobs.payload, '$.company')))"
            ],
            [user_id],
        )
        if source:
            clauses.append("jobs.source=?")
            params.append(source)
        if status:
            clauses.append("state.status=?")
            params.append(status)
        if query:
            clauses.append("jobs.payload LIKE ?")
            params.append("%" + query + "%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self.db.execute(
            "SELECT jobs.key,jobs.source,jobs.payload,state.detected_at AS first_seen,"
            "state.last_seen,"
            "state.status,state.attempts,state.next_attempt,state.sent_at,state.delivery_ms,"
            "state.error,state.applied FROM jobs "
            "JOIN user_job_state state ON state.job_key=jobs.key"
            + where
            + " AND state.user_id=? ORDER BY state.detected_at DESC,jobs.key LIMIT ?",
            [*params, user_id, limit],
        )
        result = [self.decode_job(row) for row in rows]
        for job in result:
            job["user_id"] = user_id
        return result

    def set_applied(self, key: str, applied: bool, user_id=1):
        with self.db:
            result = self.db.execute(
                "UPDATE user_job_state SET applied=? WHERE job_key=? AND user_id=?",
                (int(applied), key, user_id),
            )
        return bool(result.rowcount)

    def next_delivery(self, configured_only=False):
        configured_clause = (
            "AND users.telegram_token!='' AND users.telegram_chat_id!='' "
            if configured_only
            else ""
        )
        rows = self.db.execute(
            "SELECT jobs.key,jobs.source,jobs.payload,state.detected_at AS first_seen,"
            "state.last_seen,state.status,state.attempts,state.next_attempt,state.sent_at,"
            "state.delivery_ms,state.error,"
            "state.applied,state.user_id,users.telegram_token,users.telegram_chat_id "
            "FROM user_job_state state JOIN jobs ON jobs.key=state.job_key "
            "JOIN users ON users.id=state.user_id WHERE state.status='pending' "
            "AND state.next_attempt<=? AND users.active=1 "
            + configured_clause
            + "ORDER BY state.detected_at,jobs.key",
            (time.time(),),
        )
        for row in rows:
            job = self.decode_job(row)
            job["user_id"] = row["user_id"]
            job["telegram_token"] = row["telegram_token"]
            job["telegram_chat_id"] = row["telegram_chat_id"]
            if not self.is_banned(job["company"], row["user_id"]):
                return job
        return None

    def delivery_sent(self, user_id, key=None, duration_ms=None):
        if isinstance(key, (int, float)) and duration_ms is None:
            user_id, key, duration_ms = 1, user_id, key
        elif key is None:
            user_id, key = 1, user_id
        with self.db:
            self.db.execute(
                "UPDATE user_job_state SET status='sent',sent_at=?,delivery_ms=?,error=NULL "
                "WHERE user_id=? AND job_key=?",
                (time.time(), duration_ms, user_id, key),
            )

    def delivery_failed(self, user_id, key=None, message=None, delay=None, permanent=False):
        if isinstance(user_id, str):
            user_id, key, message, delay = 1, user_id, key, message
        with self.db:
            self.db.execute(
                "UPDATE user_job_state SET status=?,attempts=attempts+1,error=?,next_attempt=? "
                "WHERE user_id=? AND job_key=?",
                ("failed" if permanent else "pending", message, time.time() + delay, user_id, key),
            )

    def retry_failed(self, user_id=1):
        with self.db:
            self.db.execute(
                "UPDATE user_job_state SET status='pending',next_attempt=0 "
                "WHERE user_id=? AND status='failed'",
                (user_id,),
            )

    def summary(self, user_id=1):
        counts = {
            row[0]: row[1]
            for row in self.db.execute(
                "SELECT status,count(*) FROM user_job_state WHERE user_id=? GROUP BY status",
                (user_id,),
            )
        }
        latency_rows = self.db.execute(
            "SELECT (state.sent_at-state.detected_at)*1000 FROM user_job_state state "
            "JOIN jobs ON jobs.key=state.job_key WHERE state.user_id=? AND state.status='sent'",
            (user_id,),
        ).fetchall()
        latencies = [max(0, row[0]) for row in latency_rows if row[0] is not None]
        ordered_latencies = sorted(latencies)
        median_latency = statistics.median(ordered_latencies) if ordered_latencies else None
        p95_latency = (
            ordered_latencies[math.ceil(0.95 * len(ordered_latencies)) - 1]
            if ordered_latencies
            else None
        )
        return {
            "jobs": sum(counts.values()),
            "baseline": counts.get("baseline", 0),
            "pending": counts.get("pending", 0),
            "sent": counts.get("sent", 0),
            "failed": counts.get("failed", 0),
            # Keep the mean available to API clients, but display the median so a
            # one-time unconfigured-Telegram backlog cannot dominate the dashboard.
            "average_detection_to_delivery_ms": statistics.mean(latencies)
            if latencies
            else None,
            "median_detection_to_delivery_ms": median_latency,
            "p95_detection_to_delivery_ms": p95_latency,
            "delayed_delivery_count": sum(delay > 300_000 for delay in latencies),
        }
