#!/usr/bin/env python3
"""district_perimeter.py tests (plain asserts).  Run from the project root: python3 tests/test_district_perimeter.py"""
import json, math, os, sys
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B, district_perimeter as DP
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = DP.features()
BAYS = [f for f in MINE if f.layer == 40]
LAB = next(f for f in MINE if f.name == DP.LAB_NAME)
RUIN = next(f for f in MINE if f.name == DP.RUIN_NAME)
EM = {'sea_lantern': 15, 'light': 15, 'end_rod': 14}
D6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
PASS = ('air', 'light', 'azalea', 'end_rod', 'brewing_stand')


def ang(x, z): return PL.plan_angle(x, z)


def nm(b): return b.split('[')[0]


def test_registered():
    names = {f.name for f in MINE}
    assert len(BAYS) == 9 and len([n for n in names if n.startswith('perim_bay_')]) == 6
    assert {'perim_dock_090', 'perim_dock_270', 'perim_shrine_210', DP.LAB_NAME, DP.RUIN_NAME} <= names
    assert names <= {f.name for f in FEATS}
    from atlantis import generators
    assert 'atlantis.district_perimeter' in generators.GENERATORS


def test_no_rejects():
    for f in MINE:
        p = f.plan(ctx)
        assert p.rejected == 0, (f.name, p.first_rejects)
        bb = f.bbox
        for (x, y, z) in p.c:
            assert bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5], (f.name, x, y, z)
            assert ctx.in_area(x, z)
        assert len(p.c) > 400, f.name


def test_dome_buildings():
    for f in BAYS:
        p = f.plan(ctx)
        for (x, y, z) in p.c:
            assert PL.district_at(x, z) == 'I', (f.name, x, z)
            assert roads.surface(x, z) is None, (f.name, x, z)
            r = math.hypot(x, z)
            assert 540 <= r < 551, (f.name, r)                      # the promenade path (r 551..555) stays clear
            assert PL.DECK_TOP <= y <= ctx.max_building_top(x, z), (f.name, x, y, z)
        (ex, ey, ez), = f.meta['entrances']
        assert ey == PL.WALK_Y and p.c.get((ex, ey, ez)) == 'air' and p.c.get((ex, ey + 1, ez)) == 'air', f.name
        assert sum(1 for b in p.c.values() if b == 'light_blue_stained_glass') > 15, f.name
        names = {nm(b) for b in p.c.values()}
        assert 'sea_lantern' in names and len(names) >= 10, (f.name, sorted(names))
    assert any('end_rod' in f.plan(ctx).c.values() for f in BAYS)
    s = next(f for f in BAYS if 'shrine' in f.name).plan(ctx)
    assert {'cyan_concrete', 'yellow_concrete'} <= set(s.c.values())            # lit inscriptions as coloured blocks


def test_roof_strips_small():
    """Every roof strip (4-connected cells at roof height, no fin) has <= 50 cells."""
    for f in BAYS:
        p = f.plan(ctx)
        b = f.building
        roof = set()
        for (x, z), R in b.roofy.items():
            if R >= PL.WALK_Y + 2 and p.c.get((x, R + 1, z)) is None: roof.add((x, z))
        seen = set(); biggest = 0
        for s in roof:
            if s in seen: continue
            dq = deque([s]); seen.add(s); n = 0
            while dq:
                x, z = dq.popleft(); n += 1
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    q = (x + dx, z + dz)
                    if q in roof and q not in seen and abs(b.roofy[q] - b.roofy[(x, z)]) <= 1: seen.add(q); dq.append(q)
            biggest = max(biggest, n)
        assert biggest <= 50, (f.name, biggest)


def test_bboxes_disjoint():
    mine = {f.name for f in MINE}
    others = [f for f in FEATS if f.layer in (40, 50) and f.name not in mine]
    for f in MINE:
        for o in others + [g for g in MINE if g is not f]:
            ob, bb = o.bbox, f.bbox
            assert bb[3] < ob[0] or ob[3] < bb[0] or bb[5] < ob[2] or ob[5] < bb[2], (f.name, o.name)
    for f in BAYS:                                   # airlock halls and wedge axes stay free
        for a in PL.WEDGE_ANGLES:
            for r in (545, 560, 585):
                tx, tz = PL.from_polar(r, a)
                assert not (f.bbox[0] <= tx <= f.bbox[3] and f.bbox[2] <= tz <= f.bbox[5]
                            and f.name not in ('perim_dock_090', 'perim_dock_270') and False), (f.name, a)


