#!/usr/bin/env python3
"""Central Tower (tower.py) tests, plain asserts.  Run from the project root: python3 tests/test_tower.py"""
import json, math, os, sys, time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TF = float(__import__('os').environ.get('ATLANTIS_TIME_FACTOR', '1'))   # slow CI runners: > 1

from atlantis import config, tower, tower_spec as S, blocks as B, export_tiles as E, validate as V
from atlantis.validate import walk as W
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = [f for f in load_generators() if not f.name.startswith("infill_")]   # box validation clips district infill
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
BOX = (-40, 0, -40, 40, 110, 40)
_VOL = [None]


def volume():
    if _VOL[0] is None:
        _VOL[0] = E.build_box(ctx, BOX, [tower.FEATURE])
    return _VOL[0]


def is_air(b):
    return b is None or b.split('[')[0] in ('air', 'cave_air', 'light')


def solid(b):
    if is_air(b): return False
    n = b.split('[')[0]
    return n not in ('bubble_column', 'water') and not n.endswith(('_carpet', '_door'))


def test_registered():
    assert any(f.name == 'tower_central' for f in FEATS)
    m = tower.FEATURE.meta
    assert m['entrances'] == [tuple(e) for e in S.entrances()] and m['interior'] is True
    assert m['roof_y'] == S.TIP_Y and len(m['interior_box']) == 6
    assert tower.FEATURE.layer == 50
    bb = tower.FEATURE.bbox
    assert max(abs(bb[0]), abs(bb[2]), abs(bb[3]), abs(bb[5])) <= 37 and bb[4] <= S.TIP_Y


def test_determinism():
    a = E.build_box(ctx, (-32, 0, -32, -1, 110, -1), [tower.FEATURE])
    b = E.build_box(ctx, (-32, 0, -32, -1, 110, -1), [tower.FEATURE])
    assert list(a.items()) == list(b.items())
    big = volume()
    c = E.build_box(ctx, BOX, [tower.FEATURE])
    assert list(big.items()) == list(c.items())


def test_blocks_valid():
    v = volume()
    bad = set()
    for _, _, _, b in v.items():
        name = b.split('[')[0]
        if name not in VALID or not B.is_valid_state(b): bad.add(b)
    assert not bad, sorted(bad)[:10]


def sector_radius(v, y, plan, half=3.0):
    """Outermost solid cell radius within +-half degrees of a plan angle at height y."""
    best = 0.0
    for x in range(-40, 41):
        for z in range(-40, 41):
            if not solid(v.get(x, y, z)): continue
            if tower.adiff(tower.plan_of(x, z), plan) <= half: best = max(best, math.hypot(x, z))
    return best


def test_profile_vs_spec():
    v = volume()
    # shaft, crown glass band and upper shaft, measured away from fins, balconies and doors
    for y in list(range(18, 62, 5)) + [73, 75, 79, 81] + [86, 88, 91, 93]:
        r = sector_radius(v, y, 15.0)
        spec = S.outer_r(y)
        assert abs(r - spec) <= 1.0, (y, r, spec)
    # podium: wall radius 34 on the first tier, then the three terrace roofs (ring from the next tier's wall out)
    assert abs(sector_radius(v, 7, 13.0, 1.5) - 34) <= 1.5
    for y, rin, rout in ((8, 29, 34), (12, 25, 30), (16, 19, 26)):
        for a in (22.0, 38.0, 100.0):
            for r in (rin + 2, rout - 1):
                x, z = tower.cell_at(r, a)
                assert solid(v.get(x, y, z)), (y, a, r)
            x, z = tower.cell_at(rin - 3, a)
            assert not solid(v.get(x, y, z)), (y, a)
    # spire tip
    assert v.get(0, S.TIP_Y, 0).startswith('end_rod') and is_air(v.get(0, S.TIP_Y + 1, 0))
    assert max(y for x, y, z, b in [(0, yy, 0, v.get(0, yy, 0)) for yy in range(0, 111)] if solid(b)) == S.TIP_Y


