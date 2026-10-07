# Running Job Watcher

The first version is a single-user Python 3.13+ app with a local web dashboard,
two independent source workers, SQLite persistence, and a durable Telegram queue.
The original project README is preserved unchanged.

## Start on Windows

From the repository directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Create one search across selected platforms using a keyword
(`cloud`, `DevOps`, `data`, or `IT`), location, and contract. Searches default to
France and a 60-second interval. There are no preconfigured searches or example
jobs mixed into the real feed. An empty feed is normal until a successful scan.

On macOS/Linux, use `python3`, `.venv/bin/python`, and `cp .env.example .env`.
Keep the service running to keep checking. Close it with Ctrl+C. Search settings,
job history, scan state, source cooldowns, and unsent alerts survive restarts in
`data/jobs.sqlite3`. Back up the entire `data` directory when the app is stopped.

## Connect Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather), using `/newbot`.
2. Open that bot and send `/start` from the chat that should receive alerts.
3. Retrieve your chat ID with the Bot API. The following command prompts for
   the token without putting it in shell history and prints only chat IDs:

   ```powershell
   .\.venv\Scripts\python.exe -c "import getpass,httpx; token=getpass.getpass('Bot token: '); r=httpx.get('https://api.telegram.org/bot'+token+'/getUpdates').json(); print(sorted({str(u['message']['chat']['id']) for u in r.get('result',[]) if 'message' in u}))"
   ```

4. Sign in, open **Telegram delivery**, and enter your bot token and chat ID.
   Save it, then click **Send test message**. Tokens are private to each user
   and are never returned by the settings API.

The Bot API documentation is at https://core.telegram.org/bots/api#sendmessage.
The app does not poll `getUpdates` or register a webhook.

## Search behavior

- HelloWork uses native contract filters for Alternance, CDI, CDD, Stage, and
  Freelance, and native experience categories.
- LinkedIn's public guest search does not expose reliable French contract
  classification. A selected contract is added to the search keywords. The alert
  shows **Not listed** for contract information instead of inventing a contract.
  LinkedIn experience filters use its own entry/experienced categories.
- `IT` maps to `informatique` on HelloWork and a broader OR query (informatique,
  IT, cloud, DevOps, data, software, cybersécurité) on LinkedIn. Other keyword
  text is sent to each site's search engine. The returned titles must contain
  all keyword terms, case- and accent-insensitively, to remove irrelevant
  provider suggestions (for example tourism in Saint-Cloud for `cloud`). `IT`
  instead accepts a technology vocabulary covering development, cloud, data,
  systems, networks, and security. This is a title filter, not full-description
  semantic classification. HelloWork contract labels are also checked locally.
- Excluded keywords are matched case- and accent-insensitively against the
  title, employer, and location, not the full description.
- The first successful scan queues matching listings immediately, so new searches
  produce Telegram alerts without waiting for another job to be published.
  Listings already discovered by another search are not sent twice.
- Subsequent newly discovered source IDs are also queued immediately. A job seen
  by overlapping searches on the same platform generates one alert. Cross-site
  duplicates are retained: identical titles can represent different vacancies.
- The app checks only the newest result page (currently about 10–30 listings),
  with LinkedIn restricted to the last 24 hours. It can miss listings during
  high-volume bursts, long downtime, or incomplete provider search results.
  Narrow searches improve coverage; full backfill/pagination is future work.

## Timing and reliability

Each platform has its own worker. The delivery worker wakes when new jobs are
stored, independent of the next poll. Every alert is persisted before sending.
New jobs discovered while Telegram is unconfigured stay queued until setup.
Successful delivery records both API round-trip duration and time from first
detection to delivery. The dashboard displays the latter, including queue wait.

Publication dates have limited precision: LinkedIn returns a calendar date and
relative label; HelloWork cards expose a relative label. Those labels are
displayed as reported. They are not precise publication-to-detection latency.
The app cannot guarantee being the first observer of a newly published job.

Search intervals are 30–3600 seconds, default 60. Requests to each source have a
global 15-second minimum gap across searches. Many searches can delay a check.
Temporary source failures back off exponentially. HTTP 403/429/999 and upstream
rate limits apply a platform-wide cooldown; **Check now** respects that cooldown.
Challenge pages and unexpected HTML are reported as errors, never zero results.
No login sessions, CAPTCHA bypass, or proxy rotation are implemented. Public
HTML endpoints may change or restrict automated access.

Telegram delivery is sequential, spaced at least one second apart. Rate limits
honor `retry_after`; transient failures retry with backoff. Invalid credentials
or rejected chat messages become failed alerts, visible in the dashboard.
Fix settings and restart, then select **Retry failed alerts**. Delivery is
at least once: if Telegram accepts a message but its response is lost, a retry
can send it twice. History and pending alerts are retained without automatic
expiry; review the queue before connecting a bot after long downtime.

## Deployment

Run **one process and one Uvicorn worker** per database. Multiple workers or
replicas would perform duplicate scans/deliveries. The worker task supervisor
is in-process; use a service manager or container restart policy for unattended
operation. Do not use reload mode for an always-on deployment.

The dashboard supports self-registration and account login. Searches, job
history, application flags, recruiter bans, alert queues, and Telegram settings
are private to the signed-in account. An administrator can disable accounts.
Passwords are salted and hashed; API sessions expire after 30 days. Run behind
HTTPS and keep the Docker port private unless your reverse proxy protects it.

Set a strong `APP_ACCESS_TOKEN` in `.env` before using Compose (container
requests are remote from the app's perspective, even with the port bound to
localhost). On a fresh database the initial administrator is `admin`, with
that value as its starting password. Change it after signing in. On an existing
single-user database, the migration attaches existing searches, history, bans,
and legacy Telegram credentials to this admin account. New users can register
from the sign-in screen. The old shared access token is no longer accepted as a
general API credential.

