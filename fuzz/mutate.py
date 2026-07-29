#!/usr/bin/env python3
"""Mutation fuzzer for the XMLStarlet subcommands.

Driven by fuzz/run.sh, which builds an instrumented binary first; this script
only generates inputs, runs them and triages what comes back.  It deliberately
has no third-party dependencies, so it works on a bare Linux or WSL box.

Findings are whatever the sanitizers report, plus killed-by-signal and timeout.
A normal XMLStarlet error exit (bad input, failed validation) is not a finding:
almost every mutated input produces one.
"""

import argparse
import concurrent.futures
import hashlib
import os
import random
import re
import subprocess
import sys
import tempfile

# ---------------------------------------------------------------- targets

class Target:
    """One fuzzable command line.  '{in}' is replaced by the input file."""

    def __init__(self, name, argv, corpus, stdin=False):
        self.name = name
        self.argv = argv
        self.corpus = corpus
        self.stdin = stdin


TARGETS = [
    # Own-code surface first: these parse or emit with XMLStarlet's own loops.
    Target('depyx',      ['depyx', '{in}'],                       'pyx'),
    Target('pyx',        ['pyx', '{in}'],                         'xml'),
    Target('esc',        ['esc'],                                 'text', stdin=True),
    Target('unesc',      ['unesc'],                               'text', stdin=True),

    Target('fo',         ['fo', '{in}'],                          'xml'),
    Target('fo-indent',  ['fo', '-s', '4', '{in}'],               'xml'),
    Target('fo-recover', ['fo', '-R', '-C', '-N', '{in}'],        'xml'),
    Target('fo-dropdtd', ['fo', '-D', '-o', '{in}'],              'xml'),
    Target('fo-html',    ['fo', '-H', '{in}'],                    'xml'),

    Target('el',         ['el', '{in}'],                          'xml'),
    Target('el-values',  ['el', '-v', '{in}'],                    'xml'),
    Target('el-depth',   ['el', '-u', '-d3', '{in}'],             'xml'),

    Target('sel-value',  ['sel', '-t', '-v', '//*', '{in}'],      'xml'),
    Target('sel-copy',   ['sel', '-t', '-c', '/', '{in}'],        'xml'),
    Target('sel-match',  ['sel', '-t', '-m', '//*', '-v', 'name()', '-n', '{in}'], 'xml'),

    Target('ed-update',  ['ed', '-u', '//*', '-v', 'X', '{in}'],  'xml'),
    Target('ed-insert',  ['ed', '-s', '//*', '-t', 'elem', '-n', 'n', '-v', 'v', '{in}'], 'xml'),
    Target('ed-delete',  ['ed', '-d', '//*[1]', '{in}'],          'xml'),
    Target('ed-attr',    ['ed', '-s', '//*', '-t', 'attr', '-n', 'k', '-v', 'v', '{in}'], 'xml'),
    # Inserting a text node is its own case: xmlAddChild and the sibling calls
    # merge a text node into an adjacent one and free the node they were given,
    # which an element insert never does.
    Target('ed-sub-text',   ['ed', '-s', '//*', '-t', 'text', '-n', 'x', '-v', 'FOO', '{in}'], 'xml'),
    Target('ed-ins-text',   ['ed', '-i', '//*', '-t', 'text', '-n', 'x', '-v', 'FOO', '{in}'], 'xml'),
    Target('ed-app-text',   ['ed', '-a', '//*', '-t', 'text', '-n', 'x', '-v', 'FOO', '{in}'], 'xml'),
    # $prev names what was just inserted, so this walks whatever the insert left
    # behind -- including a node the merge above may already have freed.
    Target('ed-prev',    ['ed', '-s', '//*', '-t', 'text', '-n', 'x', '-v', 'FOO',
                          '-i', '$prev', '-t', 'attr', '-n', 'k', '-v', 'v', '{in}'], 'xml'),
    Target('ed-prev-del', ['ed', '-s', '//*', '-t', 'elem', '-n', 'n', '-v', 'v',
                           '-d', '$prev', '-i', '$prev', '-t', 'attr', '-n', 'k',
                           '-v', 'v', '{in}'], 'xml'),

    Target('val',        ['val', '-e', '{in}'],                   'xml'),
    Target('val-embed',  ['val', '-e', '-E', '{in}'],             'xml'),
    Target('val-dtd',    ['val', '-e', '-d', 'examples/dtd/table.dtd', '{in}'], 'xml'),
    Target('val-xsd',    ['val', '-e', '-s', 'examples/xsd/table.xsd', '{in}'], 'xml'),
    Target('val-rng',    ['val', '-e', '-r', 'examples/relaxng/address.rng', '{in}'], 'xml'),

    Target('c14n',       ['c14n', '--with-comments', '{in}'],     'xml'),
    Target('c14n-exc',   ['c14n', '--exc-without-comments', '{in}'], 'xml'),

    Target('tr-doc',     ['tr', 'examples/xsl/cat.xsl', '{in}'],  'xml'),
    Target('tr-style',   ['tr', '{in}', 'examples/xml/table.xml'], 'xsl'),
]

