import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import Job, SearchInput
from app.store import Store


@pytest.fixture
def tracker(tmp_path):
    database = str(tmp_path / "tracking.sqlite3")
    app = create_app(Settings(database=database), run_monitor=False)
    with TestClient(app) as client:
        token = client.post(
            "/api/auth/login", json={"username": "admin", "password": "admin@"}
        ).json()["token"]
        headers = {"Authorization": "Bearer " + token}
        store = app.state.store
        config = SearchInput(name="Cloud", source="linkedin", keywords="cloud")
        search = store.add_search(config)
        job = Job(
            "linkedin",
            "123",
            "Cloud engineer",
            "Example",
            "Paris",
            "https://www.linkedin.com/jobs/view/123/",
        )
        store.record_scan(search["id"], config, [job], 5)
        yield client, headers, store, job, database


def test_tracking_requires_explicit_opt_in_and_survives_restart(tracker):
    client, headers, store, job, database = tracker
    url = "/api/applications/" + job.key
    assert client.post(url, json={}, headers=headers).status_code == 409
    assert client.get("/api/applications", headers=headers).json()["total"] == 0
    store.set_applied(job.key, True)
    assert client.get("/api/applications", headers=headers).json()["total"] == 0
    created = client.post(url, json={"applied_on": "2026-11-03"}, headers=headers).json()
    assert created["status"] == "applied"
    assert created["applied_on"] == "2026-11-03"
    # A retried request must not reset progress or create duplicate history.
    again = client.post(url, json={}, headers=headers).json()
    assert again == created
    assert client.get("/api/jobs", headers=headers).json()[0]["tracked"] == 1
    reopened = Store(database)
    assert reopened.tracked_application(job.key, 1)["applied_on"] == "2026-11-03"
    reopened.close()


def test_tracking_updates_history_conflicts_filters_and_removal(tracker):
    client, headers, store, job, _ = tracker
    url = "/api/applications/" + job.key
    store.set_applied(job.key, True)
    client.post(url, json={}, headers=headers)
    payload = {
        "status": "interviewing",
        "notes": "Meet recruiter <Tuesday>",
        "applied_on": "2026-10-30",
        "version": 1,
    }
    saved = client.put(url, json=payload, headers=headers)
    assert saved.status_code == 200
    saved = saved.json()
    assert saved["version"] == 2
    assert len(saved["history"]) == 2
    assert saved["history"][0]["notes"] == payload["notes"]
    assert client.put(url, json=payload, headers=headers).status_code == 409
    assert client.get("/api/applications?status=rejected", headers=headers).json()["total"] == 0
    matches = client.get(
        "/api/applications?status=interviewing&q=EXAMPLE&limit=1", headers=headers
    ).json()
    assert matches["total"] == 1
    assert matches["counts"] == {"interviewing": 1}
    assert client.get("/api/applications?offset=1", headers=headers).json()["items"] == []
    assert client.delete(url, headers=headers).status_code == 200
    assert store.jobs()[0]["applied"] is True
    assert client.get(url, headers=headers).status_code == 404
    restored = client.post(url, json={}, headers=headers).json()
    assert restored["notes"] == payload["notes"]
    assert restored["status"] == "interviewing"
    assert [x["kind"] for x in restored["history"]] == ["readded", "removed", "updated", "added"]
    store.set_applied(job.key, False)
    assert client.get("/api/applications", headers=headers).json()["total"] == 0
    assert client.post(url, json={}, headers=headers).status_code == 409
    store.set_applied(job.key, True)
    assert client.get("/api/applications", headers=headers).json()["total"] == 0


def test_tracking_is_private_even_for_a_shared_job(tracker):
    client, headers, store, job, _ = tracker
    other = client.post(
        "/api/auth/register", json={"username": "other", "password": "other-test-password"}
    ).json()
    other_headers = {"Authorization": "Bearer " + other["token"]}
    url = "/api/applications/" + job.key
    store.set_applied(job.key, True)
    client.post(url, json={}, headers=headers)
    for method in ("get", "post", "put", "delete"):
        kwargs = {"headers": other_headers}
        if method == "post":
            kwargs["json"] = {}
        if method == "put":
            kwargs["json"] = {"status": "rejected", "version": 1}
        assert getattr(client, method)(url, **kwargs).status_code == 404
    config = SearchInput(name="Cloud", source="linkedin", keywords="cloud")
    search = store.add_search(config, other["user"]["id"])
    store.record_scan(search["id"], config, [job], 5)
    store.set_applied(job.key, True, other["user"]["id"])
    assert client.get("/api/applications", headers=other_headers).json()["total"] == 0
    client.post(url, json={}, headers=other_headers)
    client.put(
        url, json={"status": "rejected", "notes": "private", "version": 1}, headers=other_headers
    )
    original = client.get(url, headers=headers).json()
    assert original["status"] == "applied"
    assert original["notes"] == ""
    assert len(original["history"]) == 1
    assert client.get(url).status_code == 401


def test_tracking_validation_and_banned_company_retention(tracker):
    client, headers, store, job, _ = tracker
    store.set_applied(job.key, True)
    url = "/api/applications/" + job.key
    assert client.post(url, json={"applied_on": "invalid"}, headers=headers).status_code == 422
    client.post(url, json={}, headers=headers)
    for change in (
        {"status": "invented"},
        {"notes": "x" * 5001},
        {"applied_on": "2026-99-99"},
        {"version": 0},
    ):
        payload = {"status": "applied", "version": 1, **change}
        assert client.put(url, json=payload, headers=headers).status_code == 422
    store.ban_recruiter("Example")
    assert not store.jobs()
    assert client.get("/api/applications", headers=headers).json()["total"] == 1
    assert (
        client.post(url, json={}, headers={**headers, "Origin": "https://evil.example"}).status_code
        == 403
    )


def test_additive_migration_and_backup_restore_do_not_enroll_old_applications(tracker, tmp_path):
    _, _, store, job, database = tracker
    store.set_applied(job.key, True)
    backup_path = tmp_path / "verified-backup.sqlite3"
    with sqlite3.connect(backup_path) as backup:
        store.db.backup(backup)
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        backup.execute("DROP TABLE application_events")
        backup.execute("DROP TABLE application_tracking")
    upgraded = Store(str(backup_path))
    assert upgraded.jobs()[0]["applied"] is True
    assert upgraded.tracked_applications(1)["total"] == 0
    assert upgraded.summary()["jobs"] == store.summary()["jobs"]
    assert upgraded.db.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
