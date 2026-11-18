#!/usr/bin/env python3
"""Fabric tests (plain asserts). Run from the project root:  python3 tests/test_fabric.py"""
import math, os, sys, time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, fabric, planner as PL, dome as D, orchestrate
from atlantis import export_tiles as E
from atlantis.util import base_name

ctx = config.load()
FEATS = [f for f in orchestrate.load_generators() if f.layer < 30]   # base fabric only: later layers (roads, tower) cover parts of it
FAB = [f for f in FEATS if f.name.startswith('fabric_')]

AIRISH = {'air', 'cave_air', 'void_air', None}
NONSOLID = {'light', 'end_rod', 'seagrass', 'tall_seagrass', 'kelp', 'kelp_plant', 'sea_pickle', 'water'}
NONSOLID_SUFFIX = ('_pane', '_stairs', '_slab', '_coral', '_coral_fan', '_wall', '_fence')


def solid(b):
    """Strict: a full block that water cannot enter."""
    if b in AIRISH: return False
    n = base_name(b)
    return not (n in NONSOLID or n.endswith(NONSOLID_SUFFIX) or n in AIRISH)


def wet(b):
    if b is None: return False
    n = base_name(b)
    return n in ('water', 'seagrass', 'tall_seagrass', 'kelp', 'kelp_plant', 'bubble_column') or 'waterlogged=true' in b


def walkable(vol, x, y, z):
    return (vol.contains(x, y, z) and vol.get(x, y, z) in AIRISH | {'light[level=15]'} and
            vol.get(x, y + 1, z) in AIRISH | {'light[level=15]'} and solid(vol.get(x, y - 1, z))
            or (vol.contains(x, y, z) and base_name(vol.get(x, y - 1, z) or 'air').endswith(('_stairs', '_slab')) and
                vol.get(x, y, z) in AIRISH | {'light[level=15]'} and vol.get(x, y + 1, z) in AIRISH | {'light[level=15]'}))


def free(b):
    return b in AIRISH or b == 'light[level=15]'


def test_lagoon_containment():
    for lag in [f for f in FAB if f.name.startswith('fabric_lagoon')][:2]:
        x1, _, z1, x2, _, z2 = lag.bbox
        box = (x1 - 3, ctx.y_min, z1 - 3, x2 + 3, 8, z2 + 3)
        vol = E.build_box(ctx, box, FEATS)
        n = bad = 0
        for x, y, z, b in vol.items():
            if base_name(b) != 'water' or y > PL.LAGOON_SURFACE or not (ctx.inside_dome(x, y, z) or math.hypot(x, z) < 606): continue  # lagoon water only (fountains are roads')
            n += 1
            for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, -1, 0), (0, 1, 0)):
                if not vol.contains(x + dx, y + dy, z + dz): continue
                nb = vol.get(x + dx, y + dy, z + dz)
                if wet(nb) or solid(nb): continue
                if dy == 1 and y == PL.LAGOON_SURFACE: continue          # open surface
                bad += 1
                if bad < 5: print('  leak', (x, y, z), (dx, dy, dz), nb)
        assert n > 1000, (lag.name, n)
        assert bad == 0, (lag.name, bad)
        # water only in lagoon columns, only y -3..0
        for x, y, z, b in vol.items():
            if base_name(b) == 'water' and ctx.inside_dome(x, y, z) and y <= PL.LAGOON_SURFACE:   # fountains above
                assert PL.zone(x, z) == 'lagoon' and PL.LAGOON_FLOOR < y <= PL.LAGOON_SURFACE, (x, y, z)
    print('lagoon containment ok')


def _hall_box(h):
    xs = [h.bbox[0], h.bbox[3]]; zs = [h.bbox[2], h.bbox[5]]
    for s in D.make_features(ctx)[2:]:
        if s.plan == h.plan:
            xs += [s.bbox[0], s.bbox[3]]; zs += [s.bbox[2], s.bbox[5]]
    return (min(xs), -2, min(zs), max(xs), 12, max(zs))


def test_airlock_walk():
    halls = [f for f in FEATS if isinstance(f, fabric.AirlockHall)]
    assert len(halls) == 6
    for h in halls:
        box = _hall_box(h)
        vol = E.build_box(ctx, box, FEATS)
        cg, sg = h.cg, h.sg

        def at(u, v, y):
            return int(round(u * cg - v * sg)), y, int(round(u * sg + v * cg))
        # passage cells y 1..2 from r 588 to 600 are air
        for u in range(588, 601):
            for v in (-1, 0, 1):
                x, _, z = at(u, v, 0)
                uu, vv = h.uv(x, z)
                if abs(vv) > D.TUN_HW: continue
                for y in (1, 2):
                    assert free(vol.get(x, y, z)), (h.name, 'passage blocked', (x, y, z), vol.get(x, y, z))
        # walk (step up <= 1, drop <= 3, 2 free cells above) from the deck top (u 575) into the passage (u 600)
        sx, _, sz = at(575, 0, 0)
        start = (sx, PL.WALK_Y, sz)
        goal = at(600, 0, 1)
        assert walkable(vol, *start), (h.name, start, vol.get(*start), vol.get(start[0], 4, start[2]))
        seen = {start}; dq = deque([start]); ok = False
        while dq:
            c = dq.popleft()
            if abs(c[0] - goal[0]) + abs(c[2] - goal[2]) <= 1 and c[1] == 1: ok = True; break
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for dy in (1, 0, -1, -2, -3):
                    n = (c[0] + dx, c[1] + dy, c[2] + dz)
                    if n in seen or not walkable(vol, *n): continue
                    if dy == 1 and not free(vol.get(c[0], c[1] + 2, c[2])): continue
                    seen.add(n); dq.append(n)
                    break
        assert ok, (h.name, 'no walk from the deck into the airlock passage', len(seen))
    print('airlock walk ok')


