#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(git rev-parse --show-toplevel)"
export WATCHER_ENV_FILE="${WATCHER_ENV_FILE:-/etc/job-watcher.env}"
[[ -r "$WATCHER_ENV_FILE" ]] || { echo "Missing deployment environment: $WATCHER_ENV_FILE" >&2; exit 2; }
cd "$repo_root"
compose=(docker compose --project-name job-watcher --env-file "$WATCHER_ENV_FILE" -f compose.yaml)
# Record the current deployment before changing containers; never print resolved config.
echo "Current server addresses: $(hostname -I)"
"${compose[@]}" ps
# Save a consistent database backup before recreating a running deployment.
if [[ -n "$("${compose[@]}" ps --status running -q watcher)" ]]; then
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  # Pin the exact running images, not whichever image currently has the latest tag.
  for service in watcher jobteaser-browser; do
    container=$("${compose[@]}" ps -q "$service")
    [[ -n "$container" ]] || { echo "Missing $service; backup aborted" >&2; exit 1; }
    previous_image=$(docker inspect --format '{{.Image}}' "$container")
    docker tag "$previous_image" "job-watcher-rollback:$stamp-$service"
  done
  "${compose[@]}" exec -T watcher python - "$stamp" <<'PY'
import hashlib
import json
import os
import pathlib
import sqlite3
import sys

os.umask(0o077)
stamp = sys.argv[1]
root = pathlib.Path('/app/data/backups')
root.mkdir(mode=0o700, exist_ok=True)
path = root / (stamp + '.sqlite3')
source = sqlite3.connect('file:/app/data/jobs.sqlite3?mode=ro', uri=True)
target = sqlite3.connect(path)
source.backup(target)
if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
    raise RuntimeError('Database backup failed integrity validation; deployment aborted')
target.close()
source.close()
path.chmod(0o600)
digest = hashlib.sha256(path.read_bytes()).hexdigest()
metadata = {'database': path.name, 'sha256': digest, 'integrity': 'ok', 'image_tag': stamp}
(root / (stamp + '.json')).write_text(json.dumps(metadata, indent=2))
(root / (stamp + '.compose.yaml')).write_text(
    'services:\n'
    f'  watcher:\n    image: job-watcher-rollback:{stamp}-watcher\n'
    f'  jobteaser-browser:\n    image: job-watcher-rollback:{stamp}-jobteaser-browser\n'
)
print(f'Verified backup: {path.name}; SHA-256 {digest}; rollback image tag {stamp}')
PY
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
