#!/usr/bin/env python3
"""district_medical_water.py tests (plain asserts).  Run from the project root: python3 tests/test_district_medical_water.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B, district_medical_water as DM
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = DM.features()
MINE_NAMES = {f.name for f in MINE}


def plan_of(f):
    return f.plan(ctx)


def centre_r(f):
    return math.hypot((f.bbox[0] + f.bbox[3]) / 2.0, (f.bbox[2] + f.bbox[5]) / 2.0)


def test_registered_and_designs():
    reg = {f.name for f in FEATS}
    assert MINE_NAMES <= reg
    assert len(MINE_NAMES) == len(MINE)
    designs = {f.b.spec.design for f in MINE}
    assert len(designs) >= 10, designs
    per = {}
    for f in MINE: per.setdefault(f.meta['district'], set()).add(f.b.spec.design)
    assert set(per) == {'F', 'W'} and len(per['F']) >= 5 and len(per['W']) >= 5, per
    assert len(MINE) >= 40
    for f in MINE:
        assert f.layer == 40
        assert f.meta['interior'] is True and f.meta['entrances'] and f.meta['district'] in ('F', 'W')
        assert all(isinstance(v, int) for v in f.bbox), f.bbox


def test_no_rejects_and_inside_bbox():
    for f in MINE:
        p = plan_of(f)
        assert p.rejected == 0, (f.name, p.first_rejects)
        bb = f.bbox
        for (x, y, z) in p.c:
            assert bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5], (f.name, x, y, z)
        assert len(p.c) > 1000, f.name


def test_district_radius_height_roads():
    for f in MINE:
        p = plan_of(f)
        want = f.meta['district']
        top = {}
        for (x, y, z) in p.c: top[(x, z)] = max(top.get((x, z), 0), y)
        for (x, z), t in top.items():
            assert PL.district_at(x, z) == want, (f.name, x, z)
            assert roads.surface(x, z) is None, (f.name, x, z)
            assert 135 <= math.hypot(x, z) <= 295, (f.name, x, z, math.hypot(x, z))
            assert t <= ctx.max_building_top(x, z), (f.name, x, z, t)
        assert max(top.values()) >= 16, f.name


def test_heights_scale_with_radius():
    inner = [max(y for (_, y, _) in plan_of(f).c) for f in MINE if centre_r(f) < 190]
    outer = [max(y for (_, y, _) in plan_of(f).c) for f in MINE if centre_r(f) > 240]
    assert inner and outer
    assert 40 <= max(inner) <= 66, max(inner)
    assert sum(inner) / len(inner) > sum(outer) / len(outer)


def test_no_overlap():
    boxes = [(f.name, f.bbox) for f in MINE]
    for i, (na, a) in enumerate(boxes):
        for (nb, b) in boxes[i + 1:]:
            assert a[3] + 3 < b[0] or b[3] + 3 < a[0] or a[5] + 3 < b[2] or b[5] + 3 < a[2], (na, nb)
    for f in FEATS:
        if f.name in MINE_NAMES or f.layer not in (40, 50): continue
        for (n, a) in boxes:
            b = f.bbox
            assert a[3] + 3 < b[0] or b[3] + 3 < a[0] or a[5] + 3 < b[2] or b[5] + 3 < a[2], (n, f.name)


def test_entrances_standable():
    for f in MINE:
        p = plan_of(f)
        for (x, y, z) in f.meta['entrances']:
            assert y == PL.WALK_Y
            assert (x, y, z) not in p.c and (x, y + 1, z) not in p.c and (x, y + 2, z) not in p.c, (f.name, x, y, z)
            assert PL.district_at(x, z) == f.meta['district'] and roads.surface(x, z) is None


def test_blocks_valid_and_no_water():
    bad = set()
    for f in MINE:
        for b in plan_of(f).c.values():
            n = b.split('[')[0]
            assert 'water' not in n and 'kelp' not in n and 'seagrass' not in n and 'bubble' not in n, b
            assert 'waterlogged=true' not in b, b
            if n not in VALID or not B.is_valid_state(b): bad.add(b)
    assert not bad, sorted(bad)[:10]


def test_panes_explicit():
    for f in MINE:
        for b in plan_of(f).c.values():
            if b.split('[')[0].endswith(('_pane', 'iron_bars')):
                assert 'north=' in b and 'east=' in b and 'south=' in b and 'west=' in b, b


def test_tanks_use_glass_not_water():
    tanks = [f for f in MINE if f.b.spec.design in ('wat_plant', 'wat_tankfarm')]
    assert tanks
    for f in tanks:
        names = {b.split('[')[0] for b in plan_of(f).c.values()}
        assert 'light_blue_stained_glass' in names and 'water' not in names


def test_deterministic():
    for f in MINE[::9]:
        again = type(f.b)(f.b.spec).plan(ctx)
        assert again.c == plan_of(f).c, f.name


def test_furnished():
    for f in MINE:
        names = {b.split('[')[0] for b in plan_of(f).c.values()}
        assert names & {'sea_lantern', 'light'}, f.name
        if f.b.spec.design in ('med_infirmary', 'med_clinic'):
            assert any(n.endswith('_bed') for n in names), f.name
        if f.b.spec.design in ('wat_hydrohall', 'wat_hydrotower'):
            assert 'farmland' in names and names & {'wheat', 'carrots', 'potatoes', 'beetroots'}, f.name


if __name__ == '__main__':
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith('test_') and callable(v):
            v(); n += 1
            print('ok', k)
    print(n, 'tests passed')