TARGETS_BY_NAME = {t.name: t for t in TARGETS}

EXTENSIONS = {'xml': '.xml', 'pyx': '.pyx', 'text': '.txt', 'xsl': '.xsl'}

# ---------------------------------------------------------------- mutation

# Tokens worth splicing in: the delimiters and constructs each parser has to
# get right, plus a few malformed UTF-8 sequences and a lone NUL.
INTERESTING = {
    'xml': [
        b'<', b'>', b'&', b'"', b"'", b'</', b'/>', b'<?', b'?>',
        b'<!--', b'-->', b'<![CDATA[', b']]>', b'<!DOCTYPE', b'<!ENTITY',
        b'&e;', b'%e;', b'&#x0;', b'&#38;', b'&amp;', b'&#',
        b'xmlns:', b'xmlns=', b'xml:lang', b'xml:space', b':', b'::',
        b'<?xml version="1.0" encoding="UTF-16"?>',
        b'<!DOCTYPE a [<!ENTITY e SYSTEM "/etc/passwd">]>',
        b'\x00', b'\r\n', b'\xef\xbb\xbf', b'\xff\xfe', b'\xc3\x28',
        b'\xed\xa0\x80', b'\xf4\x90\x80\x80', b'a' * 512,
    ],
    'pyx': [
        b'(', b')', b'A', b'-', b'?', b'C', b'[', b']', b'D',
        b'A ', b'(a', b'\\n', b'\\t', b'\\\\', b'\\',
        b'\n', b'\x00', b'\xc3\x28', b'a' * 512,
    ],
    'text': [
        b'&', b'<', b'>', b'"', b"'", b'&amp;', b'&lt;', b'&#x41;', b'&#', b';',
        b'&#x', b'&#xzz;', b'\x00', b'\xff', b'\xc3\x28', b'a' * 512,
    ],
}
INTERESTING['xsl'] = INTERESTING['xml'] + [
    b'<xsl:template match="/">', b'<xsl:value-of select="."/>',
    b'<xsl:for-each select="//*">', b'<xsl:call-template name="x"/>',
    b'xsl:', b'version="1.0"',
]


class Mutator:
    def __init__(self, rng, kind):
        self.rng = rng
        self.tokens = INTERESTING.get(kind, INTERESTING['xml'])

    def mutate(self, data, others):
        buf = bytearray(data)
        for _ in range(self.rng.randint(1, 8)):
            if not buf:
                buf = bytearray(self.rng.choice(self.tokens))
                continue
            self.rng.choice(self.OPS)(self, buf, others)
        return bytes(buf[:1 << 20])          # cap: keep runs fast

    def _pos(self, buf):
        return self.rng.randrange(len(buf))

    def _span(self, buf):
        i = self._pos(buf)
        return i, min(len(buf), i + self.rng.randint(1, 64))

    def flip_bit(self, buf, _others):
        i = self._pos(buf)
        buf[i] ^= 1 << self.rng.randrange(8)

    def set_byte(self, buf, _others):
        buf[self._pos(buf)] = self.rng.randrange(256)

    def insert_token(self, buf, _others):
        buf[self._pos(buf):0] = self.rng.choice(self.tokens)

    def overwrite_token(self, buf, _others):
        tok = self.rng.choice(self.tokens)
        i = self._pos(buf)
        buf[i:i + len(tok)] = tok

    def delete_span(self, buf, _others):
        i, j = self._span(buf)
        del buf[i:j]

    def duplicate_span(self, buf, _others):
        i, j = self._span(buf)
        buf[i:i] = buf[i:j]

    def swap_spans(self, buf, _others):
        i, j = self._span(buf)
        k, m = self._span(buf)
        buf[i:j], buf[k:m] = buf[k:m], buf[i:j]

    def truncate(self, buf, _others):
        del buf[self._pos(buf):]

    def splice(self, buf, others):
        """Graft a chunk of another corpus entry in."""
        if not others:
            return
        other = self.rng.choice(others)
        if not other:
            return
        a = self.rng.randrange(len(other))
        b = min(len(other), a + self.rng.randint(1, 256))
        buf[self._pos(buf):0] = other[a:b]

    def repeat_all(self, buf, _others):
        n = self.rng.randint(2, 8)
        if len(buf) * n <= 1 << 20:
            buf *= n

    def nul_at_line_start(self, buf, _others):
        """Line-oriented parsers are easy to trip with an empty-looking line."""
        starts = [0] + [i + 1 for i, c in enumerate(buf) if c == 0x0A]
        buf[self.rng.choice(starts):0] = b'\x00'

    def strip_newlines(self, buf, _others):
        buf[:] = buf.replace(b'\n', b'')

    OPS = [flip_bit, set_byte, insert_token, insert_token, overwrite_token,
           delete_span, duplicate_span, swap_spans, truncate, splice, splice,
           repeat_all, nul_at_line_start, strip_newlines]


