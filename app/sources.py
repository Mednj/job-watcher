import asyncio
import re
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from app.models import Job, SearchInput


class SourceError(Exception):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


def text(node, selector: str) -> str:
    item = node.select_one(selector)
    return item.get_text(" ", strip=True) if item else ""


def parse_linkedin(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-entity-urn*="jobPosting:"]')
    if not cards:
        # A legitimate empty guest fragment has no text. Login/challenge pages do.
        if soup.get_text(strip=True):
            raise SourceError("LinkedIn returned an unrecognized page or access challenge")
        return []
    jobs = {}
    for card in cards:
        source_id = card.get("data-entity-urn", "").split(":")[-1]
        title = text(card, ".base-search-card__title")
        company = text(card, ".base-search-card__subtitle")
        date = card.select_one("time[datetime]")
        if not source_id.isdigit() or not title:
            raise SourceError("LinkedIn job card structure changed; check the parser")
        jobs[source_id] = Job(
            source="linkedin",
            source_id=source_id,
            title=title,
            company=company or "Not listed",
            location=text(card, ".job-search-card__location") or "Not listed",
            url=f"https://www.linkedin.com/jobs/view/{source_id}/",
            published_at=date.get("datetime") if date else None,
            published_precision="day" if date else None,
            published_label=date.get_text(" ", strip=True) if date else "",
        )
    return list(jobs.values())


def parse_hellowork(html: str) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('[data-cy="serpCard"]')
    if not cards:
        heading = text(soup, "h1")
        if re.search(r"\b0\s+offre", heading, re.I) or "aucune offre" in heading.lower():
            return []
        raise SourceError("HelloWork returned an unrecognized page or access challenge")
    jobs = {}
    for card in cards:
        link = card.select_one('[data-cy="offerTitle"]')
        if not link:
            raise SourceError("HelloWork job card structure changed; check the parser")
        match = re.search(r"/emplois/(\d+)\.html", link.get("href", ""))
        title = text(link, "h3 p:first-child")
        company = text(link, "h3 p:nth-child(2)")
        if not match or not title:
            raise SourceError("HelloWork job card structure changed; check the parser")
        source_id = match[1]
        jobs[source_id] = Job(
            source="hellowork",
            source_id=source_id,
            title=title,
            company=company or "Not listed",
            location=text(card, '[data-cy="localisationCard"]') or "Not listed",
            contract=text(card, '[data-cy="contractCard"]') or "Not listed",
            url=f"https://www.hellowork.com/fr-fr/emplois/{source_id}.html",
            # Relative labels are displayed verbatim, never converted to false precision.
            published_label=text(card, "div.typo-s.text-grey-500"),
        )
    return list(jobs.values())


def search_url(search: SearchInput) -> str:
    if search.source == "linkedin":
        # LinkedIn guest results expose employment categories, not French legal contracts.
        # Use the requested French contract as a search term rather than mislabel full-time CDI.
        keywords = search.keywords
        if keywords.strip().casefold() == "it":
            keywords = (
                "(informatique OR IT OR cloud OR devops OR data OR software OR cybersécurité)"
            )
        if search.contract != "any":
            keywords += " " + search.contract
        params = {
            "keywords": keywords,
            "location": search.location,
            "sortBy": "DD",
            "f_TPR": "r86400",
            "start": "0",
        }
        if search.experience != "any":
            params["f_E"] = "1,2" if search.experience == "entry" else "3,4,5,6"
        base = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?"
        return base + urlencode(params)
    keywords = "informatique" if search.keywords.strip().casefold() == "it" else search.keywords
    params = {"k": keywords, "l": search.location, "st": "date"}
    if search.contract != "any":
        params["c"] = search.contract
    if search.experience != "any":
        params["e"] = (
            ["deb", "0-1y"] if search.experience == "entry" else ["2-3y", "4-5y", "6-10y", "gt10y"]
        )
    return "https://www.hellowork.com/fr-fr/emploi/recherche.html?" + urlencode(params, doseq=True)


async def fetch_jobs(client: httpx.AsyncClient, search: SearchInput) -> list[Job]:
    try:
        response = await client.get(search_url(search))
    except httpx.HTTPError:
        raise SourceError("Source request failed or timed out") from None
    if response.status_code in (403, 429, 999):
        retry = response.headers.get("retry-after", "")
        raise SourceError(
            f"Source access restricted (HTTP {response.status_code}); monitoring will back off",
            float(retry) if retry.isdigit() else 300,
        )
    if response.status_code != 200:
        raise SourceError(f"Source returned HTTP {response.status_code}")
    # HTTPX honors charset declarations; both live sources currently return UTF-8 HTML.
    html = response.content.decode("utf-8", errors="replace")
    parser = parse_linkedin if search.source == "linkedin" else parse_hellowork
    jobs = await asyncio.to_thread(parser, html)
    return [job for job in jobs if job.matches(search)]
