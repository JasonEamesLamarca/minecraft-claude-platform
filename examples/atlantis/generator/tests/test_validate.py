#!/usr/bin/env python3
"""Validation system tests (plain asserts). Run from the project root:  python3 tests/test_validate.py

Every check must catch its planted defect and stay quiet on a clean case, using synthetic features and a small fake
dome (ctx with radius 40 / height 30)."""
import copy, json, math, os, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TF = float(__import__('os').environ.get('ATLANTIS_TIME_FACTOR', '1'))   # slow CI runners: > 1

from atlantis import config, feature
from atlantis import export_tiles as E
from atlantis.feature import FnFeature, Feature
from atlantis.volume import Volume
from atlantis import validate as V
from atlantis.validate import core, classify as K
from atlantis.validate.trace import build_traced
from atlantis.validate import runner


def make_ctx(radius=40, height=30, margin=200):
    real = config.load()
    cfg = copy.deepcopy(real.cfg)
    prof = copy.deepcopy(real.profile)
    cfg['dome'].update(radius=radius, height_max=height)
    cfg['environment']['exterior_margin'] = margin
    prof['protect'] = []
    return config.Ctx(cfg, prof)


CTX = make_ctx()


def fn_feature(name, bbox, fn, layer=40, meta=None):
    return FnFeature(name, bbox, fn, layer, meta)


def dome_shell(ctx, block='glass', radius_shift=0.0, hole=None, thickness=None):
    """A layer-20 feature building the shell band R-t <= dist < R (optionally with R shifted, and one cell removed)."""
    R, cy, t = ctx.dome_R + radius_shift, ctx.dome_cy, thickness or ctx.dome_thickness
    reach = int(ctx.dome_radius) + 4

    def build(vol, c):
        cl = vol.clip(-reach, 0, -reach, reach, int(c.dome_height) + 4, reach)
        if not cl: return
        for z in range(cl[2], cl[5] + 1):
            for x in range(cl[0], cl[3] + 1):
                for y in range(cl[1], cl[4] + 1):
                    d2 = x * x + z * z + (y - cy) ** 2
                    if (R - t) ** 2 <= d2 < R * R and (x, y, z) != hole:
                        vol.set(x, y, z, block)
    return fn_feature('shell', (-reach, 0, -reach, reach, int(ctx.dome_height) + 4, reach), build, layer=20)


def all_tiles_of_dome(ctx):
    return E.tiles_in_box(ctx, -45, -45, 45, 45)


def run(features, tiles, checks, ctx=CTX, **opts):
    rep = V.validate(ctx, tiles, features, checks, jobs=1, options=opts)
    return rep['issues']


def of(issues, check, sev=None):
    return [i for i in issues if i.check == check and (sev is None or i.severity == sev)]


def one_tile_vol(ctx, tx, tz, features):
    return E.build_box(ctx, E.tile_bounds(ctx, tx, tz), features)


# ------------------------------------------------------------------------------------------------ infra
def test_registry_and_selection():
    names = list(V.CHECKS)
    for n in ('block_states', 'leaks', 'water_inside', 'mask', 'dome_geometry', 'clearance', 'light_coverage',
              'overlaps', 'connectivity'):
        assert n in names, n
    assert [c.name for c in V.resolve_checks('leaks,mask')] == ['mask', 'leaks'] or \
        {c.name for c in V.resolve_checks('leaks,mask')} == {'leaks', 'mask'}
    try:
        V.resolve_checks('nope'); assert False
    except ValueError:
        pass
    i = V.Issue('x', 'warn', 'm', (1, 2, 3), 4)
    assert i.count == 4 and i.pos == (1, 2, 3)


