import httpx
import pytest

from app.models import SearchInput
from app.providers import RESTRICTED, parse_francetravail, parse_wttj
from app.sources import SourceError, fetch_jobs

PAYLOAD = {
    "data": [
        {
            "reference": "one",
            "name": "DevOps",
            "slug": "devops-paris",
            "organization": {"name": "Example", "slug": "example"},
            "office": {"city": "Paris", "country_code": "FR"},
            "contract_type": "apprenticeship",
            "published_at": "2026-10-05T10:00:00Z",
        }
    ]
}
HTML = """<ul class="result-list">
<li class="result" data-id-offre="ABC123"><h2>
<span class="media-heading-title">DevOps alternance</span></h2>
<p class="subtext">Example - <span>75 - PARIS</span></p>
<div class="media-right"><p class="contrat">CDD<span>Temps plein</span></p></div>
<p class="date">Publié aujourd'hui</p></li></ul>"""


def test_public_source_parsers():
    job = parse_wttj(PAYLOAD)[0]
    assert job.contract == "Alternance"
    assert job.url.endswith("/example/jobs/devops-paris")
    assert job.published_precision == "second"
    job = parse_francetravail(HTML)[0]
    assert job.company == "Example"
    assert job.contract == "Alternance"
    assert job.published_at is None
    assert parse_wttj({"data": []}) == []
    with pytest.raises(SourceError):
        parse_francetravail("<html>Security check</html>")
    with pytest.raises(SourceError):
        parse_wttj({"unexpected": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("source", list(RESTRICTED))
async def test_unavailable_sources_never_succeed_with_zero(source, monkeypatch):
    monkeypatch.delenv("JOBTEASER_CDP_ENDPOINT", raising=False)
    search = SearchInput(name="test", sources=[source], keywords="devops").for_source(source)
    async with httpx.AsyncClient() as client:
        with pytest.raises(SourceError, match="integration unavailable"):
            await fetch_jobs(client, search)


@pytest.mark.asyncio
async def test_wttj_native_contract_and_local_location():
    def respond(request):
        assert request.url.params["contract_type[]"] == "apprenticeship"
        return httpx.Response(200, json=PAYLOAD)

    search = SearchInput(
        name="test", sources=["wttj"], keywords="devops", contract="Alternance", location="Paris"
    ).for_source("wttj")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert len(await fetch_jobs(client, search)) == 1
        assert await fetch_jobs(client, search.model_copy(update={"location": "Lyon"})) == []