def test_floors_and_mezzanine():
    v = volume()
    for yf, name, _ in S.FLOORS:
        if yf in (5, 95): continue
        ru = S.usable_r(yf + 1)
        # slab present out to the usable radius at an angle without lights/stairs
        n = m = 0
        for a in range(0, 360, 6):
            for r in (8, 11, int(ru) - 1):
                x, z = tower.cell_at(r, a)
                if yf == 17 and math.hypot(x, z) <= tower.MEZZ_HOLE_R + 1.5: continue
                n += 1
                if solid(v.get(x, yf, z)): m += 1
        assert m / n > 0.95, (yf, m, n)
    # mezzanine hole of radius 10 is open and railed
    for x in range(-10, 11):
        for z in range(-10, 11):
            if math.hypot(x, z) <= tower.MEZZ_HOLE_R and math.hypot(x, z) > tower.CORE_R:
                b = v.get(x, 17, z)
                # only the gangway may be solid in the hole
                if b is not None and solid(b):
                    ca, sa = math.cos(math.radians(tower.BRIDGE_GEOM)), math.sin(math.radians(tower.BRIDGE_GEOM))
                    assert abs(-x * sa + z * ca) <= 1.6, (x, z, b)
    cx, cz = tower.cell_at(14, 200)
    assert solid(v.get(cx, 17, cz))
    # ground floor disc
    assert all(solid(v.get(x, 5, z)) for x, z in ((20, 0), (-30, 5), (0, 31), (0, -31)))


def test_core_stair_continuity():
    """BFS up the spiral staircase: from the ground floor (surface y 6) to the summit (surface y 96), touching the
    walking surface of every floor on the way."""
    v = volume()
    cells = tower.stair_cells()
    surf = {}
    for x, z in cells:
        for y in range(5, 97):
            b = v.get(x, y, z)
            if is_air(b): continue
            if b.endswith('[type=bottom]') and 'slab' in b: h = y + 0.5
            elif 'slab' in b: h = y + 1.0
            else: h = y + 1.0
            if all(is_air(v.get(x, y + k, z)) for k in (1, 2)):
                surf.setdefault((x, z), []).append(h)
    start = [(x, z, h) for (x, z), hs in surf.items() for h in hs if h == 6.0]
    assert start
    seen, dq = set(start), deque(start)
    while dq:
        x, z, h = dq.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for h2 in surf.get((x + dx, z + dz), ()):
                if 0 <= h2 - h <= 1.0 and (x + dx, z + dz, h2) not in seen:
                    seen.add((x + dx, z + dz, h2)); dq.append((x + dx, z + dz, h2))
    hs = {h for _, _, h in seen}
    assert max(hs) == 96.0, max(hs)
    for yf, name, _ in S.FLOORS:
        assert yf + 1.0 in hs, f'stair misses floor {yf}'
    # lift: soul sand under a water column through all floors, doors on every lift stop
    assert v.get(0, 5, 0) == 'soul_sand' and all(v.get(0, y, 0).startswith('bubble_column') for y in range(6, 96))
    for yf, _, _ in S.FLOORS:
        if yf < 95: assert 'warped_door' in v.get(1, yf + 1, 0), yf


def test_portals_open():
    v = volume()
    for i, a in enumerate(S.ENTRANCE_PLAN_ANGLES):
        cells = tower.portal_cells(i)
        assert len(cells) > 120
        for x, y, z in cells:
            assert not solid(v.get(x, y, z)), (i, x, y, z, v.get(x, y, z))
        ex, ey, ez = S.entrances()[i]
        assert solid(v.get(ex, ey - 1, ez)) and not solid(v.get(ex, ey, ez)) and not solid(v.get(ex, ey + 1, ez))
        # walk inwards along the portal axis from the entrance to r 20: floor at y 5, two free cells above
        sx, sz = tower.radial(a)
        for r in range(36, 19, -1):
            x, z = G_rnd(r * sx), G_rnd(r * sz)
            assert solid(v.get(x, 5, z)), (i, r)
            assert not solid(v.get(x, 6, z)) and not solid(v.get(x, 7, z)), (i, r, v.get(x, 6, z))
        width = len({(x, z) for x, y, z in cells if y == 6 and 32.5 <= x * sx + z * sz <= 33.5})
        assert 5 <= width <= 9


