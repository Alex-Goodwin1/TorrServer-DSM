#!/bin/bash
# Tests for checksum pinning in build-package.sh using a stub wget (no network).
# Run:  bash tests/test_build.sh
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAILED=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILED=$((FAILED + 1)); }
check() { local d=$1; shift; if "$@"; then pass "$d"; else fail "$d"; fi; }

mkdir -p "$TMP/bin"
cat > "$TMP/bin/wget" <<'STUB'
#!/bin/bash
out=""; while [ $# -gt 0 ]; do case "$1" in -O) out=$2; shift;; esac; shift; done
case "$out" in
  *ffprobe.zip.part)
    python3 - "$out" <<'PY'
import sys, zipfile
# Fixed timestamp: the archive hash must not depend on when the test runs.
info = zipfile.ZipInfo("ffprobe", date_time=(2020, 1, 1, 0, 0, 0))
z = zipfile.ZipFile(sys.argv[1], "w"); z.writestr(info, "#!/bin/sh\nffprobe-content"); z.close()
PY
    ;;
  *) printf '\x7fELF%s' "${STUB_CONTENT:-original}" > "$out" ;;
esac
STUB
chmod +x "$TMP/bin/wget"
export PATH="$TMP/bin:$PATH"

fresh() {  # fresh copy of the build inputs
    rm -rf "$TMP/w"; mkdir "$TMP/w"
    cp -r "$ROOT/build-package.sh" "$ROOT/src" "$TMP/w/"
    chmod +x "$TMP/w/build-package.sh" "$TMP/w/src/INFO.sh" "$TMP/w/src/scripts/"*
    cd "$TMP/w" || exit 1
}
build() { ./build-package.sh MatriX.1.0 amd64 2.0.1 >"$TMP/out.log" 2>&1; }

echo "== no pins: trust on first use, warn only"
fresh
check "build succeeds"                         build
check "warning printed"                        grep -q 'no pinned checksum' "$TMP/out.log"

echo "== UPDATE_CHECKSUMS pins new downloads"
fresh
check "download-only pins and stops"           env UPDATE_CHECKSUMS=1 DOWNLOAD_ONLY=1 ./build-package.sh MatriX.1.0 amd64 2.0.1 >"$TMP/out.log" 2>&1
check "TorrServer pinned"                      grep -q ' TorrServer-MatriX.1.0-linux-amd64$' checksums.sha256
check "ffprobe archive pinned"                 grep -q ' ffprobe-6.1-linux-64.zip$' checksums.sha256
check "no spk produced in download-only mode"  sh -c '[ ! -d spk ]'
check "hashes are 64 hex chars"                sh -c "awk '{print length(\$1)}' checksums.sha256 | grep -qx 64"

echo "== pinned and unchanged: passes"
rm -rf dest_bin build
check "build succeeds with pins"               sh -c "REQUIRE_CHECKSUMS=1 ./build-package.sh MatriX.1.0 amd64 2.0.1 >/dev/null 2>&1"
check "reports checksum OK"                    sh -c "REQUIRE_CHECKSUMS=1 ./build-package.sh MatriX.1.0 amd64 2.0.1 2>&1 | grep -q 'Checksum OK: TorrServer'"

echo "== tampered download is rejected and removed"
rm -rf dest_bin build spk
export STUB_CONTENT=tampered
check "build fails on mismatch"                sh -c '! ./build-package.sh MatriX.1.0 amd64 2.0.1 >"$0" 2>&1' "$TMP/out.log"
check "mismatch is reported"                   grep -q 'checksum mismatch for TorrServer-MatriX.1.0-linux-amd64' "$TMP/out.log"
check "bad binary not left in cache"           sh -c '[ ! -e dest_bin/MatriX.1.0/TorrServer-linux-amd64 ]'
unset STUB_CONTENT

echo "== cached binary that was modified later is rejected too"
rm -rf dest_bin build spk
./build-package.sh MatriX.1.0 amd64 2.0.1 >/dev/null 2>&1
printf '\x7fELFbackdoor' > dest_bin/MatriX.1.0/TorrServer-linux-amd64
check "cache tampering detected"               sh -c '! ./build-package.sh MatriX.1.0 amd64 2.0.1 >/dev/null 2>&1'

echo "== REQUIRE_CHECKSUMS without pins refuses"
fresh
check "unpinned download refused"              sh -c '! REQUIRE_CHECKSUMS=1 ./build-package.sh MatriX.1.0 amd64 2.0.1 >"$0" 2>&1' "$TMP/out.log"
check "tells how to fix"                       grep -q 'make checksums' "$TMP/out.log"

echo
if [ "$FAILED" -eq 0 ]; then echo "ALL PASSED"; else echo "FAILED: $FAILED"; exit 1; fi
