# Job Watcher

Job Watcher monitors selected job-search sources for opportunities in France and
sends matching alerts to each user's Telegram bot. It includes a web dashboard,
private accounts and searches, a persistent alert queue, and per-source health
reporting. It is a personal monitoring tool; it cannot guarantee that it sees a
job before another candidate or captures every listing on a site.

## Current capabilities

- **Separate accounts:** users register and sign in. Searches, job history,
  Telegram settings, application tracking, and blocked-company lists are private
  to each account. Administrators can disable accounts.
- **Flexible searches:** configure keywords, French location, contract and
  experience preferences, selected sources, and terms to exclude.
- **Telegram alerts:** each user configures their own bot token and chat ID.
  New matches are queued durably and sent without waiting for the next scheduled
  scan. Pending alerts wait until Telegram is configured.
- **Dashboard:** review jobs and source health, track applications, block
  companies, retry failed Telegram deliveries, and see alert timing. The headline
  timing is the median from discovery to delivery; the all-time 95th percentile
  and count of alerts taking over five minutes expose slow outliers.
- **Persistent storage:** searches, source state, job history, user settings, and
  pending deliveries survive container restarts in the data volume.

## Source availability

Source selection is available in the app, but the integrations do not all have
the same status. A blocked or unavailable source reports an error; it is never
treated as a successful search with zero results.

| Source | Current status | Coverage and limits |
| --- | --- | --- |
| LinkedIn | Available | Public guest results; limited to the newest result page and a recent-date window. |
| HelloWork | Available | Public search results; currently reads the newest result page. |
| Welcome to the Jungle | Available | Public search endpoint; returns a limited ranked result window. |
| France Travail | Available | Public search; currently reads up to 20 results per check. |
| JobTeaser | Experimental browser adapter | Uses the dedicated Chromium profile in Compose; scans up to five pages by default. Challenges must be handled manually. |
| APEC, Indeed, Glassdoor, Monster | Unavailable placeholders | No approved search feed is configured. Current direct access tests were denied or challenged; selecting these sources reports an availability error. |

The working adapters can still miss jobs because source results are limited,
search indexing can lag, and site layouts or access policies can change. See
[running and search details](docs/RUNNING.md) and [JobTeaser setup](docs/JOBTEASER.md).

## Run locally with Docker Compose

1. Copy `.env.example` to `.env` and set a long, unique `APP_ACCESS_TOKEN`.
2. Start the app and its dedicated JobTeaser browser:

   ```powershell
   docker compose up --build -d
   ```

3. Open [http://127.0.0.1:8000](http://127.0.0.1:8000). On a fresh database,
   sign in as `admin` with the `APP_ACCESS_TOKEN` value, then change the password.
   New users can register from the sign-in screen.
4. Create a search, choose the available sources, and configure Telegram in
   **Telegram delivery**. Each user supplies their own bot token and chat ID.

Compose stores app data under `./data` and JobTeaser's browser session in the
`jobteaser-profile` volume. The browser viewer is available locally at
`http://127.0.0.1:7900/vnc.html?autoconnect=true&resize=scale`; keep it private.
Back up `./data` and the Compose volume if you need to preserve the browser
session as well as the application database.

For Windows without Docker, setup, Telegram instructions, reliability limits,
and backup guidance are in [docs/RUNNING.md](docs/RUNNING.md).

## Development

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check .
```

Tests use synthetic listings and mocked source and Telegram responses; they do
not send real alerts. Browser UI tests are opt-in; see the running guide.

Changes pushed to `main` run CI, build the Docker images, and deploy to the
configured home server. Deployment prerequisites and recovery steps are in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).
