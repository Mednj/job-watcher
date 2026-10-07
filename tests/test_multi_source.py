import json
import sqlite3
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Job, SearchInput
from app.monitor import Monitor
from app.sources import SourceError
from app.store import Store


async def test_platforms_scan_independently_and_share_one_search(tmp_path):
    store = Store(str(tmp_path / "multi.sqlite3"))
    config = SearchInput(name="Cloud", keywords="cloud", sources=["linkedin", "hellowork"])
    search = store.add_search(config)

    async def fetch(client, target):
        if target.source == "linkedin":
            raise SourceError("Restricted", 600)
        return [
            Job(
                "hellowork",
                "1",
                "Cloud engineer",
                "Example",
                "Paris",
                "https://www.hellowork.com/fr-fr/emplois/1.html",
            )
        ]

    monitor = Monitor(store, Settings(), fetcher=fetch)
    await monitor.scan(store.searches_for_source("linkedin")[0])
    await monitor.scan(store.searches_for_source("hellowork")[0])
    states = {x["source"]: x for x in store.search(search["id"])["source_statuses"]}
    assert states["linkedin"]["error"]
    assert not states["linkedin"]["initialized"]
    assert states["hellowork"]["error"] is None
    assert states["hellowork"]["last_count"] == 1
    assert store.summary()["pending"] == 1
    store.request_check(search["id"])
    assert all(x["next_check"] == 0 for x in store.search(search["id"])["source_statuses"])
    store.update_search(search["id"], config.model_copy(update={"sources": ["hellowork"]}))
    assert store.searches_for_source("linkedin") == []
    assert store.search(search["id"])["initialized"]
    assert store.summary()["jobs"] == 1
    store.close()


def test_legacy_search_migrates_without_losing_history_or_resending(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    store = Store(path)
    config = SearchInput(name="Cloud", source="linkedin", keywords="cloud")
    search = store.add_search(config)
    job = Job(
        "linkedin",
        "1",
        "Cloud engineer",
        "Example",
        "Paris",
        "https://www.linkedin.com/jobs/view/1/",
    )
    store.record_scan(search["id"], config, [job], 10)
    store.delivery_sent(job.key, 10)
    store.close()
    with sqlite3.connect(path) as db:
        legacy = {"name": "Cloud", "source": "linkedin", "keywords": "cloud"}
        db.execute(
            "UPDATE searches SET config=?,initialized=1,last_count=1 WHERE id=?",
            (json.dumps(legacy), search["id"]),
        )
        db.execute("DELETE FROM search_sources")
    store = Store(path)
    restored = store.search(search["id"])
    assert restored["sources"] == ["linkedin"]
    assert restored["initialized"]
    assert restored["last_count"] == 1
    assert store.next_delivery() is None
    store.close()


def test_single_user_database_migrates_to_private_admin_without_data_loss(tmp_path):
    path = str(tmp_path / "pre-accounts.sqlite3")
    now = time.time()
    payload = {
        "source": "linkedin",
        "source_id": "old-1",
        "title": "Cloud engineer",
        "company": "Old employer",
        "location": "Paris",
        "url": "https://www.linkedin.com/jobs/view/old-1/",
        "contract": "CDI",
        "published_at": None,
        "published_precision": None,
        "published_label": "Today",
    }
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE banned_recruiters(
                name TEXT NOT NULL, normalized TEXT PRIMARY KEY, created_at REAL NOT NULL
            );
            CREATE TABLE searches(
                id INTEGER PRIMARY KEY, config TEXT NOT NULL, initialized INTEGER DEFAULT 0,
                next_check REAL DEFAULT 0, last_check REAL, last_success REAL,
                last_duration_ms REAL, last_count INTEGER DEFAULT 0, last_new INTEGER DEFAULT 0,
                failures INTEGER DEFAULT 0, error TEXT
            );
            CREATE TABLE jobs(
                key TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL,
                first_seen REAL NOT NULL, last_seen REAL NOT NULL, status TEXT NOT NULL,
                attempts INTEGER DEFAULT 0, next_attempt REAL DEFAULT 0, sent_at REAL,
                delivery_ms REAL, error TEXT, applied INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE search_jobs(
                search_id INTEGER, job_key TEXT, PRIMARY KEY(search_id,job_key)
            );
            CREATE TABLE search_sources(
                search_id INTEGER, source TEXT, initialized INTEGER DEFAULT 0,
                next_check REAL DEFAULT 0, last_check REAL, last_success REAL,
                last_duration_ms REAL, last_count INTEGER DEFAULT 0,
                last_new INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, error TEXT,
                PRIMARY KEY(search_id,source)
            );
            CREATE TABLE source_state(source TEXT PRIMARY KEY, next_request REAL DEFAULT 0);
        """)
        config = {"name": "Legacy cloud", "source": "linkedin", "keywords": "cloud"}
        db.execute(
            "INSERT INTO searches(id,config,initialized,last_check,last_success,last_count) "
            "VALUES (1,?,1,?,?,1)",
            (json.dumps(config), now, now),
        )
        db.execute(
            "INSERT INTO search_sources(search_id,source,initialized,last_check,last_success,"
            "last_count) "
            "VALUES (1,'linkedin',1,?,?,1)",
            (now, now),
        )
        db.execute(
            "INSERT INTO jobs(key,source,payload,first_seen,last_seen,status,sent_at,delivery_ms,"
            "applied) "
            "VALUES ('linkedin:old-1','linkedin',?,?,?,'sent',?,20,1)",
            (json.dumps(payload), now - 600, now, now),
        )
        db.execute("INSERT INTO search_jobs VALUES (1,'linkedin:old-1')")
        db.execute("INSERT INTO banned_recruiters VALUES ('Old block','old block',?)", (now,))

    store = Store(path, bootstrap_password="migrated-admin-password")
    restored = store.search(1, 1)
    assert restored["name"] == "Legacy cloud"
    assert restored["user_id"] == 1
    assert store.summary(1)["sent"] == 1
    assert store.jobs(user_id=1)[0]["applied"] is True
    assert store.banned_recruiters(1)[0]["name"] == "Old block"
    assert store.next_delivery(configured_only=True) is None
    assert store.authenticate("admin", "migrated-admin-password")["role"] == "admin"
    store.close()


def test_multi_source_api_validates_selection_and_legacy_compatibility(tmp_path):
    settings = Settings(database=str(tmp_path / "api.sqlite3"))
    with TestClient(create_app(settings, run_monitor=False)) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin@"})
        headers = {"Authorization": "Bearer " + login.json()["token"]}
        payload = {"name": "IT", "keywords": "IT", "sources": ["linkedin", "hellowork"]}
        response = client.post("/api/searches", json=payload, headers=headers)
        assert response.status_code == 201
        assert response.json()["sources"] == ["linkedin", "hellowork"]
        assert len(response.json()["source_statuses"]) == 2
        assert (
            client.post(
                "/api/searches", json={**payload, "sources": []}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/searches", json={**payload, "sources": ["unknown"]}, headers=headers
            ).status_code
            == 422
        )
        legacy = client.post(
            "/api/searches",
            json={"name": "Data", "keywords": "data", "source": "hellowork"},
            headers=headers,
        )
        assert legacy.json()["sources"] == ["hellowork"]