```powershell
docker compose up --build -d
```

The Compose volume persists the database. Updating `.env` requires recreating
the container. API reference: `/docs` (API calls require your bearer token when
one is configured).

## Development and checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

Tests use synthetic HTML and mocked HTTP/Telegram responses; they never send
real alerts. Live provider checks are separate and can fail if access is
restricted. Source adapters are in `app/sources.py`; add future platforms with
the same normalized job model and their own source worker.

Browser tests verify search creation/editing/removal, mobile layout, Telegram
setup, and escaping of untrusted listing text. They start a separate temporary
database and never use your saved searches or bot credentials. CI runs them;
locally, opt in with:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
$env:RUN_UI_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest
```

Alternatively, set `BROWSER_EXECUTABLE` to an installed Chrome or Edge executable
to run headless tests without downloading Chromium.

## Multiple websites per search

Select LinkedIn, HelloWork, or both. Search criteria are shared; each platform
has independent status, scheduling, and backoff. Check now checks all selected
websites. Adding a platform starts its first scan; removing one stops monitoring
while preserving job history. Existing searches retain their original websites.
Edit them to add another website.

The API accepts `sources: ["linkedin", "hellowork"]`. The legacy `source` field
still works for single-platform clients. Empty source lists are rejected.

### Expanded platform coverage

One search can select LinkedIn, HelloWork, Welcome to the Jungle, France Travail (formerly Pôle emploi), APEC, Glassdoor, Indeed, JobTeaser, and Monster.

Welcome to the Jungle and France Travail public searches were verified locally. Welcome to the Jungle currently returns a ranked window of 10 results; France Travail returns 20. These integrations do not guarantee discovery of every new listing. Welcome to the Jungle uses native contract filtering; additional contract and city matching are local. City names must match the returned location text. Experience filters on these two integrations are currently unavailable and report an explicit error. France Travail alternance detection relies on the offer title, so listings without that wording can be missed.

APEC, Glassdoor, Indeed, JobTeaser, and Monster returned access challenges during validation. Their dashboard entries are availability placeholders, not functioning scrapers. Selecting them reports an integration-unavailable error with backoff, independently of working sources. Approved feeds, APIs or another verified integration are needed before these entries can deliver jobs. No CAPTCHA bypass is implemented. Existing saved searches retain their selected platforms; edit a search to add the new ones.

### Track applications

Click an opportunity title or “View & apply” in the dashboard to open the listing and a “Did you apply?” popup. Choose “Yes, I applied” after submitting: the card becomes grey and shows Applied. “Not yet” leaves it unmarked, and “Decide later” closes the popup. Use “Undo applied” to correct a mistake. This status persists across refreshes, restarts and repeated scans, independently of Telegram delivery. Clicks on direct job links inside Telegram cannot trigger this dashboard popup.

### Block recruiters

The application popup includes “Ban recruiter”. This blocks the listing's company name, since individual recruiter identities are not provided by the sources. Manage the global list in the Blocked recruiters tab: add names manually or remove bans. Matching is exact after normalizing case, accents and whitespace. Blocked companies are excluded across every search and platform, including queued alerts and the existing dashboard feed. History remains stored and reappears if unblocked. Alerts already in transmission cannot be recalled.

JobTeaser now has an optional experimental open-first/CDP browser reader. It is disabled until configured. The dedicated Chrome session and keyword navigation were verified against live results. See [JobTeaser setup and limitations](JOBTEASER.md#experimental-open-first-browser-adapter).


### Docker with a dedicated JobTeaser browser

`docker compose up --build -d` runs the app and a separate ordinary Chromium browser. The browser opens JobTeaser before the app attaches. It uses a persisted dedicated Linux profile, independently of your Windows Chrome window. Keep Docker Desktop running; your Windows browser can be closed.

The dashboard is at http://127.0.0.1:8000. On a new database, sign in as `admin` with the initial `APP_ACCESS_TOKEN` value; existing databases keep their data and use that value as the initial admin password after migration. The browser viewer is at http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale. Use it to complete JobTeaser's security checks or login manually. The container session starts fresh; successful access from Windows Chrome does not prove the Linux browser will be accepted.

JobTeaser uses loopback CDP shared between the two services. No debugging port is published. Dashboard and browser viewer ports bind only to host localhost. The browser viewer provides control of the dedicated profile and should remain local.

The database is bind-mounted from `./data`, preserving the existing app history. The browser profile uses the `jobteaser-profile` named volume. Stop the host Python server before starting Compose to avoid port conflicts and duplicate notification workers. `docker compose stop` pauses monitoring; `docker compose up -d` resumes it. Recreating the watcher also requires recreating its browser because they share a network namespace: `docker compose up -d --force-recreate`.

Both services have health checks and restart policies. Browser health means Chromium is reachable, not that JobTeaser has accepted its session. Check the JobTeaser search status in the dashboard for actual access errors. After manually clearing a challenge, request a fresh check. JobTeaser reads up to five pages per scan by default.

The browser restart test passed after isolating temporary display files on tmpfs and clearing only stale profile singleton links. JobTeaser returned 16 DevOps matches again after restart.

JobTeaser pagination: set `JOBTEASER_MAX_PAGES` in `.env` (default 5, range 1–20) and `JOBTEASER_PAGE_DELAY_SECONDS` (default/minimum 2 seconds). Recreate containers after changing these values. Scans stop at the last page, deduplicate offers by UUID, and reject invalid/repeated navigation. A challenge or broken later page fails the scan rather than reporting incomplete results as a success. Alerts are queued after the complete scan, so larger page limits increase detection-to-alert time. Contract and location filters still apply locally.
