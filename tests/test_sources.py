from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.models import SearchInput
from app.sources import SourceError, fetch_jobs, parse_hellowork, parse_linkedin, search_url

LINKEDIN = """<li><div data-entity-urn="urn:li:jobPosting:123">
<h3 class="base-search-card__title">Ingénieur Cloud</h3>
<h4 class="base-search-card__subtitle">Example &amp; Co</h4>
<span class="job-search-card__location">Paris, France</span>
<time datetime="2026-10-05">2 minutes ago</time></div></li>"""
HELLOWORK = """<div data-cy="serpCard"><a data-cy="offerTitle"
href="/fr-fr/emplois/456.html"><h3><p>Développeur DevOps H/F</p>
<p>Example</p></h3></a><div data-cy="localisationCard">Lyon - 69</div>
<div data-cy="contractCard">Alternance</div>
<div class="typo-s text-grey-500">moins d'une heure</div></div>"""


def test_linkedin_ids_unicode_and_date_precision():
    job = parse_linkedin(LINKEDIN)[0]
    assert job.key == "linkedin:123"
    assert job.company == "Example & Co"
    assert job.title == "Ingénieur Cloud"
    assert job.url == "https://www.linkedin.com/jobs/view/123/"
    assert job.published_precision == "day"
    assert job.contract == "Not listed"


def test_hellowork_contract_and_relative_date():
    job = parse_hellowork(HELLOWORK)[0]
    assert job.key == "hellowork:456"
    assert job.contract == "Alternance"
    assert job.published_at is None
    assert job.published_label == "moins d'une heure"


def test_anonymous_employer_is_a_valid_listing():
    job = parse_hellowork(HELLOWORK.replace("<p>Example</p>", "<p></p>"))[0]
    assert job.company == "Not listed"


@pytest.mark.parametrize("parser", [parse_linkedin, parse_hellowork])
def test_challenge_is_not_an_empty_success(parser):
    with pytest.raises(SourceError):
        parser("<html><h1>Verify you are human</h1></html>")


def test_empty_results():
    assert parse_linkedin("<!DOCTYPE html>") == []
    assert parse_hellowork("<h1>0 offres</h1>") == []


@pytest.mark.parametrize(
    "location",
    [
        "Cracovie, Petite Pologne, Pologne",
        "Espagne",
        "Inde",
        "Raanana, Israël",
        "London, United Kingdom",
        "New York, United States",
    ],
)
async def test_linkedin_foreign_locations_are_filtered(location):
    html = LINKEDIN.replace("Paris, France", location)
    config = SearchInput(name="Cloud", source="linkedin", keywords="cloud", location="France")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    ) as client:
        assert await fetch_jobs(client, config) == []


def test_country_filter_keeps_city_only_results_but_rejects_foreign_countries():
    from app.locations import is_french_location

    assert is_french_location("Paris")
    assert is_french_location("Paris, France")
    assert not is_french_location("Cracovie, Petite Pologne, Pologne")
    assert not is_french_location("Raanana, District centre, Israël")
    assert is_french_location("Paris", country_code="FR")
    assert not is_french_location("Paris", country_code="PL")


def test_partial_card_fails_instead_of_losing_listings():
    with pytest.raises(SourceError):
        parse_linkedin(LINKEDIN + '<div data-entity-urn="urn:li:jobPosting:999"></div>')


def test_broad_it_and_contract_mapping():
    config = SearchInput(
        name="IT", source="hellowork", keywords="IT", contract="CDD", experience="entry"
    )
    params = parse_qs(urlparse(search_url(config)).query)
    assert params["k"] == ["informatique"]
    assert params["c"] == ["CDD"]
    assert params["e"] == ["deb", "0-1y"]
    config.source = "linkedin"
    params = parse_qs(urlparse(search_url(config)).query)
    assert "OR" in params["keywords"][0]
    assert params["keywords"][0].endswith(" CDD")
    assert params["sortBy"] == ["DD"]


def test_keywords_match_titles_not_irrelevant_locations():
    from app.models import Job

    config = SearchInput(name="Cloud", source="hellowork", keywords="cloud")
    tourism = Job(
        "hellowork", "1", "Conseiller Voyages", "Example", "Saint-Cloud", "https://example.com"
    )
    assert not tourism.matches(config)
    tourism.title = "Ingénieur Cloud"
    assert tourism.matches(config)
    config.contract = "CDI"
    tourism.contract = "CDD"
    assert not tourism.matches(config)


def test_it_covers_general_technology_titles():
    from app.models import Job

    config = SearchInput(name="IT", source="linkedin", keywords="IT")
    for title in [
        "Développeur Java",
        "Ingénieur Cloud",
        "DevOps",
        "Data Analyst",
        "Technicien réseaux",
    ]:
        assert Job("linkedin", "1", title, "Example", "Paris", "https://example.com").matches(
            config
        )
    assert not Job(
        "linkedin", "1", "Conseiller Voyages", "Example IT", "Paris", "https://example.com"
    ).matches(config)
    assert not Job(
        "linkedin", "1", "Chargé de Développement RH", "Example", "Paris", "https://example.com"
    ).matches(config)


async def test_fetch_rate_limit_and_accent_insensitive_exclusion():
    config = SearchInput(
        name="Cloud", source="linkedin", keywords="cloud", exclude_keywords=["ingenieur"]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=LINKEDIN))
    ) as client:
        assert await fetch_jobs(client, config) == []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(429, headers={"Retry-After": "600"})
        )
    ) as client:
        with pytest.raises(SourceError) as error:
            await fetch_jobs(client, config)
        assert error.value.retry_after == 600
