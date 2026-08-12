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
DEPS=no
LIBXML2_VER=2.15.3
LIBXSLT_VER=1.1.43

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
  --deps[=XML2/XSLT] build libxml2 and libxslt from source with the same
                     sanitizers and link against those instead of the system
                     copies (default 2.15.3/1.1.43). Slower the first time, then
                     cached -- and worth it: see below.
  --rebuild          discard the scratch tree and rebuild
  -h, --help         this text

Why --deps matters
  With the system libxml2 the sanitizers only see allocation and deallocation,
  through the malloc interceptor. A bad access made *inside* libxml2 is invisible,
  and xmlstarlet reaches libxml2 constantly, so a real bug can present as
  something far milder. "ed -u" walking a node set into memory libxml2 had already
  freed reported as a 171-byte leak, because update_string handed the freed
  pointer straight to xmlNodeSetContent and libxml2 did the dereferencing; the
  use-after-free only appeared once the read happened in instrumented code. The
  same thing hides the edInsert/$prev use-after-free, whose only visible symptom
  is an edit going missing. Build the dependencies instrumented and those report
  as what they are.
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
        --deps)             DEPS=yes ;;
        --deps=*)           DEPS=yes
                            LIBXML2_VER=${1#*=}
                            LIBXSLT_VER=${LIBXML2_VER#*/}
                            LIBXML2_VER=${LIBXML2_VER%%/*}
                            [ "$LIBXSLT_VER" != "$LIBXML2_VER" ] || {
                                echo "$0: --deps needs XML2/XSLT, e.g." \
                                     "--deps=2.15.3/1.1.43" >&2; exit 2; } ;;
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

# Fetch and build one dependency tarball from download.gnome.org, instrumented.
build_dep() {
    name=$1 ver=$2 prefix=$3; shift 3
    series=${ver%.*}
    tarball=$name-$ver.tar.xz
    url=https://download.gnome.org/sources/$name/$series/$tarball

    [ -f "$tarball" ] || {
        echo "    fetching $tarball"
        if command -v wget >/dev/null; then wget -q "$url"
        elif command -v curl >/dev/null; then curl -sSfLO "$url"
        else echo "need wget or curl to fetch $url" >&2; return 1
        fi
    }
    rm -rf "$name-$ver"
    tar xf "$tarball" || return 1
    # Deliberately without -fno-sanitize-recover here: any pre-existing UB inside
    # libxml2 itself should be reported and stepped over, not turned into an abort
    # that looks like a finding of ours. Memory errors still stop the process.
    ( cd "$name-$ver" \
      && ./configure --prefix="$prefix" --without-python "$@" \
             CC="$CC" CFLAGS="-g -O1 -fno-omit-frame-pointer$SAN_FLAGS" \
             LDFLAGS="$SAN_FLAGS" \
             >"$prefix/$name-configure.log" 2>&1 \
      && make -j"$JOBS" >"$prefix/$name-make.log" 2>&1 \
      && make install >>"$prefix/$name-make.log" 2>&1 )
}

if [ "$DEPS" = yes ]; then
    [ "$SAN" != none ] || SAN=
    # One -fsanitize= per sanitizer rather than a comma list: libtool splits on
    # the comma while assembling the link command and ends up looking for a
    # library called "undefined".
    SAN_FLAGS=
    oldifs=$IFS; IFS=,
    for s in $SAN; do SAN_FLAGS="$SAN_FLAGS -fsanitize=$s"; done
    IFS=$oldifs
    deps=$BUILD_DIR/deps-$LIBXML2_VER-$LIBXSLT_VER-$(echo "${SAN:-none}" | tr , +)
    if [ ! -x "$deps/bin/xslt-config" ] || [ "$REBUILD" = yes ]; then
        echo "==> building instrumented libxml2 $LIBXML2_VER + libxslt $LIBXSLT_VER"
        rm -rf "$deps"
        mkdir -p "$deps/src"
        ( cd "$deps/src" \
          && build_dep libxml2 "$LIBXML2_VER" "$deps" \
          && build_dep libxslt "$LIBXSLT_VER" "$deps" \
                 --with-libxml-prefix="$deps" ) || {
            echo "dependency build failed; see $deps/*-{configure,make}.log" >&2
            exit 1
        }
    else
        echo "==> reusing instrumented deps in $deps"
    fi
    LIBXML_CONFIG=$deps/bin/xml2-config
    LIBXSLT_CONFIG=$deps/bin/xslt-config
    DEPS_LDFLAGS=-Wl,-rpath,$deps/lib
fi

# Resolve the ref to a commit and key the cache on that, not on the name. A branch
# is the usual thing to point this at, and its name does not change when it moves,
# so keying on "$REF" reuses a tree built from whatever that ref meant last time
# and reports the result as if it were the current one.
sha=$(git -C "$repo" rev-parse --verify --quiet "$REF^{commit}") || {
    echo "$0: not a commit: $REF" >&2
    exit 2
}

# Rebuild whenever the commit, the sanitizer set, the compiler or the deps change.
want="$sha|$SAN|$CC|${deps:-system}"
have=$(cat "$stamp" 2>/dev/null || echo none)

if [ "$REBUILD" = yes ] || [ ! -x "$tree/xml" ] || [ "$have" != "$want" ]; then
    echo "==> exporting $REF ($(echo "$sha" | cut -c1-12)) to $tree"
    rm -rf "$tree"
    mkdir -p "$tree"
    # -c core.autocrlf=false: emit the committed bytes, not a platform variant.
    git -C "$repo" -c core.autocrlf=false archive --format=tar "$sha" \
        | tar -C "$tree" -xf -

    echo "==> building with CC=$CC CFLAGS=\"$CFLAGS_SAN\""
    ( cd "$tree" \
      && autoreconf -sif >"$BUILD_DIR/autoreconf.log" 2>&1 \
      && ./configure --disable-build-docs CC="$CC" CFLAGS="$CFLAGS_SAN" \
             ${LIBXML_CONFIG:+LIBXML_CONFIG="$LIBXML_CONFIG"} \
             ${LIBXSLT_CONFIG:+LIBXSLT_CONFIG="$LIBXSLT_CONFIG"} \
             ${DEPS_LDFLAGS:+LDFLAGS="$DEPS_LDFLAGS"} \
             >"$BUILD_DIR/configure.log" 2>&1 \
      && make -j"$JOBS" >"$BUILD_DIR/make.log" 2>&1 ) || {
        echo "build failed; see $BUILD_DIR/{autoreconf,configure,make}.log" >&2
        exit 1
    }
    "$tree/xml" --version | sed 's/^/    /'
    echo "$want" >"$stamp"
else
    echo "==> reusing $tree built from $(echo "$sha" | cut -c1-12); --rebuild to start over"
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
