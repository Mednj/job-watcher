#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(git rev-parse --show-toplevel)"
export WATCHER_ENV_FILE="${WATCHER_ENV_FILE:-/etc/job-watcher.env}"
[[ -r "$WATCHER_ENV_FILE" ]] || { echo "Missing deployment environment: $WATCHER_ENV_FILE" >&2; exit 2; }
cd "$repo_root"
compose=(docker compose --project-name job-watcher --env-file "$WATCHER_ENV_FILE" -f compose.yaml)
# Save a consistent database backup before recreating a running deployment.
if [[ -n "$("${compose[@]}" ps --status running -q watcher)" ]]; then
  "${compose[@]}" exec -T watcher python -c 'import sqlite3,time,pathlib; root=pathlib.Path("/app/data/backups"); root.mkdir(exist_ok=True); source=sqlite3.connect("/app/data/jobs.sqlite3"); target=sqlite3.connect(str(root / (time.strftime("%Y%m%d-%H%M%S",time.gmtime())+".sqlite3"))); source.backup(target); target.close(); source.close()'
fi
"${compose[@]}" up -d --build --force-recreate
for attempt in $(seq 1 60); do
  ready=true
  for service in watcher jobteaser-browser; do
    container=$("${compose[@]}" ps -q "$service")
    [[ -n "$container" ]] || { ready=false; continue; }
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
    case "$health" in
      healthy) ;;
      unhealthy|exited|dead) echo "$service deployment failed: $health" >&2; exit 1 ;;
      *) ready=false ;;
    esac
  done
  if [[ "$ready" == true ]]; then
    echo "Deployed $(git rev-parse --short HEAD): both services healthy"
    exit 0
  fi
  sleep 5
done
echo "Deployment health check timed out" >&2
"${compose[@]}" ps >&2
exit 1
