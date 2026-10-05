# JobTeaser integration investigation

Checked on 2026-10-05 using the terminal.

## Verified findings

- Public search: `https://www.jobteaser.com/fr/job-offers?keyword=devops` returns HTTP 403 and a Security checkup page to HTTPX.
- A standard JavaScript-enabled headless Edge session also returns HTTP 403 and a security challenge. Enabling JavaScript alone does not resolve access. No stealth plugins, CAPTCHA bypass or proxy rotation were used.
- The public keyword parameter is not verified, because no search results were accessible.
- JobTeaser advertises integrations for publishing recruiter offers, not a publicly documented candidate search API we can currently use.
- The app therefore retains its explicit unavailable status. These tests do not establish whether a user's normal signed-in session or school Career Center can access listings reliably.

## Next steps

1. Identify whether the user accesses JobTeaser directly or through a school Career Center, and obtain the Career Center URL when applicable.
2. Verify normal user access before implementing a session-backed integration. Authentication or security challenges require the user's own interaction; do not collect passwords in chat.
3. If native email job alerts are available in that account, consider importing them into the existing deduplication, recruiter-ban and Telegram pipeline. This depends on JobTeaser's email schedule and cannot promise immediate detection.
4. An approved candidate feed/API would be preferable for dependable rapid polling. Employer posting integrations alone do not satisfy this requirement.

Official recruiter integration information: https://recruiter.jobteaser.com/en/join-jobteaser/?flow=job

## Experimental open-first browser adapter

This adapter is opt-in and has not yet been validated against live JobTeaser results. It requires a dedicated ordinary Edge/Chromium browser opened by the user before Playwright attaches through CDP. Do not use your everyday browser profile. The initial page opening is outside Playwright, as in the Leboncoin adapter.

1. Install optional dependencies: `.\.venv\Scripts\python.exe -m pip install -r requirements.browser.txt`.
2. Open a dedicated Edge session from PowerShell in the repository directory:

```powershell
$profilePath = Join-Path (Get-Location) 'data\jobteaser-browser'
& 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe' "--user-data-dir=$profilePath" '--remote-debugging-address=127.0.0.1' '--remote-debugging-port=9223' '--no-first-run' 'https://www.jobteaser.com/fr/job-offers'
```

3. Let the page load, and complete any login or security challenge yourself. Keep exactly one JobTeaser results tab open. Set its filters to the region and roles you want to monitor. Do not enter credentials in chat.
4. Only after results are visible, set `JOBTEASER_CDP_ENDPOINT=http://127.0.0.1:9223` in the local `.env` and restart Job Watcher. Select JobTeaser in your search.

The adapter attaches to the already-open tab without creating a browser or altering browser defaults. It validates the loaded page before refreshing, and disconnects after reading it without closing the user's browser. It never solves a challenge. Access errors trigger backoff.

Currently it accepts standard structured JobPosting data only. The real results page must be inspected to confirm that format or add verified DOM selectors. An unsupported page is an explicit error, never a successful empty scan. All configured searches read the same browser results window and apply their own keyword/company-ban filters locally. No pagination or verified native query generation is implemented. The first manual page filters must include all desired roles; France filtering is not inferred from a URL. Contract matching is conservative and title-based; experience filtering is unavailable. Closing the browser disables this source. Loopback CDP access can control the dedicated browser; do not expose it on the network.

The initial automatic launch attempt was rejected by automatic approval review, so a live open-first test still requires the user's manual launch. Synthetic parser and lifecycle tests do not establish that JobTeaser permits or reliably serves this session.
