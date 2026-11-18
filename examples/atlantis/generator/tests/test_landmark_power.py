#!/usr/bin/env python3
"""District E landmarks (landmark_power.py) tests, plain asserts.  Run from the project root:
python3 tests/test_landmark_power.py"""
import json, math, os, sys, time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, landmark_power as L, planner as PL, blocks as B, export_tiles as E, validate as V
from atlantis import roads as R
from atlantis.orchestrate import load_generators

ctx = config.load()
ALL = [f for f in load_generators() if not f.name.startswith("infill_")]   # box validation clips district infill
MINE = [f for f in ALL if f.name.startswith('power_')]
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
_CACHE = {}


def mine_volume(f):
    """Volume over f's bbox holding only the power_* features that touch it (their own cells)."""
    v = _CACHE.get(f.name)
    if v is None:
        v = _CACHE[f.name] = E.build_box(ctx, f.bbox, [g for g in MINE if g.name == f.name])
    return v


def is_air(b):
    return b is None or b.split('[')[0] in ('air', 'cave_air', 'light')


def own_cells(f):
    """(x, y, z, block) of the cells the feature itself wrote (no background fill)."""
    from atlantis.volume import Volume
    v = Volume(*f.bbox)
    f.build(v, ctx)
    return list(v.items())


def test_registered():
    names = {f.name for f in MINE}
    assert {'power_crystal_hall', 'power_workshop', 'power_tower_a', 'power_tower_b'} <= names
    assert any(n.startswith('power_tunnel_main') for n in names) and any(n.startswith('power_conduit') for n in names)
    for f in MINE:
        assert f.layer == 50, f.name
        assert f.meta['district'] == 'E'
    for n in ('power_crystal_hall', 'power_workshop', 'power_tower_a', 'power_tower_b'):
        f = [g for g in MINE if g.name == n][0]
        assert f.meta['entrances'] and f.meta['interior'] is True and len(f.meta['interior_box']) == 6, n
    # only the tunnel / conduit strips may overlap other footprints
    for f in MINE:
        if f.meta.get('allow_overlap'): assert f.name.startswith(('power_tunnel', 'power_conduit')), f.name
    # the non-overlapping buildings really are disjoint from each other in x/z
    bl = [f for f in MINE if not f.meta.get('allow_overlap')]
    for i, a in enumerate(bl):
        for b in bl[i + 1:]:
            assert a.bbox[3] < b.bbox[0] or b.bbox[3] < a.bbox[0] or a.bbox[5] < b.bbox[2] or b.bbox[5] < a.bbox[2], \
                (a.name, b.name)


def test_district_and_radius():
    for f in MINE:
        n = 0
        for x, y, z, b in own_cells(f):
            n += 1
            assert PL.district_at(x, z) == 'E', (f.name, x, y, z, b)
            r = math.hypot(x, z)
            assert 305 <= r <= 495, (f.name, x, z, r)
            if f.name.startswith('power_tunnel'): assert r >= 308, (f.name, r)
        assert n > 0, f.name


def test_height_cap():
    for f in MINE:
        for x, y, z, b in own_cells(f):
            if y < 3: continue
            assert y <= ctx.max_building_top(x, z), (f.name, x, y, z, b, ctx.max_building_top(x, z))
        bb = f.bbox
        for x in (bb[0], bb[3]):
            for z in (bb[2], bb[5]):
                if f.name.startswith(('power_crystal', 'power_work', 'power_tower')):
                    assert bb[4] <= ctx.max_building_top(x, z), (f.name, bb[4], ctx.max_building_top(x, z))


def test_blocks_valid():
    seen = set()
    for f in MINE:
        for _, _, _, b in own_cells(f): seen.add(b)
    for b in seen:
        assert b.split('[')[0] in VALID and B.is_valid_state(b), b


def test_determinism():
    for f in MINE[:6]:
        a = own_cells(f); b = own_cells(f)
        assert a == b, f.name
    # tile independence: building a box in two halves gives the same cells as one big build
    f = [g for g in MINE if g.name == 'power_crystal_hall'][0]
    bb = f.bbox
    mid = (bb[0] + bb[3]) // 2
    from atlantis.volume import Volume
    full = Volume(*bb); f.build(full, ctx)
    left = Volume(bb[0], bb[1], bb[2], mid, bb[4], bb[5]); f.build(left, ctx)
    right = Volume(mid + 1, bb[1], bb[2], bb[3], bb[4], bb[5]); f.build(right, ctx)
    cells = dict(((x, y, z), b) for x, y, z, b in full.items())
    got = dict(((x, y, z), b) for x, y, z, b in list(left.items()) + list(right.items()))
    assert cells == got


