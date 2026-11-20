# Development and promotion

`main` remains the production branch. Creating development branches does not
restart or replace the production app. The existing production deployment gate,
runner label, backup script, and production environment stay in place.
The requested `prod` branch initially points to the same baseline as `main`.
It is a reference branch only: pushes to `prod` do not deploy. Moving production
deployment from `main` to `prod` is a separate migration, not part of this change.

Promotion order: feature branch -> `dev` -> `test` -> `pre-prod` -> `main`.
Use pull requests and promote only a passing candidate. These are Git branches,
not four persistent running copies of the watcher. Do not merge this candidate
into `main` until server verification has passed and production promotion is authorized.

- `dev`: integration and hosted lint, backend/UI tests, and Docker builds.
- `test`: release candidate with the same hosted checks.
- `pre-prod`: hosted checks followed by isolated verification on the existing
  dedicated Job Watcher server runner, using the `pre-production` environment.
- `main`: existing production CI/deployment workflow; `DEPLOYMENT_ENABLED` and
  the `production` environment still control deployment.

The server verification script is `bash scripts/verify-preprod.sh`. It first
prints current addresses and the production project's container status, then
checks the external root URL. It never reads `/etc/job-watcher.env` or resolves
production Compose configuration. Test builds use separate image names and a
separate checkout. Tests execute with networking disabled, synthetic temporary
databases, and no host volumes. The ephemeral app smoke container has its own
tmpfs database, no networking, no published ports, no saved searches or Telegram
credentials, and is removed on exit. It does not run a second monitor against
the production database or make live LinkedIn requests. Builds use outbound
networking to install dependencies; test execution does not.

Passing pre-production verifies application behavior on the server, not that
LinkedIn has lifted a live restriction. Live recovery can only be assessed after
an authorized production rollout over actual request cycles.

Before any production deployment, recheck the current DHCP-managed LAN address,
Compose health, and https://job-watcher.yndevs.com/. Follow DEPLOYMENT.md for
backup and rollback. Do not use any PS5 Deal Watcher resources.

Branch protection and required environment reviewers are separate GitHub
settings; branch creation alone does not enforce promotion or approvals.
