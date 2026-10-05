import json

import pytest

from app.jobteaser_browser import is_search_page, parse_page, validate_endpoint
from app.sources import SourceError


def test_jobteaser_endpoint_is_private_and_search_tab_is_specific():
    assert validate_endpoint("http://127.0.0.1:9223") == "http://127.0.0.1:9223"
    for endpoint in (
        "http://example.com:9223",
        "http://127.0.0.1:9223/path",
        "https://127.0.0.1:9223",
        "http://user@127.0.0.1:9223",
    ):
        with pytest.raises(SourceError):
            validate_endpoint(endpoint)
    assert is_search_page("https://www.jobteaser.com/fr/job-offers?keyword=cloud")
    assert not is_search_page("https://www.jobteaser.com/fr/users/sign_in")
    assert not is_search_page("https://www.jobteaser.com.evil.example/fr/job-offers")


def test_jobteaser_structured_results_and_challenge():
    payload = {
        "@type": "JobPosting",
        "title": "DevOps alternance",
        "hiringOrganization": {"name": "Example"},
        "url": "https://www.jobteaser.com/fr/job-offers/12345678-1234-1234-1234-123456789abc-example",
        "jobLocation": {"address": {"addressLocality": "Paris"}},
        "datePosted": "2026-10-05",
    }
    html = '<script type="application/ld+json">' + json.dumps({"@graph": [payload]}) + "</script>"
    job = parse_page(html, "https://www.jobteaser.com/fr/job-offers")[0]
    assert job.company == "Example"
    assert job.contract == "Alternance"
    assert job.location == "Paris"
    assert job.published_precision == "day"
    for page in (
        "<title>Just a moment...</title>",
        "<title>JobTeaser | Security checkup</title>",
        "<h1>Jobs</h1>",
    ):
        with pytest.raises(SourceError):
            parse_page(page, "https://www.jobteaser.com/fr/job-offers")


@pytest.mark.asyncio
@pytest.mark.parametrize("challenge", [False, True])
async def test_attach_reads_existing_page_before_refresh(monkeypatch, challenge):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.jobteaser_browser import fetch_browser
    from app.models import SearchInput

    monkeypatch.setenv("JOBTEASER_CDP_ENDPOINT", "http://127.0.0.1:9223")
    payload = {
        "@type": "JobPosting",
        "title": "Cloud CDI",
        "hiringOrganization": {"name": "Example"},
        "url": "https://www.jobteaser.com/fr/job-offers/12345678-1234-1234-1234-123456789abc-example",
        "jobLocation": {"address": {"addressLocality": "Paris"}},
    }
    html = (
        "<title>Just a moment...</title>"
        if challenge
        else '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
    )
    events = []

    async def content():
        events.append("read")
        return html

    async def reload(*args, **kwargs):
        events.append("reload")

    page = SimpleNamespace(
        url="https://www.jobteaser.com/fr/job-offers",
        content=content,
        goto=reload,
        wait_for_function=AsyncMock(),
    )
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])], close=AsyncMock())
    connect = AsyncMock(return_value=browser)

    class Manager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("playwright.async_api.async_playwright", Manager)
    search = SearchInput(
        name="Cloud", sources=["jobteaser"], keywords="cloud", location="Paris"
    ).for_source("jobteaser")
    if challenge:
        with pytest.raises(SourceError, match="security challenge"):
            await fetch_browser(search)
        assert events == ["read"]
    else:
        assert len(await fetch_browser(search)) == 1
        assert events == ["read", "reload", "read"]
    connect.assert_awaited_once_with("http://127.0.0.1:9223", timeout=10000, no_defaults=True)
    browser.close.assert_awaited_once()


