#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(git rev-parse --show-toplevel)"
stamp="$(git rev-parse --short=12 HEAD)-${GITHUB_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
container="job-watcher-preprod-$stamp"
app_image="job-watcher-preprod-app:$stamp"
test_image="job-watcher-preprod-tests:$stamp"
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  docker image rm "$app_image" "$test_image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# Inventory only; never resolve production Compose configuration or read its env file.
echo "Current server addresses: $(hostname -I)"
docker ps --filter label=com.docker.compose.project=job-watcher \
  --format '{{.Names}} {{.Status}} {{.Ports}}'
curl --fail --silent --show-error --max-time 20 \
  --output /dev/null https://job-watcher.yndevs.com/

docker build -f docker/Dockerfile.verify -t "$test_image" .
# No outbound network, host mounts, production data, browser profile, or Telegram credentials.
docker run --rm --network none --cpus=1 --memory=1g --pids-limit=512 "$test_image"
docker build -t "$app_image" .
# No network or published ports. The smoke check executes inside this ephemeral container.
docker run -d --name "$container" --network none --cpus=1 --memory=512m \
  --pids-limit=128 --tmpfs /app/data:uid=1000,gid=1000,mode=0700 "$app_image" >/dev/null
for attempt in $(seq 1 30); do
  state=$(docker inspect --format '{{.State.Health.Status}}' "$container")
  case "$state" in
    healthy)
      docker exec "$container" python -c \
        "import urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8000/'); assert r.status == 200; assert b'Job Watcher' in r.read()"
      echo "Pre-production verification passed: $stamp"
      exit 0 ;;
    unhealthy) echo "Pre-production app unhealthy" >&2; exit 1 ;;
  esac
  sleep 2
done
echo "Pre-production health check timed out" >&2
exit 1