def test_conduits_off_streets():
    for f in MINE:
        if not f.name.startswith('power_conduit'): continue
        for x, y, z, b in own_cells(f):
            assert not R.is_street(x, z), (f.name, x, z)
            assert y in (3, 4)


def test_entrances_standable():
    box_f = [f for f in MINE if f.meta.get('entrances') and not f.name.startswith('power_tunnel')]
    for f in box_f:
        v = E.build_box(ctx, f.bbox, [g for g in ALL if g.layer <= 50 and g.name != 'tower_central'])
        for (x, y, z) in f.meta['entrances']:
            assert not is_air(v.get(x, y - 1, z)) and v.get(x, y - 1, z) != 'water', (f.name, x, y, z)
            assert is_air(v.get(x, y, z)) and is_air(v.get(x, y + 1, z)), (f.name, x, y, z, v.get(x, y, z))


# ---------------------------------------------------------------- tunnel: enclosed and walkable
TBOX = (L.HX - 30, -8, L.WZ - 30, L.WX + 30, 8, L.HZ + 30)


def _tunnel_box():
    xs = [L.HX, L.WX, -400, -270]
    zs = [L.HZ, L.WZ, 150, 215]
    return (min(xs) - 30, -8, min(zs) - 30, max(xs) + 30, 8, max(zs) + 30)


_TV = [None]


def tunnel_volume():
    if _TV[0] is None:
        fs = [f for f in ALL if f.layer <= 50]
        _TV[0] = E.build_box(ctx, _tunnel_box(), fs)
    return _TV[0]


def tun_col(x, z):
    """Tunnel column (interior, lining) or stair shaft column."""
    if L.tunnel_kind(x, z): return True
    for xs, zs in (L.HALL_SHAFT, L.WS_SHAFT):
        if xs <= x <= xs + L.STAIR_N and zs - 2 <= z <= zs + 2: return True
    return False


def test_tunnel_enclosed():
    v = tunnel_volume()
    box = _tunnel_box()
    air = set()
    for x in range(box[0], box[3] + 1):
        for z in range(box[2], box[5] + 1):
            if not tun_col(x, z): continue
            for y in range(-5, 5):
                b = v.get(x, y, z)
                if b is not None and b.split('[')[0] in ('air', 'cave_air'): air.add((x, y, z))
    assert len(air) > 800, len(air)
    for (x, y, z) in air:
        assert -4 <= y <= 4, (x, y, z)
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            n = (x + dx, y + dy, z + dz)
            if n in air: continue
            b = v.get(*n)
            if n[1] > 4: continue                      # opens into a building above the deck (shaft top)
            assert b is not None and 'water' not in b and b.split('[')[0] not in ('air', 'cave_air', 'light'), (n, b)
    for x in range(box[0], box[3] + 1):
        for z in range(box[2], box[5] + 1):
            for y in range(-8, 1):
                b = v.get(x, y, z)
                if L.tunnel_kind(x, z) and b is not None: assert 'water' not in b, (x, y, z, b)
    # the tunnel is under the deck: ceiling and deck above are solid wherever the tunnel runs outside the shafts
    for x in range(box[0], box[3] + 1):
        for z in range(box[2], box[5] + 1):
            if L.tunnel_kind(x, z) == 'in' and v.get(x, 1, z) is not None and not is_air(v.get(x, 1, z)):
                assert not is_air(v.get(x, 0, z)), (x, z)


def _supports(v, x, z, ymin=-6, ymax=8):
    """[(h2, top_clear)]: standing places of column (x, z) in half blocks; needs 2 free cells above."""
    out = []
    for y in range(ymin, ymax):
        b = v.get(x, y, z)
        if b is None or is_air(b) or 'water' in b or b.endswith('_pane') or '_pane[' in b: continue
        if 'slab' in b and 'type=bottom' in b: h = 2 * y + 1
        elif 'stairs' in b and 'half=bottom' in b: h = 2 * y + 1
        elif 'carpet' in b: continue
        else: h = 2 * y + 2
        a1, a2 = v.get(x, y + 1, z), v.get(x, y + 2, z)
        if is_air(a1) and is_air(a2): out.append(h)
    return out


