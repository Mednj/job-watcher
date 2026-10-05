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

## Open-first browser adapter

Verified live on 2026-10-05 with the user's manually opened dedicated Chrome session. Attachment succeeded, and successive keyword searches returned 16 DevOps matches and 3 cloud matches located in France. These are validation counts, not promises of complete coverage.

The browser must be opened normally with a separate profile before automation connects. Keep exactly one JobTeaser search-results tab open and complete any login or security checks manually. The adapter attaches over local CDP, verifies the existing page, navigates to each search using the observed `q` parameter, reads the cards, then disconnects without closing Chrome. It never solves challenges.

Install optional browser dependencies with `.\.venv\Scripts\python.exe -m pip install -r requirements.browser.txt`. The Docker app image includes these dependencies.

Open Chrome yourself using PowerShell:

```powershell
& 'C:\Program Files\Google\Chrome\Application\chrome.exe' '--user-data-dir=C:\Users\mednj\Documents\Codex\2026-10-05\w\outputs\job-watcher\data\jobteaser-chrome' '--remote-debugging-address=127.0.0.1' '--remote-debugging-port=9223' '--no-first-run' 'https://www.jobteaser.com/fr/job-offers'
```

Once listings are visible, configure `JOBTEASER_CDP_ENDPOINT=http://127.0.0.1:9223` in `.env` and restart Job Watcher. Select JobTeaser in any saved search. Keep the browser open while monitoring. The local session has now been configured.

Verified DOM selectors use JobTeaser's `jobad-card` test IDs for titles, company, contract and location. Stable UUIDs identify offers; relative publication labels are preserved without invented timestamps. Missing or changed cards report an error. Confirmed empty-result pages are accepted, while challenges and unknown pages report errors with backoff.

Each search now changes the keyword independently. `IT` uses the native keyword `informatique` followed by the app's broader title matching. Up to five pages are read per scan by default, following the observed next-page links. There is no guaranteed chronological ordering. Contract and city filters are applied locally to that window. Default France searches require France in the location label, so listings with unspecified/multiple locations can be missed. Experience filtering is unavailable. Recruiter bans, deduplication, applied status and Telegram delivery use the normal shared pipeline.

Browser launch through the agent's terminal was rejected by automatic approval review; the user opened Chrome manually. The successful live test used that session. Expose the debugging port only on loopback and use a dedicated profile, never your everyday profile.


## Docker validation

On 2026-10-05 the dedicated ordinary Chromium service opened JobTeaser successfully inside Docker before attachment. Successive native keyword searches returned 16 DevOps and 3 cloud matches in France. The app and browser services were healthy, the browser viewer responded, and the existing database retained four searches and 76 jobs. Docker Desktop must remain running; the Windows Chrome window is no longer required for the containerized app. Use the local browser viewer only if the container session later presents a challenge.

JobTeaser pagination: set `JOBTEASER_MAX_PAGES` in `.env` (default 5, range 1–20) and `JOBTEASER_PAGE_DELAY_SECONDS` (default/minimum 2 seconds). Recreate containers after changing these values. Scans stop at the last page, deduplicate offers by UUID, and reject invalid/repeated navigation. A challenge or broken later page fails the scan rather than reporting incomplete results as a success. Alerts are queued after the complete scan, so larger page limits increase detection-to-alert time. Contract and location filters still apply locally.

Live multi-page validation in Docker: five DevOps pages contained 100 unique offers, of which 34 matched DevOps and France. The scan completed in approximately 15 seconds. The five-page cap was reached; the source had more pages available. These counts are a test snapshot.