def test_jobteaser_real_card_structure_and_empty_detection():
    html = """<div data-testid="jobad-card">
<p data-testid="jobad-card-company-name">Example</p>
<h3><a href="/fr/job-offers/12345678-1234-1234-1234-123456789abc-cloud">Cloud engineer</a></h3>
<div data-testid="jobad-card-contract">CDD</div>
<div data-testid="jobad-card-location">Paris, France</div>
<span>il y a 5 minutes</span></div>"""
    job = parse_page(html, "https://www.jobteaser.com/fr/job-offers?q=cloud")[0]
    assert job.contract == "CDD"
    assert job.company == "Example"
    assert job.published_label == "il y a 5 minutes"
    assert job.published_at is None
    assert (
        parse_page(
            '<div data-testid="job-ads-wrapper">0 offres</div>',
            "https://www.jobteaser.com/fr/job-offers",
        )
        == []
    )
    with pytest.raises(SourceError):
        parse_page(
            '<div data-testid="job-ads-wrapper">20 offres</div>',
            "https://www.jobteaser.com/fr/job-offers",
        )


@pytest.mark.asyncio
async def test_pagination_collects_distinct_offers_and_respects_limit():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.jobteaser_browser import collect_pages

    visited = []

    def html(ids, next_page):
        jobs = [
            {
                "@type": "JobPosting",
                "title": "Cloud CDI",
                "hiringOrganization": {"name": "Example"},
                "url": f"https://www.jobteaser.com/fr/job-offers/12345678-1234-1234-1234-{number:012d}-cloud",
            }
            for number in ids
        ]
        nav = (
            (
                '<nav data-testid="job-ads-pagination">'
                '<a aria-label="Aller à la page suivante" '
                f'href="?q=cloud&page={next_page}">Next</a></nav>'
            )
            if next_page
            else ""
        )
        return '<script type="application/ld+json">' + json.dumps(jobs) + "</script>" + nav

    pages = [html([1, 2], 2), html([2, 3], 3), html([4], None)]

    async def goto(url, **kwargs):
        visited.append(url)

    async def content():
        return pages[len(visited) - 1]

    page = SimpleNamespace(goto=goto, content=content, wait_for_function=AsyncMock())
    jobs = await collect_pages(
        page, "https://www.jobteaser.com/fr/job-offers?q=cloud", max_pages=2, page_delay=0
    )
    assert len(jobs) == 3
    assert len(visited) == 2
    visited.clear()
    jobs = await collect_pages(
        page, "https://www.jobteaser.com/fr/job-offers?q=cloud", max_pages=5, page_delay=0
    )
    assert len(jobs) == 4
    assert len(visited) == 3


def test_pagination_rejects_external_or_changed_search_links():
    from app.jobteaser_browser import next_page_url

    current = "https://www.jobteaser.com/fr/job-offers?q=cloud&page=1"
    for link in (
        "https://evil.example/fr/job-offers?q=cloud&page=2",
        "?q=data&page=2",
        "?q=cloud&page=1",
        "?q=cloud&page=bad",
    ):
        html = (
            '<nav data-testid="job-ads-pagination">'
            f'<a aria-label="Aller à la page suivante" href="{link}">Next</a></nav>'
        )
        with pytest.raises(SourceError):
            next_page_url(html, current)
    assert next_page_url("<nav></nav>", current) is None


@pytest.mark.asyncio
async def test_later_page_challenge_does_not_return_partial_success():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.jobteaser_browser import collect_pages

    payload = {
        "@type": "JobPosting",
        "title": "Cloud CDI",
        "hiringOrganization": {"name": "Example"},
        "url": "https://www.jobteaser.com/fr/job-offers/12345678-1234-1234-1234-123456789abc-cloud",
    }
    first = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
    first += (
        '<nav data-testid="job-ads-pagination">'
        '<a aria-label="Aller à la page suivante" href="?q=cloud&page=2">Next</a></nav>'
    )
    page = SimpleNamespace(
        goto=AsyncMock(),
        wait_for_function=AsyncMock(),
        content=AsyncMock(side_effect=[first, "<title>Just a moment...</title>"]),
    )
    with pytest.raises(SourceError, match="security challenge"):
        await collect_pages(page, "https://www.jobteaser.com/fr/job-offers?q=cloud", page_delay=0)