def test_tunnel_walkable():
    """BFS on foot (steps up to 0.5 free, 1 with a jump, drops <= 3) from the hall entrance down the stairs, through
    the tunnel to both ends and to the workshop landing; also back up to the workshop floor."""
    v = tunnel_volume()
    box = _tunnel_box()
    cols = {}

    def sup(x, z):
        k = (x, z)
        if k not in cols: cols[k] = _supports(v, x, z)
        return cols[k]
    ex, ey, ez = L.CrystalHall().meta['entrances'][0]
    start = (ex, ez, 2 * ey)
    assert 2 * ey in sup(ex, ez), sup(ex, ez)
    seen, dq = {start}, deque([start])
    while dq:
        x, z, h = dq.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x + dx, z + dz
            if not (box[0] <= nx <= box[3] and box[2] <= nz <= box[5]): continue
            for h2 in sup(nx, nz):
                d = h2 - h
                if -6 <= d <= 2 and (nx, nz, h2) not in seen:
                    seen.add((nx, nz, h2)); dq.append((nx, nz, h2))
    tun = {(x, z) for (x, z, h) in seen if h < 0 + 2 * 0 and h <= -6}      # standing at y <= -3 (floor -5 -> h -8)
    # landing rooms and both ends of the main tunnel reached
    for sh in (L.HALL_SHAFT, L.WS_SHAFT):
        xs, zs = sh
        assert any((x, zs, -8) in seen for x in range(xs - 3, xs + 1)), sh
    a_ends = {}
    for (x, z, h) in seen:
        if h == -8:
            a, c = L.ac(x, z)
            if abs(c) < 1.0: a_ends[round(a)] = 1
    assert min(a_ends) <= L.TUN_A0 + 2 and max(a_ends) >= L.TUN_A1 - 2, (min(a_ends), max(a_ends))
    # workshop floor reachable from the tunnel (the BFS covers it through the workshop stair)
    wx, wz = L.WX, L.WZ - 4
    assert any(z == wz and x == wx for (x, z, h) in seen if h == 10), 'workshop floor'
    # the whole interior of the tunnel (main axis cells) is reachable
    miss = 0; tot = 0
    for x in range(box[0], box[3] + 1):
        for z in range(box[2], box[5] + 1):
            if L.tunnel_kind(x, z) == 'in':
                tot += 1
                if (x, z, -8) not in seen: miss += 1
    assert tot > 300 and miss == 0, (tot, miss)


def test_tunnel_lit():
    """Every tunnel floor cell has a light source within Manhattan distance 14 through the tunnel air."""
    v = tunnel_volume()
    box = _tunnel_box()
    src = []
    air = set()
    for x in range(box[0], box[3] + 1):
        for z in range(box[2], box[5] + 1):
            if not tun_col(x, z): continue
            for y in range(-5, 5):
                b = v.get(x, y, z)
                if b is None: continue
                n = b.split('[')[0]
                if n in ('air', 'cave_air'): air.add((x, y, z))
                if n in ('sea_lantern', 'end_rod', 'light'): src.append((x, y, z))
    lit = {}
    dq = deque()
    for p in src:
        lit[p] = 15; dq.append(p)
    while dq:
        p = dq.popleft()
        lv = lit[p]
        if lv <= 1: continue
        for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            n = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
            if n in air and lit.get(n, 0) < lv - 1:
                lit[n] = lv - 1; dq.append(n)
    dark = [p for p in air if p[1] <= 4 and lit.get(p, 0) <= 0 and p[1] == -4]
    assert not dark, dark[:10]


def test_validation_clean():
    """Whole District E region through the validator (all checks): 0 errors, 0 warnings."""
    from atlantis.validate import core
    core.OPTIONS['walk_seeds'] = None
    tiles = E.tiles_in_box(ctx, -430, 130, -250, 280)
    rep = V.validate(ctx, tiles, ALL, 'all', jobs=1)
    bad = [i for i in rep['issues'] if i.severity in ('error', 'warn')]
    assert not bad, [(i.check, i.message) for i in bad]


if __name__ == '__main__':
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            t0 = time.perf_counter()
            fn()
            n += 1
            print(f'ok  {name}  ({time.perf_counter() - t0:.2f}s)')
    print(f'{n} tests passed')
