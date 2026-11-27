import httpx
import pytest

from app.models import SOURCES, Job, SearchInput
from app.sources import fetch_jobs
from app.store import Store


def offer(title="Cloud engineer", contract="Not listed", source="linkedin"):
    return Job(source, "123", title, "Example", "Paris", "https://example.com/123", contract)


def internship():
    return SearchInput(name="Internships", source="linkedin", keywords="cloud", contract="Stage")


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize(
    "title,contract,expected",
    [
        ("Stage Cloud engineer", "Not listed", True),
        ("Cloud engineering internship", "Not listed", True),
        ("Cloud intern", "Not listed", True),
        ("Stagiaire Cloud", "Not listed", True),
        ("PFE Cloud", "Not listed", True),
        ("Cloud engineer", "Stage", True),
        ("Cloud engineer", "internship", True),
        ("Cloud engineer", "Not listed", False),
        ("Senior Cloud engineer", "Not listed", False),
        ("Stage Cloud senior", "Stage", False),
        ("Cloud Lead Intern", "Not listed", False),
        ("Cloud engineer confirmé", "Stage", False),
        ("Stage Cloud engineer", "CDI", False),
        ("Cloud engineer", "CDD", False),
        ("Cloud engineer", "Freelance", False),
        ("Cloud engineer", "Alternance", False),
        ("Cloud internal tools engineer", "Not listed", False),
        ("Cloud international consultant", "Not listed", False),
        ("Stage / CDI Cloud engineer", "Not listed", False),
        ("Stage Cloud puis CDI à la clé", "Stage", True),
    ],
)
def test_stage_requires_internship_evidence_for_every_source(source, title, contract, expected):
    assert offer(title, contract, source).matches(internship()) is expected


def test_other_searches_can_still_find_senior_permanent_jobs():
    search = internship().model_copy(update={"contract": "any"})
    assert offer("Senior Cloud engineer", "CDI").matches(search)


async def test_linkedin_recommendations_are_filtered_after_fetch():
    def card(id, title):
        return (
            f'<li data-entity-urn="urn:li:jobPosting:{id}">'
            f'<h3 class="base-search-card__title">{title}</h3>'
            '<span class="job-search-card__location">Paris, France</span></li>'
        )

    html = card(1, "Senior Cloud engineer") + card(2, "Cloud intern")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    ) as client:
        jobs = await fetch_jobs(client, internship())
    assert [job.source_id for job in jobs] == ["2"]


def test_storage_guard_keeps_bad_alerts_out_without_changing_other_accounts(tmp_path):
    store = Store(str(tmp_path / "accounts.sqlite3"))
    alice = store.add_user("alice", "test-password-only")
    strict = internship()
    broad = strict.model_copy(update={"contract": "any"})
    admin_search = store.add_search(strict, 1)
    alice_search = store.add_search(broad, alice["id"])
    senior = offer("Senior Cloud engineer", "CDI")
    assert store.record_scan(admin_search["id"], strict, [senior], 1) == 0
    assert store.summary(1)["pending"] == 0
    assert store.record_scan(alice_search["id"], broad, [senior], 1) == 1
    assert store.summary(alice["id"])["pending"] == 1
    assert store.jobs(user_id=1) == []
    store.close()


def test_old_queue_and_feed_are_rechecked_per_account_without_deleting_history(tmp_path):
    store = Store(str(tmp_path / "old-queue.sqlite3"))
    alice = store.add_user("alice", "test-password-only")
    broad = internship().model_copy(update={"contract": "any"})
    admin_search = store.add_search(broad)
    alice_search = store.add_search(broad, alice["id"])
    senior = offer("Senior Cloud engineer", "CDI")
    for search in (admin_search, alice_search):
        store.record_scan(search["id"], broad, [senior], 1)
    store.update_search(admin_search["id"], internship())
    assert store.jobs(user_id=1) == []
    assert len(store.jobs(user_id=alice["id"])) == 1
    assert len(store.jobs(user_id=1, include_unmatched=True)) == 1
    pending = store.next_delivery()
    assert pending["user_id"] == alice["id"]
    assert store.summary(1)["pending"] == 0
    assert store.summary(1)["jobs"] == 1
    history = store.jobs(status="baseline", user_id=1)
    assert history[0]["key"] == senior.key
    assert "internship" in history[0]["error"]
    store.set_applied(senior.key, True)
    assert store.jobs(user_id=1)[0]["applied"]
    store.close()


def test_another_matching_search_in_same_account_can_still_authorize_offer(tmp_path):
    store = Store(str(tmp_path / "overlap.sqlite3"))
    broad = internship().model_copy(update={"contract": "any"})
    first = store.add_search(broad)
    second = store.add_search(broad)
    senior = offer("Senior Cloud engineer", "CDI")
    for search in (first, second):
        store.record_scan(search["id"], broad, [senior], 1)
    store.update_search(first["id"], internship())
    assert store.next_delivery()["key"] == senior.key
    assert store.jobs()[0]["key"] == senior.key
    store.close()


def test_hidden_offers_do_not_consume_feed_limit(tmp_path):
    store = Store(str(tmp_path / "pagination.sqlite3"))
    broad = internship().model_copy(update={"contract": "any"})
    search = store.add_search(broad)
    good = offer("Cloud intern")
    bad = offer("Senior Cloud engineer", "CDI")
    bad.source_id = "999"
    store.record_scan(search["id"], broad, [good, bad], 1)
    store.update_search(search["id"], internship())
    assert [job["key"] for job in store.jobs(limit=1)] == [good.key]
    store.close()
