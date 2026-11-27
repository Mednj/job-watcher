import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_UI_TESTS") != "1", reason="Opt-in browser checks")


@pytest.fixture
def ui_server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("dashboard")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "JOB_WATCHER_DB": str(folder / "jobs.sqlite3"),
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "APP_ACCESS_TOKEN": "",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=Path(__file__).parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(timeout=1) as client:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if client.get(url + "/api/status").status_code in (200, 401):
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail("Dashboard test server did not start")
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def page(ui_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = os.getenv("BROWSER_EXECUTABLE")
        browser = playwright.chromium.launch(headless=True, executable_path=executable)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ui_server)
        page.locator("#auth-dialog").wait_for(state="visible")
        page.locator("#login-username").fill("admin")
        page.locator("#login-password").fill("admin@")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_text("Radar online", exact=True).wait_for()
        yield page
        assert not errors, errors
        browser.close()


def test_dashboard_create_edit_remove_search(page):
    page.get_by_role("button", name="＋ New search", exact=True).first.click()
    page.locator("#search-name").fill("UI smoke test")
    page.locator("#search-linkedin").uncheck()
    assert page.locator("#search-hellowork").is_checked()
    page.locator("#search-contract").select_option("CDD")
    page.locator("#search-keywords").fill("data")
    page.locator("#search-location").fill("Paris")
    page.locator("#search-enabled").uncheck()
    page.get_by_role("button", name="Save search", exact=True).click()
    page.locator("#search-dialog").wait_for(state="hidden")
    page.get_by_role("button", name="Saved searches").click()
    page.get_by_role("heading", name="UI smoke test", exact=True).wait_for()
    page.get_by_text("Paused", exact=True).wait_for()
    page.get_by_role("button", name="Edit", exact=True).click()
    assert not page.locator("#search-linkedin").is_checked()
    page.locator("#search-linkedin").check()
    assert page.locator("#search-contract").input_value() == "CDD"
    page.locator("#search-name").fill("Edited search")
    page.locator("#search-contract").select_option("CDI")
    page.get_by_role("button", name="Save search", exact=True).click()
    page.get_by_role("heading", name="Edited search", exact=True).wait_for()
    page.get_by_role("button", name="Remove", exact=True).click()
    page.get_by_role("heading", name="A focused search is a faster start.", exact=True).wait_for()


def test_mobile_and_telegram_setup(page):
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Telegram delivery").click()
    page.get_by_role("heading", name="Not connected yet", exact=True).wait_for()
    assert page.locator("#test-telegram").is_disabled()
    page.get_by_role("button", name="Setup guide", exact=True).click()
    page.get_by_role("heading", name="Connect Telegram", exact=True).wait_for()
    page.get_by_role("button", name="Got it", exact=True).click()
    page.get_by_role("button", name="Opportunity radar").click()
    page.get_by_role("button", name="＋ New search", exact=True).first.click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Cancel", exact=True).click()


def test_all_history_requests_retained_unmatched_offers(page):
    requested = []

    def jobs(route):
        requested.append(route.request.url)
        route.fulfill(json=[])

    page.route("**/api/jobs?*", jobs)
    with page.expect_response(lambda response: "include_unmatched=true" in response.url):
        page.get_by_role("button", name="All history", exact=True).click()
    assert any("include_unmatched=true" in url for url in requested)


def test_polytech_can_be_saved_edited_and_filtered(page):
    page.get_by_role("button", name="＋ New search", exact=True).first.click()
    page.locator("#search-name").fill("Polytech search")
    page.locator("#search-keywords").fill("cloud")
    page.locator("#search-linkedin").uncheck()
    page.locator("#search-hellowork").uncheck()
    page.get_by_label("Career Polytech", exact=True).check()
    page.locator("#search-enabled").uncheck()
    page.get_by_role("button", name="Save search", exact=True).click()
    page.get_by_role("button", name="Saved searches").click()
    page.get_by_role("heading", name="Polytech search", exact=True).wait_for()
    page.get_by_role("button", name="Edit", exact=True).click()
    assert page.locator("#search-polytech").is_checked()
    page.get_by_role("button", name="Cancel", exact=True).click()
    page.get_by_role("button", name="Opportunity radar").click()
    page.locator("#source-filter").select_option("polytech")
    assert page.locator("#source-filter").input_value() == "polytech"


