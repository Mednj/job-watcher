from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def login_headers(client, username="admin", password="app-secret"):
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def test_search_crud_validation_and_dashboard(tmp_path):
    app = create_app(Settings(database=str(tmp_path / "api.sqlite3")), run_monitor=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        headers = login_headers(client, password="admin@")
        assert client.get("/api/status", headers=headers).json()["summary"]["jobs"] == 0
        config = {"name": "Cloud", "source": "hellowork", "keywords": "cloud", "contract": "CDI"}
        created = client.post("/api/searches", json=config, headers=headers)
        assert created.status_code == 201
        id = created.json()["id"]
        assert client.post(f"/api/searches/{id}/check", headers=headers).status_code == 202
        assert (
            client.put(
                f"/api/searches/{id}", json={**config, "enabled": False}, headers=headers
            ).status_code
            == 200
        )
        assert client.post(f"/api/searches/{id}/check", headers=headers).status_code == 400
        assert (
            client.post(
                "/api/searches", json={**config, "interval_seconds": 1}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/searches", json={**config, "keywords": "  "}, headers=headers
            ).status_code
            == 422
        )
        assert client.post("/api/telegram/test", headers=headers).status_code == 400
        assert client.delete(f"/api/searches/{id}", headers=headers).status_code == 200
        assert client.delete(f"/api/searches/{id}", headers=headers).status_code == 404


def test_auth_and_cross_origin_writes(tmp_path):
    settings = Settings(
        database=str(tmp_path / "secure.sqlite3"),
        access_token="app-secret",
        telegram_token="bot-secret",
        telegram_chat_id="123",
    )
    with TestClient(create_app(settings, run_monitor=False)) as client:
        assert client.get("/api/status").status_code == 401
        headers = login_headers(client)
        status = client.get("/api/status", headers=headers)
        assert status.status_code == 200
        assert "bot-secret" not in status.text
        assert "app-secret" not in status.text
        config = {"name": "IT", "source": "linkedin", "keywords": "IT"}
        assert (
            client.post(
                "/api/searches", json=config, headers={**headers, "Origin": "https://other.example"}
            ).status_code
            == 403
        )


def test_reverse_proxy_public_origin_allows_same_origin_write_only(tmp_path):
    settings = Settings(
        database=str(tmp_path / "proxy.sqlite3"),
        access_token="app-secret",
        public_origin="https://job-watcher.example.com/",
    )
    with TestClient(create_app(settings, run_monitor=False)) as client:
        headers = login_headers(client)
        headers["Origin"] = "https://job-watcher.example.com"
        # The middleware allows this same-origin request through to the route.
        # A missing job returns 404, proving it was not stopped with an origin 403.
        assert (
            client.patch(
                "/api/jobs/missing/application", json={"applied": True}, headers=headers
            ).status_code
            == 404
        )
        headers["Origin"] = "https://attacker.example"
        assert (
            client.patch(
                "/api/jobs/missing/application", json={"applied": True}, headers=headers
            ).status_code
            == 403
        )


def test_remote_requests_require_access_token(tmp_path):
    settings = Settings(database=str(tmp_path / "remote.sqlite3"))
    with TestClient(create_app(settings, run_monitor=False), client=("203.0.113.1", 123)) as client:
        assert client.get("/api/status").status_code == 403


def test_accounts_keep_searches_jobs_bans_and_telegram_private(tmp_path):
    from app.models import Job, SearchInput

    settings = Settings(
        database=str(tmp_path / "accounts.sqlite3"),
        access_token="initial-admin-password",
    )
    with TestClient(create_app(settings, run_monitor=False)) as client:
        admin_headers = login_headers(client, password="initial-admin-password")
        alice = client.post(
            "/api/users",
            json={"username": "alice", "password": "alice-password-1"},
            headers=admin_headers,
        )
        bob = client.post(
            "/api/users",
            json={"username": "bob", "password": "bob-password-11"},
            headers=admin_headers,
        )
        assert alice.status_code == bob.status_code == 201
        alice_headers = login_headers(client, "alice", "alice-password-1")
        bob_headers = login_headers(client, "bob", "bob-password-11")

        config = {"name": "Cloud", "source": "linkedin", "keywords": "cloud"}
        alice_search = client.post("/api/searches", json=config, headers=alice_headers).json()
        bob_search = client.post("/api/searches", json=config, headers=bob_headers).json()
        assert (
            client.put(
                f"/api/searches/{bob_search['id']}", json=config, headers=alice_headers
            ).status_code
            == 404
        )
        assert (
            client.delete(f"/api/searches/{bob_search['id']}", headers=alice_headers).status_code
            == 404
        )
        assert len(client.get("/api/status", headers=alice_headers).json()["searches"]) == 1
        assert len(client.get("/api/status", headers=bob_headers).json()["searches"]) == 1
        assert client.get("/api/status", headers=admin_headers).json()["searches"] == []

        store = client.app.state.store
        job = Job(
            "linkedin",
            "private-1",
            "Cloud engineer",
            "Example employer",
            "Paris",
            "https://www.linkedin.com/jobs/view/private-1/",
        )
        search_config = SearchInput.model_validate(config).for_source("linkedin")
        store.record_scan(alice_search["id"], search_config, [job], 2)
        store.record_scan(bob_search["id"], search_config, [job], 2)
        assert [x["key"] for x in client.get("/api/jobs", headers=alice_headers).json()] == [
            job.key
        ]
        assert [x["key"] for x in client.get("/api/jobs", headers=bob_headers).json()] == [job.key]
        assert client.get("/api/jobs", headers=admin_headers).json() == []
        assert (
            client.patch(
                f"/api/jobs/{job.key}/application",
                json={"applied": True},
                headers=admin_headers,
            ).status_code
            == 404
        )

        assert (
            client.post(
                "/api/recruiter-bans",
                json={"name": "Example employer"},
                headers=alice_headers,
            ).status_code
            == 200
        )
        assert len(client.get("/api/recruiter-bans", headers=alice_headers).json()) == 1
        assert client.get("/api/recruiter-bans", headers=bob_headers).json() == []
        assert client.get("/api/jobs", headers=alice_headers).json() == []
        assert len(client.get("/api/jobs", headers=bob_headers).json()) == 1

        for headers, token, chat_id in (
            (alice_headers, "alice-bot-secret", "1001"),
            (bob_headers, "bob-bot-secret", "2002"),
        ):
            saved = client.put(
                "/api/telegram/settings",
                json={"bot_token": token, "chat_id": chat_id},
                headers=headers,
            )
            assert saved.json() == {"configured": True, "chat_id": chat_id}
            assert token not in saved.text
        assert client.get("/api/telegram/settings", headers=admin_headers).json() == {
            "configured": False,
            "chat_id": "",
        }

        assert client.get("/api/users", headers=alice_headers).status_code == 403
        users = client.get("/api/users", headers=admin_headers)
        assert users.status_code == 200
        assert {entry["username"] for entry in users.json()} == {"admin", "alice", "bob"}
        assert "password_hash" not in users.text
        assert "bot-secret" not in users.text


def test_self_registration_creates_a_scoped_non_admin_account(tmp_path):
    settings = Settings(
        database=str(tmp_path / "registration.sqlite3"),
        access_token="registration-admin-password",
    )
    with TestClient(create_app(settings, run_monitor=False)) as client:
        registered = client.post(
            "/api/auth/register",
            json={"username": "newperson", "password": "newperson-password"},
        )
        assert registered.status_code == 201
        assert registered.json()["user"]["role"] == "user"
        headers = {"Authorization": "Bearer " + registered.json()["token"]}
        assert client.get("/api/status", headers=headers).json()["searches"] == []
        assert client.get("/api/users", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/users",
                json={"username": "hacker", "password": "not-an-admin-password"},
                headers=headers,
            ).status_code
            == 403
        )


