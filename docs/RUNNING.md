# Running Job Watcher

Job Watcher is a multi-user Python 3.13+ app with a web dashboard, independent
source workers, SQLite persistence, and a durable Telegram queue. Searches,
opportunities, application status, blocked companies, and Telegram settings are
scoped to the signed-in account.

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
- Stage searches also require local internship evidence on every source: an
  internship contract label, or an explicit Stage/Stagiaire/Intern/Internship/PFE
  title when the contract is unknown. Known other contracts and explicit senior,
  lead, principal, staff, confirmé or expérimenté titles are rejected. This is a
  conservative title/metadata check, not full-description classification; offers
  with insufficient evidence can be missed. Other contract searches are unchanged.
  Queued offers are rechecked against the owning account's linked searches;
  mismatches confined to Stage searches become Previously skipped. Those offers
  are hidden from the normal radar but remain stored and can be reviewed under
  Previously skipped. Applied/tracked offers stay accessible. A broader linked
  search in the same account can still qualify an offer; another account cannot.
  All history shows retained offers, including older deliveries that no longer
  match the current internship criteria, without overriding company bans.
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
- Result limits vary by source: LinkedIn and HelloWork read the newest result
  page, Welcome to the Jungle returns a ranked window of about 10, France Travail
  returns up to 20, and JobTeaser scans up to five pages by default. LinkedIn is
  restricted to the last 24 hours. These limits can miss listings during
  high-volume bursts, downtime, or incomplete provider search results.

## Timing and reliability

Each platform has its own worker. The delivery worker wakes when new jobs are
stored, independent of the next poll. Every alert is persisted before sending.
New jobs discovered while Telegram is unconfigured stay queued until setup.
Successful delivery records both API round-trip duration and time from first
detection to delivery. The dashboard displays the median discovery-to-delivery
time, plus the all-time 95th percentile and the count of alerts taking over five
minutes. The API also retains the arithmetic mean for clients that need it. A
backlog while Telegram is not configured can create a long tail; queued messages
wait and drain after the user's bot and chat are configured.

Publication dates have limited precision: LinkedIn returns a calendar date and
relative label; HelloWork cards expose a relative label. Those labels are
displayed as reported. They are not precise publication-to-detection latency.
The app cannot guarantee being the first observer of a newly published job.

Search intervals are 30–3600 seconds, default 60. Requests to each source have a
global 15-second minimum gap across searches. Many searches can delay a check.
Temporary source failures back off exponentially. HTTP 403/429/999 and upstream
rate limits apply a platform-wide cooldown; **Check now** respects that cooldown.
Challenge pages and unexpected HTML are reported as errors, never zero results.
LinkedIn also retains adaptive pacing across restarts. An HTTP 429 doubles its
shared request gap from the configured minimum (normally 15 seconds), up to
120 seconds, without reducing a higher configured minimum. All LinkedIn searches
and accounts share that gap and cooldown; their job data remains private.
After the cooldown, eight consecutive successful requests reduce the gap by 25%,
with further groups of eight gradually restoring the minimum. A failed request
resets the success streak. Retry-After seconds or HTTP dates are respected; a
missing or invalid value defaults to five minutes, and existing search backoff
can wait longer. Manual checks and restarts cannot bypass the cooldown.
The dashboard shows cooling down, its end time, and the recovery request gap.
Search intervals remain unchanged; pacing can delay their actual execution.
This reduces repeated rate limiting but cannot guarantee LinkedIn access.
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

Career Polytech (`https://career.polytech.network/`) is available as a separate
source. It uses the site's public offer-list endpoint, with France and native
Stage, Alternance, CDI or CDD filters, and reads the first 12 results per check.
Results follow the site's ranking; offers outside that window may be missed.
Title keywords, excluded terms, contract labels and city matching are checked
locally. Experience and Freelance filters are unsupported and report an error.
Offer viewing is public; applying or accessing contact details may require a
Polytech account. No login or credentials are used by the adapter. Missing cards,
malformed replies, access restrictions and incomplete result windows report
errors rather than a successful empty scan. Existing saved searches retain their
selected sources; edit a search to enable Career Polytech.

Select any available sources for a search. Criteria are shared; each platform
has independent status, scheduling, and backoff. Check now requests an immediate
check of selected sources. Adding a platform starts its first scan; removing one
stops monitoring while preserving job history. Existing searches retain their
original sources until edited.

The API accepts a list of selected sources. The legacy `source` field still
works for single-platform clients. Empty source lists are rejected.

### Expanded platform coverage

One search can select LinkedIn, HelloWork, Welcome to the Jungle, France Travail
(formerly Pôle emploi), APEC, Glassdoor, Indeed, JobTeaser, and Monster.

Welcome to the Jungle and France Travail public searches were verified locally. Welcome to the Jungle currently returns a ranked window of 10 results; France Travail returns 20. These integrations do not guarantee discovery of every new listing. Welcome to the Jungle uses native contract filtering; additional contract and city matching are local. City names must match the returned location text. Experience filters on these two integrations are currently unavailable and report an explicit error. France Travail alternance detection relies on the offer title, so listings without that wording can be missed.

