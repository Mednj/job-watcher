import asyncio
import logging
import time
from types import SimpleNamespace

import httpx

from app.config import Settings
from app.models import SOURCES, SearchInput
from app.sources import SourceError, fetch_jobs
from app.store import Store
from app.telegram import DeliveryError, Telegram, format_message

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(self, store: Store, settings: Settings, fetcher=fetch_jobs):
        self.store, self.settings, self.fetcher = store, settings, fetcher
        self.tasks = []
        self.wake_delivery = asyncio.Event()
        self.client = None
        self.telegram_client = None
        self.telegram = None
        self.delivery_error = {}
        self.running = False
        self.telegram_gate = {}

    async def start(self):
        self.client = httpx.AsyncClient(
            timeout=self.settings.http_timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; JobWatcher/0.1)",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
        )
        self.telegram_client = httpx.AsyncClient(timeout=self.settings.http_timeout)
        self.telegram = Telegram(self.settings, self.telegram_client)
        self.running = True
        self.tasks = [asyncio.create_task(self.source_loop(source)) for source in SOURCES]
        self.tasks.append(asyncio.create_task(self.delivery_loop()))

    async def stop(self):
        self.running = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.client:
            await self.client.aclose()
        if self.telegram_client:
            await self.telegram_client.aclose()

    async def scan(self, search):
        config = SearchInput.model_validate(search).for_source(
            search.get("source") or search["sources"][0]
        )
        start = time.perf_counter()
        try:
            jobs = await self.fetcher(self.client, config)
            new = self.store.record_scan(
                search["id"], config, jobs, (time.perf_counter() - start) * 1000
            )
            if new:
                self.wake_delivery.set()
        except SourceError as exc:
            current = self.store.search(search["id"])
            delay = max(exc.retry_after or 0, config.interval_seconds * 2)
            if (
                current
                and current["enabled"]
                and config.source in current["sources"]
                and SearchInput.model_validate(current).for_source(config.source) == config
            ):
                delay = self.store.record_failure(
                    search["id"], str(exc), exc.retry_after, config.source
                )
            # A platform restriction applies to all searches on that source.
            if exc.retry_after:
                self.store.set_source_ready(config.source, time.time() + delay)
        except Exception:
            logger.error("Unexpected source error; search id=%s", search["id"])
            current = self.store.search(search["id"])
            if (
                current
                and current["enabled"]
                and config.source in current["sources"]
                and SearchInput.model_validate(current).for_source(config.source) == config
            ):
                self.store.record_failure(
                    search["id"],
                    "Unexpected source error; inspect the service",
                    source=config.source,
                )
        finally:
            self.store.set_source_ready(
                config.source,
                max(
                    self.store.source_ready_at(config.source),
                    time.time() + self.settings.source_gap,
                ),
            )

    async def source_loop(self, source):
        while True:
            try:
                now = time.time()
                candidates = [
                    s
                    for s in self.store.searches_for_source(source)
                    if s["source"] == source and s["enabled"] and s["next_check"] <= now
                ]
                if candidates and self.store.source_ready_at(source) <= now:
                    await self.scan(min(candidates, key=lambda s: (s["next_check"], s["id"])))
            except Exception:
                logger.error("Source worker error; source=%s", source)
            await asyncio.sleep(0.5)

    async def deliver_one(self, job):
        start = time.perf_counter()
        user_id = job.get("user_id", 1)
        telegram_token = job.get("telegram_token", "")
        telegram_chat_id = job.get("telegram_chat_id", "")
        personal_bot = self.telegram if not telegram_token and user_id == 1 else None
        if personal_bot is None:
            personal_bot = Telegram(
                SimpleNamespace(
                    telegram_token=telegram_token,
                    telegram_chat_id=telegram_chat_id,
                    telegram_configured=bool(telegram_token and telegram_chat_id),
                ),
                self.telegram_client,
            )
        try:
            await personal_bot.send(format_message(job))
            self.store.delivery_sent(
                job["user_id"], job["key"], (time.perf_counter() - start) * 1000
            )
            self.delivery_error.pop(job["user_id"], None)
            self.telegram_gate[job["user_id"]] = time.time() + 1.05
        except DeliveryError as exc:
            delay = max(exc.retry_after or 0, min(3600, 2 ** min(job["attempts"] + 1, 10)))
            self.store.delivery_failed(job["user_id"], job["key"], str(exc), delay, exc.permanent)
            self.delivery_error[job["user_id"]] = str(exc)
            # Slow or invalid bot credentials must not trigger a burst for every queued job.
            self.telegram_gate[job["user_id"]] = time.time() + (60 if exc.permanent else delay)

    async def delivery_loop(self):
        while True:
            try:
                job = self.store.next_delivery(configured_only=True)
                if job and time.time() >= self.telegram_gate.get(job["user_id"], 0):
                    await self.deliver_one(job)
                    continue
            except Exception:
                logger.error("Unexpected notification worker error")
            self.wake_delivery.clear()
            try:
                await asyncio.wait_for(self.wake_delivery.wait(), timeout=0.5)
            except TimeoutError:
                pass