# ---------------------------------------------------------------- triage

# Anchored deliberately.  libxslt emits its own diagnostics worded
# "runtime error: file ... element ..." for a bad stylesheet, so matching a bare
# "runtime error:" reports ordinary XSLT errors as sanitizer findings.  UBSan
# always prefixes the file:line:col of the offending source line.
SANITIZER_RE = re.compile(
    rb'(?:(?:Address|Leak|Memory|Thread|UndefinedBehavior)Sanitizer[:\s]'
    rb'|^\S+:\d+:\d+: runtime error:)', re.M)

SUMMARY_RE = re.compile(rb'SUMMARY: (\w+): ([^\n]*)')
RUNTIME_RE = re.compile(rb'^(\S+:\d+:\d+): (runtime error: [^\n]*)', re.M)

# Only frames carrying file:line match, i.e. only our own -g compiled sources;
# libxml2/libxslt frames appear as "(/usr/lib/....so+0xNNN)" and are skipped.
FRAME_RE = re.compile(rb'#\d+ 0x[0-9a-f]+ in (\S+) ([^\s:()]+:\d+)')

# Every allocation runs through XMLStarlet's wrappers, so those frames say
# nothing about where a leak came from.
ALLOC_WRAPPERS = frozenset((
    'xmalloc', 'xrealloc', 'xstrdup', 'malloc', 'realloc', 'calloc', 'strdup',
    '__interceptor_malloc', '__interceptor_realloc', '__interceptor_calloc',
    '__interceptor_strdup', 'operator new',
))


def _relative(loc):
    """Trim the scratch build path so keys survive a different --build-dir."""
    text = loc.decode('utf-8', 'replace')
    marker = text.find('src/')
    return text[marker:] if marker >= 0 else text


def _pick_site(stderr):
    """Innermost own-source frame that is not an allocator wrapper."""
    for func, loc in FRAME_RE.findall(stderr):
        name = func.decode('utf-8', 'replace')
        if name in ALLOC_WRAPPERS:
            continue
        return '%s %s' % (name, _relative(loc))
    return ''

# Addresses, sizes and pids differ run to run; fold them out of the key.
NUMBERS_RE = re.compile(rb'0x[0-9a-f]+|\d+')


def classify(returncode, stderr, timed_out):
    """Return (kind, dedup_key) or (None, None) for an uninteresting run."""
    if timed_out:
        return 'timeout', 'timeout'

    if SANITIZER_RE.search(stderr):
        # Prefer a UBSan 'runtime error' site: it names the exact source line.
        m = RUNTIME_RE.search(stderr)
        if m:
            where = _relative(m.group(1))
            what = NUMBERS_RE.sub(b'N', m.group(2)).decode('utf-8', 'replace')
            return 'ubsan', '%s %s' % (where, what)

        kind = 'asan'
        detail = ''
        m = SUMMARY_RE.search(stderr)
        if m:
            detail = NUMBERS_RE.sub(b'N', m.group(2)).decode('utf-8', 'replace')
            if b'Leak' in m.group(1) or b'leaked' in m.group(2):
                kind = 'leak'

        return kind, ('%s @ %s' % (detail, _pick_site(stderr))).strip()

    if returncode is not None and returncode < 0:
        return 'signal', 'signal %d' % -returncode

    return None, None


# ---------------------------------------------------------------- runner

class Finding:
    def __init__(self, kind, key, target, argv, stderr, data):
        self.kind = kind
        self.key = key
        self.target = target
        self.argv = argv
        self.stderr = stderr
        self.data = data
        self.count = 1