def G_rnd(v):
    return int(math.floor(v + 0.5))


def test_clearance_under_dome():
    v = volume()
    top = 0
    for x, y, z, b in v.items():
        if y < 3 or is_air(b) or b.startswith('water'): continue
        lim = ctx.max_building_top(x, z)
        assert y <= lim, (x, y, z, b, lim)
        top = max(top, y)
    assert top == S.TIP_Y
    assert ctx.max_building_top(0, 0) >= S.TIP_Y


def test_footprint_radius():
    v = volume()
    for x, y, z, b in v.items():
        if y >= 3 and not is_air(b) and not b.startswith('water'): assert math.hypot(x, z) <= 36.8, (x, y, z, b)


def test_seeded_floors_reachable():
    """Every floor, terrace, the crown deck and a balcony are in the main walk component (validator walk model)."""
    tiles = E.tiles_in_box(ctx, -64, -64, 63, 63)
    seeds = [W.default_seeds()[0]]

    def seed(name, x, z, y, r=2):
        h = 2 * (y + 1)
        seeds.append({'name': name, 'x': x, 'z': z, 'r': r, 'hmin': h - 1, 'hmax': h + 1})
    for yf, name, _ in S.FLOORS:
        rr = 15 if yf == 17 else (8 if yf == 95 else min(S.outer_r(yf) - 4, 14.5))
        a = math.radians(200)
        seed(f'floor{yf}', int(round(rr * math.sin(a))), int(round(-rr * math.cos(a))), yf)
    seed('terrace1', 20, -27, 8); seed('terrace2', -17, -24, 12); seed('terrace3', -16, 17, 16)
    seed('deck', -17, 3, 83)
    seed('balcony', int(24.6 * math.sin(math.radians(30))), int(-24.6 * math.cos(math.radians(30))), 71)
    rep = V.validate(ctx, tiles, FEATS, 'connectivity,accessibility', jobs=1, options={'walk_seeds': seeds})
    errs = [i for i in rep['issues'] if i.severity == 'error']
    assert not errs, [i.message for i in errs]
    from atlantis.validate import core
    core.OPTIONS['walk_seeds'] = None


def test_validation_clean():
    from atlantis.validate import core
    core.OPTIONS['walk_seeds'] = None
    tiles = E.tiles_in_box(ctx, -64, -64, 63, 63)
    rep = V.validate(ctx, tiles, FEATS, 'all', jobs=1)
    bad = [i for i in rep['issues'] if i.severity in ('error', 'warn')]
    assert not bad, [(i.check, i.message) for i in bad]


def test_timing():
    tower._OPS = None
    t0 = time.perf_counter()
    E.build_tile(ctx, 0, 0, [tower.FEATURE])
    first = time.perf_counter() - t0
    worst = 0.0
    for tx in (-2, -1, 0, 1):
        for tz in (-2, -1, 0, 1):
            t0 = time.perf_counter()
            E.build_tile(ctx, tx, tz, [tower.FEATURE])
            worst = max(worst, time.perf_counter() - t0)
    print(f'  first tile (incl. one-time tables) {first * 1000:.0f} ms, worst warm tile {worst * 1000:.0f} ms')
    assert first < 0.5 * TF and worst < 0.3 * TF


if __name__ == '__main__':
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            t0 = time.perf_counter()
            fn()
            n += 1
            print(f'ok  {name}  ({time.perf_counter() - t0:.2f}s)')
    print(f'{n} tests passed')
