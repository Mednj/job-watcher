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

    async def reload(**kwargs):
        events.append("reload")

    page = SimpleNamespace(
        url="https://www.jobteaser.com/fr/job-offers", content=content, reload=reload
    )
    browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[page])], close=AsyncMock())
    connect = AsyncMock(return_value=browser)

    class Manager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("playwright.async_api.async_playwright", Manager)
    search = SearchInput(name="Cloud", sources=["jobteaser"], keywords="cloud").for_source(
        "jobteaser"
    )
    if challenge:
        with pytest.raises(SourceError, match="security challenge"):
            await fetch_browser(search)
        assert events == ["read"]
    else:
        assert len(await fetch_browser(search)) == 1
        assert events == ["read", "reload", "read"]
    connect.assert_awaited_once_with("http://127.0.0.1:9223", timeout=10000, no_defaults=True)
    browser.close.assert_awaited_once()
