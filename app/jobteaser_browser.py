"""Experimental JobTeaser reader for a browser opened manually before attachment."""

import asyncio
import json
import logging
import os
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from app.models import Job, normalize
from app.sources import SourceError

LOCK = asyncio.Lock()
logger = logging.getLogger(__name__)


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
    cards = soup.select('[data-testid="jobad-card"]')
    for card in cards:
        link = card.select_one('h3 a[href*="/job-offers/"]')
        company = card.select_one('[data-testid="jobad-card-company-name"]')
        contract = card.select_one('[data-testid="jobad-card-contract"]')
        location = card.select_one('[data-testid="jobad-card-location"]')
        url = urljoin(page_url, link.get("href", "")) if link else ""
        match = re.search(r"/job-offers/([0-9a-f-]{36})(?:-|/|$)", urlparse(url).path)
        if not link or not company or not location or not contract or not match:
            raise SourceError("JobTeaser card structure changed; parser needs verification")
        title_text = link.get_text(" ", strip=True)
        if not title_text or urlparse(url).hostname != "www.jobteaser.com":
            raise SourceError("JobTeaser returned an invalid job card")
        label = next(
            (
                text
                for text in card.stripped_strings
                if re.match(r"^(il y a|Publié|Publie)", text, re.I)
            ),
            "",
        )
        jobs[match[1]] = Job(
            source="jobteaser",
            source_id=match[1],
            title=title_text,
            company=company.get_text(" ", strip=True),
            location=location.get_text(" ", strip=True),
            contract=contract.get_text(" ", strip=True).split(" ")[0],
            url=url,
            published_label=label,
        )
    if cards:
        return list(jobs.values())
    if soup.select_one('[data-testid="job-ads-wrapper"]') and re.search(
        r"(?<![0-9])0\s+offres?", soup.get_text(" ", strip=True)
    ):
        return []

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


def next_page_url(html, current_url):
    soup = BeautifulSoup(html, "html.parser")
    link = soup.select_one(
        '[data-testid="job-ads-pagination"] a[aria-label="Aller à la page suivante"]'
    )
    if not link:
        return None
    target = urljoin(current_url, link.get("href", ""))
    current = parse_qs(urlparse(current_url).query)
    query = parse_qs(urlparse(target).query)
    try:
        valid = (
            is_search_page(target)
            and query.get("q") == current.get("q")
            and int(query.get("page", ["0"])[0]) == int(current.get("page", ["1"])[0]) + 1
        )
    except (ValueError, IndexError):
        valid = False
    if not valid:
        raise SourceError("JobTeaser pagination changed; refusing an invalid next-page link")
    return target


async def collect_pages(page, url, max_pages=5, page_delay=2):
    jobs = {}
    visited = set()
    pages_checked = 0
    while url and pages_checked < max_pages:
        if url in visited:
            raise SourceError("JobTeaser pagination repeated a page")
        visited.add(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_function(
            "document.querySelector('[data-testid=jobad-card]') || "
            "/security|just a moment|checkup/i.test(document.title) || "
            "(document.querySelector('[data-testid=job-ads-wrapper]') && "
            "/(?:^|[^0-9])0\\s+offres?/.test(document.body.innerText))",
            timeout=15000,
        )
        html = await page.content()
        batch = parse_page(html, url)
        pages_checked += 1
        before = len(jobs)
        jobs.update({job.key: job for job in batch})
        if pages_checked > 1 and batch and len(jobs) == before:
            raise SourceError("JobTeaser returned repeated results on a different page")
        url = next_page_url(html, url) if batch else None
        if url and pages_checked < max_pages:
            await asyncio.sleep(page_delay)
    logger.info(
        "JobTeaser checked %s pages, %s unique offers, capped=%s",
        pages_checked,
        len(jobs),
        bool(url),
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
                    keyword = (
                        "informatique" if normalize(search.keywords) == "it" else search.keywords
                    )
                    url = "https://www.jobteaser.com/fr/job-offers?" + urlencode({"q": keyword})
                    try:
                        max_pages = max(1, min(20, int(os.getenv("JOBTEASER_MAX_PAGES", "5"))))
                        delay = max(2, float(os.getenv("JOBTEASER_PAGE_DELAY_SECONDS", "2")))
                    except ValueError:
                        raise SourceError("Invalid JobTeaser pagination settings", 3600) from None
                    jobs = await collect_pages(page, url, max_pages, delay)
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
            (normalize(search.location) == "france" and "france" in normalize(job.location))
            or (
                normalize(search.location) != "france"
                and normalize(search.location) in normalize(job.location)
            )
        )
    ]
