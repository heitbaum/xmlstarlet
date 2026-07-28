#!/bin/sh
# Build XMLStarlet under sanitizers and fuzz its subcommands.
#
# Maintainer tool; not wired into the build and not distributed.  Run it from
# anywhere inside the checkout:
#
#   fuzz/run.sh                               # asan+ubsan, suite, 200 muts/target
#   fuzz/run.sh --san=undefined --no-check    # ubsan only, skip the suite
#   fuzz/run.sh --targets=depyx,pyx -i 5000   # hammer the own-code parsers
#   fuzz/run.sh --san=none --no-fuzz          # plain build, just run the suite
#
# Builds a chosen ref in a scratch directory rather than in place, so the working
# tree keeps no build artifacts and any ref can be fuzzed without checking it out.

set -eu

SAN=address,undefined
REF=HEAD
ITERATIONS=200
TARGETS=all
SEED=1
TIMEOUT=10
BUILD_DIR=
JOBS=$(nproc 2>/dev/null || echo 4)
RUN_CHECK=yes
RUN_FUZZ=yes
LEAKS=yes
REBUILD=no
SEEDCAP=0

usage() {
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    cat <<'EOF'

Options
  --san=LIST         sanitizers for -fsanitize (default address,undefined;
                     "none" builds without any)
  --ref=REF          git ref to export and build (default HEAD)
  --build-dir=DIR    scratch tree (default ~/.cache/xmlstarlet-fuzz)
  -i, --iterations=N mutated inputs per target (default 200)
  --targets=LIST     comma-separated target names, or "all"
  --list-targets     show the fuzz targets and exit
  --seed=STR         mutation seed; same seed reproduces the same inputs
  --timeout=SECS     per-run timeout (default 10)
  -j, --jobs=N       build and fuzz parallelism (default nproc)
  --no-check         skip the "make check" baseline
  --no-fuzz          build (and check) only, do not fuzz
  --no-leaks         turn LeakSanitizer off while fuzzing
  --seeds=N          cap the seed corpus per input kind
  --rebuild          discard the scratch tree and rebuild
  -h, --help         this text
EOF
}

while [ $# -gt 0 ]; do
    case $1 in
        --san=*)            SAN=${1#*=} ;;
        --ref=*)            REF=${1#*=} ;;
        --build-dir=*)      BUILD_DIR=${1#*=} ;;
        --iterations=*)     ITERATIONS=${1#*=} ;;
        -i)                 shift; ITERATIONS=$1 ;;
        --targets=*)        TARGETS=${1#*=} ;;
        --seed=*)           SEED=${1#*=} ;;
        --timeout=*)        TIMEOUT=${1#*=} ;;
        --jobs=*)           JOBS=${1#*=} ;;
        -j)                 shift; JOBS=$1 ;;
        --seeds=*)          SEEDCAP=${1#*=} ;;
        --no-check)         RUN_CHECK=no ;;
        --no-fuzz)          RUN_FUZZ=no ;;
        --no-leaks)         LEAKS=no ;;
        --rebuild)          REBUILD=yes ;;
        --list-targets)     LIST_TARGETS=yes; RUN_CHECK=no ;;
        -h|--help)          usage; exit 0 ;;
        *)                  echo "$0: unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(git -C "$here" rev-parse --show-toplevel)
# Under ~/.cache, not /tmp, so the build and any saved reproducers survive a
# reboot -- and WSL's /tmp wipe.
: "${BUILD_DIR:=${XDG_CACHE_HOME:-$HOME/.cache}/xmlstarlet-fuzz}"
tree=$BUILD_DIR/tree
stamp=$BUILD_DIR/.built

if [ "${LIST_TARGETS:-no}" = yes ]; then
    exec python3 "$here/mutate.py" --list-targets --binary - --tree -
fi

CC=${CC:-$(command -v clang || command -v gcc)}
CFLAGS_SAN="-g -O1 -fno-omit-frame-pointer"
if [ "$SAN" != none ]; then
    CFLAGS_SAN="$CFLAGS_SAN -fsanitize=$SAN -fno-sanitize-recover=all"
fi

# Rebuild whenever the ref, the sanitizer set or the compiler changes.
want="$REF|$SAN|$CC"
have=$(cat "$stamp" 2>/dev/null || echo none)

if [ "$REBUILD" = yes ] || [ ! -x "$tree/xml" ] || [ "$have" != "$want" ]; then
    echo "==> exporting $REF to $tree"
    rm -rf "$tree"
    mkdir -p "$tree"
    # -c core.autocrlf=false: emit the committed bytes, not a platform variant.
    git -C "$repo" -c core.autocrlf=false archive --format=tar "$REF" \
        | tar -C "$tree" -xf -

    echo "==> building with CC=$CC CFLAGS=\"$CFLAGS_SAN\""
    ( cd "$tree" \
      && autoreconf -sif >"$BUILD_DIR/autoreconf.log" 2>&1 \
      && ./configure --disable-build-docs CC="$CC" CFLAGS="$CFLAGS_SAN" \
             >"$BUILD_DIR/configure.log" 2>&1 \
      && make -j"$JOBS" >"$BUILD_DIR/make.log" 2>&1 ) || {
        echo "build failed; see $BUILD_DIR/{autoreconf,configure,make}.log" >&2
        exit 1
    }
    "$tree/xml" --version | sed 's/^/    /'
    echo "$want" >"$stamp"
else
    echo "==> reusing $tree ($have); --rebuild to start over"
fi

if [ "$RUN_CHECK" = yes ]; then
    echo "==> make check under ${SAN:-no} sanitizers"
    if ( cd "$tree" && make check >"$BUILD_DIR/check.log" 2>&1 ); then
        status=pass
    else
        status=FAIL
    fi
    grep -E '^# (TOTAL|PASS|FAIL|SKIP|ERROR|XPASS|XFAIL):' \
        "$BUILD_DIR/check.log" | sed 's/^# /    /' || true
    echo "    suite: $status  (log: $BUILD_DIR/check.log)"
    if [ "$status" = FAIL ]; then
        echo "    note: a failure here is a finding in itself, not just noise" >&2
    fi
fi

[ "$RUN_FUZZ" = yes ] || exit 0

echo "==> fuzzing"
leakarg=
[ "$LEAKS" = yes ] || leakarg=--no-leaks
exec python3 "$here/mutate.py" \
    --binary "$tree/xml" \
    --tree "$tree" \
    --iterations "$ITERATIONS" \
    --targets "$TARGETS" \
    --seed "$SEED" \
    --timeout "$TIMEOUT" \
    --jobs "$JOBS" \
    --seeds "$SEEDCAP" \
    --out "$BUILD_DIR/findings" \
    $leakarg