def test_trace_matches_builder():
    ctx = CTX
    feats = [dome_shell(ctx), fn_feature('box', (3, 1, 3, 8, 6, 8),
                                         lambda v, c: v.fill(3, 1, 3, 8, 6, 8, 'stone', replace={None, 'water'}), 40)]
    for tx, tz in ((0, 0), (-1, -1), (3, 3)):
        box = E.tile_bounds(ctx, tx, tz)
        a, b = E.build_box(ctx, box, feats), build_traced(ctx, box, feats)
        assert [a.palette[c] for c in a.cells] == [b.palette[c] for c in b.cells]
    b = build_traced(ctx, E.tile_bounds(ctx, 0, 0), feats)
    i = b._i(5, 3, 5)
    assert b.order[b.writer[i] - 1].name == 'box' and b.touched[i] == 1
    assert b.touched[b._i(20, 3, 20)] == 0 and b.writer[b._i(20, 3, 20)] == 0     # background


# ------------------------------------------------------------------------------------------------ block_states
def test_block_states():
    def feat(state):
        return fn_feature('s', (130, 5, 130, 130, 5, 130), lambda v, c: v.set(130, 5, 130, state))
    clean = ['quartz_stairs[facing=north,half=bottom]', 'smooth_quartz', 'light[level=7]', 'minecraft:stone',
             'quartz_stairs', 'glass_pane[north=true,waterlogged=false]']
    for s in clean:
        assert K.state_problems(s) == [], s
        assert not of(run([feat(s)], [(4, 4)], 'block_states'), 'block_states'), s
    bad = ['quartz_stairz', 'quartz_stairs[facing=up]', 'quartz_stairs[colour=red]', 'stone[', 'light[level=16]',
           'quartz_stairs[facing=north,facing=south]', 'foo:stone']
    for s in bad:
        iss = of(run([feat(s)], [(4, 4)], 'block_states'), 'block_states', 'error')
        assert len(iss) == 1 and iss[0].count == 1 and iss[0].pos == (130, 5, 130), (s, iss)


# ------------------------------------------------------------------------------------------------ water_inside
def test_water_inside():
    ctx = CTX
    assert ctx.inside_dome(0, 5, 0) and not ctx.inside_dome(100, 5, 100)
    water = fn_feature('pool', (2, 5, 2, 3, 6, 3), lambda v, c: v.fill(2, 5, 2, 3, 6, 3, 'water'), 40)
    iss = of(run([water], [(0, 0)], 'water_inside'), 'water_inside')
    assert len(iss) == 1 and iss[0].count == 8 and iss[0].severity == 'error' and iss[0].pos[1] == 5, iss
    # clean: nothing placed
    assert not of(run([], [(0, 0)], 'water_inside'), 'water_inside')
    # water outside the dome is fine
    out = fn_feature('sea', (100, 5, 100, 101, 5, 101), lambda v, c: v.fill(100, 5, 100, 101, 5, 101, 'water'))
    assert not of(run([out], [(3, 3)], 'water_inside'), 'water_inside')
    # decorative lagoon with water_ok is exempt
    ok = fn_feature('lagoon', (2, 5, 2, 3, 6, 3), lambda v, c: v.fill(2, 5, 2, 3, 6, 3, 'water'), 40,
                    meta={'water_ok': True})
    assert not of(run([ok], [(0, 0)], 'water_inside'), 'water_inside')


# ------------------------------------------------------------------------------------------------ leaks
def sealed_box(x1, y1, z1, x2, y2, z2):
    """Quartz hollow box with explicit air inside (a dry pocket under water)."""
    def b(v, c):
        v.fill(x1, y1, z1, x2, y2, z2, 'quartz_block')
        v.fill(x1 + 1, y1 + 1, z1 + 1, x2 - 1, y2 - 1, z2 - 1, 'air')
    return b


