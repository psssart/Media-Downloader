#!/usr/bin/env bash
# Load check for video compression on the target server.
#
# Starts N compression jobs from N distinct clients, then until all of them
# finish samples API latency (/api/health), container memory and free disk
# once a second. Prints a summary and exits non-zero if a limit is breached.
#
# Usage (on the server, next to the running container):
#   deploy/loadtest/compression_load.sh [N] [VIDEO_URL] [TARGET_MB]
# Env: BASE (default http://127.0.0.1:8082), CONTAINER (media-downloader),
#      DOWNLOADS_DIR (/var/www/media-downloader/downloads),
#      MAX_P95_MS (500), MAX_MEM_MB (2048), MIN_FREE_DISK_MB (2048), TRIM_END (120)
set -euo pipefail

N="${1:-3}"
VIDEO_URL="${2:-https://www.youtube.com/watch?v=aqz-KE-bpKQ}"
TARGET_MB="${3:-20}"
BASE="${BASE:-http://127.0.0.1:8082}"
CONTAINER="${CONTAINER:-media-downloader}"
DOWNLOADS_DIR="${DOWNLOADS_DIR:-/var/www/media-downloader/downloads}"
MAX_P95_MS="${MAX_P95_MS:-500}"
MAX_MEM_MB="${MAX_MEM_MB:-2048}"
MIN_FREE_DISK_MB="${MIN_FREE_DISK_MB:-2048}"
TRIM_END="${TRIM_END:-120}"

OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
api() { curl -s -H 'Content-Type: application/json' "$@"; }
json() { python3 -c "import sys, json; d = json.load(sys.stdin); print($1)"; }

declare -a TASKS CLIENTS
for i in $(seq 1 "$N"); do
    cid=$(python3 -c 'import uuid; print(uuid.uuid4())')
    body="{\"url\":\"$VIDEO_URL\",\"compression\":{\"target_size_mb\":$TARGET_MB,\"trim_end\":$TRIM_END}}"
    resp=$(api -X POST "$BASE/api/downloads/start" -H "X-Client-ID: $cid" -H "X-Real-IP: 192.0.2.$i" -d "$body")
    tid=$(echo "$resp" | json "d.get('task_id') or ''")
    if [[ -z $tid ]]; then
        echo "job $i rejected: $(echo "$resp" | json "d['detail'] if isinstance(d['detail'], str) else d['detail']['code'] + ': ' + d['detail']['message']")"
        continue
    fi
    echo "job $i -> task $tid"
    TASKS+=("$tid"); CLIENTS+=("$cid")
done
(( ${#TASKS[@]} )) || { echo "no job admitted"; exit 1; }

max_running=0
while :; do
    done_n=0 running=0
    for k in "${!TASKS[@]}"; do
        st=$(api "$BASE/api/downloads/tasks/${TASKS[$k]}" -H "X-Client-ID: ${CLIENTS[$k]}" | json "d['status']")
        [[ $st == compressing ]] && running=$((running + 1))
        [[ $st =~ ^(completed|failed|cancelled)$ ]] && done_n=$((done_n + 1))
    done
    (( running > max_running )) && max_running=$running
    curl -s -o /dev/null -w '%{time_total}\n' "$BASE/api/health" >> "$OUT/lat"
    docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" | awk '{print $1}' \
        | python3 -c "import sys,re; v=sys.stdin.read().strip(); n,u=re.match(r'([\d.]+)(\w+)',v).groups(); print(float(n)*{'B':1/2**20,'KiB':1/1024,'MiB':1,'GiB':1024}[u])" >> "$OUT/mem"
    df -Pm "$DOWNLOADS_DIR" | awk 'NR==2 {print $4}' >> "$OUT/disk"
    (( done_n == ${#TASKS[@]} )) && break
    sleep 1
done

echo
for k in "${!TASKS[@]}"; do
    api "$BASE/api/downloads/tasks/${TASKS[$k]}" -H "X-Client-ID: ${CLIENTS[$k]}" \
        | json "f\"{d['task_id']}: {d['status']} {d.get('compression_result') or d.get('error')}\""
done
python3 - "$OUT" "$MAX_P95_MS" "$MAX_MEM_MB" "$MIN_FREE_DISK_MB" "$max_running" <<'PY'
import sys, statistics
out, max_p95, max_mem, min_disk, max_running = sys.argv[1], *map(float, sys.argv[2:])
read = lambda f: [float(x) for x in open(f"{out}/{f}") if x.strip()]
lat = sorted(x * 1000 for x in read("lat")); mem = read("mem"); disk = read("disk")
p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
print(f"\nAPI /health latency: median {statistics.median(lat):.0f} ms, p95 {p95:.0f} ms, max {lat[-1]:.0f} ms")
print(f"Container memory peak: {max(mem):.0f} MB   Free disk min: {min(disk):.0f} MB")
print(f"Max simultaneous encodes seen: {int(max_running)}")
bad = []
if p95 > max_p95: bad.append(f"latency p95 {p95:.0f} ms > {max_p95:.0f}")
if max(mem) > max_mem: bad.append(f"memory {max(mem):.0f} MB > {max_mem:.0f}")
if min(disk) < min_disk: bad.append(f"free disk {min(disk):.0f} MB < {min_disk:.0f}")
print("FAIL: " + "; ".join(bad) if bad else "OK: all limits held")
sys.exit(1 if bad else 0)
PY
echo "Estimate accuracy: curl -s $BASE/api/downloads/compression/stats | python3 -m json.tool"