def test_deck_height():
    pts = [(0, 0), (100, 50), (-250, 100), (0, -200), (0, -400), (60, -420), (0, -530), (280, 0), (0, 400), (0, 560)]
    for x, z in pts:
        assert PL.has_deck(x, z), (x, z, PL.zone(x, z))
        tx, tz = x // ctx.tile, z // ctx.tile
        vol = E.build_tile(ctx, tx, tz, FEATS)
        t = fabric.deck_top_at(ctx, x, z)
        assert t == PL.DECK_TOP, (x, z, t)
        for y in range(1, t + 1):
            assert solid(vol.get(x, y, z)), (x, y, z, vol.get(x, y, z))
        assert not solid(vol.get(x, t + 1, z)) or base_name(vol.get(x, t + 1, z)) in ('quartz_pillar',) or \
            base_name(vol.get(x, t + 1, z)).endswith('_pane'), (x, z, vol.get(x, t + 1, z))
    # perimeter ramp: top never leaves less than 2 free cells under the shell
    for r in range(566, 592, 2):
        x, z = r, 40
        t = fabric.deck_top_at(ctx, x, z)
        if t is None: continue
        vol = E.build_tile(ctx, x // ctx.tile, z // ctx.tile, FEATS)
        if t >= 1:
            assert solid(vol.get(x, t, z)) or base_name(vol.get(x, t, z)).endswith('_stairs'), (r, vol.get(x, t, z))
        for y in (t + 1, t + 2):
            assert free(vol.get(x, y, z)), (r, y, vol.get(x, y, z))
    # edges: a lagoon-facing column has the waterline course and a cornice
    ed = None
    for z in range(-400, -300):
        for x in range(60, 100):
            if fabric.edge_dirs(x, z): ed = (x, z); break
        if ed: break
    assert ed
    vol = E.build_tile(ctx, ed[0] // ctx.tile, ed[1] // ctx.tile, FEATS)
    assert vol.get(ed[0], 0, ed[1]) == 'dark_prismarine' and vol.get(ed[0], 1, ed[1]) == 'dark_prismarine'
    assert base_name(vol.get(ed[0], 4, ed[1])).endswith('_stairs')
    print('deck height ok')


def test_rim_access():
    """The deck ramps down to the rim floor all around the circle: the top reaches y 0 at the rim."""
    n = 0
    for k in range(0, 720):
        a = k / 2.0
        r_in = None
        for r in range(566, 589):
            x, z = PL.from_polar(r, a)
            t = fabric.deck_top_at(ctx, int(round(x)), int(round(z)))
            if t is not None and t <= 1: r_in = r; break
        assert r_in is not None and r_in <= 588, (a, r_in)
        n += 1
    print('rim ramp ok around', n, 'sample points')


def test_determinism():
    for tx, tz in ((0, -10), (7, -9), (17, 0)):
        h = [E.tile_hash(ctx, E.build_tile(ctx, tx, tz, FEATS)) for _ in range(2)]
        assert h[0] == h[1], (tx, tz)
        # feature build order independent of the order of FEATS
        h2 = E.tile_hash(ctx, E.build_tile(ctx, tx, tz, list(reversed(FEATS))))
        assert h2 == h[0], (tx, tz, 'order dependent')
    print('determinism ok')


def test_timing():
    # typical tiles: core, inner ring, pier + lagoon edge, perimeter ramp + hall
    worst = 0
    for tx, tz in ((0, 0), (-3, -6), (0, -12), (3, -13), (17, 0), (18, 0), (-10, 14)):
        t0 = time.time()
        for _ in range(3):
            vol = fvol = E.build_tile(ctx, tx, tz, [f for f in FEATS if f.layer in (25, 26)])
        dt = (time.time() - t0) / 3
        worst = max(worst, dt)
        print(f'  tile ({tx},{tz}) fabric build {dt * 1000:.0f} ms')
    assert worst < 0.15, worst
    print('timing ok (worst %.0f ms)' % (worst * 1000))


if __name__ == '__main__':
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            t0 = time.time()
            fn()
            print(f'{name}: {time.time() - t0:.1f}s')
    print('all fabric tests passed')
