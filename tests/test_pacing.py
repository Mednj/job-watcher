import asyncio
import sqlite3
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import SearchInput
from app.monitor import Monitor
from app.sources import SourceError, fetch_jobs
from app.store import Store


@pytest.fixture
def clock(monkeypatch):
    now = [1_800_000_000.0]
    monkeypatch.setattr("time.time", lambda: now[0])
    return now


@pytest.fixture
def store(tmp_path):
    database = Store(str(tmp_path / "pacing.sqlite3"))
    yield database
    database.close()


def add_search(store, source="linkedin", user_id=1):
    return store.add_search(
        SearchInput(name="Pacing", source=source, keywords="cloud", interval_seconds=30), user_id
    )


async def test_rate_limit_is_shared_persistent_and_manual_check_cannot_bypass(store, clock):
    alice = store.add_user("alice", "alice-test-password")
    first = add_search(store)
    second = add_search(store, user_id=alice["id"])
    other = add_search(store, "hellowork")

    async def rate_limited(client, config):
        raise SourceError("Rate limited", 600, status_code=429)

    monitor = Monitor(store, Settings(), fetcher=rate_limited)
    await monitor.scan(first)
    assert monitor.source_status("linkedin")["gap_seconds"] == 30
    assert monitor.source_status("linkedin")["cooldown_until"] == clock[0] + 600
    assert monitor.source_status("linkedin")["pacing_status"] == "cooling_down"
    assert monitor.source_status("hellowork")["gap_seconds"] == 15
    assert store.search(other["id"])["error"] is None
    store.request_check(second["id"], alice["id"])

    # A restarted monitor and connection keep both the cooldown and pacing.
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    restarted_store = Store(path)
    restarted = Monitor(restarted_store, Settings(), fetcher=rate_limited)
    assert restarted.source_status("linkedin") == monitor.source_status("linkedin")

    calls = []

    async def must_not_run(client, config):
        calls.append(config)

    restarted.fetcher = must_not_run
    task = asyncio.create_task(restarted.source_loop("linkedin"))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == []
    restarted_store.close()


async def test_repeated_limits_cap_gap_and_recovery_requires_consecutive_successes(store, clock):
    search = add_search(store)

    async def rate_limited(client, config):
        raise SourceError("Rate limited", 300, status_code=429)

    monitor = Monitor(store, Settings(), fetcher=rate_limited)
    for expected in (30, 60, 120, 120):
        await monitor.scan(search)
        assert monitor.source_status("linkedin")["gap_seconds"] == expected
        clock[0] = monitor.source_status("linkedin")["next_request"]

    async def success(client, config):
        return []

    monitor.fetcher = success
    for _ in range(7):
        await monitor.scan(search)
        assert monitor.source_status("linkedin")["gap_seconds"] == 120
        clock[0] = monitor.source_status("linkedin")["next_request"]

    async def broken(client, config):
        raise SourceError("Unrecognized page")

    monitor.fetcher = broken
    await monitor.scan(search)
    monitor.fetcher = success
    for _ in range(7):
        await monitor.scan(search)
        assert monitor.source_status("linkedin")["gap_seconds"] == 120
        clock[0] = monitor.source_status("linkedin")["next_request"]
    await monitor.scan(search)
    assert monitor.source_status("linkedin")["gap_seconds"] == 90
    assert monitor.source_status("linkedin")["pacing_status"] == "recovering"
    for _ in range(80):
        clock[0] = monitor.source_status("linkedin")["next_request"]
        await monitor.scan(search)
    assert monitor.source_status("linkedin")["gap_seconds"] == 15
    assert monitor.source_status("linkedin")["pacing_status"] == "normal"


async def test_fair_scheduling_uses_shared_gap_after_cooldown(store, clock, monkeypatch):
    first = add_search(store)
    second = add_search(store)
    store.save_source_pacing("linkedin", 60, 0, clock[0] + 300)
    store.set_source_ready("linkedin", clock[0] + 300)
    calls = []

    async def success(client, config):
        calls.append(clock[0])
        return []

    monitor = Monitor(store, Settings(), fetcher=success)
    ticks = iter((299, 1, 59, 1))

    async def tick(seconds):
        try:
            clock[0] += next(ticks)
        except StopIteration:
            raise asyncio.CancelledError

    monkeypatch.setattr("app.monitor.asyncio.sleep", tick)
    start = clock[0]
    with pytest.raises(asyncio.CancelledError):
        await monitor.source_loop("linkedin")
    assert calls == [start + 300, start + 360]
    assert store.search(first["id"])["last_success"] == calls[0]
    assert store.search(second["id"])["last_success"] == calls[1]


async def test_existing_longer_cooldown_and_custom_minimum_are_preserved(store, clock):
    search = add_search(store)
    store.set_source_ready("linkedin", clock[0] + 900)

    async def limited(client, config):
        raise SourceError("Rate limited", 60, status_code=429)

    monitor = Monitor(store, Settings(source_gap=180), fetcher=limited)
    await monitor.scan(search)
    assert monitor.source_status("linkedin")["gap_seconds"] == 180
    assert monitor.source_status("linkedin")["next_request"] == clock[0] + 900


@pytest.mark.parametrize("header,expected", [("600", 600), ("0", 1), ("invalid", 300), ("", 300)])
async def test_retry_after_formats_and_status(header, expected, clock):
    config = SearchInput(name="Retry", source="linkedin", keywords="cloud")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(429, headers={"Retry-After": header})
    )) as client:
        with pytest.raises(SourceError) as exc:
            await fetch_jobs(client, config)
    assert exc.value.retry_after == expected
    assert exc.value.status_code == 429


async def test_retry_after_http_date_is_not_replaced_with_shorter_fallback(clock):
    future = format_datetime(datetime.fromtimestamp(clock[0] + 1800, UTC), usegmt=True)
    await test_retry_after_formats_and_status(future, 1800, clock)


def test_additive_migration_keeps_existing_source_cooldown(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE source_state (source TEXT PRIMARY KEY, next_request REAL)")
        db.execute("INSERT INTO source_state VALUES ('linkedin', 12345)")
    store = Store(path)
    assert store.source_ready_at("linkedin") == 12345
    assert store.source_pacing("linkedin", 15)["gap_seconds"] == 15
    store.close()


def test_status_exposes_pacing_without_other_accounts_searches(tmp_path, clock):
    settings = Settings(database=str(tmp_path / "api.sqlite3"), access_token="pacing-test-only")
    app = create_app(settings, run_monitor=False)
    with TestClient(app) as client:
        store = app.state.store
        other = store.add_user("other", "other-test-password")
        add_search(store, user_id=other["id"])
        store.save_source_pacing("linkedin", 60, 0, clock[0] + 900)
        token = client.post("/api/auth/login", json={
            "username": "admin", "password": "pacing-test-only"
        }).json()["token"]
        status = client.get("/api/status", headers={"Authorization": f"Bearer {token}"}).json()
        assert status["searches"] == []
        pacing = next(x for x in status["sources"] if x["source"] == "linkedin")
        assert pacing["pacing_status"] == "cooling_down"
        assert pacing["next_request"] == clock[0] + 900
        assert pacing["gap_seconds"] == 60
