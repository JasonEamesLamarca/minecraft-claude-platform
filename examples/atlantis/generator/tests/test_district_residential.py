#!/usr/bin/env python3
"""district_residential.py tests (plain asserts).  Run from the project root: python3 tests/test_district_residential.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B, district_residential as DR
from atlantis.volume import Volume
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = DR.features()


def plan_of(f):
    return f.plan(ctx)


def test_registered_and_designs():
    names = {f.name for f in MINE}
    assert names <= {f.name for f in FEATS}
    assert len(MINE) >= 40
    designs = {f.meta['design'] for f in MINE}
    assert len(designs) >= 18, designs
    for d in ('C1', 'C2', 'C3'):
        assert len({f.meta['design'] for f in MINE if f.meta['district'] == d}) >= 6
    for f in MINE:
        assert f.layer == 40 and f.meta['interior'] is True and f.meta['entrances']


def test_no_rejects_and_inside_bbox():
    for f in MINE:
        p = plan_of(f)
        assert p.rejected == 0, (f.name, p.first_rejects)
        bb = f.bbox
        for (x, y, z) in p.c:
            assert bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5], (f.name, x, y, z)


def test_district_radius_height_roads():
    for f in MINE:
        p = plan_of(f)
        top = {}
        for (x, y, z) in p.c: top[(x, z)] = max(top.get((x, z), 0), y)
        for (x, z), t in top.items():
            assert PL.district_at(x, z) == f.meta['district'], (f.name, x, z)
            assert roads.surface(x, z) is None, (f.name, x, z)
            assert 305 <= math.hypot(x, z) <= 492, (f.name, x, z)
            assert t <= ctx.max_building_top(x, z), (f.name, x, z, t)


def test_height_profile():
    def cr(f): return math.hypot((f.bbox[0] + f.bbox[3]) / 2, (f.bbox[2] + f.bbox[5]) / 2)
    def top(f): return max(y for (_, y, _) in plan_of(f).c)
    hi = [top(f) for f in MINE if f.meta['district'] != 'C3' and cr(f) < 360]
    lo = [top(f) for f in MINE if f.meta['district'] != 'C3' and cr(f) > 430]
    assert hi and lo and max(hi) > max(lo)


def test_entrances_standable():
    for f in MINE:
        p = plan_of(f)
        for (x, y, z) in f.meta['entrances']:
            assert y == PL.WALK_Y
            assert (x, y, z) not in p.c and (x, y + 1, z) not in p.c, (f.name, x, y, z)
            assert PL.district_at(x, z) == f.meta['district']


def test_blocks_valid():
    bad = set()
    for f in MINE:
        for b in plan_of(f).c.values():
            if b.split('[')[0] not in VALID or not B.is_valid_state(b): bad.add(b)
    assert not bad, sorted(bad)[:10]


def test_panes_explicit():
    for f in MINE:
        for b in plan_of(f).c.values():
            if b.split('[')[0].endswith('_pane'):
                assert 'north=' in b and 'south=' in b, b


def test_disjoint():
    others = [f for f in FEATS if f.layer in (40, 50) and not f.name.startswith('res_')]
    for f in MINE:
        for o in others:
            ob, bb = o.bbox, f.bbox
            assert bb[3] < ob[0] or ob[3] < bb[0] or bb[5] < ob[2] or ob[5] < bb[2], (f.name, o.name)
    fs = sorted(MINE, key=lambda f: f.bbox[0])
    for i, f in enumerate(fs):
        for g in fs[i + 1:]:
            if g.bbox[0] > f.bbox[3]: break
            assert f.bbox[5] < g.bbox[2] or g.bbox[5] < f.bbox[2], (f.name, g.name)


def test_determinism():
    for f in MINE[::6]:
        bb = f.bbox
        box = (bb[0], bb[1], bb[2], min(bb[3], bb[0] + 31), bb[4], min(bb[5], bb[2] + 31))
        a, b = Volume(*box), Volume(*box)
        f.build(a, ctx)
        DR._CACHE.pop(f.name, None)
        f.build(b, ctx)
        assert list(a.items()) == list(b.items()), f.name


if __name__ == '__main__':
    import time
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith('test_') and callable(v):
            t = time.time(); v(); n += 1
            print('ok  %-40s %.1fs' % (k, time.time() - t))
    print('%d tests passed' % n)
