#!/usr/bin/env bash
# =============================================================================
# Next runtime relocation canary (single supported Next version).
#
# Proves the dual-artifact contract end-to-end on the host (no Docker):
#   1. build the fixture twice (port + sentinel path variants)
#   2. assemble standalone artifacts exactly like the scaffold Dockerfile
#   3. index the path artifact (fail-closed relocation manifest)
#   4. prove malformed-path and source-tamper starts fail closed
#   5. boot path mode under a real /runtime/apps/<uuid> prefix and assert
#      pages, redirect .meta, RSC flight, chunks, health, runtime.json
#   6. boot port mode from the native artifact and assert no base path
#   7. enforce the cold-start gates (prepare <= 5000 ms, RSS <= 96 MiB)
#
# Usage:
#   scripts/test-next-runtime-canary.sh [--extlib <tarball-or-version>]
#
# By default this SDK-owned gate builds and packs the TypeScript runtime from
# the current checkout. --extlib overrides that package with an explicit
# tarball, registry version, URL, or file: dependency.
#
# Debug overrides:
#   KZ_CANARY_PATH_PORT / KZ_CANARY_PORT_PORT pin loopback ports.
#   KZ_CANARY_KEEP_WORK=1 preserves the temporary workdir and failure logs.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$REPO_ROOT/tests/next-runtime-canary/frontend"
SENTINEL="/__KZ_RUNTIME_BASE_7F3A91C2__"
APP_PATH="/runtime/apps/550e8400-e29b-41d4-a716-446655440000"

