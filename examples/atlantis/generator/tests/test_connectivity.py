#!/usr/bin/env python3
"""Walk-graph connectivity / accessibility tests (plain asserts).  Run from the project root:
    python3 tests/test_connectivity.py

Small cases use a fake dome (radius 40) and a solid stone world (y 0..29) in which rooms are carved, so only the
carved cells are walkable.  The last test builds a synthetic full-city deck and prints timings."""
import copy, math, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner
from atlantis import export_tiles as E
from atlantis.feature import FnFeature
from atlantis.volume import Volume
from atlantis import validate as V
from atlantis.validate import runner


def make_ctx(radius=40, height=30, margin=200):
    real = config.load()
    cfg, prof = copy.deepcopy(real.cfg), copy.deepcopy(real.profile)
    cfg['dome'].update(radius=radius, height_max=height)
    cfg['environment']['exterior_margin'] = margin
    prof['protect'] = []
    return config.Ctx(cfg, prof)


CTX = make_ctx()
TILES = [(-1, -1), (0, -1), (-1, 0), (0, 0)]
SOLID = FnFeature('solid', (-33, 0, -33, 32, 29, 32), lambda v, c: v.fill(-33, 0, -33, 32, 29, 32, 'stone'), 0)


def seed(name, x, z, r=1.5, hmax=60):
    return {'name': name, 'x': x, 'z': z, 'r': r, 'hmin': 0, 'hmax': hmax}


def feat(name, box, fn, meta=None):
    """Layer 40 feature: `fn(vol)` draws inside `box`."""
    return FnFeature(name, box, lambda v, c: fn(v), 40, meta)


def run(feats, seeds, checks='connectivity,accessibility', tiles=TILES):
    return V.validate(CTX, tiles, [SOLID] + feats, checks, jobs=1, options={'walk_seeds': seeds})['issues']


def of(issues, sev=None, text=None, check=None):
    return [i for i in issues if (sev is None or i.severity == sev) and (text is None or text in i.message)
            and (check is None or i.check == check)]


def main_nodes(issues):
    m = of(issues, 'info', 'main walk component')[0]
    return m.count


def swap(axis, x1, y1, z1, x2, y2, z2):
    """Box in (along, y, across) -> (x, y, z) or, for axis 'z', with x and z swapped."""
    return (x1, y1, z1, x2, y2, z2) if axis == 'x' else (z1, y1, x1, z2, y2, x2)


# ---------------------------------------------------------------- rooms joined by a door
def rooms(joined=True, door=True):
    def draw(v):
        v.fill(0, 1, 0, 7, 4, 7, 'air'); v.fill(12, 1, 0, 19, 4, 7, 'air')
        if joined:
            v.fill(8, 1, 3, 11, 4, 3, 'air')
            if door:
                v.set(9, 1, 3, 'oak_door[half=lower,facing=east]'); v.set(9, 2, 3, 'oak_door[half=upper,facing=east]')
    return feat('rooms', (0, 1, 0, 19, 4, 7), draw)


def test_two_rooms_door():
    iss = run([rooms()], [seed('A', 3, 3), seed('B', 15, 3)])
    assert not of(iss, 'error') and not of(iss, 'warn'), iss
    assert main_nodes(iss) == 64 + 64 + 4, main_nodes(iss)          # both rooms + the corridor, one component
    # no corridor: room B (64 nodes, inside a building feature) is an island, and its seed is not connected
    iss = run([rooms(joined=False)], [seed('A', 3, 3), seed('B', 15, 3)])
    isl = of(iss, 'error', 'island')
    assert len(isl) == 1 and isl[0].count == 64 and 'rooms' in isl[0].message, iss
    assert 12 <= isl[0].pos[0] <= 19 and isl[0].pos[1] == 1, isl[0].pos
    assert of(iss, 'error', 'seed B'), iss
    assert main_nodes(iss) == 64


