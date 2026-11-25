"""Public Career Polytech search, using the same endpoint as its offer list."""

import asyncio
import re

import httpx
from bs4 import BeautifulSoup

from app.models import Job, normalize
from app.sources import SourceError, restriction_delay

ORIGIN = "https://career.polytech.network"
RESULT_LIMIT = 12


def parse_polytech(payload):
    if (
        not isinstance(payload, dict)
        or type(payload.get("total")) is not int
        or payload["total"] < 0
        or type(payload.get("shown")) is not int
        or not isinstance(payload.get("html"), str)
    ):
        raise SourceError("Career Polytech returned an invalid search response")
    soup = BeautifulSoup(payload["html"], "html.parser")
    cards = soup.select("article.opp-card[data-opp-id]")
    if (
        len(cards) != min(payload["total"], RESULT_LIMIT)
        or payload["shown"] != len(cards)
        or len(soup.select("article.opp-card")) != len(cards)
        or (payload["total"] == 0 and payload["html"].strip())
    ):
        raise SourceError("Career Polytech returned an incomplete or unrecognized result page")
    jobs = []
    seen = set()
    for card in cards:
        source_id = card["data-opp-id"]
        title = card.select_one(".os-card-title a")
        subtitle = card.select_one(".os-card-sub")
        meta = card.select(".os-meta > span")
        if (
            not re.fullmatch(r"[0-9]+", source_id)
            or source_id in seen
            or not title
            or not title.get_text(strip=True)
            or title.get("href") != f"/opportunity/{source_id}"
            or not subtitle
            or len(meta) < 2
            or not meta[0].get("title")
        ):
            raise SourceError("Career Polytech job structure changed")
        seen.add(source_id)
        # Require explicit country metadata, even when the server was asked for France.
        if normalize(meta[0]["title"]) != "france":
            continue
        location = meta[0].get_text(" ", strip=True).removeprefix("🇫🇷").strip()
        company, _, published = subtitle.get_text(" ", strip=True).rpartition(" · ")
        if not company or not location:
            raise SourceError("Career Polytech job structure changed")
        label = meta[1].get_text(" ", strip=True)
        contract = next(
            (
                x
                for x in ("Stage", "Alternance", "CDI", "CDD", "Freelance")
                if normalize(x) == normalize(label)
            ),
            "Not listed",
        )
        jobs.append(
            Job(
                source="polytech",
                source_id=source_id,
                title=title.get_text(" ", strip=True),
                company=company,
                location=f"{location}, France",
                contract=contract,
                url=f"{ORIGIN}/opportunity/{source_id}",
                published_label=published,
            )
        )
    return jobs


async def fetch_polytech(client, search):
    if search.experience != "any":
        raise SourceError("Career Polytech does not support experience filtering", 3600)
    if search.contract == "Freelance":
        raise SourceError("Career Polytech does not expose a Freelance contract filter", 3600)
    params = {
        "q": "informatique" if normalize(search.keywords) == "it" else search.keywords,
        "country": "FR",
        "offset": 0,
        "limit": RESULT_LIMIT,
    }
    if search.contract in ("Stage", "Alternance"):
        params["type"] = "internship" if search.contract == "Stage" else "apprenticeship"
    elif search.contract in ("CDI", "CDD"):
        params.update(type="job", term=search.contract.lower())
    try:
        response = await client.get(f"{ORIGIN}/api/opportunities", params=params)
    except httpx.HTTPError:
        raise SourceError("Career Polytech request failed or timed out") from None
    if response.status_code in (403, 429, 999):
        raise SourceError(
            f"Career Polytech access restricted (HTTP {response.status_code})",
            restriction_delay(response.headers.get("retry-after", "").strip()),
            status_code=response.status_code,
        )
    if response.status_code != 200:
        raise SourceError(f"Career Polytech returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise SourceError("Career Polytech returned an invalid search response") from None
    jobs = await asyncio.to_thread(parse_polytech, payload)
    return [
        job
        for job in jobs
        if job.matches(search)
        and (
            normalize(search.location) in ("france", "any", "")
            or normalize(search.location) in normalize(job.location)
        )
    ]
