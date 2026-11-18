#!/usr/bin/env bash
# Smoke test: start the real exporter process and check auth, the client and
# host allowlists and clean shutdown. Needs the deps from requirements.txt and
# no real Extron device (rejected requests never reach the network).
#
#   scripts/smoke-test.sh            # uses `python` on PATH
#   PYTHON=.venv/bin/python scripts/smoke-test.sh
set -u

PYTHON="${PYTHON:-python}"
PORT="${PORT:-19877}"
BASE="http://127.0.0.1:${PORT}"
cd "$(dirname "$0")/.."

CFG="$(mktemp)"
LOG1="$(mktemp)"
LOG2="$(mktemp)"
trap 'rm -f "$CFG" "$LOG1" "$LOG2"' EXIT
cat > "$CFG" <<'YAML'
allowed_hosts:
  - '^switch\d+\.example\.org$'
YAML

fail=0
check() { # check <description> <expected-http-code> <curl args...>
    local desc="$1" want="$2"
    shift 2
    local got
    got=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$@")
    if [ "$got" = "$want" ]; then
        echo "ok   $desc ($got)"
    else
        echo "FAIL $desc: expected $want, got $got"
        fail=1
    fi
}

wait_up() { for _ in $(seq 1 30); do curl -s -m 1 -o /dev/null "$1/healthz" && return; sleep 0.5; done; }

EXTRONSIS_AUTH_USERNAME=prom EXTRONSIS_AUTH_PASSWORD=s3cret \
    "$PYTHON" -m extronsis_exporter -c "$CFG" --host 127.0.0.1 --port "$PORT" >"$LOG1" 2>&1 &
pid=$!
wait_up "$BASE"

check "healthz is open"              200 "$BASE/healthz"
check "metrics without auth"         401 "$BASE/metrics"
check "metrics with wrong password"  401 -u prom:wrong "$BASE/metrics"
check "metrics with auth"            200 -u prom:s3cret "$BASE/metrics"
check "probe without auth"           401 "$BASE/probe?host=switch1.example.org"
check "probe host outside allowlist" 403 -u prom:s3cret "$BASE/probe?host=evil.example.com"
check "probe lookalike host"         403 -u prom:s3cret "$BASE/probe?host=switch1.example.org.evil.com"
check "probe port outside allowlist" 403 -u prom:s3cret "$BASE/probe?host=switch1.example.org&port=22"

# Client IP allowlist: a second instance that only permits a foreign address.
PORT2=$((PORT + 1))
EXTRONSIS_ALLOWED_CLIENTS=203.0.113.0/24 \
    "$PYTHON" -m extronsis_exporter -c "$CFG" --host 127.0.0.1 --port "$PORT2" >"$LOG2" 2>&1 &
pid2=$!
wait_up "http://127.0.0.1:${PORT2}"
check "client outside allowed_clients" 403 "http://127.0.0.1:${PORT2}/metrics"
check "healthz open to any client"     200 "http://127.0.0.1:${PORT2}/healthz"
kill -TERM "$pid2"; wait "$pid2" 2>/dev/null

# SIGTERM must stop the server promptly (regression: shutdown() deadlock).
kill -TERM "$pid"
for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
done
if kill -0 "$pid" 2>/dev/null; then
    echo "FAIL SIGTERM: server still running after 5s"
    kill -9 "$pid"
    fail=1
else
    echo "ok   SIGTERM shuts down cleanly"
fi

[ "$fail" = 0 ] || { echo "--- server log"; cat "$LOG1"; }
exit "$fail"
