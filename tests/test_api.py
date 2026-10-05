from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_search_crud_validation_and_dashboard(tmp_path):
    app = create_app(Settings(database=str(tmp_path / "api.sqlite3")), run_monitor=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/status").json()["summary"]["jobs"] == 0
        config = {"name": "Cloud", "source": "hellowork", "keywords": "cloud", "contract": "CDI"}
        created = client.post("/api/searches", json=config)
        assert created.status_code == 201
        id = created.json()["id"]
        assert client.post(f"/api/searches/{id}/check").status_code == 202
        assert (
            client.put(f"/api/searches/{id}", json={**config, "enabled": False}).status_code == 200
        )
        assert client.post(f"/api/searches/{id}/check").status_code == 400
        assert (
            client.post("/api/searches", json={**config, "interval_seconds": 1}).status_code == 422
        )
        assert client.post("/api/searches", json={**config, "keywords": "  "}).status_code == 422
        assert client.post("/api/telegram/test").status_code == 400
        assert client.delete(f"/api/searches/{id}").status_code == 200
        assert client.delete(f"/api/searches/{id}").status_code == 404


def test_auth_and_cross_origin_writes(tmp_path):
    settings = Settings(
        database=str(tmp_path / "secure.sqlite3"),
        access_token="app-secret",
        telegram_token="bot-secret",
        telegram_chat_id="123",
    )
    with TestClient(create_app(settings, run_monitor=False)) as client:
        assert client.get("/api/status").status_code == 401
        headers = {"Authorization": "Bearer app-secret"}
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


def test_remote_requests_require_access_token(tmp_path):
    settings = Settings(database=str(tmp_path / "remote.sqlite3"))
    with TestClient(create_app(settings, run_monitor=False), client=("203.0.113.1", 123)) as client:
        assert client.get("/api/status").status_code == 403


def test_application_status_persists_and_is_independent_of_delivery(tmp_path):
    from app.models import Job, SearchInput
    from app.store import Store

    database = str(tmp_path / "applications.sqlite3")
    with TestClient(create_app(Settings(database=database), run_monitor=False)) as client:
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
            client.patch("/api/jobs/linkedin:123/application", json={"applied": True}).status_code
            == 200
        )
        saved = client.get("/api/jobs").json()[0]
        assert saved["applied"] is True
        assert saved["status"] == "pending"
        store.record_scan(search["id"], config.for_source("linkedin"), [job], 5)
        assert client.get("/api/jobs").json()[0]["applied"] is True
        assert (
            client.patch("/api/jobs/missing/application", json={"applied": True}).status_code == 404
        )
        assert (
            client.patch("/api/jobs/linkedin:123/application", json={"applied": "yes"}).status_code
            == 422
        )
    store = Store(database)
    assert store.jobs()[0]["applied"] is True
    store.set_applied(job.key, False)
    assert store.jobs()[0]["applied"] is False
    store.close()