APEC, Glassdoor, Indeed, and Monster are availability placeholders, not
functioning adapters. APEC and Indeed returned HTTP 403 from the deployed server
during read-only checks; the exact denial reason was not provided. Selecting a
placeholder reports an integration-unavailable error with backoff, not a
successful empty search. An approved feed or another authorized integration is
needed. No CAPTCHA bypass is implemented. JobTeaser has a separate experimental
browser adapter described below.

### Track applications

Click an opportunity title or “View & apply” in the dashboard to open the listing and a “Did you apply?” popup. Choose “Yes, I applied” after submitting: the card becomes grey and shows Applied. “Not yet” leaves it unmarked, and “Decide later” closes the popup. Use “Undo applied” to correct a mistake. This status persists across refreshes, restarts and repeated scans, independently of Telegram delivery. Clicks on direct job links inside Telegram cannot trigger this dashboard popup.

After successfully confirming “Yes, I applied”, a separate prompt asks whether to
track the application. “No, thanks”, Escape, or closing the page leaves it Applied
without enrolling it. Existing applied offers are never enrolled automatically;
use **Track application** on an applied opportunity card to add one later.

Open **My applications** for status counts, title/company search, status filters,
and paginated application cards. **Update progress** lets you set Applied,
Interviewing, Offer received, Accepted, Rejected, or Withdrawn; record an optional
application date; and save notes. Unknown dates remain blank for older applications.
The latest 100 changes are shown in each application's history. Simultaneous edits
are detected: close and reopen the editor to load the latest version before retrying.

Removing an application keeps its Applied flag and saves its notes and history for
re-adding later. Undoing Applied also removes it from the active tracker; confirming
Applied again still requires a separate opt-in. Blocking a company does not hide
applications you already chose to track. All tracker data is private to your account.
Statuses are manual; Gmail integration is a separate future phase.

### Block recruiters

The application popup includes “Ban recruiter”. This blocks the listing's company name, since individual recruiter identities are not provided by the sources. Manage the global list in the Blocked recruiters tab: add names manually or remove bans. Matching is exact after normalizing case, accents and whitespace. Blocked companies are excluded across every search and platform, including queued alerts and the existing dashboard feed. History remains stored and reappears if unblocked. Alerts already in transmission cannot be recalled.

JobTeaser has an experimental open-first/CDP browser reader. The deployed Compose
stack starts its dedicated Chromium session, and a live search smoke test
returned matching results. It stops on challenges and requires manual handling;
it does not bypass them. See [JobTeaser setup and limitations](JOBTEASER.md#experimental-open-first-browser-adapter).


### Docker with a dedicated JobTeaser browser

`docker compose up --build -d` runs the app and a separate ordinary Chromium browser. The browser opens JobTeaser before the app attaches. It uses a persisted dedicated Linux profile, independently of your Windows Chrome window. Keep Docker Desktop running; your Windows browser can be closed.

The dashboard is at http://127.0.0.1:8000. On a new database, sign in as `admin` with the initial `APP_ACCESS_TOKEN` value; existing databases keep their data and use that value as the initial admin password after migration. The browser viewer is at http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale. Use it to complete JobTeaser's security checks or login manually. The container session starts fresh; successful access from Windows Chrome does not prove the Linux browser will be accepted.

JobTeaser uses loopback CDP shared between the two services. No debugging port is published. Dashboard and browser viewer ports bind only to host localhost. The browser viewer provides control of the dedicated profile and should remain local.

The database is bind-mounted from `./data`, preserving the existing app history. The browser profile uses the `jobteaser-profile` named volume. Stop the host Python server before starting Compose to avoid port conflicts and duplicate notification workers. `docker compose stop` pauses monitoring; `docker compose up -d` resumes it. Recreating the watcher also requires recreating its browser because they share a network namespace: `docker compose up -d --force-recreate`.

Both services have health checks and restart policies. Browser health means Chromium is reachable, not that JobTeaser has accepted its session. Check the JobTeaser search status in the dashboard for actual access errors. After manually clearing a challenge, request a fresh check. JobTeaser reads up to five pages per scan by default.

The browser restart test passed after isolating temporary display files on tmpfs and clearing only stale profile singleton links. JobTeaser returned 16 DevOps matches again after restart.

JobTeaser pagination: set `JOBTEASER_MAX_PAGES` in `.env` (default 5, range 1–20) and `JOBTEASER_PAGE_DELAY_SECONDS` (default/minimum 2 seconds). Recreate containers after changing these values. Scans stop at the last page, deduplicate offers by UUID, and reject invalid/repeated navigation. A challenge or broken later page fails the scan rather than reporting incomplete results as a success. Alerts are queued after the complete scan, so larger page limits increase detection-to-alert time. Contract and location filters still apply locally.
