"""Orchestrator CLI.  cd examples/atlantis/generator && python3 -m atlantis.orchestrate <features|gen|render|summary> [options]"""
import argparse, importlib, json, multiprocessing, os, sys, time

from . import config, feature, export_tiles as E

ROOT = config.ROOT
DEFAULT_OUT = os.path.join(ROOT, 'out', 'tiles')

_ctx = None
_features = None


def load_generators():
    """Import every module in generators.GENERATORS (idempotent); returns the registered features."""
    from . import generators
    for m in generators.GENERATORS:
        importlib.import_module(m)
    return feature.registry()


def parse_tiles(ctx, spec):
    """'all' | 'tx,tz;tx,tz' | 'box:x1,z1,x2,z2' (local block coordinates) -> sorted list of (tx, tz)."""
    spec = (spec or 'all').strip()
    if spec == 'all': return E.all_tiles(ctx)
    if spec.startswith('box:'):
        x1, z1, x2, z2 = (int(v) for v in spec[4:].split(','))
        return E.tiles_in_box(ctx, x1, z1, x2, z2)
    return sorted({tuple(int(v) for v in p.split(',')) for p in spec.split(';') if p.strip()})


def _init_worker():
    global _ctx, _features
    _ctx = config.load()
    _features = load_generators()


def _work(job):
    tx, tz, out_dir = job
    t0 = time.time()
    vol = E.build_tile(_ctx, tx, tz, _features)
    t1 = time.time()
    h = E.tile_hash(_ctx, vol)
    name = E.tile_filename(tx, tz, h)
    if os.path.exists(os.path.join(out_dir, name)):
        return tx, tz, name, h, 'skipped', t1 - t0, 0.0
    name, h, _ = E.write_tile(_ctx, tx, tz, vol, out_dir)
    return tx, tz, name, h, 'written', t1 - t0, time.time() - t1


