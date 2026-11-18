#!/usr/bin/env python3
"""Terrain + foundation tests (plain asserts). Run from the project root:  python3 tests/test_terrain.py"""
import math, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, background, feature, terrain, foundation
from atlantis import export_tiles as E
from atlantis.volume import Volume

ctx = config.load()
EDGE = ctx.profile['edge_seabed_y'] - ctx.profile['floor_y']
MAXR = int(ctx.area_radius)


def test_registered():
    names = [f.name for f in feature.registry()]
    for m in ('atlantis.terrain', 'atlantis.foundation'):
        __import__(m)
    reg = feature.registry()
    forms = [f for f in reg if f.meta.get('formation')]
    assert 8 <= len(forms) <= 20, len(forms)
    assert any(f.meta.get('temporary_light') for f in reg)
    assert len({f.name for f in reg}) == len(reg)


def test_determinism():
    pts = [(x, z) for x in range(-790, 791, 97) for z in range(-790, 791, 89)]
    a = [terrain.seabed_height(ctx, x, z) for x, z in pts]
    b = [terrain.seabed_height(ctx, x, z) for x, z in pts]
    assert a == b
    assert [background.seabed_height(ctx, x, z) for x, z in pts] == a      # installed in background
    blocks = [background.background(ctx, x, y, z) for x, z in pts[:200] for y in (-3, 20, 60)]
    assert blocks == [background.background(ctx, x, y, z) for x, z in pts[:200] for y in (-3, 20, 60)]
    vol1 = E.build_tile(ctx, 20, 3, feature.registry())
    vol2 = E.build_tile(ctx, 20, 3, feature.registry())
    assert E.tile_hash(ctx, vol1) == E.tile_hash(ctx, vol2)


def test_profile():
    for a in range(0, 360, 7):
        c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
        for r in (0, 300, 599, 605):
            assert terrain.seabed_height(ctx, round(r * c), round(r * s)) == 0, (a, r)
        assert abs(terrain.seabed_height(ctx, round(800 * c), round(800 * s)) - EDGE) <= 3
        assert abs(terrain.seabed_height(ctx, round(795 * c), round(795 * s)) - EDGE) <= 4
        mid = terrain.seabed_height(ctx, round(700 * c), round(700 * s))
        assert 20 < mid < EDGE, mid                      # a real wall, not flat
    hs = [terrain.seabed_height(ctx, r, 0) for r in range(606, 801)]
    assert max(hs) <= EDGE + 6 and min(hs) >= 0


def test_continuity():
    H = terrain.seabed_height
    worst = 0
    step = 1
    for x in range(-MAXR, MAXR + 1):
        for z in range(-MAXR, MAXR + 1):
            q = x * x + z * z
            if q > MAXR * MAXR or q < 590 * 590: continue
            h = H(ctx, x, z)
            for dx, dz in ((1, 0), (0, 1)):
                worst = max(worst, abs(h - H(ctx, x + dx, z + dz)))
    assert worst <= 6, worst
    return worst


def test_formations():
    forms = [f for f in feature.registry() if f.meta.get('formation')]
    for f in forms:
        cx, cz = (f.bbox[0] + f.bbox[3]) / 2, (f.bbox[2] + f.bbox[5]) / 2
        r = math.hypot(cx, cz)
        assert 615 < r < 790, (f.name, r)
        assert f.bbox[4] <= 111, f.name
        assert max(abs(f.bbox[0]), abs(f.bbox[3])) <= MAXR and max(abs(f.bbox[2]), abs(f.bbox[5])) <= MAXR
        assert f.bbox[3] - f.bbox[0] <= 60, f.name                      # tight bbox
        # actually built: every written cell stays below 113 and the formation puts stone above the seabed
        vol = Volume(f.bbox[0], ctx.y_min, f.bbox[2], f.bbox[3], ctx.y_max, f.bbox[5])
        f.build(vol, ctx)
        top, above = -99, 0
        for x, y, z, b in ((x, y, z, b) for x, y, z, b in vol.items()):
            top = max(top, y)
            if y > terrain.seabed_height(ctx, x, z) + 2: above += 1
        assert top <= 111, (f.name, top)
        assert above > 30, (f.name, above)
        # a tile-sized sub-volume gets exactly the same cells (clip-correct)
        sub = Volume(f.bbox[0], ctx.y_min, f.bbox[2], f.bbox[0] + 15, ctx.y_max, f.bbox[2] + 15)
        f.build(sub, ctx)
        for x, y, z, b in sub.items():
            assert vol.get(x, y, z) == b