# ---------------------------------------------------------------- steps, stairs, slabs, jumps
def stairs_world(kind):
    """Room A (floor y0) -> corridor x 8..11 -> room C (floor top y3, three blocks of rise)."""
    def draw(v):
        v.fill(0, 1, 0, 7, 4, 7, 'air'); v.fill(12, 4, 0, 19, 8, 7, 'air'); v.fill(8, 1, 3, 11, 8, 3, 'air')
        if kind == 'blocks':                      # full-block steps: a jump (1 block) each
            v.set(9, 1, 3, 'stone'); v.fill(10, 1, 3, 10, 2, 3, 'stone'); v.fill(11, 1, 3, 11, 3, 3, 'stone')
        elif kind == 'stairs':                    # stairs, no jump needed
            for k, x in enumerate((9, 10, 11)):
                v.set(x, 1 + k, 3, 'stone_stairs[facing=east,half=bottom]')
                if k: v.fill(x, 1, 3, x, k, 3, 'stone')
    return feat('stairs', (0, 1, 0, 19, 8, 7), draw)


def test_missing_staircase_island():
    iss = run([stairs_world('none')], [seed('A', 3, 3)])
    isl = of(iss, 'error', 'island')
    assert len(isl) == 1 and isl[0].count == 64 and isl[0].pos[1] == 4, iss      # room C floats 3 blocks up
    for kind in ('blocks', 'stairs'):
        iss = run([stairs_world(kind)], [seed('A', 3, 3), seed('C', 15, 3)])
        assert not of(iss, 'error') and not of(iss, 'warn'), (kind, iss)


def slab_world(step):
    def draw(v):
        v.fill(0, 1, 0, 7, 4, 7, 'air'); v.fill(12, 1, 0, 19, 6, 7, 'air'); v.fill(8, 1, 3, 11, 6, 3, 'air')
        if step == 'slab':                         # half-block floor: no jump needed
            v.fill(11, 1, 3, 11, 1, 3, 'stone_slab[type=bottom]'); v.fill(12, 1, 0, 19, 1, 7, 'stone_slab[type=bottom]')
        elif step == 'two':                        # two blocks up: impossible
            v.fill(11, 1, 3, 11, 2, 3, 'stone'); v.fill(12, 1, 0, 19, 2, 7, 'stone')
        elif step == 'lowceil':                    # one block up, but a 2-high corridor: no room to jump
            v.fill(8, 3, 3, 11, 6, 3, 'stone'); v.fill(12, 1, 0, 19, 1, 7, 'stone')
    return feat('slabs', (0, 1, 0, 19, 6, 7), draw)


def test_slab_step_up_and_jump_rules():
    iss = run([slab_world('slab')], [seed('A', 3, 3), seed('B', 15, 3)])
    assert not of(iss, 'error') and not of(iss, 'warn'), iss
    assert main_nodes(iss) == 64 + 64 + 4, main_nodes(iss)
    iss = run([slab_world('two')], [seed('A', 3, 3)])
    assert of(iss, 'error', 'island'), iss
    iss = run([slab_world('lowceil')], [seed('A', 3, 3)])
    assert of(iss, 'error', 'island'), iss


def drop_world(depth, axis='x'):
    """Upper room U (floor `depth` blocks above L's floor) next to lower room L, no wall between."""
    def draw(v):
        v.fill(*swap(axis, 0, depth + 1, 0, 7, 9, 7), 'air')           # U: floor top y = depth
        v.fill(*swap(axis, 8, 1, 0, 15, 9, 7), 'air')                  # L: floor top y 0
    return feat('drop', swap(axis, 0, 1, 0, 15, 9, 7), draw)


def test_drop_3_ok_one_way_and_4_blocked():
    # 3 block drop: U (seed) reaches L; seed in L cannot reach U (no way up) -> U is an island
    iss = run([drop_world(3)], [seed('U', 3, 3), seed('L', 12, 3)])
    assert not of(iss, 'error') and not of(iss, 'warn'), iss
    iss = run([drop_world(3)], [seed('L', 12, 3), seed('U', 3, 3)])
    assert [i.count for i in of(iss, 'error', 'island')] == [64], iss
    assert of(iss, 'error', 'seed U'), iss
    # 4 block drop: nothing connects the two rooms in either direction
    for seeds in ([seed('U', 3, 3)], [seed('L', 12, 3)]):
        iss = run([drop_world(4)], seeds)
        assert [i.count for i in of(iss, 'error', 'island')] == [64], iss
        assert main_nodes(iss) == 64


