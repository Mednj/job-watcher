# Server deployment and CI/CD

Pushes to main run lint, all tests (including browser UI tests), and both Docker image builds on GitHub-hosted runners for pull requests and on the dedicated server runner for trusted main commits. Once the repository variable `DEPLOYMENT_ENABLED` is set to `true` after server provisioning, only a successful main-branch run deploys, using a dedicated self-hosted runner labelled `job-watcher`. Pull requests never run on that server runner. Workflow dispatch can redeploy main.

The production server needs Docker Compose v2, Git, outbound HTTPS, the runner service and `/etc/job-watcher.env` readable only by the deployment account. The public repository contains no credentials or browser profiles. Keep repository write access restricted to trusted maintainers; the deployment runner has Docker access.

Provision `/etc/job-watcher.env` outside the runner checkout with a strong APP_ACCESS_TOKEN and:

```
WATCHER_ENV_FILE=/etc/job-watcher.env
JOB_WATCHER_DATA_DIR=/var/lib/job-watcher/data
WATCHER_PORT=8000
BROWSER_VIEWER_PORT=7900
JOBTEASER_MAX_PAGES=5
JOBTEASER_PAGE_DELAY_SECONDS=2
```

Use available server ports. Create the data directory owned by UID/GID 1000 for the container user. The deployment script fixes the Compose project name, binds the viewer only to loopback and the dashboard to WATCHER_BIND_ADDRESS (loopback by default), backs up the database before updating an existing deployment, and recreates both services together. The database lives outside the disposable Actions checkout; the dedicated Linux browser profile stays in a named volume. Secrets are passed through the external env file, not GitHub workflow secrets or logs.

Expose the dashboard through the server's existing HTTPS reverse proxy. Keep the browser viewer private; use an SSH tunnel if a manual JobTeaser challenge needs completion. Never publish CDP or VNC publicly.

When the browser uses HTTPS through a reverse proxy, set `APP_PUBLIC_ORIGIN` in `/etc/job-watcher.env` to the exact browser-visible origin (for example, `https://job-watcher.example.com`). This allows same-origin API writes such as marking a job as applied when the app internally sees HTTP, while other origins remain rejected. Include only scheme, hostname, and an optional port; do not include a path. Leave it unset for direct access if the request origin already matches the app's URL.

After bootstrap, run `WATCHER_ENV_FILE=/etc/job-watcher.env bash scripts/deploy.sh` from the checkout, or let the production workflow deploy the tested commit. Verify container health, authenticated dashboard status, Telegram configuration, and actual source checks. Browser health alone does not establish JobTeaser access.

Migrate the local SQLite database with an online backup while the local watcher is stopped, preserving queued/sent jobs, searches, application flags and recruiter bans. On first startup the existing workspace is assigned to the initial admin; each account has separate searches, job history, recruiter blocks, alert queue, and Telegram credentials. Start only one monitoring deployment per database to prevent duplicate scans and alerts. The browser profile on a new server must be validated independently.

Deployments fail visibly on unhealthy containers. There is no automatic schema rollback. Retain database backups and redeploy a previously tested commit when needed, assessing schema compatibility first.

This installation uses a dedicated `job-runner` account and `/opt/job-watcher-runner`, independently of the PS5 app. The dashboard binds to the server LAN address 192.168.1.76 on port 8000 with app-token authentication. The viewer remains loopback-only on port 7900. Browser test system dependencies are installed during provisioning; main CI needs no sudo. A public HTTPS hostname still requires the existing reverse proxy/DNS configuration.