def _coverage(x1, z1, x2, z2):
    """Every floor cell (inside the dome at y=1) in the rectangle must be within Manhattan distance 14 of a light."""
    pts = foundation.light_points(ctx, x1 - 15, z1 - 15, x2 + 15, z2 + 15)
    n = 0
    for x in range(x1, x2 + 1):
        for z in range(z1, z2 + 1):
            if not ctx.inside_dome(x, 1, z): continue
            n += 1
            assert any(abs(px - x) + abs(pz - z) <= 14 for px, pz in pts), (x, z)
    return n


def test_light_patches():
    assert _coverage(-32, -32, 31, 31) == 64 * 64
    # next to the dome edge (east, north-east and a diagonal)
    for cx, cz in ((560, 0), (0, -560), (400, 400), (-420, 410)):
        assert _coverage(cx - 32, cz - 32, cx + 31, cz + 31) > 500
    assert _coverage(570, -32, 633, 31) > 500                               # straddles the edge


def test_light_rules_and_density():
    pts = foundation.light_points(ctx, -700, -700, 700, 700)
    for x, z in pts:
        assert ctx.inside_dome(x, 1, z)
        d = math.sqrt(x * x + (1 - ctx.dome_cy) ** 2 + z * z)
        assert ctx.dome_R - ctx.dome_thickness - d >= 2, (x, z)
    # sparser than a square grid of spacing 15 (225 cells per light) over the dome floor
    area = math.pi * 600 * 600
    assert len(pts) < area / 225 * 0.8, len(pts)


def test_full_floor_coverage():
    """Whole-floor proof with an L1 distance transform."""
    o, N = 610, 1221
    INF = 10 ** 6
    d = [[INF] * N for _ in range(N)]
    for x, z in foundation.light_points(ctx, -o, -o, o, o): d[z + o][x + o] = 0
    for row in d:
        for x in range(1, N):
            if row[x - 1] + 1 < row[x]: row[x] = row[x - 1] + 1
        for x in range(N - 2, -1, -1):
            if row[x + 1] + 1 < row[x]: row[x] = row[x + 1] + 1
    for z in range(1, N):
        a, b = d[z - 1], d[z]
        for x in range(N):
            if a[x] + 1 < b[x]: b[x] = a[x] + 1
    for z in range(N - 2, -1, -1):
        a, b = d[z + 1], d[z]
        for x in range(N):
            if a[x] + 1 < b[x]: b[x] = a[x] + 1
    worst = 0
    for z in range(-o, o + 1):
        for x in range(-o, o + 1):
            if ctx.inside_dome(x, 1, z):
                worst = max(worst, d[z + o][x + o])
    assert worst <= 14, worst
    return worst


def test_foundation_blocks():
    vol = E.build_tile(ctx, 0, 0, feature.registry())
    assert vol.get(5, 0, 5) == 'smooth_stone' and vol.get(5, -3, 5) in ('deepslate', 'stone', 'tuff')
    assert vol.get(1, 0, 0) == 'smooth_stone'
    vol = E.build_tile(ctx, 1, 0, feature.registry())                       # x 32..63: ring at r=50
    assert vol.get(50, 0, 0) == 'polished_andesite'
    t = ctx.tile
    vol = E.build_tile(ctx, 18, 0, feature.registry())                      # x 576..607: the dark band
    assert vol.get(600, 0, 0) in ('polished_deepslate', 'dark_prismarine')
    assert vol.get(600, -4, 0) in ('polished_deepslate', 'dark_prismarine')
    assert vol.get(590, 0, 0) in ('smooth_stone', 'polished_andesite')
    assert vol.get(590, ctx.y_min, 0) == 'bedrock'
    # light blocks are only at y=1
    n = sum(1 for x, y, z, b in vol.items() if b == 'light[level=15]')
    assert n > 0 and all(y == 1 for x, y, z, b in vol.items() if b == 'light[level=15]')


def _time_tile(tx, tz):
    feats = feature.registry()
    t0 = time.time()
    E.build_tile(ctx, tx, tz, feats)
    return time.time() - t0


def test_timing():
    foundation.light_points(ctx, 0, 0, 40, 40)                              # warm the edge-point cache like a worker
    res = {'centre': _time_tile(0, 0), 'wall': _time_tile(21, 0), 'wall_rock': _time_tile(21, 1),
           'dome_edge': _time_tile(18, 0), 'edge': _time_tile(24, 0)}
    for k, v in res.items():
        print(f'  tile {k}: {v:.3f}s')
        assert v < 1.0, (k, v)                                           # hard limit; target < 0.3 s
    return res


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            t0 = time.time()
            r = fn()
            print(f'ok  {name}  ({time.time() - t0:.1f}s)' + (f'  -> {r}' if r is not None and name != 'test_timing' else ''))
    print('all terrain tests passed')