# ---------------------------------------------------------------- ladder
def ladder_world(ladder):
    def draw(v):
        v.fill(0, 1, 0, 7, 3, 7, 'air'); v.fill(0, 8, 0, 7, 11, 7, 'air'); v.fill(8, 1, 3, 8, 11, 3, 'air')
        if ladder:
            for y in range(1, 11): v.set(8, y, 3, 'ladder[facing=west]')
    return feat('ladder', (0, 1, 0, 8, 11, 7), draw)


def test_ladder_column():
    iss = run([ladder_world(True)], [seed('low', 3, 3, hmax=6)])
    assert not of(iss, 'error') and not of(iss, 'warn'), iss
    # the upper room (floor 7 blocks up, 64 nodes) is part of the main component only with the ladder
    n_with = main_nodes(iss)
    iss = run([ladder_world(False)], [seed('low', 3, 3, hmax=6)])
    assert [i.count for i in of(iss, 'error', 'island')] == [64], iss
    assert n_with - main_nodes(iss) >= 64 + 10 - 1, (n_with, main_nodes(iss))


# ---------------------------------------------------------------- water
def water_world(bridge=False):
    def draw(v):
        v.fill(0, 1, 0, 7, 4, 7, 'air'); v.fill(12, 1, 0, 19, 4, 7, 'air'); v.fill(8, 1, 0, 11, 4, 7, 'air')
        v.fill(8, 0, 0, 11, 1, 7, 'water')                         # pool: surface cell y 1, bed y -1
        if bridge: v.fill(8, 0, 3, 11, 0, 3, 'stone'); v.fill(8, 1, 3, 11, 1, 3, 'air')
    return feat('pool', (0, 1, 0, 19, 4, 7), draw)


def test_water_only_crossing_is_warning():
    iss = run([water_world()], [seed('A', 3, 3), seed('B', 15, 3)])
    assert not of(iss, 'error'), iss
    assert of(iss, 'warn', 'seed B') and 'swimming' in of(iss, 'warn', 'seed B')[0].message, iss
    assert of(iss, 'warn', 'only by swimming'), iss
    iss = run([water_world(bridge=True)], [seed('A', 3, 3), seed('B', 15, 3)])
    assert not of(iss, 'error') and not of(iss, 'warn'), iss


# ---------------------------------------------------------------- tile borders
def border_world(axis, mode):
    """Rooms on both sides of the tile border (x = -1|0, or z = -1|0 for axis 'z') joined by a corridor."""
    def draw(v):
        if mode == 'drop':                                         # A (floor top y 3) directly beside B (floor y 0)
            v.fill(*swap(axis, -12, 4, 0, -1, 7, 7), 'air')
            v.fill(*swap(axis, 0, 1, 0, 11, 7, 7), 'air')
            return
        v.fill(*swap(axis, -12, 1, 0, -5, 4, 7), 'air'); v.fill(*swap(axis, 4, 1, 0, 11, 4, 7), 'air')
        v.fill(*swap(axis, -4, 1, 3, 3, 4, 3), 'air')
        if mode == 'wall': v.fill(*swap(axis, -1, 1, 3, -1, 4, 3), 'stone')       # broken on the tile's last column
        if mode == 'wall2': v.fill(*swap(axis, 0, 1, 3, 0, 4, 3), 'stone')        # ... or the first column
    return feat('border', swap(axis, -12, 1, 0, 11, 7, 7), draw)


