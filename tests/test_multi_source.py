import json
import sqlite3

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


def test_multi_source_api_validates_selection_and_legacy_compatibility(tmp_path):
    settings = Settings(database=str(tmp_path / "api.sqlite3"))
    with TestClient(create_app(settings, run_monitor=False)) as client:
        payload = {"name": "IT", "keywords": "IT", "sources": ["linkedin", "hellowork"]}
        response = client.post("/api/searches", json=payload)
        assert response.status_code == 201
        assert response.json()["sources"] == ["linkedin", "hellowork"]
        assert len(response.json()["source_statuses"]) == 2
        assert client.post("/api/searches", json={**payload, "sources": []}).status_code == 422
        assert (
            client.post("/api/searches", json={**payload, "sources": ["unknown"]}).status_code
            == 422
        )
        legacy = client.post(
            "/api/searches", json={"name": "Data", "keywords": "data", "source": "hellowork"}
        )
        assert legacy.json()["sources"] == ["hellowork"]