def test_light_mode_is_saved_and_keeps_mobile_layout(page):
    page.evaluate('localStorage.removeItem("jw-theme")')
    page.reload()
    assert page.locator("html").get_attribute("data-theme") == "dark"
    page.get_by_role("button", name="Light mode", exact=True).click()
    assert page.locator("html").get_attribute("data-theme") == "light"
    assert page.get_by_role("button", name="Dark mode", exact=True).get_attribute(
        "aria-pressed"
    ) == "true"
    assert page.evaluate('localStorage.getItem("jw-theme")') == "light"
    page.reload()
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_linkedin_pacing_is_visible_without_hiding_source_errors(page):
    cooling = [True]

    def status(route):
        data = route.fetch().json()
        now = time.time()
        data["sources"] = [{
            "source": "linkedin", "next_request": now + 600,
            "gap_seconds": 60, "cooldown_until": now + 600 if cooling[0] else 0,
            "pacing_status": "cooling_down" if cooling[0] else "recovering",
        }]
        data["searches"] = [{
            "id": 999, "name": "Pacing test", "sources": ["linkedin"],
            "keywords": "cloud", "location": "France", "contract": "any",
            "interval_seconds": 30, "exclude_keywords": [], "enabled": True,
            "initialized": True, "error": "Source access restricted (HTTP 429)",
            "source_statuses": [{
                "source": "linkedin", "initialized": True, "last_success": now - 900,
                "last_count": 3, "last_new": 0, "last_duration_ms": 300,
                "next_check": now + 300, "error": "Source access restricted (HTTP 429)",
            }],
        }]
        route.fulfill(json=data)

    page.route("**/api/status", status)
    page.reload()
    page.get_by_text("Cooling down", exact=True).wait_for()
    page.get_by_role("button", name="Saved searches").click()
    page.locator("#searches").get_by_text("Source access restricted (HTTP 429)").wait_for()
    assert "Requests resume at least 60s apart" in page.locator("#searches").inner_text()
    cooling[0] = False
    page.reload()
    page.get_by_role("button", name="Saved searches").click()
    page.locator("#searches").get_by_text(
        "Recovering after rate limiting · requests at least 60s apart."
    ).wait_for()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_job_text_cannot_inject_html(page):
    malicious = '<img src=x onerror="window.injected=true">'
    job = {
        "source": "linkedin",
        "title": malicious,
        "company": "Example",
        "location": "Paris",
        "contract": "Not listed",
        "published_label": "",
        "status": "baseline",
        "url": "javascript:alert(1)",
        "first_seen": time.time(),
        "error": None,
    }
    page.route("**/api/jobs?*", lambda route: route.fulfill(json=[job]))
    page.locator("#source-filter").select_option("linkedin")
    page.locator("#jobs h3 a").wait_for()
    assert page.locator("#jobs h3 a").inner_text() == malicious
    assert page.locator("#jobs img").count() == 0
    assert page.locator("#jobs h3 a").get_attribute("href") == "#"


def test_applied_popup_and_undo(page):
    job = {
        "key": "linkedin:999",
        "source": "linkedin",
        "title": "Cloud popup test",
        "company": "Example",
        "location": "Paris",
        "contract": "CDI",
        "url": "https://www.linkedin.com/jobs/view/999/",
        "status": "sent",
        "first_seen": time.time(),
        "applied": False,
    }
    page.route("**/api/jobs?*", lambda route: route.fulfill(json=[job]))

    def update(route):
        job["applied"] = json.loads(route.request.post_data)["applied"]
        route.fulfill(json={"key": job["key"], "applied": job["applied"]})

    page.route("**/api/jobs/linkedin%3A999/application", update)
    page.reload()
    link = page.get_by_role("link", name="Cloud popup test")
    link.wait_for()
    # Keep the test offline while exercising the normal link click handler.
    link.evaluate('(element) => element.addEventListener("click", event => event.preventDefault())')
    link.click()
    page.locator("#application-dialog").wait_for(state="visible")
    page.get_by_role("button", name="Not yet", exact=True).click()
    page.locator("#application-dialog").wait_for(state="hidden")
    assert not job["applied"]
    link.click()
    page.get_by_role("button", name="Yes, I applied", exact=True).click()
    page.locator(".job.applied").wait_for()
    page.locator("#tracking-prompt").wait_for(state="visible")
    page.get_by_role("button", name="No, thanks", exact=True).click()
    page.reload()
    page.locator(".job.applied").wait_for()
    page.get_by_role("button", name="Undo applied", exact=True).click()
    page.locator(".job:not(.applied)").wait_for()
    assert not job["applied"]


