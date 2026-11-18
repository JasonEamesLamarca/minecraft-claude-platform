"""Run checks over tiles (serial or in a pool) and reduce across tiles."""
import json, multiprocessing, os, time

from .. import export_tiles as E
from . import core
from .core import Issue, Partial, resolve_checks, make_key
from .trace import build_traced


def check_tile(ctx, tx, tz, features, checks, vol=None):
    """Build tile (tx, tz) (traced if a selected check needs it) and run `checks`.
    Returns (issues, partials, timings{check: seconds, '_build': seconds})."""
    key = make_key(tx, tz)
    box = E.tile_bounds(ctx, tx, tz)
    t0 = time.perf_counter()
    if vol is None:
        vol = build_traced(ctx, box, features) if any(c.trace for c in checks) else E.build_box(ctx, box, features)
    tf = [f for f in features if E.feature_intersects(f, box)]
    timing = {'_build': time.perf_counter() - t0}
    issues, partials = [], []
    for c in checks:
        t1 = time.perf_counter()
        for r in c.fn(ctx, key, vol, tf):
            if isinstance(r, Partial): partials.append(r)
            else:
                if r.tile is None: r.tile = key
                issues.append(r)
        timing[c.name] = time.perf_counter() - t1
    return issues, partials, timing


def reduce_all(ctx, checks, partials, features):
    by = {}
    for p in partials: by.setdefault(p.check, []).append(p)
    out, timing = [], {}
    for c in checks:
        if c.reduce is None: continue
        t0 = time.perf_counter()
        out.extend(c.reduce(by.get(c.name, []), ctx, features))
        timing[c.name] = time.perf_counter() - t0
    return out, timing


def selected_features(ctx, features, tiles):
    boxes = [E.tile_bounds(ctx, *t) for t in tiles]
    return [f for f in features if any(E.feature_intersects(f, b) for b in boxes)]


# ---------------------------------------------------------------- pool plumbing
_W = {}


def _init(names, options):
    from .. import config
    from ..orchestrate import load_generators
    core.configure(**options)
    _W['ctx'] = config.load()
    _W['features'] = load_generators()
    core.set_all_features(_W['features'])
    _W['checks'] = resolve_checks(names)


def _work(tile):
    return tile, check_tile(_W['ctx'], tile[0], tile[1], _W['features'], _W['checks'])


def validate(ctx, tiles, features, check_spec='all', jobs=1, options=None, progress=None):
    """Validate `tiles` [(tx, tz)]. Returns a report dict:
    {'issues': [Issue], 'timing': {check: cpu seconds}, 'tiles': n, 'wall': s, 'errors': n, 'warnings': n}.
    jobs > 1 uses a process pool whose workers load the registered generators themselves (like `gen`)."""
    t_start = time.time()
    options = dict(options or {})
    core.OPTIONS.update(core.DEFAULTS)
    core.configure(**options)
    core.set_all_features(features)
    checks = resolve_checks(check_spec)
    issues, partials, timing = [], [], {}
    n = len(tiles)
    jobs = max(1, min(jobs, n)) if n else 1
    if jobs == 1:
        it = (((tx, tz), check_tile(ctx, tx, tz, features, checks)) for tx, tz in tiles)
        pool = None
    else:
        from ..util import mp_pool
        pool = mp_pool(jobs, initializer=_init, initargs=(check_spec, options))
        it = pool.imap_unordered(_work, tiles, chunksize=max(1, min(8, n // (jobs * 4))))
    last = 0.0
    for k, (_t, (iss, par, tim)) in enumerate(it, 1):
        issues.extend(iss); partials.extend(par)
        for name, s in tim.items(): timing[name] = timing.get(name, 0.0) + s
        if progress and (time.time() - last > 2 or k == n):
            last = time.time(); progress(k, n, last - t_start)
    if pool: pool.close(); pool.join()
    red, rtim = reduce_all(ctx, checks, partials, selected_features(ctx, features, tiles))
    for name, s in rtim.items(): timing['reduce:' + name] = s
    issues.extend(red)
    issues.sort(key=lambda i: (i.check, {'error': 0, 'warn': 1, 'info': 2}[i.severity], i.tile or ''))
    return {'issues': issues, 'timing': timing, 'tiles': n, 'wall': time.time() - t_start,
            'errors': sum(1 for i in issues if i.severity == 'error'),
            'warnings': sum(1 for i in issues if i.severity == 'warn')}


def summarize(report, checks, top=2):
    """Rows of (check, errors, warnings, infos, ms_per_tile, [top messages]) where the numbers are summed `count`s."""
    n = max(1, report['tiles'])
    rows = []
    for c in checks:
        mine = [i for i in report['issues'] if i.check == c.name]
        cnt = {s: sum(i.count for i in mine if i.severity == s) for s in core.SEVERITIES}
        msgs = {}
        for i in mine:
            if i.severity != 'info': msgs[i.message] = msgs.get(i.message, 0) + i.count
        for i in mine:
            if i.severity == 'info': msgs.setdefault(i.message, 0)
        topm = [f'{m} (x{k})' if k else m for m, k in sorted(msgs.items(), key=lambda kv: -kv[1])[:top]]
        ms = (report['timing'].get(c.name, 0.0) + report['timing'].get('reduce:' + c.name, 0.0)) / n * 1000
        rows.append((c.name, cnt['error'], cnt['warn'], cnt['info'], ms, topm))
    return rows


def format_table(report, checks):
    rows = summarize(report, checks)
    w = max(len(r[0]) for r in rows) if rows else 5
    lines = [f'{"check":{w}s}  {"errors":>8s} {"warnings":>8s}  {"ms/tile":>8s}  top messages']
    for name, e, wn, inf, ms, top in rows:
        lines.append(f'{name:{w}s}  {e:8d} {wn:8d}  {ms:8.1f}  {" | ".join(top) if top else "ok"}')
    lines.append(f'build (traced) {report["timing"].get("_build", 0) / max(1, report["tiles"]) * 1000:.0f} ms/tile; '
                 f'{report["tiles"]} tiles, wall {report["wall"]:.1f}s')
    return '\n'.join(lines)


def write_json(path, report, checks):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    data = {'tiles': report['tiles'], 'wall_seconds': report['wall'], 'errors': report['errors'],
            'warnings': report['warnings'], 'timing_cpu_seconds': report['timing'],
            'summary': [{'check': r[0], 'errors': r[1], 'warnings': r[2], 'infos': r[3], 'ms_per_tile': r[4],
                         'top': r[5]} for r in summarize(report, checks, top=5)],
            'issues': [i.to_dict() for i in report['issues']]}
    with open(path, 'w') as f: json.dump(data, f, indent=1, default=list)