def build_env(leaks):
    env = dict(os.environ)
    asan = ['abort_on_error=0', 'exitcode=86', 'allocator_may_return_null=1',
            'max_allocation_size_mb=2048',
            'detect_leaks=%d' % (1 if leaks else 0)]
    env['ASAN_OPTIONS'] = ':'.join(asan)
    env['UBSAN_OPTIONS'] = 'print_stacktrace=1:halt_on_error=1:exitcode=86'
    env['LSAN_OPTIONS'] = 'exitcode=86'
    return env


def run_once(binary, target, data, tree, env, timeout):
    """Run one input; return (kind, key, argv, stderr)."""
    suffix = EXTENSIONS.get(target.corpus, '.bin')
    fd, path = tempfile.mkstemp(prefix='xsfuzz-', suffix=suffix)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(data)

        argv = [binary] + [path if a == '{in}' else a for a in target.argv]
        timed_out = False
        rc, err = None, b''
        try:
            proc = subprocess.run(
                argv, cwd=tree, env=env,
                input=data if target.stdin else b'',
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=timeout)
            rc, err = proc.returncode, proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            err = exc.stderr or b''

        kind, key = classify(rc, err, timed_out)
        return kind, key, argv, err[:8192]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def load_corpus(tree, binary, env, seeds_wanted):
    """Build a seed corpus per input kind, keyed as in Target.corpus."""
    corpora = {}

    xml_dir = os.path.join(tree, 'examples', 'xml')
    xml = []
    for name in sorted(os.listdir(xml_dir)):
        if name.endswith(('.xml', '.dtd', '.xpath')):
            with open(os.path.join(xml_dir, name), 'rb') as fh:
                xml.append(fh.read())
    corpora['xml'] = xml

    xsl_dir = os.path.join(tree, 'examples', 'xsl')
    xsl = []
    if os.path.isdir(xsl_dir):
        for name in sorted(os.listdir(xsl_dir)):
            with open(os.path.join(xsl_dir, name), 'rb') as fh:
                xsl.append(fh.read())
    corpora['xsl'] = xsl or xml

    # PYX seeds are generated by XMLStarlet itself, so the depyx corpus is
    # exactly the dialect its own writer emits.
    pyx = []
    for name in sorted(os.listdir(xml_dir)):
        if not name.endswith('.xml'):
            continue
        try:
            proc = subprocess.run(
                [binary, 'pyx', os.path.join(xml_dir, name)],
                cwd=tree, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=30)
        except subprocess.TimeoutExpired:
            continue
        if proc.stdout.strip():
            pyx.append(proc.stdout)
    corpora['pyx'] = pyx or [b'(a\nA k v\n-text\n)a\n']

    corpora['text'] = [
        b'plain text', b'<a href="x">&amp;</a>', b'&lt;&gt;&quot;&apos;&amp;',
        b'&#x41;&#66;&#xzz;', b'a & b < c > d " e \' f',
        b'\xc3\xa9\xc3\xa8 unicode', b'&unknown; &#x0; &#;',
    ] + [d[:400] for d in xml[:6]]

    if seeds_wanted:
        for kind in corpora:
            corpora[kind] = corpora[kind][:seeds_wanted]
    return corpora


def fuzz_target(target, corpora, binary, tree, env, args):
    data_set = corpora.get(target.corpus) or []
    if not data_set:
        return target.name, {}, {}

    # Per-target seed: reproducible, and independent of which targets ran.
    rng = random.Random('%s/%s' % (args.seed, target.name))
    mutator = Mutator(rng, target.corpus)

    stats = {'runs': 0, 'clean': 0, 'ok': 0, 'timeout': 0,
             'signal': 0, 'asan': 0, 'ubsan': 0, 'leak': 0}
    findings = {}

    # Round 0: the untouched seeds, as a baseline for this build.
    queue = [(d, True) for d in data_set]
    queue += [(mutator.mutate(rng.choice(data_set), data_set), False)
              for _ in range(args.iterations)]

    for data, pristine in queue:
        kind, key, argv, err = run_once(binary, target, data, tree, env,
                                        args.timeout)
        stats['runs'] += 1
        if pristine:
            stats['clean'] += 1
        if kind is None:
            stats['ok'] += 1
            continue
        stats[kind] = stats.get(kind, 0) + 1
        full = '%s: %s' % (kind, key)
        if full in findings:
            findings[full].count += 1
        else:
            findings[full] = Finding(kind, key, target.name, argv, err, data)
            if pristine:
                findings[full].key += '  [unmutated seed]'
    return target.name, stats, findings