def test_application_status_persists_and_is_independent_of_delivery(tmp_path):
    from app.models import Job, SearchInput
    from app.store import Store

    database = str(tmp_path / "applications.sqlite3")
    with TestClient(create_app(Settings(database=database), run_monitor=False)) as client:
        headers = login_headers(client, password="admin@")
        store = client.app.state.store
        config = SearchInput(name="IT", sources=["linkedin"], keywords="cloud")
        search = store.add_search(config)
        job = Job(
            "linkedin",
            "123",
            "Cloud engineer",
            "Example",
            "Paris",
            "https://www.linkedin.com/jobs/view/123/",
        )
        store.record_scan(search["id"], config.for_source("linkedin"), [job], 5)
        assert (
            client.patch(
                "/api/jobs/linkedin:123/application", json={"applied": True}, headers=headers
            ).status_code
            == 200
        )
        saved = client.get("/api/jobs", headers=headers).json()[0]
        assert saved["applied"] is True
        assert saved["status"] == "pending"
        store.record_scan(search["id"], config.for_source("linkedin"), [job], 5)
        assert client.get("/api/jobs", headers=headers).json()[0]["applied"] is True
        assert (
            client.patch(
                "/api/jobs/missing/application", json={"applied": True}, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.patch(
                "/api/jobs/linkedin:123/application", json={"applied": "yes"}, headers=headers
            ).status_code
            == 422
        )
    store = Store(database)
    assert store.jobs()[0]["applied"] is True
    store.set_applied(job.key, False)
    assert store.jobs()[0]["applied"] is False
    store.close()


def test_recruiter_bans_filter_new_scans_and_queued_alerts(tmp_path):
    from app.models import Job, SearchInput
    from app.store import Store

    database = str(tmp_path / "bans.sqlite3")
    with TestClient(create_app(Settings(database=database), run_monitor=False)) as client:
        headers = login_headers(client, password="admin@")
        store = client.app.state.store
        config = SearchInput(name="Cloud", sources=["linkedin"], keywords="cloud")
        search = store.add_search(config)
        job = Job(
            "linkedin",
            "111",
            "Cloud engineer",
            "Éxample",
            "Paris",
            "https://www.linkedin.com/jobs/view/111/",
        )
        store.record_scan(search["id"], config.for_source("linkedin"), [job], 1)
        assert store.next_delivery() is not None
        assert (
            client.post(
                "/api/recruiter-bans", json={"name": "example"}, headers=headers
            ).status_code
            == 200
        )
        assert client.get("/api/jobs", headers=headers).json() == []
        assert store.next_delivery() is None
        second = Job(
            "linkedin",
            "222",
            "Cloud engineer",
            "EXAMPLE",
            "Paris",
            "https://www.linkedin.com/jobs/view/222/",
        )
        assert store.record_scan(search["id"], config.for_source("linkedin"), [second], 1) == 0
        assert (
            client.post(
                "/api/recruiter-bans", json={"name": "Not listed"}, headers=headers
            ).status_code
            == 422
        )
    store = Store(database)
    assert store.is_banned("Éxample")
    assert len(store.banned_recruiters()) == 1
    store.unban_recruiter("EXAMPLE")
    assert store.next_delivery()["key"] == job.key
    assert store.record_scan(search["id"], config.for_source("linkedin"), [second], 1) == 1
    store.close()