def test_leaks_inside_dome_and_pockets():
    ctx = CTX
    # clean: a sealed dry pocket in the sea outside the dome is NOT a leak
    pocket = fn_feature('pocket', (100, 1, 100, 108, 8, 108), sealed_box(100, 1, 100, 108, 8, 108))
    assert not of(run([pocket], [(3, 3)], 'leaks'), 'leaks')
    # planted: open one wall cell -> water touches the explicit air
    def holed(v, c):
        sealed_box(100, 1, 100, 108, 8, 108)(v, c)
        v.set(104, 4, 100, 'water')          # replaces a wall cell with water: wet cell next to inner air
    iss = of(run([fn_feature('pocket2', (100, 1, 100, 108, 8, 108), holed)], [(3, 3)], 'leaks'), 'leaks')
    assert iss and iss[0].severity == 'error' and iss[0].count >= 1, iss
    # planted: a waterlogged stair inside the dome
    wl = fn_feature('wl', (5, 3, 5, 5, 3, 5), lambda v, c: v.set(5, 3, 5, 'quartz_stairs[facing=north,waterlogged=true]'))
    iss = of(run([wl], [(0, 0)], 'leaks'), 'leaks')
    assert any(i.message == 'waterlogged block inside the dome' and i.count == 1 and i.pos == (5, 3, 5)
               for i in iss), iss
    # the same stair outside the dome, in the sea, is fine
    wl2 = fn_feature('wl2', (130, 3, 130, 130, 3, 130),
                     lambda v, c: v.set(130, 3, 130, 'quartz_stairs[facing=north,waterlogged=true]'))
    assert not of(run([wl2], [(4, 4)], 'leaks'), 'leaks')
    # water inside the dome next to air is a leak with count and <= 20 positions
    pool = fn_feature('pool', (2, 5, 2, 9, 5, 9), lambda v, c: v.fill(2, 5, 2, 9, 5, 9, 'water'))
    iss = of(run([pool], [(0, 0)], 'leaks'), 'leaks')
    assert iss and iss[0].count > 20 and len(iss[0].positions) == 20, iss
    # a torch (non-full, dry inside the dome) next to water, and a 'light' block in the open sea (no leak)
    lt = fn_feature('lt', (130, 3, 130, 130, 3, 130), lambda v, c: v.set(130, 3, 130, 'light[level=15]'))
    assert not of(run([lt], [(4, 4)], 'leaks'), 'leaks')
    tw = fn_feature('tw', (5, 3, 5, 5, 3, 6), lambda v, c: (v.set(5, 3, 5, 'torch'), v.set(5, 3, 6, 'water')))
    assert of(run([tw], [(0, 0)], 'leaks'), 'leaks')
    # water_ok lagoon: surface water next to air is allowed inside its bbox
    lag = fn_feature('lagoon', (2, 4, 2, 9, 5, 9), lambda v, c: v.fill(2, 4, 2, 9, 5, 9, 'water'), 40,
                     meta={'water_ok': True})
    S = dome_shell(ctx)
    assert not of(run([S], [(0, 0)], 'leaks'), 'leaks')          # a tight shell: nothing leaks by itself
    assert of(run([S, fn_feature('pool2', (2, 4, 2, 9, 5, 9), lambda v, c: v.fill(2, 4, 2, 9, 5, 9, 'water'))],
               [(0, 0)], 'leaks'), 'leaks')
    assert not of(run([S, lag], [(0, 0)], 'leaks'), 'leaks')


def test_leaks_cross_tile():
    ctx = CTX
    A, B = (3, 3), (4, 3)

    def pocket(v, c):                                           # open to the east: air at x=127 faces water at 128
        v.fill(124, 4, 109, 127, 6, 111, 'quartz_block')
        v.fill(125, 5, 110, 127, 5, 110, 'air')                 # inner air up to the very last column of tile A
    f = fn_feature('pocketA', (124, 4, 109, 127, 6, 111), pocket)
    alone = of(run([f], [A], 'leaks'), 'leaks')
    assert not [i for i in alone if 'border' in i.message]
    # tile A alone: air at x=127 has water... no: x=128 is outside tile A, so no in-tile leak
    assert not alone, alone
    both = of(run([f], [A, B], 'leaks'), 'leaks')
    assert len(both) == 1 and 'across a tile border' in both[0].message, both
    assert both[0].count == 1 and both[0].pos == (127, 5, 110), both
    # clean twin: wall off the last column (quartz at x=127) -> no leak across the border
    def closed(v, c):
        pocket(v, c)
        v.set(127, 5, 110, 'quartz_block')
    g = fn_feature('pocketA', (124, 4, 109, 127, 6, 111), closed)
    assert not of(run([g], [A, B], 'leaks'), 'leaks')
    # and the other direction / the z border: dry at first column of B next to water at last column of A
    def pocket_b(v, c):
        v.fill(128, 4, 109, 131, 6, 111, 'quartz_block')
        v.fill(128, 5, 110, 130, 5, 110, 'air')
    h = fn_feature('pocketB', (128, 4, 109, 131, 6, 111), pocket_b)
    both = of(run([h], [A, B], 'leaks'), 'leaks')
    assert len(both) == 1 and both[0].pos == (127, 5, 110), both
    # z border: tiles (3,3) and (3,4)
    def pocket_z(v, c):
        v.fill(110, 4, 124, 112, 6, 127, 'quartz_block')
        v.fill(111, 5, 125, 111, 5, 127, 'air')
    z = fn_feature('pocketZ', (110, 4, 124, 112, 6, 127), pocket_z)
    both = of(run([z], [A, (3, 4)], 'leaks'), 'leaks')
    assert len(both) == 1 and both[0].pos == (111, 5, 127), both


