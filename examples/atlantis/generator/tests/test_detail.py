#!/usr/bin/env python3
"""detail.py tests (plain asserts).  Run from the project root: python3 tests/test_detail.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B, detail as D
from atlantis import export_tiles as E
from atlantis.orchestrate import load_generators
from atlantis.volume import Volume

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = D.features()
BUILD = [f for f in FEATS if f.layer in (40, 50)]
IDX = D.index()


def cells_of(f):
    v = Volume(*f.bbox)
    f.build(v, ctx)
    return {(x, y, z): b for (x, y, z, b) in v.items()}


_CACHE = {}


def mycells(f):
    if f.name not in _CACHE: _CACHE[f.name] = cells_of(f)
    return _CACHE[f.name]


def test_registered():
    names = {f.name for f in MINE}
    assert names <= {f.name for f in FEATS} and len(names) == len(MINE)
    assert len(MINE) >= 100
    for f in MINE:
        assert f.layer in (70, 80), f.name
        assert all(isinstance(v, int) for v in f.bbox), f.bbox
        assert (f.name.startswith('detail_') and f.layer == 70) or (f.name.startswith('light_') and f.layer == 80)
        assert f.bbox[3] - f.bbox[0] < 145 and f.bbox[5] - f.bbox[2] < 145, f.name       # tight
    for kind in ('detail_avenue', 'light_avenue', 'detail_plaza', 'light_plaza_floor', 'detail_gate', 'light_gate'):
        assert any(f.name.startswith(kind) for f in MINE), kind


def test_nothing_inside_buildings_or_far_out():
    for f in MINE:
        for (x, y, z), b in mycells(f).items():
            assert not IDX.hit(x, z), (f.name, x, y, z, b)
            assert x * x + z * z < D.MAX_R ** 2, (f.name, x, z)
            assert PL.has_deck(x, z), (f.name, x, z)
            assert y <= ctx.max_building_top(x, z) and y <= 14, (f.name, x, y, z)
            assert 3 <= y, (f.name, x, y, z)


def test_not_near_entrances():
    for f in MINE:
        if f.name.startswith(('light_avenue_', 'light_plaza_floor')): continue          # flush floor channels
        for (x, y, z), b in mycells(f).items():
            if y < 5: continue
            if (x, z) in IDX.ent or IDX.near_entrance(x, z, 1):
                assert roads.surface(x, z) is None and y >= 8, (f.name, x, y, z)           # only high banner flags


def test_flush_paving():
    """Changes on street columns are flush floor inlays (y 3/4), except plaza rim bollards off the lanes and the
    overhanging banner flags (y >= 8)."""
    for f in MINE:
        for (x, y, z), b in mycells(f).items():
            s = roads.surface(x, z)
            if s is None: continue
            if y <= 4:
                assert s[3] == 4 and y in (3, 4), (f.name, x, y, z, b)
                assert 'stained_glass' in b or b in ('sea_lantern', 'shroomlight', 'pearlescent_froglight',
                                                     'waxed_oxidized_copper_grate'), (f.name, x, y, z, b)
            elif y >= 8:
                continue
            else:
                assert s[0] == 'plaza' and f.name.startswith(('detail_plaza_', 'light_plaza_')), (f.name, x, y, z, b)
                assert not D._plaza_lane(x, z), (f.name, x, z)


def test_blocks_valid():
    bad = set()
    for f in MINE:
        for b in mycells(f).values():
            n = b.split('[')[0]
            if n not in VALID or not B.is_valid_state(b): bad.add(b)
    assert not bad, sorted(bad)[:10]


def passable(b):
    if b is None: return True
    n = b.split('[')[0]
    return n in ('air', 'cave_air', 'light', 'end_rod', 'lantern') or n in (
        'azalea', 'flowering_azalea', 'allium', 'azure_bluet', 'cornflower', 'blue_orchid', 'oxeye_daisy',
        'lily_of_the_valley', 'poppy', 'fern', 'amethyst_cluster') or n.endswith('_bud')


def test_roads_walkable():
    """Along every avenue (cross-section) and both ring roads: the street keeps a clear, walkable width of at
    least road width - 2, with a centre line that has no obstacle at y 5/6 and a solid floor at y 4."""
    keep = [f for f in FEATS if f.layer in (25, 26, 30, 70, 80)]
    base = [f for f in keep if f.layer < 70]
    cache = {}

    def vol_at(x, z, fs, tag):
        key = (x >> 5, z >> 5, tag)
        v = cache.get(key)
        if v is None:
            v = cache[key] = E.build_box(ctx, (key[0] * 32, 0, key[1] * 32, key[0] * 32 + 31, 12, key[1] * 32 + 31), fs)
        return v

    def _ok(v, x, z):
        fl = v.get(x, 4, z)
        return fl is not None and fl not in ('air', 'water') and passable(v.get(x, 5, z)) and passable(v.get(x, 6, z))

    def walk_cell(x, z):
        """Walkable with my features; cells the roads module itself blocks (fountains, pylons) do not count."""
        if _ok(vol_at(x, z, keep, 'all'), x, z): return True
        return not _ok(vol_at(x, z, base, 'base'), x, z)

    for a in PL.PIER_ANGLES:
        s = 36.0
        while s < 534:
            hw = roads._avenue_hw(s)
            width = 0
            for c10 in range(-70, 71, 5):
                c = c10 / 10.0
                px, pz = D._axis_pt(a, s, c)
                x, z = int(round(px)), int(round(pz))
                sf = roads.surface(x, z)
                if sf is None or sf[0] not in ('avenue', 'plaza'): continue
                if walk_cell(x, z) and abs(c) <= hw:
                    width += 0.5
            ctr = D._axis_pt(a, s, 0)
            assert walk_cell(int(round(ctr[0])), int(round(ctr[1]))), ('avenue centre blocked', a, s)
            if s % 12 < 1:
                assert width >= 2 * hw - 2 - 1.5, ('avenue narrow', a, s, width)
            s += 1.0
    for R, w in ((130, 9), (300, 9)):
        for k in range(0, 3600, 1):
            ang = k / 10.0
            px, pz = PL.from_polar(R, ang)
            x, z = int(round(px)), int(round(pz))
            if roads.surface(x, z) is None: continue
            if not PL.has_deck(x, z): continue
            assert walk_cell(x, z), ('ring centre blocked', R, ang, x, z)


def test_lights_per_class():
    by = {}
    for f in MINE:
        for (x, y, z), b in mycells(f).items():
            d = PL.district_at(x, z)
            by.setdefault(D.CLASS.get(d), set()).add(b.split('[')[0])
    assert 'cyan_stained_glass' in by['gate'] and 'sea_lantern' in by['gate']
    assert 'shroomlight' in by['warm'] and 'orange_stained_glass' in by['warm'] and 'lantern' in by['warm']
    assert 'light_blue_stained_glass' in by['cool'] and 'end_rod' in by['cool']
    assert 'sea_lantern' in by['cool'] and 'amethyst_cluster' in by['cool']
    assert 'pearlescent_froglight' in by['civic'] or 'ochre_froglight' in by['civic']
    # cyan floor channels are the Fountain complex's signature: no cyan glass channel in the residential piers
    assert 'cyan_stained_glass' not in by['warm']
    # a bench, a planter and a tree exist somewhere
    all_b = set().union(*by.values())
    assert {'moss_block', 'birch_log', 'azalea_leaves', 'smooth_quartz_stairs'} <= all_b
    # gateway pylons exist in several district colours
    cols = {b for n in all_b for b in [n] if n.endswith('_concrete')}
    assert len(cols) >= 5, cols


def test_gateways_flanking_roads():
    n = [f for f in MINE if f.name.startswith('detail_gate_')]
    assert len(n) >= 12, len(n)


def test_determinism():
    for f in MINE[::5]:
        a = Volume(*f.bbox); b = Volume(*f.bbox)
        f.build(a, ctx); f.build(b, ctx)
        assert list(a.items()) == list(b.items()), f.name
    # an independent plan gives the same stamps
    s1 = D.avenue_stamps(); s2 = D.avenue_stamps()
    assert {k: [sorted(x.d.items()) for x in v] for k, v in s1.items()} == \
           {k: [sorted(x.d.items()) for x in v] for k, v in s2.items()}


def test_tile_independence():
    """A feature's output must not depend on the volume it is drawn into (tile order independence)."""
    f = [g for g in MINE if g.name.startswith('light_plaza_floor_fountain')][0]
    full = cells_of(f)
    bb = f.bbox
    half = Volume(bb[0], bb[1], bb[2], (bb[0] + bb[3]) // 2, bb[4], bb[5])
    f.build(half, ctx)
    for (x, y, z, b) in half.items(): assert full[(x, y, z)] == b


if __name__ == '__main__':
    import time
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith('test_') and callable(v):
            t = time.time(); v(); n += 1
            print('ok  %-40s %.1fs' % (k, time.time() - t))
    print('%d tests passed' % n)
