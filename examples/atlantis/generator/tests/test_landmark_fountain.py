#!/usr/bin/env python3
"""District B Fountain & Arrival Complex (landmark_fountain.py) tests, plain asserts.
Run from the project root: python3 tests/test_landmark_fountain.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, landmark_fountain as L, planner as PL, roads as R, blocks as B, validate as V
from atlantis.orchestrate import load_generators
from atlantis.validate import classify as K

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
MINE = L.FEATURES
NAMES = {'rings_dais', 'arrival_approach_s', 'arrival_approach_n', 'arrival_hall', 'checkpoint_w', 'checkpoint_e'}


def cells(f):
    return list(L.Piece._private(f, ctx).items())


def test_registered():
    reg = {f.name for f in FEATS}
    assert NAMES <= reg, NAMES - reg
    for f in MINE:
        assert f.layer == 50 and f.meta['district'] == 'B'
        bb = f.bbox
        for x, z in ((bb[0], bb[2]), (bb[3], bb[2]), (bb[0], bb[5]), (bb[3], bb[5])):
            r = math.hypot(x, z)
            assert 135 <= r <= 295, (f.name, r)
    for n in ('arrival_hall', 'checkpoint_w', 'checkpoint_e'):
        m = [f for f in MINE if f.name == n][0].meta
        assert m['interior'] is True and m['entrances'] and len(m['interior_box']) == 6 and m['roof_y']


def test_bboxes_disjoint():
    for i, a in enumerate(MINE):
        for b in MINE[i + 1:]:
            assert a.bbox[3] < b.bbox[0] or b.bbox[3] < a.bbox[0] or a.bbox[5] < b.bbox[2] or b.bbox[5] < a.bbox[2], \
                (a.name, b.name)
    others = [f for f in FEATS if f.layer in (40, 50) and f not in MINE]
    for a in MINE:
        for b in others:
            assert a.bbox[3] < b.bbox[0] or b.bbox[3] < a.bbox[0] or a.bbox[5] < b.bbox[2] or b.bbox[5] < a.bbox[2], \
                (a.name, b.name)


def test_district_height_bbox():
    for f in MINE:
        bb = f.bbox
        for x, y, z, b in cells(f):
            assert PL.district_at(x, z) == 'B', (f.name, x, z)
            assert 135 <= math.hypot(x, z) <= 295, (f.name, x, z)
            assert y <= ctx.max_building_top(x, z), (f.name, x, y, z)
            assert bb[0] <= x <= bb[3] and bb[2] <= z <= bb[5] and bb[1] <= y <= bb[4]
            assert y >= PL.DECK_TOP and ctx.inside_dome(x, y, z) or y == PL.DECK_TOP, (f.name, x, y, z)


def test_blocks_valid():
    bad = set()
    for f in MINE:
        for _, _, _, b in cells(f):
            if b.split('[')[0] not in VALID or not B.is_valid_state(b): bad.add(b)
            if '_pane' in b and '[' not in b: bad.add(b)
            if '_stairs' in b and 'facing=' not in b: bad.add(b)
    assert not bad, sorted(bad)[:10]


def test_no_water_and_lit_emitters():
    from atlantis import palette as P
    for f in MINE:
        cs = cells(f)
        assert not any('water' in b for _, _, _, b in cs)
        assert any(b.split('[')[0] in P.EMISSION or b.startswith('light[') for _, _, _, b in cs), f.name


def test_roads_not_blocked():
    """Nothing of mine stands within head height (y 5..8) on a road/ring cell outside the plaza, and the avenue
    centreline x = 0 stays walkable: no obstacle at y 7..10 anywhere, and y 5..6 only the dais steps."""
    for f in MINE:
        for x, y, z, b in cells(f):
            if b == 'air' or y <= PL.DECK_TOP or y > PL.DECK_TOP + 4: continue
            s = R.surface(x, z)
            if s is None or s[0] == 'plaza': continue            # plaza paving is ours to decorate
            raise AssertionError((f.name, x, y, z, s[0], b))
    solid = {}
    for f in MINE:
        for x, y, z, b in cells(f):
            solid[(x, y, z)] = b
    gz = L.GZ
    for z in range(-270, -139):
        for y in range(7, 11):
            assert solid.get((0, y, z), 'air') == 'air', (z, y, solid.get((0, y, z)))
        if abs(z - gz) > 16:
            for y in (5, 6):
                assert solid.get((0, y, z), 'air') == 'air', (z, y)
    # the dais floor is continuous along the centreline (steps are <= 1 block apart)
    top = {}
    for (x, y, z), b in solid.items():
        if x == 0 and b != 'air' and y <= 6: top[z] = max(top.get(z, 0), y)
    for z in range(gz - 15, gz + 16):
        assert top.get(z, 0) >= 5, z
        assert abs(top.get(z, 5) - top.get(z + 1, 5)) <= 1, z


def test_entrances_and_walkability():
    for n in ('arrival_hall', 'checkpoint_w', 'checkpoint_e'):
        f = [g for g in MINE if g.name == n][0]
        v = L.Piece._private(f, ctx)
        for (x, y, z) in f.meta['entrances']:
            for yy in (y, y + 1, y + 2):
                assert v.get(x, yy, z) == 'air', (n, x, yy, z, v.get(x, yy, z))
            assert v.get(x, y - 1, z) not in (None, 'air')
        # the interior is lit: every standable interior cell has a lamp within 14 blocks (cheap proxy; the real
        # light_coverage check runs in `orchestrate validate`)
        lamps = [(x, y, z) for x, y, z, b in v.items() if b.split('[')[0] in ('sea_lantern',)]
        assert lamps, n
        bx = f.meta['interior_box']
        for x in range(bx[0], bx[3] + 1):
            for z in range(bx[2], bx[5] + 1):
                if v.get(x, 5, z) == 'air' and v.get(x, 4, z) not in (None, 'air'):
                    assert min(abs(x - lx) + abs(z - lz) + abs(5 - ly) for lx, ly, lz in lamps) <= 14, (n, x, z)


def test_determinism():
    for f in MINE:
        a = list(cells(f))
        L.Piece._cache.clear()
        b = list(cells(f))
        assert a == b, f.name


if __name__ == '__main__':
    for k, fn in sorted(globals().items()):
        if k.startswith('test_') and callable(fn):
            fn(); print('ok', k)
    print('all ok')
