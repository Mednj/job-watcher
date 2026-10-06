"""Additional public sources; access challenges are failures, never empty scans."""

import asyncio
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from app.locations import is_french_location
from app.models import Job, SearchInput, normalize
from app.sources import SourceError

CONTRACTS = {
    "full_time": "CDI",
    "temporary": "CDD",
    "apprenticeship": "Alternance",
    "internship": "Stage",
    "freelance": "Freelance",
}
RESTRICTED = {
    "apec": "APEC search currently requires access approval; no verified public integration",
    "glassdoor": "Glassdoor currently blocks public monitoring with a security check",
    "indeed": "Indeed currently blocks public monitoring with a security check",
    "jobteaser": "JobTeaser currently blocks public monitoring with a security check",
    "monster": "Monster currently blocks public monitoring with a security check",
}


def parse_wttj(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise SourceError("Welcome to the Jungle search response changed")
    jobs = []
    for row in payload["data"]:
        try:
            if (row.get("office") or {}).get("country_code") != "FR":
                continue
            org = row["organization"]
            slug = (org.get("website_organization") or {}).get("slug") or org["slug"]
            jobs.append(
                Job(
                    source="wttj",
                    source_id=row["reference"],
                    title=row["name"],
                    company=org["name"],
                    location=(row.get("office") or {}).get("city") or "Not listed",
                    contract=CONTRACTS.get(row.get("contract_type"), "Not listed"),
                    url=f"https://www.welcometothejungle.com/fr/companies/{slug}/jobs/{row['slug']}",
                    published_at=row.get("published_at"),
                    published_precision="second" if row.get("published_at") else None,
                )
            )
        except (KeyError, TypeError):
            raise SourceError("Welcome to the Jungle job structure changed") from None
    return jobs


def parse_francetravail(html):
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("li.result[data-id-offre]")
    if not cards:
        if (
            soup.select_one(".result-list")
            or "aucune offre" in soup.get_text(" ", strip=True).lower()
        ):
            return []
        raise SourceError("France Travail returned an unrecognized page or access challenge")
    jobs = []
    for card in cards:
        title = card.select_one(".media-heading-title")
        employer = card.select_one(".subtext")
        place = employer.select_one("span") if employer else None
        contract = card.select_one(".media-right .contrat") or card.select_one(".contrat")
        label = (
            "".join(contract.find_all(string=True, recursive=False)).strip()
            if contract
            else "Not listed"
        )
        title_text = title.get_text(" ", strip=True) if title else ""
        if not title_text:
            raise SourceError("France Travail job structure changed")
        if any(term in normalize(title_text) for term in ("alternance", "apprentissage")):
            label = "Alternance"
        date = card.select_one(".date")
        source_id = card["data-id-offre"]
        jobs.append(
            Job(
                source="francetravail",
                source_id=source_id,
                title=title_text,
                company="".join(employer.find_all(string=True, recursive=False)).strip(" -\n\t")
                if employer
                else "Not listed",
                location=place.get_text(" ", strip=True) if place else "Not listed",
                contract=label,
                url=f"https://candidat.francetravail.fr/offres/recherche/detail/{source_id}",
                published_label=date.get_text(" ", strip=True) if date else "",
            )
        )
    return jobs


async def fetch_extended(client, search: SearchInput):
    if search.source == "jobteaser":
        from app.jobteaser_browser import fetch_browser

        return await fetch_browser(search)
    if search.source in RESTRICTED:
        raise SourceError(RESTRICTED[search.source] + "; integration unavailable", 3600)
    if search.experience != "any":
        raise SourceError("Experience filtering is not supported by this integration yet", 3600)
    keyword = "informatique" if normalize(search.keywords) == "it" else search.keywords
    if search.source == "wttj":
        params = {"job_title": keyword}
        if search.contract != "any":
            params["contract_type[]"] = next(
                key for key, value in CONTRACTS.items() if value == search.contract
            )
        url = "https://api.welcometothejungle.com/api/v3/public/jobs?" + urlencode(params)
    else:
        params = {"motsCles": keyword, "offresPartenaires": "true", "range": "0-19", "tri": "0"}
        if search.contract == "Alternance":
            params["motsCles"] += " alternance"
        url = "https://candidat.francetravail.fr/offres/recherche?" + urlencode(params)
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        raise SourceError("Source request failed or timed out") from None
    if response.status_code in (403, 429, 999):
        raise SourceError(
            f"Source access restricted (HTTP {response.status_code}); monitoring will back off", 300
        )
    if response.status_code != 200:
        raise SourceError(f"Source returned HTTP {response.status_code}")
    if search.source == "wttj":
        try:
            jobs = parse_wttj(response.json())
        except ValueError:
            raise SourceError("Welcome to the Jungle returned an invalid search response") from None
    else:
        jobs = await asyncio.to_thread(
            parse_francetravail, response.content.decode("utf-8", errors="replace")
        )
    return [
        job
        for job in jobs
        if is_french_location(job.location) and job.matches(search)
        and (
            normalize(search.location) in ("france", "any", "")
            or normalize(search.location) in normalize(job.location)
        )
    ]