def test_cross_tile_stitching_x_and_z():
    for axis in 'xz':
        a, b = (seed('A', -8, 3), seed('B', 8, 3)) if axis == 'x' else (seed('A', 3, -8), seed('B', 3, 8))
        iss = run([border_world(axis, 'ok')], [a, b])
        assert not of(iss, 'error') and not of(iss, 'warn'), (axis, iss)
        assert main_nodes(iss) == 64 + 64 + 8, (axis, main_nodes(iss))
        for mode, isl in (('wall', 64 + 4), ('wall2', 64 + 3)):      # room B plus the corridor columns beyond the wall
            iss = run([border_world(axis, mode)], [a, b])
            assert [i.count for i in of(iss, 'error', 'island')] == [isl], (axis, mode, iss)
        # one-way drop across the border: seed on the high side reaches the low side only
        hi, lo = (seed('A', -8, 3), seed('B', 8, 3)) if axis == 'x' else (seed('A', 3, -8), seed('B', 3, 8))
        iss = run([border_world(axis, 'drop')], [hi, lo])
        assert not of(iss, 'error'), (axis, iss)
        iss = run([border_world(axis, 'drop')], [lo, hi])
        assert [i.count for i in of(iss, 'error', 'island')] == [96], (axis, iss)      # A is 12 x 8


# ---------------------------------------------------------------- accessibility
def access_world(entrances, door=True, wall_x=None, interior=True):
    def yard(v): v.fill(-8, 1, 0, -1, 4, 7, 'air')

    def hall(v):
        v.fill(1, 1, 0, 8, 4, 7, 'air')
        if door:
            v.fill(0, 1, 3, 0, 2, 3, 'air')
            v.set(0, 1, 3, 'oak_door[half=lower,facing=east]'); v.set(0, 2, 3, 'oak_door[half=upper,facing=east]')
        if wall_x is not None: v.fill(wall_x, 1, 0, wall_x, 4, 7, 'stone')
    meta = {'entrances': entrances, 'interior_box': (1, 1, 0, 8, 4, 7), 'roof_y': 5}
    if interior: meta['interior'] = True
    return [feat('yard', (-8, 1, 0, -1, 4, 7), yard),
            feat('hall', (0, 1, 0, 9, 5, 7), hall, meta)]


def test_accessibility_entrances():
    sd = [seed('yard', -4, 3)]
    iss = run(access_world([(0, 1, 3)]), sd)
    assert not of(iss, 'error') and not of(iss, 'warn', check='accessibility'), iss
    assert of(iss, 'info', 'feature(s)', 'accessibility')[0].count == 1
    # entrance declared where the wall is solid: no standable node
    iss = run(access_world([(0, 1, 5)]), sd)
    assert of(iss, 'error', 'no standable node', 'accessibility'), iss
    # no door: the entrance cell is inside the sealed hall
    iss = run(access_world([(2, 1, 3)], door=False), sd)
    assert of(iss, 'error', 'not in the main component', 'accessibility'), iss
    assert of(iss, 'warn', 'interior nodes', 'accessibility'), iss
    # no entrance at all but a reachable interior is fine; none reachable only warns (interior flag)
    iss = run(access_world(None), sd)
    assert not of(iss, 'error', check='accessibility'), iss


def test_accessibility_interior_fraction():
    sd = [seed('yard', -4, 3)]
    # wall across the hall at x = 5: 4 of 8 columns reachable via the door -> 57% < 80%
    iss = run(access_world([(0, 1, 3)], wall_x=5), sd)
    w = of(iss, 'warn', '% of', 'accessibility')
    assert len(w) == 1 and '57%' in w[0].message, iss
    assert not of(iss, 'error', check='accessibility'), iss
    # wall at x = 8: 7 of 8 columns = 87% -> fine
    iss = run(access_world([(0, 1, 3)], wall_x=8), sd)
    assert not of(iss, 'warn', check='accessibility'), iss
    # entrance reachable, but an interior box with no standable node behind it: error
    ws = access_world([(0, 1, 3)], interior=False)
    ws[1].meta['interior_box'] = (3, 1, 0, 8, 1, 7)       # feet y 1..1 only; make the interior solid at y 1
    f = ws[1]
    orig = f.fn
    f.fn = lambda v, c: (orig(v, c), v.fill(1, 1, 0, 8, 1, 7, 'stone'))
    iss = run(ws, sd)
    assert of(iss, 'error', 'no interior node reachable', 'accessibility'), iss


