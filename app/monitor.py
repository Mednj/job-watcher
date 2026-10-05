import asyncio
import logging
import time

import httpx

from app.config import Settings
from app.models import SearchInput
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
        self.delivery_error = None
        self.running = False
        self.telegram_gate = 0

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
        self.tasks = [
            asyncio.create_task(self.source_loop(source)) for source in ("linkedin", "hellowork")
        ]
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
        config = SearchInput.model_validate(search)
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
            if current and current["enabled"] and SearchInput.model_validate(current) == config:
                delay = self.store.record_failure(search["id"], str(exc), exc.retry_after)
            # A platform restriction applies to all searches on that source.
            if exc.retry_after:
                self.store.set_source_ready(config.source, time.time() + delay)
        except Exception:
            logger.error("Unexpected source error; search id=%s", search["id"])
            current = self.store.search(search["id"])
            if current and current["enabled"] and SearchInput.model_validate(current) == config:
                self.store.record_failure(
                    search["id"], "Unexpected source error; inspect the service"
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
                    for s in self.store.searches()
                    if s["source"] == source and s["enabled"] and s["next_check"] <= now
                ]
                if candidates and self.store.source_ready_at(source) <= now:
                    await self.scan(min(candidates, key=lambda s: (s["next_check"], s["id"])))
            except Exception:
                logger.error("Source worker error; source=%s", source)
            await asyncio.sleep(0.5)

    async def deliver_one(self, job):
        start = time.perf_counter()
        try:
            await self.telegram.send(format_message(job))
            self.store.delivery_sent(job["key"], (time.perf_counter() - start) * 1000)
            self.delivery_error = None
            self.telegram_gate = time.time() + 1.05
        except DeliveryError as exc:
            delay = max(exc.retry_after or 0, min(3600, 2 ** min(job["attempts"] + 1, 10)))
            self.store.delivery_failed(job["key"], str(exc), delay, exc.permanent)
            self.delivery_error = str(exc)
            # Slow or invalid bot credentials must not trigger a burst for every queued job.
            self.telegram_gate = time.time() + (60 if exc.permanent else delay)

    async def delivery_loop(self):
        while True:
            try:
                if self.settings.telegram_configured and time.time() >= self.telegram_gate:
                    job = self.store.next_delivery()
                    if job:
                        await self.deliver_one(job)
                        continue
            except Exception:
                self.delivery_error = "Unexpected notification worker error"
                logger.error(self.delivery_error)
            self.wake_delivery.clear()
            try:
                await asyncio.wait_for(self.wake_delivery.wait(), timeout=0.5)
            except TimeoutError:
                pass
