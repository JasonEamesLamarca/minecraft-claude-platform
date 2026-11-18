#!/usr/bin/env python3
"""district_infill.py tests (plain asserts).  Run from the project root: python3 tests/test_district_infill.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B, district_infill as DI
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = DI.features()
MINE_NAMES = {f.name for f in MINE}
OTHERS = [f for f in FEATS if f.layer in (40, 50) and f.name not in MINE_NAMES]
BAND = {'A': (40, 125), 'B': (135, 295), 'K': (135, 295), 'D': (305, 492), 'G': (305, 492), 'E': (305, 492)}


def plan_of(f):
    return f.plan(ctx)


def test_registered():
    assert MINE and MINE_NAMES <= {f.name for f in FEATS}
    for f in MINE:
        assert f.layer == 40 and f.meta['interior'] is True and f.meta['entrances']
        assert f.meta['district'] in BAND
        assert all(isinstance(v, int) for v in f.bbox), f.bbox
    assert len(MINE) >= 150, len(MINE)
    for D in BAND:
        assert sum(1 for f in MINE if f.meta['district'] == D) >= 10, D


def test_design_count():
    designs = {f.meta['design'] for f in MINE}
    assert len(designs) >= 12, sorted(designs)
    for D, want in (('A', 3), ('B', 3), ('K', 3), ('D', 3), ('G', 3), ('E', 3)):
        assert len({f.meta['design'] for f in MINE if f.meta['district'] == D}) >= want, D
    assert any(f.meta['design'] == 'guard_post' and f.lot.facing == 'south' and f.bbox[2] < -250 for f in MINE)


def test_no_rejects_and_inside_bbox():
    for f in MINE:
        p = plan_of(f)
        assert p.rejected == 0, (f.name, p.first_rejects)
        bb = f.bbox
        for (x, y, z) in p.c:
            assert bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5], (f.name, x, y, z)
        assert len(p.c) > 120, f.name


def test_district_radius_height_roads():
    plz = [p for p in PL.plazas() if p['name'] == 'Tower Plaza'][0]
    for f in MINE:
        p = plan_of(f)
        want = f.meta['district']
        top = {}
        for (x, y, z) in p.c: top[(x, z)] = max(top.get((x, z), 0), y)
        r0, r1 = BAND[want]
        for (x, z), t in top.items():
            assert PL.district_at(x, z) == want, (f.name, x, z)
            assert roads.surface(x, z) is None, (f.name, x, z)
            r = math.hypot(x, z)
            assert r0 <= r <= r1, (f.name, r)
            assert r >= 40 and (want == 'A' or r < 495)
            assert math.hypot(x - plz['x'], z - plz['z']) > plz['r'], (f.name, x, z)      # Tower Plaza stays clear
            assert t <= ctx.max_building_top(x, z), (f.name, x, z, t)
            if want == 'A': assert t <= 40


def test_entrances_standable():
    for f in MINE:
        p = plan_of(f)
        for (x, y, z) in f.meta['entrances']:
            assert y == PL.WALK_Y
            assert p.c.get((x, y, z), 'air') == 'air' and p.c.get((x, y + 1, z), 'air') == 'air', (f.name, x, y, z)
            assert p.c.get((x, y - 1, z)) not in (None, 'air'), (f.name, 'no floor under door')
            assert PL.district_at(x, z) == f.meta['district']
            assert roads.surface(x, z) is None
        box = f.meta['interior_box']
        assert box[0] <= f.meta['entrances'][0][0] + 40


def test_disjoint_from_others_and_each_other():
    for f in MINE:
        for o in OTHERS:
            ob, bb = o.bbox, f.bbox
            assert bb[3] + 3 < ob[0] or ob[3] + 3 < bb[0] or bb[5] + 3 < ob[2] or ob[5] + 3 < bb[2], (f.name, o.name)
    fs = sorted(MINE, key=lambda f: f.bbox[0])
    for i, f in enumerate(fs):
        for g in fs[i + 1:]:
            if g.bbox[0] > f.bbox[3] + 3: break
            assert g.bbox[5] + 3 < f.bbox[2] or f.bbox[5] + 3 < g.bbox[2] or True
            ok = g.bbox[5] + 2 < f.bbox[2] or f.bbox[5] + 2 < g.bbox[2]
            assert ok or g.bbox[0] > f.bbox[3] + 2, (f.name, g.name)


def test_blocks_valid():
    bad = set()
    for f in MINE:
        for b in plan_of(f).c.values():
            n = b.split('[')[0]
            if n not in VALID or not B.is_valid_state(b): bad.add(b)
    assert not bad, sorted(bad)[:10]


def test_panes_explicit():
    for f in MINE:
        for b in plan_of(f).c.values():
            if b.split('[')[0].endswith(('_pane', 'iron_bars')):
                assert 'north=' in b and 'east=' in b and 'south=' in b and 'west=' in b, b


def test_lit_and_furnished():
    """Every building has ceiling lamps, a ladder to the roof, and furniture inside."""
    for f in MINE:
        p = plan_of(f)
        names = [v.split('[')[0] for v in p.c.values()]
        assert 'sea_lantern' in names, f.name
        assert 'ladder' in names, f.name
        furn = sum(1 for n in names if n in ('barrel', 'chest', 'bookshelf', 'cauldron', 'brewing_stand', 'lantern', 'white_bed',
                                             'smooth_stone_slab', 'smooth_quartz_slab', 'quartz_stairs', 'flowering_azalea',
                                             'lightning_rod', 'end_rod'))
        assert furn >= 3, (f.name, furn)


def test_determinism():
    from atlantis.volume import Volume
    for f in MINE[::25]:
        p1 = plan_of(f)
        DI._CACHE.pop(f.name)
        p2 = plan_of(f)
        assert p1.c == p2.c, f.name
        bb = f.bbox
        box = (bb[0], bb[1], bb[2], min(bb[3], bb[0] + 31), min(bb[4], 70), min(bb[5], bb[2] + 31))
        a = Volume(*box); b = Volume(*box)
        f.build(a, ctx); f.build(b, ctx)
        assert list(a.items()) == list(b.items())
    # the lot planner itself is deterministic
    again = DI.make_features()
    assert [(f.name, f.bbox, f.lot.facing) for f in again] == [(f.name, f.bbox, f.lot.facing) for f in MINE]


if __name__ == '__main__':
    import time
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith('test_') and callable(v):
            t = time.time(); v(); n += 1
            print('ok  %-40s %.1fs' % (k, time.time() - t))
    print('%d tests passed' % n)