def test_rejected_recruiter_ban_closes_popup_and_shows_reason(page):
    job = {
        "key": "hellowork:78824163",
        "source": "hellowork",
        "title": "Example role",
        "company": "Not listed",
        "location": "Paris - 75",
        "contract": "CDI",
        "url": "https://www.hellowork.com/fr-fr/emplois/78824163.html",
        "status": "sent",
        "first_seen": time.time(),
        "applied": False,
    }
    page.route("**/api/jobs?*", lambda route: route.fulfill(json=[job]))

    def reject_ban(route):
        route.fulfill(
            status=422,
            json={
                "detail": [
                    {
                        "loc": ["body", "name"],
                        "msg": "Value error, Enter an identifiable company name",
                        "type": "value_error",
                    }
                ]
            },
        )

    page.route("**/api/recruiter-bans", reject_ban)
    page.reload()
    link = page.get_by_role("link", name="Example role")
    link.wait_for()
    link.evaluate('(element) => element.addEventListener("click", event => event.preventDefault())')
    link.click()
    page.locator("#application-dialog").wait_for(state="visible")
    page.get_by_role("button", name="Ban recruiter", exact=True).click()
    page.locator("#application-dialog").wait_for(state="hidden")
    page.locator("#toast").get_by_text(
        "Can't ban this recruiter: Enter an identifiable company name", exact=True
    ).wait_for(state="visible")


def test_account_registration_and_private_telegram_settings(page):
    page.evaluate('document.getElementById("auth-dialog").showModal()')
    page.get_by_role("button", name="Create an account", exact=True).click()
    page.locator("#register-username").fill("ui_private_user")
    page.locator("#register-password").fill("ui-private-pass-123")
    page.get_by_role("button", name="Create account", exact=True).click()
    page.locator("#auth-dialog").wait_for(state="hidden")
    page.locator("#current-user").get_by_text("ui_private_user", exact=True).wait_for()
    assert page.locator("#users-nav").is_hidden()

    page.get_by_role("button", name="Telegram delivery").click()
    page.locator("#telegram-token").fill("private-test-bot-token")
    page.locator("#telegram-chat-id").fill("123456")
    page.get_by_role("button", name="Save bot settings", exact=True).click()
    page.locator("#toast.visible").wait_for(state="visible")
    assert page.locator("#toast").inner_text() == "Your Telegram bot is saved for this account."
    page.get_by_role("heading", name="Credentials configured", exact=True).wait_for()
    assert page.locator("#telegram-token").input_value() == ""


def test_blocked_recruiter_tab_add_remove(page):
    page.get_by_role("button", name="Blocked recruiters").click()
    page.locator("#ban-name").fill("UI blocked company")
    page.locator("#ban-form").get_by_role("button", name="Ban recruiter", exact=True).click()
    page.get_by_role("heading", name="UI blocked company", exact=True).wait_for()
    page.reload()
    page.get_by_role("button", name="Blocked recruiters").click()
    page.get_by_role("heading", name="UI blocked company", exact=True).wait_for()
    page.get_by_role("button", name="Remove ban", exact=True).click()
    page.get_by_text("No blocked recruiters.", exact=True).wait_for()


