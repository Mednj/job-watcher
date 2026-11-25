import httpx
import pytest

from app.models import SearchInput
from app.polytech import parse_polytech
from app.sources import SourceError, fetch_jobs

CARD = """<article class="opp-card os-card" data-opp-id="123">
<h3 class="os-card-title"><a href="/opportunity/123">Stage Développeur Cloud</a></h3>
<div class="os-card-sub">Example &amp; Co · il y a 2 j</div>
<div class="os-meta"><span title="France">🇫🇷 Lyon</span><span>Stage</span>
<span title="Source">Recruteur</span></div></article>"""


def payload(html=CARD, total=1, shown=1):
    return {"html": html, "total": total, "shown": shown}


def config(**kwargs):
    return SearchInput(name="Polytech", source="polytech", keywords="cloud", **kwargs)


def test_parse_unicode_contract_location_and_canonical_link():
    job = parse_polytech(payload())[0]
    assert job.key == "polytech:123"
    assert job.title == "Stage Développeur Cloud"
    assert job.company == "Example & Co"
    assert job.location == "Lyon, France"
    assert job.contract == "Stage"
    assert job.url == "https://career.polytech.network/opportunity/123"
    assert job.published_label == "il y a 2 j"
    assert job.published_at is None
    assert job.published_precision is None
    assert parse_polytech(payload("", 0, 0)) == []
    assert parse_polytech(payload(CARD.replace('title="France"', 'title="Belgique"'))) == []


@pytest.mark.parametrize(
    "data",
    [
        {},
        [],
        {"total": True, "shown": 0, "html": ""},
        payload("<h1>Access challenge</h1>"),
        payload("<h1>Access challenge</h1>", 0, 0),
        payload(CARD, 2, 2),
        payload(CARD, 1, 0),
        payload(CARD.replace('data-opp-id="123"', 'data-opp-id="x"')),
        payload(CARD.replace('href="/opportunity/123"', 'href="https://evil.example/123"')),
        payload(CARD.replace('title="France"', 'title=""')),
        payload(CARD.replace('class="os-card-title"', 'class="unknown"')),
        payload(CARD + CARD, 2, 2),
    ],
)
def test_broken_or_partial_responses_never_become_empty_successes(data):
    with pytest.raises(SourceError):
        parse_polytech(data)


@pytest.mark.parametrize(
    "contract,expected",
    [
        ("any", {}),
        ("Stage", {"type": "internship"}),
        ("Alternance", {"type": "apprenticeship"}),
        ("CDI", {"type": "job", "term": "cdi"}),
        ("CDD", {"type": "job", "term": "cdd"}),
    ],
)
async def test_native_filters_and_local_filtering(contract, expected):
    def respond(request):
        assert str(request.url).startswith("https://career.polytech.network/api/opportunities?")
        assert dict(request.url.params) == {
            "q": "cloud",
            "country": "FR",
            "offset": "0",
            "limit": "12",
            **expected,
        }
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        jobs = await fetch_jobs(client, config(contract=contract, location="Lyon"))
        assert len(jobs) == (1 if contract in ("any", "Stage") else 0)
        assert await fetch_jobs(client, config(contract=contract, location="Paris")) == []
        excluded = config(contract=contract, exclude_keywords=["example"])
        assert await fetch_jobs(client, excluded) == []


async def test_it_keyword_translation_and_strict_title_matching():
    def respond(request):
        assert request.url.params["q"] == "informatique"
        return httpx.Response(200, json=payload())

    search = config().model_copy(update={"keywords": "IT"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert len(await fetch_jobs(client, search)) == 1


@pytest.mark.parametrize("status", [403, 429, 999, 500])
async def test_http_failures_are_visible(status):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, headers={"Retry-After": "900"})
        )
    ) as client:
        with pytest.raises(SourceError) as exc:
            await fetch_jobs(client, config())
    assert str(status) in str(exc.value)
    if status != 500:
        assert exc.value.retry_after == 900


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>Log in</html>"),
        httpx.Response(200, json={"unexpected": []}),
    ],
)
async def test_invalid_responses_are_errors(response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(SourceError):
            await fetch_jobs(client, config())


@pytest.mark.parametrize("options", [{"experience": "entry"}, {"contract": "Freelance"}])
async def test_unsupported_filters_fail_before_any_request(options):
    def unexpected(request):
        pytest.fail("Unsupported filters must not make a request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(SourceError):
            await fetch_jobs(client, config(**options))


async def test_network_failure_is_a_source_error():
    def failed(request):
        raise httpx.ConnectError("unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
        with pytest.raises(SourceError, match="timed out"):
            await fetch_jobs(client, config())