def gen(ctx, tiles, jobs, out_dir):
    t_start = time.time()
    os.makedirs(out_dir, exist_ok=True)
    work = [(tx, tz, out_dir) for tx, tz in tiles]
    n = len(work)
    jobs = max(1, min(jobs, n))
    print(f'{n} tiles, {jobs} jobs -> {out_dir}')
    entries, stats = {}, {'written': 0, 'skipped': 0}
    tb = te = 0.0
    last = 0.0
    if jobs == 1:
        _init_worker()
        it = map(_work, work)
        pool = None
    else:
        from .util import mp_pool
        pool = mp_pool(jobs, initializer=_init_worker)
        it = pool.imap_unordered(_work, work, chunksize=max(1, min(8, n // (jobs * 4))))
    for k, (tx, tz, name, h, status, b, e) in enumerate(it, 1):
        entries[(tx, tz)] = (name, h)
        stats[status] += 1; tb += b; te += e
        if time.time() - last > 2 or k == n:
            last = time.time()
            el = last - t_start
            print(f'  {k}/{n}  {el:6.1f}s  eta {el / k * (n - k):6.1f}s', flush=True)
    if pool: pool.close(); pool.join()
    E.update_tiles_json(ctx, out_dir, entries)
    wall = time.time() - t_start
    print(f'done: {stats["written"]} written, {stats["skipped"]} skipped (same hash); wall {wall:.1f}s; '
          f'cpu build {tb:.1f}s ({tb / n * 1000:.0f} ms/tile), cpu export {te:.1f}s '
          f'({te / max(1, stats["written"]) * 1000:.0f} ms/written tile); {wall / n * 1000:.0f} ms/tile wall')
    return entries


def validate_cmd(ctx, feats, a):
    """`validate`: build tiles like `gen` (same builder, no export) and run the checks. Exit 1 on any error."""
    from . import validate as V
    if a.list_checks:
        for c in V.resolve_checks('all'):
            print(f'{c.name:14s} {"[trace] " if c.trace else ""}{c.doc.splitlines()[0] if c.doc else ""}')
        return 0
    checks = V.resolve_checks(a.checks)
    tiles = parse_tiles(ctx, a.tiles)
    print(f'validating {len(tiles)} tiles with {", ".join(c.name for c in checks)} ({a.jobs} jobs)')
    opts = {'light_halo': not a.no_light_halo, 'clearance_margin': a.clearance_margin}
    rep = V.validate(ctx, tiles, feats, a.checks, a.jobs, opts,
                     progress=lambda k, n, el: print(f'  {k}/{n}  {el:6.1f}s', flush=True))
    print(V.format_table(rep, checks))
    for i in [i for i in rep['issues'] if i.severity == 'error'][:10]:
        print(f'  ERROR {i.check} {i.tile or ""} x{i.count} at {i.pos}: {i.message}')
    if a.json: V.write_json(a.json, rep, checks); print('wrote', a.json)
    print(f'{rep["errors"]} error issue(s), {rep["warnings"]} warning issue(s)')
    return 1 if rep['errors'] else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='atlantis.orchestrate')
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('features', help='list the registered features')
    sub.add_parser('summary', help='print ctx.summary()')
    g = sub.add_parser('gen', help='build and export tiles')
    g.add_argument('--tiles', default='all', help="all | 'tx,tz;tx,tz' | box:x1,z1,x2,z2 (local blocks)")
    g.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 2))
    g.add_argument('--out', default=DEFAULT_OUT)
    v = sub.add_parser('validate', help='run the validation checks on built tiles (no NBT export)')
    v.add_argument('--tiles', default='all', help="all | 'tx,tz;tx,tz' | box:x1,z1,x2,z2 (local blocks)")
    v.add_argument('--checks', default='all', help='all | comma separated check names (see --list-checks)')
    v.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 2))
    v.add_argument('--json', help='write the full issue list + timings to this json file')
    v.add_argument('--list-checks', action='store_true')
    v.add_argument('--no-light-halo', action='store_true',
                   help='light_coverage: skip near-edge cells instead of building a 14-block halo (faster)')
    v.add_argument('--clearance-margin', type=int, default=0, help='extra blocks allowed above max_building_top')
    r = sub.add_parser('render', help='top | section | iso views -> out/renders/*.png')
    r.add_argument('view', choices=['top', 'section', 'iso', 'plan'])
    r.add_argument('--box', help='x1,z1,x2,z2 local blocks (top: default whole area; iso: required)')
    r.add_argument('--axis', choices=['x', 'z'], default='x')
    r.add_argument('--at', type=int, default=0, help='section: the x (or z) coordinate of the slice')
    r.add_argument('--range', help='section: a,b limits along the slice (default whole area)')
    r.add_argument('--scale', type=int, default=1, help='pixels per block (iso: block size in px, even, default 4)')
    r.add_argument('--no-dome', action='store_true', help='top/iso: hide the dome glass')
    r.add_argument('--y', help='iso: y1,y2 limits (default full height); plan: the y of the slice')
    r.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 2))
    r.add_argument('--out', help='output png (default out/renders/<view>.png)')
    a = ap.parse_args(argv)

    ctx = config.load()
    if a.cmd == 'summary':
        print(json.dumps(ctx.summary(), indent=1)); return 0
    feats = load_generators()
    if a.cmd == 'features':
        for f in sorted(feats, key=lambda f: (f.layer, f.name)):
            print(f'layer {f.layer:3d}  {f.name:30s} bbox={f.bbox}  meta={f.meta}')
        print(f'{len(feats)} features')
        return 0
    if a.cmd == 'gen':
        gen(ctx, parse_tiles(ctx, a.tiles), a.jobs, a.out)
        return 0
    if a.cmd == 'validate':
        return validate_cmd(ctx, feats, a)
    if a.cmd == 'render':
        from . import render
        path = render.render(ctx, a)
        print('wrote', path)
        return 0


if __name__ == '__main__':
    sys.exit(main())
