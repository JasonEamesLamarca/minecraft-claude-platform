#!/usr/bin/env python3
"""district_civic.py tests (plain asserts).  Run from the project root: python3 tests/test_district_civic.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B, district_civic as DC
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = DC.features()


def test_registered_and_designs():
    names = {f.name for f in MINE}
    assert len(names) == len(MINE) and len(MINE) >= 40, len(MINE)
    assert names <= {f.name for f in FEATS}
    designs = {f.lot.design for f in MINE}
    assert len(designs) >= 12, designs
    for need in ('market_hall', 'library', 'museum', 'cafeteria', 'admin_tower', 'conservatory', 'academy',
                 'auditorium', 'terraced_garden'):
        assert need in designs, need
    assert {f.lot.district for f in MINE} == {'H', 'L'}
    for f in MINE:
        assert f.layer == 40
        assert f.meta['interior'] is True and len(f.meta['entrances']) == 1
        assert all(isinstance(v, int) for v in f.bbox)


def test_no_rejects_height_district_roads():
    for f in MINE:
        p = f.plan(ctx)
        assert p.rejected == 0, (f.name, p.first_rejects)
        want = f.meta['district']
        top = {}
        for (x, y, z) in p.c:
            top[(x, z)] = max(top.get((x, z), 0), y)
        for (x, z), t in top.items():
            assert PL.district_at(x, z) == want, (f.name, x, z)
            assert roads.surface(x, z) is None, (f.name, x, z)
            assert 134 <= math.hypot(x, z) <= 296, (f.name, x, z)
            assert t <= ctx.max_building_top(x, z), (f.name, x, z, t)
        assert len(p.c) > 100


def test_entrances_standable():
    for f in MINE:
        p = f.plan(ctx)
        for (x, y, z) in f.meta['entrances']:
            assert y == PL.WALK_Y
            assert (x, y, z) not in p.c and (x, y + 1, z) not in p.c, (f.name, x, y, z)
            assert (x, 4, z) in p.c


def test_blocks_valid_and_panes():
    bad = set()
    for f in MINE:
        for b in f.plan(ctx).c.values():
            n = b.split('[')[0]
            if n not in VALID or not B.is_valid_state(b): bad.add(b)
            if n.endswith('_pane'):
                assert 'north=' in b and 'west=' in b, b
    assert not bad, sorted(bad)[:10]


def test_bboxes_disjoint():
    others = [f for f in FEATS if f.layer in (40, 50) and f.name not in {m.name for m in MINE}]
    for f in MINE:
        for o in others:
            a, b = f.bbox, o.bbox
            assert a[3] < b[0] or b[3] < a[0] or a[5] < b[2] or b[5] < a[2], (f.name, o.name)
    for i, f in enumerate(MINE):
        for g in MINE[i + 1:]:
            a, b = f.bbox, g.bbox
            assert a[3] + 3 < b[0] or b[3] + 3 < a[0] or a[5] + 3 < b[2] or b[5] + 3 < a[2], (f.name, g.name)


def test_interior_lit_and_reachable():
    """Hidden-light model: every standable cell inside the plate has light; BFS from the door reaches the roof."""
    from collections import deque
    for f in MINE:
        b = f.building(ctx)
        p = b.p
        c = p.c
        assert any(v.startswith(('sea_lantern', 'light')) or 'lantern' in v for v in c.values()), f.name
        if f.meta['kind'] in ('kiosk', 'garden_pavilion'): continue

        def solid(v):
            n = v.split('[')[0]
            return not (n in ('light', 'air') or n.endswith(('_pane', 'carpet', '_rod', 'lantern', 'button')) or
                        n in ('azalea', 'flowering_azalea', 'poppy', 'blue_orchid', 'allium', 'azure_bluet', 'cornflower',
                              'lily_of_the_valley', 'dandelion', 'oxeye_daisy', 'fern'))

        def stand(x, y, z):
            below = c.get((x, y - 1, z))
            ok = (below is not None and solid(below)) or (y - 1 == 4 and below is None)
            if not ok: return False
            for k in (0, 1):
                v = c.get((x, y + k, z))
                if v is not None and solid(v) and 'stairs' not in v and 'slab' not in v: return False
            return True
        e = f.meta['entrances'][0]
        seen = {e}
        dq = deque([e])
        while dq:
            x, y, z = dq.popleft()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for dy in (0, 1, -1, 2, -2, 3, -3):
                    n = (x + dx, y + dy, z + dz)
                    if n in seen or not p.inside(*n) or not stand(*n) or dy > 1: continue
                    if dy == 1 and (x, y + 2, z) in c and solid(c[(x, y + 2, z)]): continue
                    seen.add(n); dq.append(n)
        floors = {lv.F + 1 for lv in b.L}
        assert floors <= {y for (_, y, _) in seen}, (f.name, floors, sorted({y for (_, y, _) in seen}))


def test_determinism():
    from atlantis.volume import Volume
    for f in MINE[:6]:
        b2 = DC.make_building(f.lot, ctx)
        assert b2.p.c == f.plan(ctx).c, f.name
        bb = f.bbox
        box = (bb[0], bb[1], bb[2], min(bb[3], bb[0] + 31), min(bb[4], 70), min(bb[5], bb[2] + 31))
        a, c = Volume(*box), Volume(*box)
        f.build(a, ctx); f.build(c, ctx)
        assert list(a.items()) == list(c.items())


if __name__ == '__main__':
    import time
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith('test_') and callable(v):
            t = time.time(); v(); n += 1
            print('ok  %-40s %.1fs' % (k, time.time() - t))
    print('%d tests passed' % n)