# ---------------------------------------------------------------- block rules
def test_block_rules():
    from atlantis.validate import walk as W, classify as K
    P = lambda s: W.block_props(K.info(s))
    assert P('oak_door[half=lower]')[0] == 1 and P('light[level=5]')[0] == 1 and P('white_carpet')[0] == 1
    assert P('stone')[1] == 2 and P('glass')[1] == 2 and P('stone_slab[type=bottom]')[1] == 1
    assert P('stone_slab[type=top]')[1] == 2 and P('stone_stairs[half=bottom]')[1] == 4
    assert P('stone_stairs[half=top]')[1] == 2 and P('oak_fence')[1] == 3 and P('cobblestone_wall')[1] == 3
    assert P('ladder[facing=north]')[2] == 1 and P('vine')[2] == 1 and P('water')[3] == 1 and P('lava') == (0, 0, 0, 0, 0)
    # movement rule corner cases
    assert W.can(2, 5, 0, 3, 5, 0) and W.can(3, 5, 0, 2, 5, 0)          # half step both ways
    assert W.can(2, 5, 0, 4, 5, 0) and not W.can(2, 2, 0, 4, 5, 0)       # jump needs 3 free cells at the takeoff
    assert not W.can(2, 9, 0, 6, 9, 0) and W.can(8, 9, 0, 2, 9, 0)       # 2 blocks up no, 3 blocks down yes
    assert not W.can(10, 9, 0, 2, 9, 0)                                  # 4 blocks down no
    assert not W.can(8, 9, 0, 2, 4, 0)                                   # ceiling in the landing column


