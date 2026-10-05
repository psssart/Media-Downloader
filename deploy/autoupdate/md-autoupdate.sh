#!/usr/bin/env bash
# Updates fast-moving extractor dependencies (yt-dlp, instaloader) in production
# without waiting for a CI rebuild.
#
# Flow: pull base image -> build a thin layer with `pip install --upgrade` on top ->
# compare versions with the running container -> smoke-test -> wait for active
# downloads -> retag the new image as $IMAGE -> recreate -> wait for healthy ->
# roll back to the previous image if the healthcheck fails.
#
# The new image is tagged locally as $IMAGE, so the existing CI deploy
# (`docker compose pull && up`) keeps working and simply replaces it with the
# registry build on the next push.
set -euo pipefail
set -f  # package specs like "yt-dlp[default]" must not be glob-expanded

APP_DIR="${APP_DIR:-/var/www/media-downloader}"
IMAGE="${IMAGE:-pasubi/media-downloader:latest}"
CONTAINER="${CONTAINER:-media-downloader}"
PACKAGES="${PACKAGES:-yt-dlp[default,curl-cffi] instaloader}"
DOWNLOADS_DIR="${DOWNLOADS_DIR:-$APP_DIR/downloads}"
IDLE_WAIT_MAX="${IDLE_WAIT_MAX:-1800}"    # max seconds to wait for active downloads
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"   # max seconds to wait for "healthy"
FORCE="${FORCE:-0}"                       # 1 = recreate even if versions are unchanged

CANDIDATE="${IMAGE%:*}:autoupdate-candidate"

log() { echo "[md-autoupdate] $*"; }

# Package names without extras: "yt-dlp[default]" -> "yt-dlp"
NAMES=()
for p in $PACKAGES; do NAMES+=("${p%%[*}"); done

VERSIONS_PY='import sys, importlib.metadata as m; print(" ".join(f"{p}={m.version(p)}" for p in sys.argv[1:]))'

running_versions() { docker exec "$CONTAINER" python -c "$VERSIONS_PY" "${NAMES[@]}"; }
image_versions()   { docker run --rm --entrypoint python "$1" -c "$VERSIONS_PY" "${NAMES[@]}"; }

container_health() {
    docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "$CONTAINER" 2>/dev/null || echo missing
}

wait_healthy() {
    local deadline=$((SECONDS + HEALTH_TIMEOUT)) status
    while (( SECONDS < deadline )); do
        status=$(container_health)
        [[ $status == healthy ]] && return 0
        [[ $status == unhealthy || $status == exited ]] && return 1
        sleep 5
    done
    return 1
}

# yt-dlp writes *.part / *.ytdl files while downloading; a recently touched one
# means a download is in progress and a restart would kill it.
wait_idle() {
    local waited=0
    while find "$DOWNLOADS_DIR" \( -name '*.part*' -o -name '*.ytdl' \) -mmin -2 -print -quit 2>/dev/null | grep -q .; do
        if (( waited >= IDLE_WAIT_MAX )); then
            log "downloads still active after ${IDLE_WAIT_MAX}s, restarting anyway"
            return
        fi
        (( waited == 0 )) && log "active downloads detected, waiting..."
        sleep 30
        waited=$((waited + 30))
    done
}

compose_up() { (cd "$APP_DIR" && docker compose up -d --force-recreate); }

RUNNING_IMAGE=$(docker inspect -f '{{.Image}}' "$CONTAINER")
SWITCHED=0

# `docker pull` below moves the $IMAGE tag. Unless we switched successfully,
# point it back to what is actually running, so a later plain `compose up`
# doesn't silently downgrade.
cleanup() {
    local rc=$?
    if (( SWITCHED == 0 )); then
        docker tag "$RUNNING_IMAGE" "$IMAGE" || true
    fi
    docker rmi "$CANDIDATE" >/dev/null 2>&1 || true
    docker image prune -f >/dev/null 2>&1 || true
    exit "$rc"
}
trap cleanup EXIT

log "pulling $IMAGE"
docker pull -q "$IMAGE" >/dev/null

log "building candidate with: $PACKAGES"
{
    echo "FROM $IMAGE"
    echo "USER root"
    printf 'RUN pip install --no-cache-dir --upgrade'
    printf ' "%s"' $PACKAGES
    echo
    echo "USER appuser"
} | docker build --no-cache -q -t "$CANDIDATE" - >/dev/null

CURRENT=$(running_versions)
NEW=$(image_versions "$CANDIDATE")
log "running:   $CURRENT"
log "candidate: $NEW"

if [[ $CURRENT == "$NEW" && $FORCE != 1 ]]; then
    log "already up to date"
    exit 0
fi

log "smoke-testing candidate"
docker run --rm --entrypoint python "$CANDIDATE" -c 'import yt_dlp, instaloader, app.main' \
    || { log "smoke test failed, keeping current version"; exit 1; }

wait_idle

docker tag "$CANDIDATE" "$IMAGE"
SWITCHED=1
log "recreating container"
compose_up || true

if wait_healthy; then
    log "updated: $NEW"
    exit 0
fi

log "container not healthy ($(container_health)), rolling back to $RUNNING_IMAGE"
docker tag "$RUNNING_IMAGE" "$IMAGE"
compose_up
wait_healthy || log "rollback container is not healthy either!"
exit 1
