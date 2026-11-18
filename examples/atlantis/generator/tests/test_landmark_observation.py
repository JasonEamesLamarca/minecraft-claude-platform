#!/usr/bin/env python3
"""landmark_observation.py tests (plain asserts).  Run from the project root: python3 tests/test_landmark_command.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B, landmark_observation as LO
from atlantis import export_tiles as E
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = LO.features()


def plan_of(f):
    p = f.plan(ctx)
    if not p.cols: p.finish()
    return p


def test_registered():
    assert [f.name for f in MINE] == ['obs_tower']
    f = MINE[0]
    assert f.layer == 50 and f.name in {g.name for g in FEATS}
    assert f.meta['interior'] is True and f.meta['district'] == 'K' and len(f.meta['entrances']) >= 1
    assert all(isinstance(v, int) for v in f.bbox), f.bbox


def test_no_rejects_and_inside_bbox():
    p = plan_of(MINE[0])
    assert p.rejected == 0, p.first_rejects
    bb = MINE[0].bbox
    for (x, y, z) in p.c:
        assert bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5], (x, y, z)
    assert len(p.c) > 5000


def test_district_radius_height_roads():
    p = plan_of(MINE[0])
    top = {}
    for (x, y, z) in p.c: top[(x, z)] = max(top.get((x, z), 0), y)
    for (x, z), t in top.items():
        assert PL.district_at(x, z) == 'K', (x, z)
        assert roads.surface(x, z) is None, (x, z)
        assert 130 <= math.hypot(x, z) <= 290
        assert t <= ctx.max_building_top(x, z), (x, z, t)


def test_as_tall_as_the_dome_allows():
    p = plan_of(MINE[0])
    ymax = max(y for (_, y, _) in p.c)
    cap = int(ctx.max_building_top(LO.CX, LO.CZ))
    assert ymax >= cap - 1, (ymax, cap)
    assert ymax - 4 >= 90


def test_features_of_the_design():
    p = plan_of(MINE[0])
    names = [b.split('[')[0] for b in p.c.values()]
    assert names.count('ladder') >= 70                       # the lift ladder, lobby to roof
    assert 'water' not in names and 'bubble_column' not in names
    assert names.count("lightning_rod") >= 6                 # block telescopes in the gallery
    assert names.count('light_blue_stained_glass') > 600     # glazed shaft and gallery
    assert sum(1 for n in names if n.endswith('_stairs')) > 30 and names.count('sea_lantern') > 30
    gal = [(x, z) for (x, y, z), b in p.c.items() if y == LO.GAL + 1 and b.split('[')[0] in ('quartz_stairs', 'lectern')]
    assert len(gal) >= 12, len(gal)                          # benches and lecterns on the gallery floor
    cons = [1 for (x, y, z), b in p.c.items() if y == LO.GAL + 1 and 'console' in b or y == LO.GAL + 4 and b == 'light_blue_stained_glass'
            and math.hypot(x - LO.CX, z - LO.CZ) < 6]
    assert cons                                              # consoles with hologram canopies


def test_entrances_standable():
    p = plan_of(MINE[0])
    for (x, y, z) in MINE[0].meta['entrances']:
        assert y == PL.WALK_Y
        assert (x, y, z) not in p.c and (x, y + 1, z) not in p.c, (x, y, z)
        assert PL.district_at(x, z) == 'K' and roads.surface(x, z) is None


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
            if b.split('[')[0].endswith('_pane'):
                assert 'north=' in b and 'east=' in b and 'south=' in b and 'west=' in b, b


def test_light():
    """Every walkable floor cell of every level has a light source within 14 blocks through free cells."""
    from collections import deque
    EM = {'sea_lantern': 15, 'end_rod': 14, 'verdant_froglight': 15, 'ochre_froglight': 15, 'lantern': 15}
    for f in MINE:
        p = plan_of(f)
        c = p.c
        # emitters
        src = {}
        for (x, y, z), b in c.items():
            n = b.split('[')[0]
            if n == 'light': src[(x, y, z)] = 15
            elif n in EM: src[(x, y, z)] = EM[n]

        def opaque(b):
            n = b.split('[')[0]
            return not (n.endswith(('glass', '_pane', '_stairs', '_slab', 'carpet', '_rod', 'lantern', 'bars')) or n in (
                'light', 'air', 'azalea', 'flowering_azalea', 'brewing_stand', 'cauldron', 'stone_button'))
        lvl = {}
        dq = deque()
        for k, v in src.items():
            lvl[k] = v; dq.append(k)
        while dq:
            k = dq.popleft()
            v = lvl[k]
            if v <= 1: continue
            x, y, z = k
            for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                n = (x + d[0], y + d[1], z + d[2])
                if lvl.get(n, 0) >= v - 1: continue
                b = c.get(n)
                if b is not None and opaque(b): continue
                lvl[n] = v - 1; dq.append(n)
        dark = []
        for (x, z), col in p.cols.items():
            ys = {y: b for y, b in col}
            for y, b in col:
                # a standable top: solid-ish block with two free cells above
                if opaque(b) and (y + 1) not in ys and (y + 2) not in ys and y > 4:
                    if lvl.get((x, y + 1, z), 0) == 0:
                        # only count cells with a floor-like role: inside the footprint of a level (floors) or terraces
                        if (x, y, z) in p.floors: dark.append((x, y + 1, z))
        assert not dark, (f.name, len(dark), dark[:6])


def test_determinism():
    from atlantis.volume import Volume
    for f in MINE:
        p1 = plan_of(f)
        r = f.maker(ctx)                                 # an independent second build must be identical
        p2 = r
        assert p1.c == p2.c, f.name
        bb = f.bbox
        box = (bb[0], bb[1], bb[2], min(bb[3], bb[0] + 31), min(bb[4], 70), min(bb[5], bb[2] + 31))
        a = Volume(*box); b = Volume(*box)
        f.build(a, ctx); f.build(b, ctx)
        assert list(a.items()) == list(b.items())


def test_bboxes_disjoint_from_others():
    others = [f for f in FEATS if f.layer in (40, 50) and f.name not in {m.name for m in MINE}]
    for f in MINE:
        for o in others:
            ob, bb = o.bbox, f.bbox
            assert bb[3] < ob[0] or ob[3] < bb[0] or bb[5] < ob[2] or ob[5] < bb[2], (f.name, o.name)


def test_levels_connected():
    """BFS over standable cells of each tower plan: from the entrance, most floor cells are reachable (stairs, doors)."""
    from collections import deque
    for f in MINE:
        pass
        p = plan_of(f)
        c = p.c
        bb = f.bbox

        def solid(b):
            n = b.split('[')[0]
            return not (n in ('light', 'air') or n.endswith(('_pane', 'carpet', '_rod', 'lantern', 'button')) or
                        n in ('azalea', 'flowering_azalea', 'brewing_stand', 'cauldron'))

        def deck(x, y, z):
            return y == 4 and PL.district_at(x, z) is not None

        def stand(x, y, z):          # feet cell
            below = c.get((x, y - 1, z))
            below_ok = (below is not None and solid(below)) or (y - 1 == 4 and below is None)
            if not below_ok:
                return False
            for k in (0, 1):
                b = c.get((x, y + k, z))
                if b is not None and solid(b) and 'stairs' not in b and 'slab' not in b: return False
            return True

        seen = set()
        e = f.meta['entrances'][0]
        dq = deque([e]); seen.add(e)
        while dq:
            x, y, z = dq.popleft()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for dy in (0, 1, -1, 2, -2, 3, -3):
                    n = (x + dx, y + dy, z + dz)
                    if n in seen: continue
                    if not (bb[0] <= n[0] <= bb[3] and bb[2] <= n[2] <= bb[5]): continue
                    if not stand(*n): continue
                    if dy > 1:
                        continue
                    if dy == 1 and not stand(x, y + 1, z) and c.get((x, y + 2, z)) is not None and solid(c[(x, y + 2, z)]): continue
                    seen.add(n); dq.append(n)
        ys = {y for (_, y, _) in seen}
        roof = max(y for (_, y, _) in p.floors) + 1
        assert max(ys) >= roof, (f.name, max(ys), roof)            # the roof deck is reachable by stairs
        for fl in sorted({y for (_, y, _) in p.floors if y > 4}):
            assert fl + 1 in ys, (f.name, 'floor not reached', fl + 1)


if __name__ == '__main__':
    import time
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith('test_') and callable(v):
            t = time.time(); v(); n += 1
            print('ok  %-40s %.1fs' % (k, time.time() - t))
    print('%d tests passed' % n)