def test_application_tracking_flow_and_mobile_layout(page):
    job = {
        "key": "linkedin:tracking",
        "source": "linkedin",
        "title": "Cloud tracking test",
        "company": "Example",
        "location": "Paris",
        "contract": "CDI",
        "url": "https://www.linkedin.com/jobs/view/123/",
        "status": "sent",
        "first_seen": time.time(),
        "applied": False,
        "tracked": False,
    }
    record = None
    page.route("**/api/jobs?*", lambda route: route.fulfill(json=[job]))

    def apply(route):
        job["applied"] = json.loads(route.request.post_data)["applied"]
        route.fulfill(json={"key": job["key"], "applied": job["applied"]})

    def listing(route):
        from urllib.parse import parse_qs, urlparse

        params = parse_qs(urlparse(route.request.url).query)
        match = (
            record
            and job["tracked"]
            and (not params.get("status") or params["status"][0] == record["status"])
        )
        route.fulfill(
            json={
                "items": [record] if match else [],
                "total": int(bool(match)),
                "counts": {record["status"]: 1} if record and job["tracked"] else {},
            }
        )

    def application(route):
        nonlocal record
        method = route.request.method
        if method == "POST":
            data = json.loads(route.request.post_data)
            if not record:
                record = {
                    **{k: job[k] for k in ("title", "company", "url", "location", "source")},
                    "job_key": job["key"],
                    "status": "applied",
                    "notes": "",
                    "applied_on": data.get("applied_on"),
                    "version": 1,
                    "created_at": time.time(),
                    "updated_at": time.time(),
                    "history": [],
                }
            job["tracked"] = True
        elif method == "PUT":
            record.update(json.loads(route.request.post_data))
            record["version"] += 1
            record["history"].insert(
                0,
                {
                    "kind": "updated",
                    "status": record["status"],
                    "notes": record["notes"],
                    "applied_on": record["applied_on"],
                    "created_at": time.time(),
                },
            )
        elif method == "DELETE":
            job["tracked"] = False
            route.fulfill(json={"ok": True})
            return
        route.fulfill(json=record)

    page.route("**/api/jobs/linkedin%3Atracking/application", apply)
    page.route("**/api/applications?*", listing)
    page.route("**/api/applications/linkedin%3Atracking", application)
    page.reload()
    link = page.get_by_role("link", name=job["title"], exact=True)
    link.wait_for()
    link.evaluate('(element) => element.addEventListener("click", event => event.preventDefault())')
    link.click()
    page.get_by_role("button", name="Yes, I applied", exact=True).click()
    page.locator("#tracking-prompt").wait_for(state="visible")
    assert record is None  # Merely confirming applied must not enroll it.
    page.get_by_role("button", name="No, thanks", exact=True).click()
    page.get_by_role("button", name="My applications").click()
    page.get_by_role("heading", name="Your next chapter starts here", exact=True).wait_for()
    page.get_by_role("button", name="Opportunity radar").click()
    page.get_by_role("button", name="Track application", exact=True).click()
    assert page.locator("#tracking-add-date").input_value() == ""
    page.locator("#tracking-add-date").fill("2026-11-03")
    page.get_by_role("button", name="Yes, track it", exact=True).click()
    page.locator("#tracking-prompt").wait_for(state="hidden")
    page.get_by_role("button", name="My applications").click()
    page.get_by_role("button", name="Update progress", exact=True).click()
    page.locator("#tracking-status").select_option("interviewing")
    note = '<img src=x onerror="window.trackingInjected=true"> Interview on Friday'
    page.locator("#tracking-notes").fill(note)
    page.get_by_role("button", name="Save progress", exact=True).click()
    page.locator("#tracking-detail").wait_for(state="hidden")
    page.locator(".tracking-badge.interviewing").wait_for()
    assert page.locator(".tracking-note").inner_text() == note
    assert not page.evaluate("Boolean(window.trackingInjected)")
    assert page.locator("#tracking-list img").count() == 0
    page.locator("#tracking-filter").select_option("rejected")
    page.get_by_role("heading", name="No applications match these filters", exact=True).wait_for()
    page.locator("#tracking-filter").select_option("")
    page.locator(".tracking-card").wait_for()
    screenshots = os.getenv("TRACKING_SCREENSHOTS")
    if screenshots:
        Path(screenshots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshots) / "tracking-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if screenshots:
        page.screenshot(path=str(Path(screenshots) / "tracking-mobile.png"), full_page=True)
    page.get_by_role("button", name="Update progress", exact=True).click()
    assert page.locator("#tracking-notes").input_value() == note
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator(".tracking-history summary").click()
    assert page.locator("#tracking-history li").count() == 1
    assert page.locator("#tracking-history img").count() == 0
    page.get_by_role("button", name="Remove from tracker", exact=True).click()
    page.get_by_role("heading", name="Your next chapter starts here", exact=True).wait_for()
    assert job["applied"]
    page.get_by_role("button", name="Opportunity radar").click()
    page.get_by_role("button", name="Track application", exact=True).click()
    page.get_by_role("button", name="Yes, track it", exact=True).click()
    page.locator("#tracking-prompt").wait_for(state="hidden")
    page.get_by_role("button", name="My applications").click()
    page.locator(".tracking-badge.interviewing").wait_for()
    assert record["notes"] == note


def test_tracking_prompt_only_after_successful_application_save(page):
    job = {
        "key": "linkedin:failure",
        "source": "linkedin",
        "title": "Cloud failed save",
        "company": "Example",
        "location": "Paris",
        "contract": "CDI",
        "url": "https://www.linkedin.com/jobs/view/123/",
        "status": "sent",
        "first_seen": time.time(),
        "applied": False,
    }
    page.route("**/api/jobs?*", lambda route: route.fulfill(json=[job]))
    page.route(
        "**/api/jobs/linkedin%3Afailure/application",
        lambda route: route.fulfill(status=500, json={"detail": "Save failed"}),
    )
    page.reload()
    link = page.get_by_role("link", name=job["title"], exact=True)
    link.wait_for()
    link.evaluate('(element) => element.addEventListener("click", event => event.preventDefault())')
    link.click()
    page.get_by_role("button", name="Yes, I applied", exact=True).click()
    page.get_by_text("Save failed", exact=True).wait_for()
    assert page.locator("#application-dialog").is_visible()
    assert not page.locator("#tracking-prompt").is_visible()
    page.get_by_role("button", name="Decide later", exact=True).click()