EXTLIB=""
resolve_extlib() {
    local value="$1"
    if [[ -e "$value" ]]; then
        local directory basename
        directory="$(cd "$(dirname "$value")" && pwd)"
        basename="$(basename "$value")"
        printf '%s/%s\n' "$directory" "$basename"
        return
    fi

    case "$value" in
        *.tgz|/*|./*|../*)
            echo "--extlib path does not exist: $value" >&2
            return 2
            ;;
        @kamiwaza-ai/extensions-lib@*|http://*|https://*|file:*|git+*)
            printf '%s\n' "$value"
            ;;
        *)
            printf '@kamiwaza-ai/extensions-lib@%s\n' "$value"
            ;;
    esac
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --extlib)
            [[ $# -ge 2 ]] || { echo "--extlib requires a value" >&2; exit 2; }
            [[ -n "$2" ]] || { echo "--extlib requires a nonempty value" >&2; exit 2; }
            EXTLIB="$(resolve_extlib "$2")"
            shift 2
            ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

WORK="$(mktemp -d "/tmp/kz-next-canary.XXXXXX")"
mkdir -p "$WORK/targets"
TARGET="$WORK/targets/path"
PORT_TARGET="$WORK/targets/port"
INVALID_TARGET="$WORK/targets/invalid"
TAMPER_TARGET="$WORK/targets/tampered"
VERSION_TARGET="$WORK/targets/version-mismatch"
PIDS=()
stop_pid() {
    local pid="$1"
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}
cleanup() {
    local pid
    for pid in "${PIDS[@]:-}"; do stop_pid "$pid"; done
    if [[ "${KZ_CANARY_KEEP_WORK:-0}" == "1" ]]; then
        printf '%s\n' "[canary] preserving workdir $WORK" >&2
        return
    fi
    rm -rf "$WORK" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

log() { printf '\033[1;34m[canary]\033[0m %s\n' "$*"; }
fail() {
    printf '\033[1;31m[canary] FAIL:\033[0m %s\n' "$*" >&2
    for log_file in "$WORK"/*.log "$WORK"/*.err; do
        if [[ -s "$log_file" ]]; then
            printf '%s\n' "--- $log_file (last 200 lines) ---" >&2
            tail -200 "$log_file" >&2
        fi
    done
    exit 1
}

canary_curl() {
    curl --connect-timeout 2 --max-time 10 "$@"
}

pick_free_port() {
    node -e '
const server = require("node:net").createServer();
server.on("error", (error) => {
    console.error(error.message);
    process.exit(1);
});
server.listen({ host: "127.0.0.1", port: 0 }, () => {
    const address = server.address();
    if (!address || typeof address === "string") process.exit(1);
    console.log(address.port);
    server.close();
});
'
}

validate_port() {
    local label="$1" port="$2"
    if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
        fail "$label must be an integer from 1 to 65535: $port"
    fi
}

if [[ -n "${KZ_CANARY_PATH_PORT:-}" ]]; then
    validate_port "KZ_CANARY_PATH_PORT" "$KZ_CANARY_PATH_PORT"
fi
if [[ -n "${KZ_CANARY_PORT_PORT:-}" ]]; then
    validate_port "KZ_CANARY_PORT_PORT" "$KZ_CANARY_PORT_PORT"
fi

expect_boot_failure() {
    local needle="$1" log_file="$2" unpublished_target="$3"; shift 3
    "$@" >"$log_file" 2>&1 &
    local pid=$! status=0
    for _ in $(seq 1 150); do
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" || status=$?
            (( status != 0 )) || fail "expected boot failure: $needle"
            grep -qF "$needle" "$log_file" || {
                cat "$log_file" >&2
                fail "boot failed without expected message: $needle"
            }
            [[ ! -e "$unpublished_target" ]] \
                || fail "failed boot published a runtime tree at $unpublished_target"
            return
        fi
        sleep 0.1
    done
    stop_pid "$pid"
    fail "boot did not fail closed: $needle"
}

expect_command_failure() {
    local needle="$1" log_file="$2"; shift 2
    local status=0
    "$@" >"$log_file" 2>&1 || status=$?
    (( status != 0 )) || fail "expected command failure: $needle"
    grep -qF "$needle" "$log_file" || {
        cat "$log_file" >&2
        fail "command failed without expected message: $needle"
    }
}

scan_sentinel_tree() {
    local root="$1" matches="$2"
    : >"$matches"
    find -L "$root" -type f -exec sh -c '
        sentinel="$1"
        matches="$2"
        shift 2
        for file do
            status=0
            grep -l -F "$sentinel" "$file" >>"$matches" || status=$?
            [ "$status" -eq 0 ] || [ "$status" -eq 1 ] || exit "$status"
        done
    ' sh "$SENTINEL" "$matches" {} +
}

# --- 1. install ---------------------------------------------------------
log "workdir $WORK"
if [[ -z "$EXTLIB" ]]; then
    LOCAL_EXTLIB="$REPO_ROOT/kamiwaza-ai-extensions-lib"
    [[ -f "$LOCAL_EXTLIB/package.json" ]] \
        || fail "local TypeScript runtime package not found at $LOCAL_EXTLIB"
    log "building local @kamiwaza-ai/extensions-lib"
    (
        cd "$LOCAL_EXTLIB"
        npm ci --no-audit --no-fund >/dev/null
        npm run build >/dev/null
        npm pack --silent --pack-destination "$WORK"
    ) >"$WORK/npm-pack.out"
    EXTLIB="$WORK/$(tail -1 "$WORK/npm-pack.out")"
    [[ -f "$EXTLIB" ]] || fail "npm pack did not produce $EXTLIB"
fi
cp -R "$FIXTURE/." "$WORK/app/"
cd "$WORK/app"
EXPECTED_NEXT="$(node -p "require('./package.json').dependencies.next")"
[[ "$EXPECTED_NEXT" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || fail "fixture must pin Next to an exact version: $EXPECTED_NEXT"
log "installing with extensions-lib override: $EXTLIB"
npm install --no-audit --no-fund "$EXTLIB" >/dev/null
npm install --no-audit --no-fund >/dev/null
INSTALLED_NEXT="$(node -p "require('next/package.json').version")"
[[ "$INSTALLED_NEXT" == "$EXPECTED_NEXT" ]] \
    || fail "installed next@$INSTALLED_NEXT does not match fixture pin next@$EXPECTED_NEXT"
INSTALLED_EXTLIB="$(node -p \
    "require('./node_modules/@kamiwaza-ai/extensions-lib/package.json').version")"
[[ "$INSTALLED_EXTLIB" =~ ^0\.5\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] \
    || fail "installed extensions-lib@$INSTALLED_EXTLIB does not satisfy the 0.5.x canary contract"
log "installed next@$INSTALLED_NEXT with extensions-lib@$INSTALLED_EXTLIB"
TOOLS="$WORK/app/node_modules/@kamiwaza-ai/extensions-lib/scripts"
[[ -f "$TOOLS/index-next-runtime.mjs" && -f "$TOOLS/start-next-runtime.mjs" ]] \
    || fail "installed extensions-lib is missing the Next runtime scripts under $TOOLS"

assemble() {
    local variant="$1" out="$2"
    log "building $variant variant"
    rm -rf .next
    KZ_NEXT_BUILD_VARIANT="$variant" NEXT_TELEMETRY_DISABLED=1 npm run build >/dev/null
    mkdir -p "$out/.next/static"
    cp -R .next/standalone/. "$out/"
    cp -R .next/static/. "$out/.next/static/"
    if [[ -d public ]]; then
        cp -R public "$out/public"
    fi
}

# --- 2. dual artifacts ---------------------------------------------------
mkdir -p "$WORK/runtime"
assemble port "$WORK/runtime/port"
assemble path "$WORK/runtime/path"

log "checking dual-artifact sentinel preconditions"
PATH_SENTINEL_FILES="$WORK/path-sentinel-files"
scan_sentinel_tree "$WORK/runtime/path" "$PATH_SENTINEL_FILES" \
    || fail "could not scan the path artifact for the sentinel"
[[ -s "$PATH_SENTINEL_FILES" ]] \
    || fail "path artifact has no sentinel bytes to relocate"
PORT_SENTINEL_FILES="$WORK/port-sentinel-files"
scan_sentinel_tree "$WORK/runtime/port" "$PORT_SENTINEL_FILES" \
    || fail "could not scan the port artifact for the sentinel"
if [[ -s "$PORT_SENTINEL_FILES" ]]; then
    cat "$PORT_SENTINEL_FILES" >&2
    fail "port artifact unexpectedly contains the path sentinel"
fi

# --- 3. index ------------------------------------------------------------
log "indexing path artifact"
node "$TOOLS/index-next-runtime.mjs" \
    --root "$WORK/runtime/path" \
    --sentinel "$SENTINEL" \
    --next-version "$EXPECTED_NEXT" \
    --output "$WORK/runtime/kz-next-relocations.json"

# The fixture deliberately prerenders / and /go, so their .rsc/.meta outputs
# must be part of the relocation manifest regardless of JSON formatting.
MANIFEST_PATH="$WORK/runtime/kz-next-relocations.json"
# shellcheck disable=SC2016 # JavaScript template literals are intentionally single-quoted.
node -e '
const manifest = require(process.argv[1]);
const expectedNext = process.argv[2];
if (manifest.nextVersion !== expectedNext) {
    console.error(
        `relocation manifest recorded next@${manifest.nextVersion}; expected next@${expectedNext}`,
    );
    process.exit(1);
}
const paths = new Set(manifest.files.map(({ path }) => path));
for (const expected of [
    ".next/server/app/go.meta",
    ".next/server/app/index.rsc",
]) {
    if (!paths.has(expected)) {
        console.error("fixture output missing from relocation manifest: " + expected);
        process.exit(1);
    }
}
' "$MANIFEST_PATH" "$EXPECTED_NEXT"

# --- 4. fail-closed probes ----------------------------------------------
log "probing unsupported Next build rejection"
NEXT_PACKAGE_JSON="$WORK/app/node_modules/next/package.json"
(
    NEXT_PACKAGE_BACKUP="$WORK/next-package.json.backup"
    cp "$NEXT_PACKAGE_JSON" "$NEXT_PACKAGE_BACKUP"
    trap 'cp "$NEXT_PACKAGE_BACKUP" "$NEXT_PACKAGE_JSON"' EXIT
    node -e '
const fs = require("node:fs");
const file = process.argv[1];
const pkg = JSON.parse(fs.readFileSync(file, "utf8"));
pkg.version = "0.0.0";
fs.writeFileSync(file, JSON.stringify(pkg));
' "$NEXT_PACKAGE_JSON"
    expect_command_failure "Next 0.0.0 is not validated for runtime relocation" \
        "$WORK/unsupported-next.log" \
        env KZ_NEXT_BUILD_VARIANT=path node -e 'require("./next.config.js")'
)

log "probing malformed runtime path rejection"
expect_boot_failure "invalid runtime path segment" \
    "$WORK/invalid-path.log" "$INVALID_TARGET" \
    env \
    KAMIWAZA_ROUTING_MODE=path \
    KAMIWAZA_APP_PATH="/runtime/apps/a%2Fb" \
    KZ_RUNTIME_IMAGE_ROOT="$WORK/runtime" \
    KZ_RUNTIME_TARGET="$INVALID_TARGET" \
    node "$TOOLS/start-next-runtime.mjs"

log "probing relocation source hash rejection"
cp -R "$WORK/runtime" "$WORK/tampered-runtime"
TAMPER_FILE="$(node -e '
const manifest = require(process.argv[1]);
process.stdout.write(manifest.files[0].path);
' "$WORK/tampered-runtime/kz-next-relocations.json")"
printf '\ncanary-tamper\n' >>"$WORK/tampered-runtime/path/$TAMPER_FILE"
expect_boot_failure "relocation source hash mismatch" \
    "$WORK/tamper.log" "$TAMPER_TARGET" \
    env \
    KAMIWAZA_ROUTING_MODE=path \
    KAMIWAZA_APP_PATH="$APP_PATH" \
    KZ_RUNTIME_IMAGE_ROOT="$WORK/tampered-runtime" \
    KZ_RUNTIME_TARGET="$TAMPER_TARGET" \
    node "$TOOLS/start-next-runtime.mjs"

log "probing artifact/manifest Next version rejection"
cp -R "$WORK/runtime" "$WORK/version-mismatch-runtime"
node -e '
const fs = require("node:fs");
const file = process.argv[1];
const manifest = JSON.parse(fs.readFileSync(file, "utf8"));
manifest.nextVersion = "0.0.0";
fs.writeFileSync(file, JSON.stringify(manifest));
' "$WORK/version-mismatch-runtime/kz-next-relocations.json"
expect_boot_failure \
    "artifact next@$EXPECTED_NEXT does not match relocation manifest next@0.0.0" \
    "$WORK/version-mismatch.log" "$VERSION_TARGET" \
    env \
    KAMIWAZA_ROUTING_MODE=path \
    KAMIWAZA_APP_PATH="$APP_PATH" \
    KZ_RUNTIME_IMAGE_ROOT="$WORK/version-mismatch-runtime" \
    KZ_RUNTIME_TARGET="$VERSION_TARGET" \
    node "$TOOLS/start-next-runtime.mjs"

wait_http() {
    local url="$1" pid="$2" tries="${3:-50}"
    for _ in $(seq 1 "$tries"); do
        kill -0 "$pid" 2>/dev/null || return 1
        if canary_curl --max-time 2 -fs -o /dev/null "$url"; then return 0; fi
        sleep 0.2
    done
    return 1
}

assert_no_sentinel_in_tree() {
    local root="$1" matches="$WORK/sentinel-files"
    if ! scan_sentinel_tree "$root" "$matches"; then
        fail "could not scan published runtime tree: $root"
    fi
    if [[ -s "$matches" ]]; then
        cat "$matches" >&2
        fail "sentinel leaked into published runtime tree: $root"
    fi
}

log "probing sentinel scan through symlinked files"
SENTINEL_SCAN_ROOT="$WORK/sentinel-scan-probe"
mkdir -p "$SENTINEL_SCAN_ROOT/tree"
printf '%s\n' "$SENTINEL" >"$SENTINEL_SCAN_ROOT/source"
ln -s ../source "$SENTINEL_SCAN_ROOT/tree/linked"
SENTINEL_SCAN_MATCHES="$WORK/sentinel-scan-probe.matches"
scan_sentinel_tree "$SENTINEL_SCAN_ROOT/tree" "$SENTINEL_SCAN_MATCHES" \
    || fail "sentinel scan failed on a valid symlinked file"
grep -qF "$SENTINEL_SCAN_ROOT/tree/linked" "$SENTINEL_SCAN_MATCHES" \
    || fail "sentinel scan skipped a symlinked file"

expect_status() {
    local expected="$1" url="$2"; shift 2
    local got
    got="$(canary_curl -so /dev/null -w '%{http_code}' "$@" "$url")" \
        || fail "GET $url failed while checking status"
    [[ "$got" == "$expected" ]] || fail "GET $url -> $got (expected $expected)"
}

expect_no_sentinel() {
    local url="$1"
    local body
    body="$(canary_curl -fsS "$url")" \
        || fail "GET $url failed while checking for sentinel"
    if grep -qF "$SENTINEL" <<<"$body"; then
        fail "sentinel leaked in response from $url"
    fi
}

# --- 5. path mode --------------------------------------------------------
log "booting path mode under $APP_PATH"
BOOT_LOG="$WORK/path-boot.log"
PATH_READY=0
for attempt in 1 2 3; do
    PATH_PORT="${KZ_CANARY_PATH_PORT:-$(pick_free_port)}"
    validate_port "KZ_CANARY_PATH_PORT" "$PATH_PORT"
    BOOT_STARTED_MS="$(node -e 'process.stdout.write(String(Date.now()))')"
    KAMIWAZA_ROUTING_MODE=path \
    KAMIWAZA_APP_PATH="$APP_PATH" \
    KAMIWAZA_APP_PATH_URL="http://127.0.0.1:$PATH_PORT$APP_PATH" \
    KAMIWAZA_DEPLOYMENT_ID=550e8400 \
    KZ_RUNTIME_IMAGE_ROOT="$WORK/runtime" \
    KZ_RUNTIME_TARGET="$TARGET" \
    PORT=$PATH_PORT \
    node "$TOOLS/start-next-runtime.mjs" >"$BOOT_LOG" 2>&1 &
    PIDS=($!)
    BASE="http://127.0.0.1:$PATH_PORT$APP_PATH"
    if wait_http "$BASE/health" "${PIDS[0]}"; then
        PATH_READY=1
        break
    fi
    stop_pid "${PIDS[0]}"
    PIDS=()
    if [[ -n "${KZ_CANARY_PATH_PORT:-}" ]] \
        || ! grep -qE 'EADDRINUSE|address already in use' "$BOOT_LOG"; then
        fail "path-mode server never became healthy"
    fi
    rm -rf "$TARGET"
    log "path port $PATH_PORT was claimed concurrently; retrying ($attempt/3)"
done
(( PATH_READY == 1 )) || fail "path-mode server exhausted port-allocation retries"
BOOT_READY_MS="$(node -e 'process.stdout.write(String(Date.now()))')"
BOOT_ELAPSED_MS=$((BOOT_READY_MS - BOOT_STARTED_MS))
(( BOOT_ELAPSED_MS > 0 && BOOT_ELAPSED_MS <= 10000 )) \
    || fail "path-mode health took ${BOOT_ELAPSED_MS}ms (gate 10000ms)"
assert_no_sentinel_in_tree "$TARGET"

# Cold-start gates from the first runtime event; a changed event order is a
# contract drift that this canary should surface rather than skip.
STATS="$(grep -o '{"event":"kz_next_runtime".*}' "$BOOT_LOG" | head -1 || true)"
[[ -n "$STATS" ]] || fail "path boot log has no runtime stats event"
log "boot stats: $STATS"
GATE_VALUES=""
if ! GATE_VALUES="$(node -e '
const stats = JSON.parse(process.argv[1]);
for (const field of ["prepare_ms", "prepare_rss_mib"]) {
    if (!Number.isInteger(stats[field]) || stats[field] <= 0) process.exit(2);
}
console.log(String(stats.prepare_ms) + " " + String(stats.prepare_rss_mib));
' "$STATS" 2>/dev/null)"; then
    fail "runtime stats event has invalid cold-start metrics: $STATS"
fi
read -r PREPARE_MS RSS_MIB <<<"$GATE_VALUES"
(( PREPARE_MS <= 5000 )) || fail "prepare took ${PREPARE_MS}ms (gate 5000ms)"
(( RSS_MIB <= 96 )) || fail "prepare RSS ${RSS_MIB}MiB (gate 96MiB)"

expect_status 200 "$BASE"
# Next canonicalizes the trailing-slash form of the base path root.
expect_status 308 "$BASE/"
expect_status 200 "$BASE/nested"
expect_status 200 "$BASE/api/echo"
expect_status 200 "$BASE/health"
expect_status 200 "$BASE/kmza-icon.svg"
expect_status 200 "$BASE/excluded"

HOME_HTML="$(canary_curl -fsS "$BASE")" || fail "GET $BASE failed"
grep -qF "$APP_PATH/_next/" <<<"$HOME_HTML" || fail "asset URLs not prefixed in home HTML"
grep -qF "__KAMIWAZA_RUNTIME__" <<<"$HOME_HTML" || fail "runtime bootstrap missing from HTML"
grep -qF "\"appPath\":\"$APP_PATH\"" <<<"$HOME_HTML" || fail "bootstrap appPath not relocated"
grep -qF "KZ_FLIGHT_TAIL_9A6E2D43" <<<"$HOME_HTML" \
    || fail "long-text tail missing from rendered HTML"
expect_no_sentinel "$BASE"
expect_no_sentinel "$BASE/nested"

MIDDLEWARE_HEADERS="$WORK/middleware-headers"
canary_curl -fsS -D "$MIDDLEWARE_HEADERS" -o /dev/null "$BASE/nested" \
    || fail "GET $BASE/nested failed while checking middleware headers"
grep -qi '^x-kz-canary-middleware:[[:space:]]*matched' "$MIDDLEWARE_HEADERS" \
    || fail "relocated middleware matcher did not run for /nested"

# A regular route excluded by the serialized negative lookahead proves the
# relocated matcher still preserves exclusions without relying on Next's
# special handling for static assets.
EXCLUDED_HEADERS="$WORK/excluded-headers"
canary_curl -fsS -D "$EXCLUDED_HEADERS" -o /dev/null "$BASE/excluded" \
    || fail "GET $BASE/excluded failed while checking middleware headers"
if grep -qi '^x-kz-canary-middleware:' "$EXCLUDED_HEADERS"; then
    fail "middleware matcher unexpectedly ran for /excluded"
fi

# First prefixed chunk from the HTML must be servable.
CHUNK="$(grep -o "$APP_PATH/_next/static/[^\"< ]*\.js" <<<"$HOME_HTML" | head -1 || true)"
[[ -n "$CHUNK" ]] || fail "no prefixed chunk URL found in home HTML"
expect_status 200 "http://127.0.0.1:$PATH_PORT$CHUNK"
expect_no_sentinel "http://127.0.0.1:$PATH_PORT$CHUNK"
CHUNK_HEADERS="$WORK/chunk-headers"
canary_curl -fsS -D "$CHUNK_HEADERS" -o /dev/null \
    "http://127.0.0.1:$PATH_PORT$CHUNK" \
    || fail "GET $CHUNK failed while checking chunk headers"
if grep -qi '^x-kz-canary-middleware:' "$CHUNK_HEADERS"; then
    fail "middleware matcher unexpectedly ran for a static chunk"
fi

# Static redirect (.meta relocation): /go must land on the prefixed /nested.
REDIRECT_TARGET="$(canary_curl -so /dev/null -w '%{redirect_url}' "$BASE/go")" \
    || fail "GET $BASE/go failed while checking redirect"
[[ "$REDIRECT_TARGET" == *"$APP_PATH/nested"* ]] \
    || fail "redirect target not relocated: $REDIRECT_TARGET"

# RSC flight payload under the prefix (long-text page exercises T-frames).
RSC_HEADERS="$WORK/rsc-headers"
RSC_BODY="$WORK/rsc-body"
canary_curl -fsS -D "$RSC_HEADERS" -o "$RSC_BODY" -H 'RSC: 1' "$BASE" \
    || fail "RSC GET $BASE failed"
grep -qi '^content-type:[[:space:]]*text/x-component' "$RSC_HEADERS" \
    || fail "RSC request returned a non-Flight content type"
grep -qF "$APP_PATH/kmza-icon.svg" "$RSC_BODY" \
    || fail "appAsset URL not relocated in flight payload"
grep -qF "KZ_FLIGHT_TAIL_9A6E2D43" "$RSC_BODY" \
    || fail "long-text tail missing from flight payload"
if grep -qF "$SENTINEL" "$RSC_BODY"; then
    fail "sentinel leaked in flight payload"
fi
# shellcheck disable=SC2016 # JavaScript template literals are intentionally single-quoted.
node -e '
const fs = require("node:fs");
const body = fs.readFileSync(process.argv[1]);
const marker = Buffer.from("KZ_FLIGHT_TAIL_9A6E2D43");
let pos = 0;
let markerInTextFrame = false;
const isHex = (byte) =>
    (byte >= 0x30 && byte <= 0x39) || (byte >= 0x61 && byte <= 0x66);
while (pos < body.length) {
    let cursor = pos;
    while (cursor < body.length && isHex(body[cursor])) cursor += 1;
    if (body[cursor] !== 0x3a) {
        throw new Error(`invalid Flight row at byte ${pos}`);
    }
    cursor += 1;
    const tag = body[cursor];
    const isLetter =
        (tag >= 0x41 && tag <= 0x5a) || (tag >= 0x61 && tag <= 0x7a);
    let hexEnd = cursor + 1;
    while (hexEnd < body.length && isHex(body[hexEnd])) hexEnd += 1;
    if (isLetter && hexEnd > cursor + 1 && body[hexEnd] === 0x2c) {
        const length = Number.parseInt(body.subarray(cursor + 1, hexEnd).toString("ascii"), 16);
        const payloadStart = hexEnd + 1;
        const payloadEnd = payloadStart + length;
        if (!Number.isSafeInteger(length) || payloadEnd > body.length) {
            throw new Error(`invalid Flight frame length at byte ${pos}`);
        }
        if (tag === 0x54 && body.subarray(payloadStart, payloadEnd).includes(marker)) {
            markerInTextFrame = true;
        }
        pos = payloadEnd;
        continue;
    }
    const newline = body.indexOf(0x0a, cursor);
    if (newline === -1) {
        pos = body.length;
    } else {
        pos = newline + 1;
    }
}
if (!markerInTextFrame) {
    throw new Error("long-text marker was not inside a byte-length-framed Flight T row");
}
' "$RSC_BODY" || fail "RSC Flight byte framing is invalid"

# Runtime config route.
RUNTIME_JSON="$(canary_curl -fsS "$BASE/kamiwaza/runtime.json")" \
    || fail "GET $BASE/kamiwaza/runtime.json failed"
node -e "JSON.parse(process.argv[1])" "$RUNTIME_JSON" 2>/dev/null \
    || fail "runtime.json is not JSON: $(head -c 300 <<<"$RUNTIME_JSON")"
node -e "
const c = JSON.parse(process.argv[1]);
if (c.appPath !== '$APP_PATH') throw new Error('runtime.json appPath: ' + c.appPath);
if (c.routingMode !== 'path') throw new Error('runtime.json mode: ' + c.routingMode);
" "$RUNTIME_JSON"

# Unprefixed root must NOT serve the app (no proxy stripping dependence).
UNPREFIXED="$(canary_curl -so /dev/null -w '%{http_code}' \
    "http://127.0.0.1:$PATH_PORT/")" \
    || fail "GET unprefixed root failed"
[[ "$UNPREFIXED" == "404" || "$UNPREFIXED" == "308" || "$UNPREFIXED" == "307" ]] \
    || fail "unprefixed / returned $UNPREFIXED (expected 404 or base-path redirect)"

stop_pid "${PIDS[0]}"
PIDS=()

# --- 6. port mode --------------------------------------------------------
log "booting port mode"
PORT_LOG="$WORK/port-boot.log"
PORT_READY=0
for attempt in 1 2 3; do
    PORT_PORT="${KZ_CANARY_PORT_PORT:-$(pick_free_port)}"
    validate_port "KZ_CANARY_PORT_PORT" "$PORT_PORT"
    KAMIWAZA_ROUTING_MODE=port \
    KZ_RUNTIME_IMAGE_ROOT="$WORK/runtime" \
    KZ_RUNTIME_TARGET="$PORT_TARGET" \
    PORT=$PORT_PORT \
    node "$TOOLS/start-next-runtime.mjs" >"$PORT_LOG" 2>&1 &
    PIDS=($!)
    PBASE="http://127.0.0.1:$PORT_PORT"
    if wait_http "$PBASE/health" "${PIDS[0]}"; then
        PORT_READY=1
        break
    fi
    stop_pid "${PIDS[0]}"
    PIDS=()
    if [[ -n "${KZ_CANARY_PORT_PORT:-}" ]] \
        || ! grep -qE 'EADDRINUSE|address already in use' "$PORT_LOG"; then
        fail "port-mode server never became healthy"
    fi
    log "port-mode port $PORT_PORT was claimed concurrently; retrying ($attempt/3)"
done
(( PORT_READY == 1 )) || fail "port-mode server exhausted port-allocation retries"
expect_status 200 "$PBASE/"
expect_status 200 "$PBASE/nested"
expect_status 200 "$PBASE/api/echo"
expect_no_sentinel "$PBASE/"
PORT_HTML="$(canary_curl -fsS "$PBASE/")" || fail "GET $PBASE/ failed"
grep -qF '"/_next/' <<<"$PORT_HTML" || fail "port-mode asset URLs are not root-relative"
grep -qF '"routingMode":"port"' <<<"$PORT_HTML" || fail "port-mode bootstrap missing"
[[ ! -e "$PORT_TARGET" ]] || fail "port mode copied an artifact to $PORT_TARGET"

log "ALL CANARY CHECKS PASSED (next@$INSTALLED_NEXT, extensions-lib@$INSTALLED_EXTLIB, health ${BOOT_ELAPSED_MS}ms, prepare ${PREPARE_MS}ms, rss ${RSS_MIB}MiB)"
