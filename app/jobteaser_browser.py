"""Experimental JobTeaser reader for a browser opened manually before attachment."""

import asyncio
import json
import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models import Job, normalize
from app.sources import SourceError

LOCK = asyncio.Lock()


def validate_endpoint(endpoint):
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise SourceError("JobTeaser browser endpoint must be http://127.0.0.1:<port>", 3600)
    return endpoint.rstrip("/")


def is_search_page(url):
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "www.jobteaser.com"
        and bool(re.fullmatch(r"/[a-z]{2}/job-offers/?", parsed.path))
    )


def parse_page(html, page_url):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).casefold() if soup.title else ""
    if any(marker in title for marker in ("security", "just a moment", "checkup")):
        raise SourceError("JobTeaser security challenge: complete it manually in the browser", 300)
    jobs = {}

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            kinds = value.get("@type", [])
            if kinds == "JobPosting" or isinstance(kinds, list) and "JobPosting" in kinds:
                name = value.get("title")
                org = value.get("hiringOrganization") or {}
                company = org.get("name") if isinstance(org, dict) else None
                url = urljoin(page_url, value.get("url") or "")
                parsed = urlparse(url)
                match = re.search(r"/job-offers/([0-9a-f-]{36})(?:-|/|$)", parsed.path)
                if (
                    not name
                    or not company
                    or parsed.hostname != "www.jobteaser.com"
                    or parsed.scheme != "https"
                    or not match
                ):
                    raise SourceError(
                        "JobTeaser structured listing is incomplete; parser needs verification"
                    )
                locations = value.get("jobLocation") or []
                if isinstance(locations, dict):
                    locations = [locations]
                cities = []
                for location in locations:
                    address = location.get("address") or {}
                    if isinstance(address, dict) and address.get("addressLocality"):
                        cities.append(address["addressLocality"])
                # EmploymentType FULL_TIME does not prove a French CDI.
                contract = "Not listed"
                for kind in ("Alternance", "CDI", "CDD", "Stage", "Freelance"):
                    if re.search(r"(?<!\w)" + normalize(kind) + r"(?!\w)", normalize(name)):
                        contract = kind
                        break
                jobs[match[1]] = Job(
                    source="jobteaser",
                    source_id=match[1],
                    title=name,
                    company=company,
                    location=", ".join(cities) or "Not listed",
                    contract=contract,
                    url=url,
                    published_at=value.get("datePosted"),
                    published_precision="second"
                    if "T" in str(value.get("datePosted", ""))
                    else "day"
                    if value.get("datePosted")
                    else None,
                )
            for child in value.values():
                if isinstance(child, (list, dict)):
                    visit(child)

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            raise SourceError("JobTeaser structured data could not be read") from None
    if not jobs:
        raise SourceError(
            "JobTeaser page has no verified structured job listings; parser needs live validation",
            300,
        )
    return list(jobs.values())


async def fetch_browser(search):
    endpoint = os.getenv("JOBTEASER_CDP_ENDPOINT", "").strip()
    if not endpoint:
        raise SourceError(
            "JobTeaser integration unavailable: open the dedicated browser, "
            "then configure JOBTEASER_CDP_ENDPOINT",
            3600,
        )
    validate_endpoint(endpoint)
    if search.experience != "any":
        raise SourceError("JobTeaser experience filtering is not supported yet", 3600)
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError:
        raise SourceError(
            "Install requirements.browser.txt to enable the JobTeaser browser reader", 3600
        ) from None
    async with LOCK:
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(
                    endpoint, timeout=10000, no_defaults=True
                )
                try:
                    pages = [
                        page
                        for context in browser.contexts
                        for page in context.pages
                        if is_search_page(page.url)
                    ]
                    if len(pages) != 1:
                        raise SourceError(
                            "Open exactly one JobTeaser results tab in the dedicated browser",
                            300,
                        )
                    page = pages[0]
                    # Read the already-loaded page first. Do not refresh a manual challenge.
                    parse_page(await page.content(), page.url)
                    await page.reload(wait_until="domcontentloaded", timeout=25000)
                    jobs = parse_page(await page.content(), page.url)
                finally:
                    # Disconnect our CDP client; leave the dedicated browser open.
                    await browser.close()
        except Error:
            raise SourceError(
                "JobTeaser browser unavailable or timed out; reopen the dedicated session", 300
            ) from None
    return [
        job
        for job in jobs
        if job.matches(search)
        and (search.contract == "any" or normalize(job.contract) == normalize(search.contract))
        and (
            normalize(search.location) == "france"
            or normalize(search.location) in normalize(job.location)
        )
    ]