# ------------------------------------------------------------------------------------------------ mask
def test_mask():
    ctx = CTX
    # clean tile entirely inside / entirely outside the work area, built by the real builder
    assert not of(run([], [(0, 0)], 'mask'), 'mask')
    assert not of(run([], [(7, 0)], 'mask'), 'mask')        # straddles the area edge (radius 240)
    assert ctx.in_area(230, 0) and not ctx.in_area(250, 0)
    # planted by hand: a stone cell outside the area, and a structure_void inside it
    vol = one_tile_vol(ctx, 7, 0, [])
    vol.set(250, 10, 0, 'stone')
    vol.set(230, 10, 0, 'structure_void')
    iss = of(runner.check_tile(ctx, 7, 0, [], V.resolve_checks('mask'), vol=vol)[0], 'mask')
    msgs = {i.message: i for i in iss}
    assert msgs['non-void cell outside ctx.in_area'].pos == (250, 10, 0)
    assert msgs['structure_void cell inside ctx.in_area'].pos == (230, 10, 0)
    # an all-void tile far outside is clean
    vol = one_tile_vol(ctx, 12, 12, [])
    assert not runner.check_tile(ctx, 12, 12, [], V.resolve_checks('mask'), vol=vol)[0]


# ------------------------------------------------------------------------------------------------ dome_geometry
def test_dome_geometry():
    ctx = CTX
    tiles = all_tiles_of_dome(ctx)
    shell = dome_shell(ctx)
    rep = V.validate(ctx, tiles, [shell], 'dome_geometry', jobs=1)
    errs = of(rep['issues'], 'dome_geometry', 'error')
    assert not errs, errs
    assert any(i.severity == 'info' and 'dome fit' in i.message for i in rep['issues']), rep['issues']
    # a hole
    ys = [y for y in range(0, 31) if (ctx.dome_R - ctx.dome_thickness) ** 2 <= (y - ctx.dome_cy) ** 2 + 100 < ctx.dome_R ** 2]
    hole = (10, ys[0], 0)
    rep = V.validate(ctx, tiles, [dome_shell(ctx, hole=hole)], 'dome_geometry', jobs=1)
    errs = of(rep['issues'], 'dome_geometry', 'error')
    assert len(errs) == 1 and errs[0].count == 1 and errs[0].pos == hole, errs
    # shell built 1.5 blocks too big: radius deviates > 0.5
    rep = V.validate(ctx, tiles, [dome_shell(ctx, radius_shift=1.5)], 'dome_geometry', jobs=1)
    errs = of(rep['issues'], 'dome_geometry', 'error')
    assert errs, rep['issues']
    # sub-tolerance shift (0.3) passes
    rep = V.validate(ctx, tiles, [dome_shell(ctx, radius_shift=0.3)], 'dome_geometry', jobs=1)
    assert not [i for i in rep['issues'] if i.severity == 'error' and 'hole' not in i.message]