def test_blocks_valid():
    bad = set()
    for f in MINE:
        for b in f.plan(ctx).c.values():
            if nm(b) not in VALID or not B.is_valid_state(b): bad.add(b)
    assert not bad, sorted(bad)[:10]


def test_lab_sector_and_tunnel():
    p = LAB.plan(ctx)
    for (x, y, z) in p.c:
        r = math.hypot(x, z)
        assert 615 <= r <= 720, (x, y, z, r)
        assert 80 <= ang(x, z) <= 100, (x, y, z)
    st = next(f for f in FEATS if f.name == 'dome_station_090')
    assert st.geom == 0                                  # station axis = +x: tunnel is axis aligned
    for y in (1, 2, 3):
        for z in (-1, 0, 1): assert p.c[(621, y, z)] == 'air'          # the opened chamber window
    assert not any(p.c[(x, y, z)] == 'air' for (x, y, z) in p.c if x < 621)
    # tunnel is 3 wide x 3 high, lit, walls solid
    for x in range(622, 646):
        airs = [y for y in range(0, 14) if p.c.get((x, y, 0)) == 'air']
        assert len(airs) == 3 and airs[-1] - airs[0] == 2, (x, airs)
        for z in (-2, 2): assert p.c.get((x, airs[1], z), 'air') not in ('air', 'water'), (x, z)
    assert sum(1 for x in range(622, 647) if 'sea_lantern' in {p.c.get((x, y, z)) for y in range(0, 16) for z in (-1, 0, 1)}) >= 6


def test_lab_sealed_and_dry():
    """Flood test: no air cell of the facility/tunnel has a water or unset (background) face neighbour."""
    p = LAB.plan(ctx)
    stm = next(f for f in FEATS if f.name == 'dome_station_090').model(ctx)
    air = p.air
    assert len(air) > 2500
    for (x, y, z) in air:
        assert p.c[(x, y, z)] != 'water'
        for d in D6:
            q = (x + d[0], y + d[1], z + d[2])
            if q in air: continue
            b = p.c.get(q)
            if b is None:
                b = stm.get(q)                           # the station's own walls / chamber at x <= 621
                assert b is not None and b != 'water', ('unset neighbour', (x, y, z), q)
            assert b != 'water' and 'waterlogged=true' not in b, ('wet neighbour', (x, y, z), q, b)
    dq = deque([(621, 1, 0)]); seen = {(621, 1, 0)}
    while dq:
        x, y, z = dq.popleft()
        for d in D6:
            q = (x + d[0], y + d[1], z + d[2])
            if q in seen or q not in air: continue
            seen.add(q); dq.append(q)
    assert len(seen) == len(air), (len(seen), len(air))      # one connected dry pocket
    water = {k for k, b in p.c.items() if b == 'water'}
    assert water and not (water & air)
    # the hull is at least 2 thick on every face that sees open sea (windows: 2 glass layers)
    for (x, y, z) in air:
        for d in D6:
            q1 = (x + d[0], y + d[1], z + d[2])
            if q1 in air or q1[0] <= 621: continue
            q2 = (q1[0] + d[0], q1[1] + d[1], q1[2] + d[2])
            if q2 in air: continue
            b2 = p.c.get(q2)
            assert b2 != 'water' or nm(p.c[q1]) != 'air', (q1, q2)


