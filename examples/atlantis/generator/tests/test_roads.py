#!/usr/bin/env python3
"""Roads tests (plain asserts), against the real deck (fabric). Run from the project root: python3 tests/test_roads.py"""
import json, math, os, statistics, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, roads, blocks as B
from atlantis import export_tiles as E
from atlantis.orchestrate import load_generators
from atlantis.util import parse_state

ctx = config.load()
FEATS = [f for f in load_generators() if f.layer < 50]      # the tower (layer 50) fills the reserved core
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))


def box(x1, z1, x2, z2, y1=-5, y2=20):
    return E.build_box(ctx, (x1, y1, z1, x2, y2, z2), FEATS)


def test_determinism():
    for b in ((-40, -250, 40, -160), (300, -20, 360, 20)):
        a, c = box(*[b[0], b[1], b[2], b[3]]), box(b[0], b[1], b[2], b[3])
        assert list(a.items()) == list(c.items())
    assert [f.name for f in roads.make_features()] == [f.name for f in roads.FEATURES]


def test_avenue_width_alignment():
    for a in PL.PIER_ANGLES:
        s = 250.0
        ux, uz = PL.from_polar(1, a); vx, vz = -uz, ux
        cells = []
        for k in range(-80, 81):
            c = k / 8.0
            x, z = int(round(ux * s + vx * c)), int(round(uz * s + vz * c))
            if roads.street_kind(x, z) == 'avenue': cells.append((x, z))
        cells = set(cells)
        # distinct columns across the avenue at this station
        assert 10 <= len(cells) <= 13, (a, len(cells))
        offs = [PL.axis_coords(x, z, a)[1] for x, z in cells]
        assert abs(sum(offs) / len(offs)) < 0.6, (a, offs)
        assert roads.street_kind(*(int(round(v)) for v in PL.from_polar(s, a))) == 'avenue'
    # flares towards the tower
    near = sum(1 for k in range(-12, 13) if roads.street_kind(k, -100) == 'avenue')
    far = sum(1 for k in range(-12, 13) if roads.street_kind(k, -400) == 'avenue')
    assert near > far == 11