# ------------------------------------------------------------------------------------------------ clearance
def test_clearance():
    ctx = CTX
    top = ctx.max_building_top(2, 2)                      # lowest limit under the tower footprint corners
    assert top > 5
    def tower(h, name='tower', layer=40, meta=None):
        return fn_feature(name, (-2, 1, -2, 2, h, 2), lambda v, c: v.fill(-2, 1, -2, 2, h, 2, 'quartz_block'),
                          layer, meta)
    ok = tower(int(top))
    assert not of(run([ok], [(-1, -1), (0, -1), (-1, 0), (0, 0)], 'clearance'), 'clearance')
    too_tall = tower(int(top) + 3)
    iss = of(run([too_tall], [(-1, -1), (0, -1), (-1, 0), (0, 0)], 'clearance'), 'clearance')
    err = of(iss, 'clearance', 'error')
    assert len(err) == 4 and all(e.pos[1] > top and 'tower' in e.message for e in err) and sum(e.count for e in err) > 0, iss
    assert of(iss, 'clearance', 'warn')                         # meta-based bbox warning
    # exempt
    ex = tower(int(top) + 3, meta={'clearance_ok': True})
    assert not of(run([ex], [(0, 0)], 'clearance'), 'clearance')
    # layers below 30 (the dome shell, layer 20) are not subject to the rule
    assert not of(run([dome_shell(ctx)], all_tiles_of_dome(ctx), 'clearance'), 'clearance')
    # margin option
    assert not of(run([too_tall], [(-1, -1), (0, -1), (-1, 0), (0, 0)], 'clearance', clearance_margin=5), 'clearance',
                  'error')


# ------------------------------------------------------------------------------------------------ light
def lit_room(x1, z1, size=7, lamp=None, y=1):
    """Sealed room outside the dome (explicit air inside, quartz walls, sand floor from the seabed)."""
    x2, z2 = x1 + size - 1, z1 + size - 1

    def build(v, c):
        v.fill(x1, y - 1, z1, x2, y + 3, z2, 'quartz_block')
        v.fill(x1 + 1, y, z1 + 1, x2 - 1, y + 2, z2 - 1, 'air')
        if lamp: v.set(*lamp[:3], lamp[3])
    return fn_feature('room', (x1, y - 1, z1, x2, y + 3, z2), build)


def test_light_coverage():
    ctx = CTX
    dark = lit_room(100, 100)
    iss = of(run([dark], [(3, 3)], 'light_coverage'), 'light_coverage')
    assert len(iss) == 1 and iss[0].severity == 'warn' and iss[0].count == 25, iss      # 5x5 interior
    for lamp in ((103, 2, 103, 'light[level=15]'), (103, 3, 103, 'sea_lantern'), (101, 1, 101, 'torch')):
        # a torch (14) in the corner of a 5x5 room: farthest cell is 8 away -> lit
        r = lit_room(100, 100, lamp=lamp)
        assert not of(run([r], [(3, 3)], 'light_coverage'), 'light_coverage'), lamp
    # light level 3 reaches only 2 blocks -> far corner stays dark
    r = lit_room(100, 100, lamp=(103, 1, 103, 'light[level=3]'))
    iss = of(run([r], [(3, 3)], 'light_coverage'), 'light_coverage')
    assert len(iss) == 1 and 0 < iss[0].count < 25
    # opaque walls stop light: lamp in an adjacent sealed room does not light this one
    two = [lit_room(100, 100), lit_room(108, 100, lamp=(111, 1, 103, 'light[level=15]'))]
    iss = of(run(two, [(3, 3)], 'light_coverage'), 'light_coverage')
    assert len(iss) == 1 and iss[0].count == 25, iss
    # the room straddling the tile border (x 124..130): lamp is in tile B, dark cells in tile A are lit via the halo
    room = lit_room(124, 100, lamp=(129, 1, 103, 'light[level=15]'))
    assert not of(run([room], [(3, 3), (4, 3)], 'light_coverage', light_halo=True), 'light_coverage')
    assert not of(run([room], [(3, 3)], 'light_coverage', light_halo=True), 'light_coverage')
    # same room with a far lamp (outside the 14-block reach) really is dark
    far = lit_room(124, 100, size=7)
    assert of(run([far], [(3, 3), (4, 3)], 'light_coverage', light_halo=True), 'light_coverage')
    # without the halo the near-edge cells are skipped (conservative)
    assert not of(run([room], [(3, 3)], 'light_coverage', light_halo=False), 'light_coverage')
    # inside the dome: the floor is dry, dark, walkable
    iss = of(run([], [(0, 0)], 'light_coverage'), 'light_coverage')
    assert iss and iss[0].count > 100


