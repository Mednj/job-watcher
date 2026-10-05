import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_UI_TESTS") != "1", reason="Opt-in browser checks")


@pytest.fixture(scope="module")
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
                    if client.get(url + "/api/status").status_code == 200:
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
    import json

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
    page.reload()
    page.locator(".job.applied").wait_for()
    page.get_by_role("button", name="Undo applied", exact=True).click()
    page.locator(".job:not(.applied)").wait_for()
    assert not job["applied"]