# ---------------------------------------------------------------- synthetic full city
def synth_feature():
    """Stand-in fabric: deck y 0..4 over planner.has_deck, lagoons as water (-3..0, bed -4), bridges along the minor
    radials at y 4, and a stepped hall (floor y 0..4) at each airlock."""
    box = (-600, -4, -600, 599, 4, 599)
    mr = planner.minor_radials()
    wedge = [(a['plan'], a['bridge'], a['width']) for a in mr]
    halls = [(a['plan'],) for a in planner.airlocks()]

    def build(vol, ctx):
        c = vol.clip(*box)
        if not c: return
        x1, z1, x2, z2 = c[0], c[2], c[3], c[5]
        for z in range(z1, z2 + 1):
            run_start = None
            for x in range(x1, x2 + 2):
                d = x <= x2 and planner.has_deck(x, z)
                if d and run_start is None: run_start = x
                elif not d and run_start is not None:
                    vol.fill(run_start, 0, z, x - 1, 4, z, 'quartz_block'); run_start = None
        rmin, rmax = planner.INNER_R - 2, planner.PERIM_R0 + 2
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                r2 = x * x + z * z
                if r2 < rmin * rmin or r2 > rmax * rmax: continue
                if planner.zone(x, z) == 'lagoon':
                    vol.fill(x, -3, z, x, 0, z, 'water'); vol.set(x, -4, z, 'sand')
        for plan, (b0, b1), width in wedge:
            for r in range(b0, b1 + 1):
                for t in range(-(width // 2), width // 2 + 1):
                    px, pz = planner.from_polar(r, plan)
                    ux, uz = planner.from_polar(1.0, plan)
                    x, z = int(round(px - uz * t)), int(round(pz + ux * t))
                    vol.set(x, 4, z, 'quartz_block')
        for plan, in halls:
            ux, uz = planner.from_polar(1.0, plan)
            for al in range(584, 597):
                top = 4 if al <= 589 else max(0, 4 - (al - 589))
                for t in range(-3, 4):
                    x, z = int(round(ux * al - uz * t)), int(round(uz * al + ux * t))
                    vol.fill(x, 0, z, x, top, z, 'quartz_block')
    return FnFeature('synthetic_city', box, build, 30, {'water_ok': True})


def test_synthetic_city():
    from atlantis.validate import core
    core.OPTIONS['walk_seeds'] = None            # planner seeds (earlier tests set custom ones)
    ctx = config.load()
    f = synth_feature()
    checks = V.resolve_checks('connectivity,accessibility')
    tiles = E.all_tiles(ctx)
    partials, tg, nodes, deck_tiles, t_build = [], 0.0, 0, 0, 0.0
    worst = 0.0
    t_all = time.perf_counter()
    for tx, tz in tiles:
        box = E.tile_bounds(ctx, tx, tz)
        t0 = time.perf_counter()
        vol = Volume(*box)
        if E.feature_intersects(f, box): f.build(vol, ctx)
        t_build += time.perf_counter() - t0
        iss, par, tim = runner.check_tile(ctx, tx, tz, [f], checks, vol=vol)
        assert not iss, iss
        partials.extend(par)
        g = par[0].data
        nodes += g['nodes']
        if g['nodes'] > 500:
            deck_tiles += 1; tg += tim['connectivity']; worst = max(worst, tim['connectivity'])
    t_tiles = time.perf_counter() - t_all
    t0 = time.perf_counter()
    issues, rtim = runner.reduce_all(ctx, checks, partials, [f])
    t_red = time.perf_counter() - t0
    report = {'issues': issues, 'timing': {**{'reduce:' + k: v for k, v in rtim.items()}}, 'tiles': len(tiles),
              'wall': t_red}
    print(V.format_table(report, checks))
    for i in issues: print(f'    [{i.severity}] {i.check}: {i.message}')
    avg = tg / max(1, deck_tiles) * 1000
    print(f'    synthetic city: {len(tiles)} tiles, {nodes} nodes, {deck_tiles} tiles with >500 nodes')
    print(f'    graph extraction (connectivity fn, incl. tinfo): avg {avg:.1f} ms, worst {worst * 1000:.1f} ms per '
          f'deck tile; reduce {t_red:.2f} s total (both checks, {rtim}); tile loop {t_tiles:.1f} s '
          f'(volume build {t_build:.1f} s)')
    assert not [i for i in issues if i.severity in ('error', 'warn')], issues
    main = of(issues, 'info', 'main walk component')[0]
    assert main.count > 500_000, main            # the whole deck is one component
    assert not of(issues, 'info', 'seed(s) outside'), issues     # tower, gate plaza and all six airlock halls seeded
    assert avg < 100 and t_red < 60, (avg, t_red)
    # every district with deck cells was seen by the district check
    decks = {d for p in partials for d in p.data.get('graph', p.data)['deck_d']}
    assert decks == {d for d, v in planner.DISTRICTS.items() if v[1] != 'exterior'}, decks
    # cut the city: drop every tile between r 270 and 330 -> piers, perimeter and airlock halls are cut off
    from atlantis.validate.core import parse_key

    def ring(p):
        tx, tz = parse_key(p.tile)
        t = ctx.tile
        return 270 <= math.hypot((tx + .5) * t, (tz + .5) * t) <= 330
    cut = [p for p in partials if not ring(p)]
    t0 = time.perf_counter()
    iss2, _ = runner.reduce_all(ctx, checks, cut, [f])
    print(f'    cut city reduce: {time.perf_counter() - t0:.2f} s, {len(of(iss2, "error"))} errors, '
          f'e.g. {of(iss2, "error")[0].message!r}')
    bad = {i.message.split(' (')[0] for i in of(iss2, 'error', 'district')}
    assert {'district G', 'district D', 'district I', 'district E', 'district C1'} <= bad, bad
    assert not bad & {'district A', 'district B'}, bad
    assert of(iss2, 'error', 'seed Airlock hall'), iss2


if __name__ == '__main__':
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    only = sys.argv[1:]
    for name, fn in tests:
        if only and not any(o in name for o in only): continue
        t0 = time.time()
        fn()
        print(f'ok  {name}  ({time.time() - t0:.2f}s)')
    print(f'{len(tests)} tests passed')