# ------------------------------------------------------------------------------------------------ overlaps
def test_overlaps():
    ctx = CTX
    def b(name, x1, z1, x2, z2, layer=40, meta=None):
        return FnFeature(name, (x1, 1, z1, x2, 10, z2), lambda v, c: None, layer, meta)
    a, c = b('a', 0, 0, 20, 20), b('b', 15, 15, 40, 40)
    res = V.CHECKS['overlaps'].reduce([], ctx, [a, c])
    assert len(res) == 1 and res[0].severity == 'error' and res[0].count == 36, res        # 6x6 columns
    assert not V.CHECKS['overlaps'].reduce([], ctx, [a, b('far', 30, 30, 50, 50)])         # clean
    assert not V.CHECKS['overlaps'].reduce([], ctx, [a, b('b', 15, 15, 40, 40, meta={'allow_overlap': True})])
    assert not V.CHECKS['overlaps'].reduce([], ctx, [a, b('b', 15, 15, 40, 40, meta={'allow_overlap': ['a']})])
    assert V.CHECKS['overlaps'].reduce([], ctx, [a, b('b', 15, 15, 40, 40, meta={'allow_overlap': ['zzz']})])
    # layers other than 40/50 are ignored
    assert not V.CHECKS['overlaps'].reduce([], ctx, [a, b('b', 15, 15, 40, 40, layer=70)])
    # via the runner: features reach reduce even though the tile checks return nothing
    iss = run([a, c], [(0, 0), (1, 1)], 'overlaps')
    assert len(of(iss, 'overlaps')) == 1


# ------------------------------------------------------------------------------------------------ connectivity
def test_connectivity_basic():
    """The full walk-graph check lives in test_connectivity.py; here only that the registered check runs: with no
    deck features the tower plaza seed has no standable node at deck level (the city floor is y 0)."""
    iss = run([], [(-1, -1), (0, -1), (-1, 0), (0, 0)], 'connectivity')
    c = of(iss, 'connectivity')
    assert any(i.severity == 'info' and 'main walk component' in i.message for i in c), c
    assert any(i.severity == 'error' and 'Tower Plaza' in i.message for i in c), c


# ------------------------------------------------------------------------------------------------ CLI / runner
def test_cli_and_json():
    from atlantis import orchestrate
    out = os.path.join(tempfile.mkdtemp(), 'v.json')
    rc = orchestrate.main(['validate', '--tiles', '0,0;1,0', '--checks', 'block_states,mask', '--jobs', '1',
                           '--json', out])
    assert rc == 0, rc
    d = json.load(open(out))
    assert d['tiles'] == 2 and d['errors'] == 0 and {s['check'] for s in d['summary']} == {'block_states', 'mask'}


def test_timing_report():
    """Informational: per-tile validation time on a busy synthetic tile (not an assertion on speed)."""
    ctx = CTX
    def busy(v, c):
        for k in range(0, 28, 7):
            v.fill(k, 0, 4, k + 4, 14, 24, 'quartz_block')
            v.fill(k + 1, 1, 5, k + 3, 13, 23, 'air')
            v.fill(k + 1, 4, 5, k + 3, 4, 23, 'quartz_slab[type=bottom]')
            v.set(k + 2, 3, 6, 'sea_lantern')
    f = [dome_shell(ctx), fn_feature('busy', (0, 0, 0, 31, 14, 31), busy)]
    t0 = time.perf_counter()
    rep = V.validate(ctx, [(0, 0)], f, 'all', jobs=1)
    dt = time.perf_counter() - t0
    print(f'    busy dome tile, all checks: {dt * 1000:.0f} ms')
    assert dt < 3 * TF


if __name__ == '__main__':
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for name, fn in tests:
        t0 = time.time()
        fn()
        print(f'ok  {name}  ({time.time() - t0:.2f}s)')
    print(f'{len(tests)} tests passed')