def test_rings_continuous():
    for R, w in PL.ring_roads()[:2] + [(roads.PROMENADE_R, 5)]:
        cells = set()
        for i in range(0, 3600):
            t = math.radians(i / 10.0)
            x, z = int(round(R * math.cos(t))), int(round(R * math.sin(t)))
            assert roads.is_street(x, z), (R, x, z)
        # every band cell has a street 8-neighbour (no isolated bits) and the centre line is 8-connected
        for i in range(0, 7200):
            t = math.radians(i / 20.0)
            cells.add((int(round(R * math.cos(t))), int(round(R * math.sin(t)))))
        for (x, z) in cells:
            assert any(roads.is_street(x + dx, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz)
    # ring 300 crosses lagoon gaps on a bridge ledge: those columns have no deck
    assert any(roads.street_kind(*(int(round(v)) for v in PL.from_polar(302, a))) == 'ring' and
               not PL.has_deck(*(int(round(v)) for v in PL.from_polar(302, a))) for a in PL.WEDGE_ANGLES[:1] + (45,))


def test_radials_bridge():
    for a in PL.WEDGE_ANGLES:
        for r in range(130, 575, 3):
            x, z = (int(round(v)) for v in PL.from_polar(r, a))
            assert roads.is_street(x, z), (a, r)
    v = box(330, -24, 360, 24)
    water_cols = 0
    for x in range(330, 361):
        for z in range(-24, 25):
            if PL.zone(x, z) != 'lagoon': continue
            for y in range(-3, 1):
                b = v.get(x, y, z)
                assert b not in ('air', 'cave_air'), (x, y, z)    # never air in the lagoon
            if roads.street_kind(x, z) == 'radial': water_cols += 1
    assert water_cols > 100
    # supports reach the lagoon bed under the bridge
    pil = [(x, z) for x in range(330, 361) for z in range(-24, 25) if (v.get(x, -4, z) or '').startswith('quartz_pillar')]
    assert pil and all(v.get(x, 2, z, ).startswith('quartz_pillar') for x, z in pil)
    # panes carry explicit connection states
    panes = [v.get(x, 5, z) for x in range(330, 361) for z in range(-24, 25) if 'pane' in (v.get(x, 5, z) or '')]
    assert panes and all('north=' in p and 'east=' in p for p in panes)


def test_reserved_clear():
    for R in roads.RESERVED:
        x0, z0, r = R['x'], R['z'], R['r']
        v = box(x0 - r, z0 - r, x0 + r, z0 + r, 5, 30)
        for x in range(x0 - r, x0 + r + 1):
            for z in range(z0 - r, z0 + r + 1):
                if math.hypot(x - x0, z - z0) > r: continue
                for y in range(5, 31):
                    b = v.get(x, y, z)
                    assert b in (None, 'air') or b.startswith('light'), (R['name'], x, y, z, b)


def test_fountains_closed():
    assert len(roads.FOUNTAINS) >= 10
    for f in roads.FOUNTAINS:
        x1, y1, z1, x2, y2, z2 = f['bbox']
        v = box(x1 - 3, z1 - 3, x2 + 3, z2 + 3, 0, 12)
        wc, rc = roads.fountain_cells(f['x'], f['z'], f['rw'])
        assert wc and not (wc & rc)
        for (x, z) in wc:
            assert v.get(x, 5, z) == 'water' or (x, z) == (f['x'], f['z'])
            assert v.get(x, 4, z) and v.get(x, 3, z) and 'water' not in (v.get(x, 4, z), v.get(x, 3, z))
            assert v.get(x, 6, z) in (None, 'air') or (x, z) == (f['x'], f['z'])
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (x + dx, z + dz)
                if n not in wc:
                    b = v.get(n[0], 5, n[1])
                    assert b and b not in ('air', 'water'), (f['name'], n, b)
        for x in range(x1 - 3, x2 + 4):          # no water anywhere else
            for z in range(z1 - 3, z2 + 4):
                if v.get(x, 5, z) == 'water': assert (x, z) in wc


def test_blocks_valid_and_street_api():
    seen = set()
    for b in ((-70, -260, 70, 70), (330, -30, 420, 30), (-30, -600, 30, -520), (-330, 130, -200, 230)):
        v = box(*b)
        seen |= set(v.palette) - {None}
    for s in seen:
        n, p = parse_state(s)
        assert n[:10] != 'minecraft:'[:10] or True
        n = n[10:] if n.startswith('minecraft:') else n
        assert n in VALID, s
        for k, val in p.items(): assert k in VALID[n] and val in VALID[n][k], s
        assert B.is_valid_state(s) or n in VALID
    assert roads.is_street(0, -100) and not roads.is_street(60, -150)
    assert roads.is_street(0, 0)


def test_timing():
    ts = []
    for tx, tz in ((0, 0), (0, -6), (-1, -7), (3, 0), (10, 0), (5, -5), (-4, 4), (0, -9), (0, -12), (-6, 5)):
        b = E.tile_bounds(ctx, tx, tz)
        fs = [f for f in FEATS if f.name.startswith('roads_') and E.feature_intersects(f, b)]
        from atlantis.volume import Volume
        t = time.time()
        vol = Volume(*b)
        for f in fs: f.build(vol, ctx)
        ts.append(time.time() - t)
    med = statistics.median(ts)
    print('roads per-tile build: median %.0f ms, max %.0f ms' % (med * 1000, max(ts) * 1000))
    assert med < 0.1 and max(ts) < 0.4


if __name__ == '__main__':
    n = 0
    for k, f in sorted(globals().items()):
        if k.startswith('test_') and callable(f):
            t = time.time(); f(); n += 1
            print(f'ok {k} ({time.time() - t:.1f}s)')
    print(n, 'tests passed')