def save(finding, outdir, target):
    digest = hashlib.sha1(
        ('%s%s' % (target, finding.key)).encode()).hexdigest()[:10]
    stem = os.path.join(outdir, '%s-%s-%s' % (target, finding.kind, digest))
    ext = EXTENSIONS.get(TARGETS_BY_NAME[target].corpus, '.bin')
    with open(stem + ext, 'wb') as fh:
        fh.write(finding.data)
    with open(stem + '.log', 'w') as fh:
        redacted = [a if not a.startswith('/tmp/xsfuzz') else stem + ext
                    for a in finding.argv]
        fh.write('target:  %s\n' % target)
        fh.write('kind:    %s\n' % finding.kind)
        fh.write('key:     %s\n' % finding.key)
        fh.write('hits:    %d\n' % finding.count)
        fh.write('command: %s\n\n' % ' '.join(redacted))
        fh.write(finding.stderr.decode('utf-8', 'replace'))
    return stem + ext


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--binary', required=True)
    ap.add_argument('--tree', required=True,
                    help='source tree the binary was built in (for examples/)')
    ap.add_argument('--iterations', type=int, default=200,
                    help='mutated inputs per target (default 200)')
    ap.add_argument('--targets', default='all',
                    help='comma-separated target names, or "all"')
    ap.add_argument('--seed', default='1')
    ap.add_argument('--jobs', type=int, default=os.cpu_count() or 4)
    ap.add_argument('--timeout', type=float, default=10.0)
    ap.add_argument('--out', default=None, help='directory for reproducers')
    ap.add_argument('--seeds', type=int, default=0,
                    help='cap the seed corpus per kind (0 = no cap)')
    ap.add_argument('--no-leaks', action='store_true',
                    help='disable LeakSanitizer during fuzzing')
    ap.add_argument('--list-targets', action='store_true')
    args = ap.parse_args()

    if args.list_targets:
        for t in TARGETS:
            argv = ' '.join(t.argv)
            print('%-12s %-5s xml %s%s'
                  % (t.name, t.corpus, argv,
                     ' < {in}' if t.stdin else ''))
        return 0

    if args.targets == 'all':
        chosen = TARGETS
    else:
        chosen = []
        for name in args.targets.split(','):
            name = name.strip()
            if name not in TARGETS_BY_NAME:
                sys.exit('unknown target: %s (try --list-targets)' % name)
            chosen.append(TARGETS_BY_NAME[name])

    outdir = args.out or os.path.join(os.path.dirname(args.binary), 'findings')
    os.makedirs(outdir, exist_ok=True)

    env = build_env(not args.no_leaks)
    corpora = load_corpus(args.tree, args.binary, env, args.seeds)
    print('corpus: ' + ', '.join('%s=%d' % (k, len(v))
                                 for k, v in sorted(corpora.items())))
    print('fuzzing %d target(s), %d mutations each, seed %s, %d job(s)\n'
          % (len(chosen), args.iterations, args.seed, args.jobs))

    header = ('%-12s %6s %6s %6s %6s %6s %6s %6s'
              % ('target', 'runs', 'ok', 'sig', 'ubsan', 'asan', 'leak', 't/o'))
    print(header)
    print('-' * len(header))

    all_findings = {}
    totals = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(fuzz_target, t, corpora, args.binary,
                               args.tree, env, args) for t in chosen]
        for fut in concurrent.futures.as_completed(futures):
            name, stats, findings = fut.result()
            if not stats:
                continue
            print('%-12s %6d %6d %6d %6d %6d %6d %6d'
                  % (name, stats['runs'], stats['ok'], stats['signal'],
                     stats['ubsan'], stats['asan'], stats['leak'],
                     stats['timeout']))
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + value
            for key, finding in findings.items():
                all_findings.setdefault((name, key), finding)

    print('-' * len(header))
    print('%-12s %6d %6d %6d %6d %6d %6d %6d'
          % ('TOTAL', totals.get('runs', 0), totals.get('ok', 0),
             totals.get('signal', 0), totals.get('ubsan', 0),
             totals.get('asan', 0), totals.get('leak', 0),
             totals.get('timeout', 0)))

    if not all_findings:
        print('\nno findings')
        return 0

    print('\n%d unique finding(s):\n' % len(all_findings))
    for (target, key), finding in sorted(all_findings.items()):
        path = save(finding, outdir, target)
        print('  [%s] %s' % (target, key))
        print('      x%-4d %s' % (finding.count, path))
    print('\nreproducers and logs in %s' % outdir)
    return 1


if __name__ == '__main__':
    sys.exit(main())