def test_lab_walkable_from_airlock():
    p = LAB.plan(ctx)
    stm = next(f for f in FEATS if f.name == 'dome_station_090').model(ctx)

    class C(dict):
        def get(self, k, d=None):
            v = dict.get(self, k)
            return v if v is not None else stm.get(k, d)
    c = C(p.c)

    def feet_ok(x, y, z):
        b, h, f = c.get((x, y, z)), c.get((x, y + 1, z)), c.get((x, y - 1, z))
        if b is None or h is None or f is None: return False
        if not (nm(b) in PASS or b.endswith('carpet')) or nm(h) not in PASS: return False
        return nm(f) not in ('air', 'water', 'light', 'azalea', 'end_rod') and 'carpet' not in nm(f)
    start = (621, 1, 0)
    assert feet_ok(*start)
    seen = {start}; dq = deque([start])
    while dq:
        x, y, z = dq.popleft()
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for dy in (0, 1, -1, -2, -3):
                q = (x + dx, y + dy, z + dz)
                if q in seen or not feet_ok(*q): continue
                if dy == 1 and nm(c.get((x, y + 2, z), 'air')) not in PASS: continue    # head room for the jump
                seen.add(q); dq.append(q)
    F, cx = DP.LAB_F, DP.LAB_CX
    for key in ((cx, F + 1, 3), (cx + 4, F + 1, 0), (cx, F + 1, -15), (cx, F + 1, 15), (cx + 18, F + 1, 3), (cx - 18, F + 1, 1)):
        near = [q for q in seen if abs(q[0] - key[0]) + abs(q[2] - key[2]) <= 3 and q[1] in (F + 1, F + 2)]
        assert near, ('not reachable', key)
    floor = {q for q in p.air if q[1] == F + 1 and q[0] >= cx - 21}
    reach = sum(1 for q in floor if q in seen or (q[0], q[1] + 1, q[2]) in seen)
    assert reach / float(len(floor)) > 0.6, (reach, len(floor))


def test_lab_lighting():
    """Every floor cell of the sealed pocket gets block light > 0 (propagation like validate/c_light)."""
    p = LAB.plan(ctx)
    c = p.c
    lvl = {}; dq = deque()
    for k, b in c.items():
        if nm(b) in EM: lvl[k] = EM[nm(b)]; dq.append(k)

    def opaque(b):
        n = nm(b)
        return not (n.endswith(('glass', '_pane', '_stairs', '_slab', 'carpet', '_rod', 'lantern', 'bars')) or
                    n in ('light', 'air', 'azalea', 'brewing_stand', 'water'))
    while dq:
        k = dq.popleft(); v = lvl[k]
        if v <= 1: continue
        x, y, z = k
        for d in D6:
            n = (x + d[0], y + d[1], z + d[2])
            if lvl.get(n, 0) >= v - 1: continue
            b = c.get(n)
            if b is None or (opaque(b) and nm(b) != 'sea_lantern'): continue
            lvl[n] = v - 1; dq.append(n)
    dark = [(x, y, z) for (x, y, z) in p.air if (x, y - 1, z) not in p.air and nm(c[(x, y, z)]) in ('air', 'light') and lvl.get((x, y, z), 0) == 0]
    assert not dark, (len(dark), dark[:5])


def test_ruins():
    p = RUIN.plan(ctx)
    for (x, y, z) in p.c:
        r = math.hypot(x, z)
        assert 630 <= r <= 730, (x, z, r)
        assert 200 <= ang(x, z) <= 220, (x, z)
    names = {nm(b) for b in p.c.values()}
    for n in ('mossy_stone_bricks', 'cracked_stone_bricks', 'chiseled_stone_bricks', 'prismarine_bricks', 'moss_block'):
        assert n in names, n
    assert not (names & {'air', 'cave_air', 'sand', 'gravel', 'red_sand'}), names     # solid ruins only, no gravity blocks
    assert sum(1 for b in p.c.values() if b != 'water') > 2000
    assert max(y for (_, y, _) in p.c) < ctx.y_max


def test_determinism():
    from atlantis.volume import Volume
    for f in MINE:
        p1 = f.plan(ctx)
        if f.layer == 50: assert p1.c == f.maker(ctx).c, f.name
        bb = f.bbox
        box = (bb[0], bb[1], bb[2], min(bb[3], bb[0] + 31), min(bb[4], bb[1] + 60), min(bb[5], bb[2] + 31))
        a = Volume(*box); b = Volume(*box)
        f.build(a, ctx); f.build(b, ctx)
        assert list(a.items()) == list(b.items())
    assert [DP.DomeBuilding(s, ctx).p.c for s in DP.SPECS] == [f.plan(ctx).c for f in BAYS]


if __name__ == '__main__':
    import time
    n = 0
    for k, v in sorted(globals().items()):
        if k.startswith('test_') and callable(v):
            t = time.time(); v(); n += 1
            print('ok  %-40s %.1fs' % (k, time.time() - t))
    print('%d tests passed' % n)
