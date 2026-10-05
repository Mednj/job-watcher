import time

import httpx
import pytest

from app.config import Settings
from app.models import Job, SearchInput
from app.monitor import Monitor
from app.sources import SourceError
from app.store import Store
from app.telegram import DeliveryError, Telegram, format_message


@pytest.fixture
def store(tmp_path):
    database = Store(str(tmp_path / "jobs.sqlite3"))
    yield database
    database.close()


def config(**kwargs):
    return SearchInput(name="Cloud", source="linkedin", keywords="cloud", **kwargs)


def job(id="1"):
    return Job(
        "linkedin",
        id,
        "Cloud engineer",
        "Example",
        "Paris",
        f"https://www.linkedin.com/jobs/view/{id}/",
    )


def test_first_scan_alerts_then_only_new_jobs_notify_and_survive_restart(tmp_path):
    path = str(tmp_path / "persistent.sqlite3")
    store = Store(path)
    search = store.add_search(config())
    assert store.record_scan(search["id"], config(), [job()], 10) == 1
    assert store.jobs()[0]["status"] == "pending"
    store.delivery_sent(job().key, 10)
    assert store.record_scan(search["id"], config(), [job(), job("2")], 10) == 1
    store.close()
    store = Store(path)
    assert store.search(search["id"])["initialized"]
    assert store.next_delivery()["key"] == "linkedin:2"
    assert store.record_scan(search["id"], config(), [job(), job("2")], 10) == 0
    assert store.summary()["jobs"] == 2
    store.close()


def test_same_job_across_searches_has_one_alert(store):
    first = store.add_search(config())
    second_config = config(location="Paris")
    second = store.add_search(second_config)
    store.record_scan(first["id"], config(), [], 1)
    store.record_scan(second["id"], second_config, [], 1)
    assert store.record_scan(first["id"], config(), [job()], 1) == 1
    assert store.record_scan(second["id"], second_config, [job()], 1) == 0
    assert store.summary()["pending"] == 1


def test_pause_resume_and_scope_change_first_scan_state(store):
    search = store.add_search(config())
    store.record_scan(search["id"], config(), [job()], 1)
    store.update_search(search["id"], config(enabled=False))
    assert store.search(search["id"])["initialized"]
    store.update_search(search["id"], config())
    assert store.search(search["id"])["initialized"]
    store.update_search(search["id"], config(location="Lyon"))
    assert not store.search(search["id"])["initialized"]
    # An old in-flight response cannot contaminate a newly edited search.
    store.record_scan(search["id"], config(), [job("2")], 1)
    assert store.summary()["jobs"] == 1


def test_failed_initial_scan_does_not_suppress_first_successful_alerts(store):
    search = store.add_search(config())
    delay = store.record_failure(search["id"], "Blocked", 600)
    assert delay >= 600
    assert not store.search(search["id"])["initialized"]
    store.record_scan(search["id"], config(), [job()], 1)
    assert store.summary()["pending"] == 1


async def test_platform_backoff_survives_manual_check(store):
    async def blocked(client, search):
        raise SourceError("Blocked", 600)

    search = store.add_search(config())
    monitor = Monitor(store, Settings(), fetcher=blocked)
    await monitor.scan(search)
    ready = store.source_ready_at("linkedin")
    assert ready >= time.time() + 590
    store.request_check(search["id"])
    assert store.source_ready_at("linkedin") == ready


async def test_stale_failed_request_does_not_poison_edited_search(store):
    search = store.add_search(config())

    async def stale_failure(client, old_config):
        store.update_search(search["id"], config(location="Lyon"))
        raise SourceError("Blocked", 600)

    monitor = Monitor(store, Settings(), fetcher=stale_failure)
    await monitor.scan(search)
    current = store.search(search["id"])
    assert current["location"] == "Lyon"
    assert current["error"] is None
    assert store.source_ready_at("linkedin") >= time.time() + 590


def test_message_escapes_untrusted_job_text():
    data = job().to_dict()
    data["title"] = '<script>alert("x")</script>'
    message = format_message(data)
    assert "<script>" not in message
    assert "&lt;script&gt;" in message
    assert "View &amp; apply" in message


async def test_telegram_success_retry_and_secret_redaction():
    settings = Settings(telegram_token="secret-token", telegram_chat_id="123")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
        )
    ) as client:
        assert await Telegram(settings, client).send("Test") == 7
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                429, json={"ok": False, "parameters": {"retry_after": 50}}
            )
        )
    ) as client:
        with pytest.raises(DeliveryError) as error:
            await Telegram(settings, client).send("Test")
        assert error.value.retry_after == 50

    def network_failure(request):
        raise httpx.ConnectError(str(request.url), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(network_failure)) as client:
        with pytest.raises(DeliveryError) as error:
            await Telegram(settings, client).send("Test")
        assert "secret-token" not in str(error.value)


async def test_delivery_retries_and_records_latency(store):
    search = store.add_search(config())
    store.record_scan(search["id"], config(), [], 1)
    store.record_scan(search["id"], config(), [job()], 1)
    monitor = Monitor(store, Settings())

    class RetryBot:
        async def send(self, message):
            raise DeliveryError("Rate limited", 30)

    monitor.telegram = RetryBot()
    await monitor.deliver_one(store.next_delivery())
    assert store.next_delivery() is None
    pending = store.jobs()[0]
    assert pending["status"] == "pending"
    assert pending["attempts"] == 1
    assert pending["next_attempt"] > time.time() + 25

    class WorkingBot:
        async def send(self, message):
            return 1

    monitor.telegram = WorkingBot()
    await monitor.deliver_one(pending)
    assert store.jobs()[0]["status"] == "sent"
    assert store.summary()["average_detection_to_delivery_ms"] >= 0


def test_permanent_failure_retry_and_search_deletion_keeps_history(store):
    search = store.add_search(config())
    store.record_scan(search["id"], config(), [], 1)
    store.record_scan(search["id"], config(), [job()], 1)
    store.delivery_failed(job().key, "Bad credentials", 30, permanent=True)
    assert store.summary()["failed"] == 1
    store.retry_failed()
    assert store.next_delivery()["key"] == job().key
    store.delete_search(search["id"])
    assert store.summary()["jobs"] == 1
