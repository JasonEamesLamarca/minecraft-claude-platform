#!/usr/bin/env python3
"""Tests for the Central Tower interiors (tower_interior.py, interior_kit.py, interior_floors.py).
Run from the project root:   python3 tests/test_tower_interior.py        (plain asserts, stdlib only)

Uses the stand-in tower structure (tests/_standin_tower.py: wall, slabs, core spiral, portals), never registered.
  * determinism (two builds are identical, also across tiles)
  * the interior never writes the core / landing (r <= CORE_R + 1), the wall (r > usable_r) or the slab layer
    (except same-height floor inlays that replace a slab block)
  * 2-wide circulation: from the core landing through the clear ring to the doorway of every room (2x2 flood fill)
  * light: every walkable cell of every floor has block light >= 1 (validator light model)
  * timing: < 0.3 s per tile
"""
import math, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

from atlantis import config, tower_spec as T, export_tiles as E
from atlantis import tower_interior as TI
from atlantis.validate import classify as K, core as VC, c_light as CL
from atlantis.orchestrate import load_generators


def all_features(ctx):
    return load_generators()


class _NoStand:
    pass

CTX = config.load()
FLOORS = T.floor_list()


def base():
    return [f for f in all_features(CTX) if not f.name.startswith('tower_interior')]


def interior_features():
    return [f for f in all_features(CTX) if f.name.startswith('tower_interior')]


def build_floor(F, with_interior=True, box=None):
    """Volume over the tower area for one floor (y range F - 1 .. F + 7), standin + (optionally) the interiors."""
    box = box or (-36, F - 1, -36, 36, F + 7 if F != 5 else 18, 36)
    feats = base() + (interior_features() if with_interior else [])
    return E.build_box(CTX, box, feats)


def info(F):
    return [f for f in FLOORS if f[0] == F][0]


def clear(state):
    """True if a body can occupy the cell (air, light, non-colliding)."""
    if state is None: return True
    if state.startswith('light_blue_carpet'): return True
    return K.info(state).cls in (K.AIR, K.NOCOLL)


# ------------------------------------------------------------------------------------------------------------
def test_determinism():
    a = {F[0]: TI.build_plan(CTX.seed, F[0]) for F in FLOORS}
    TI.build_plan.cache_clear()
    for f in FLOORS:
        b = TI.build_plan(CTX.seed, f[0])
        assert a[f[0]].c == b.c, f'floor {f[0]} not deterministic'
    # tile independence: the same cell gets the same block whichever tile builds it
    v1 = E.build_box(CTX, (-32, 5, -32, -1, 100, -1), base() + interior_features())
    v2 = E.build_box(CTX, (-32, 5, -32, 31, 100, 31), base() + interior_features())
    for (x, y, z, b) in v1.items():
        assert v2.get(x, y, z) == b, (x, y, z)
    print('determinism ok')


def test_limits():
    """Everything the interiors change relative to the bare stand-in respects core / wall / slab limits."""
    floors = {f[0]: f for f in FLOORS}
    for F, walk, ceil, name, prog, R in FLOORS:
        if R < 6.5: continue
        box = (-36, F, -36, 36, ceil if F != 5 else 17, 36)
        bare = E.build_box(CTX, box, base())
        full = E.build_box(CTX, box, base() + interior_features())
        changed = 0
        for y in range(box[1], box[4] + 1):
            for z in range(-36, 37):
                for x in range(-36, 37):
                    b0, b1 = bare.get(x, y, z), full.get(x, y, z)
                    if b0 == b1: continue
                    changed += 1
                    r = math.hypot(x, z)
                    if F == 17 and y == F and r < TI.MEZZ_OPEN_R: continue      # bridges over the well / stair top
                    assert r > T.CORE_R + 1, f'{name}: core cell changed {x, y, z} r={r:.1f}'
                    assert r <= R + 1e-9, f'{name}: wall zone changed {x, y, z} r={r:.1f} > {R:.1f}'
                    if y == F:    # floor slab layer: only a same-height full block (pattern inlay) or the mezzanine bridge
                        n = K.info(b1).name
                        assert b1 and not n.endswith(('_stairs', '_slab', '_pane', '_wall', '_fence', '_carpet', 'air')), \
                            f'{name}: slab layer got {b1} at {x, y, z}'
                        assert b0 is not None or F == 17, f'{name}: slab layer written where there was no slab {x, y, z}'
        assert changed > 100, f'{name}: nothing built ({changed})'
    print('limits ok')


def _clear_cells(F, vol, R):
    W = F + 1
    out = set()
    n = int(R) + 1
    for z in range(-n, n + 1):
        for x in range(-n, n + 1):
            r = math.hypot(x, z)
            if r <= 5.0 or r > R: continue
            fl = vol.get(x, F, z)
            if fl is None or K.info(fl).cls != K.FULL: continue
            if clear(vol.get(x, W, z)) and clear(vol.get(x, W + 1, z)): out.add((x, z))
    return out


def reach2(cells, starts):
    """Flood fill over 2x2 squares of clear cells; returns the set of cells covered by reachable squares."""
    sq = {(x, z) for (x, z) in cells if all(c in cells for c in ((x + 1, z), (x, z + 1), (x + 1, z + 1)))}
    seen, stack = set(), [s for s in starts if s in sq]
    seen.update(stack)
    while stack:
        x, z = stack.pop()
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + d[0], z + d[1])
            if n in sq and n not in seen: seen.add(n); stack.append(n)
    cov = set()
    for (x, z) in seen: cov.update(((x, z), (x + 1, z), (x, z + 1), (x + 1, z + 1)))
    return cov


def circulation(F):
    """-> (problems, stats) for floor F: each registered room's doorway and centre must lie in the 2-wide network."""
    F_, walk, ceil, name, prog, R = info(F)
    plan = TI.build_plan(CTX.seed, F)
    vol = build_floor(F)
    cells = _clear_cells(F, vol, R)
    starts = [(x, z) for (x, z) in cells if 5.0 < math.hypot(x, z) <= 7.8 and (x + 1, z + 1) in cells]
    cov = reach2(cells, starts)
    probs = []
    for rm in plan.rooms:
        d, c = rm['door'], rm['centre']
        near = lambda p: any((p[0] + dx, p[1] + dz) in cov for dx in (-1, 0, 1) for dz in (-1, 0, 1))
        if not near(d): probs.append(f'{name}: door of {rm["name"]} {d} not in the 2-wide network')
        if not near(c): probs.append(f'{name}: centre of {rm["name"]} {c} not in the 2-wide network')
    return probs, (len(cov), len(cells))


def test_circulation():
    bad = []
    for f in FLOORS:
        if f[5] < 6.5: continue
        probs, (nc, nt) = circulation(f[0])
        bad += probs
        print(f'  {f[0]:3d} {f[3]:22s} rooms {len(TI.build_plan(CTX.seed, f[0]).rooms):2d}  2-wide network {nc}/{nt} clear cells')
    assert not bad, '\n'.join(bad)
    print('circulation ok')


def dark_cells(F):
    """Walkable cells with block light 0 inside the tower interior of floor F (validator light model)."""
    F_, walk, ceil, name, prog, R = info(F)
    box = (-48, 0, -48, 47, ceil + 8, 47)
    vol = E.build_box(CTX, box, all_features(CTX))
    t = VC.tinfo(CTX, vol)
    walkm, t = CL._dark(CTX, vol, [], None)
    lit = CL.propagate(t)
    dark = walkm & ~lit
    out = []
    if dark:
        db = VC.tobytes(dark, t.n)
        for i, v in enumerate(db):
            if v:
                x, y, z = t.xyz(i)
                top = (16 if math.hypot(x, z) > 10.5 else 18) if F == 5 else ceil       # ground: wall tops at y 17 are not interior
                if walk <= y <= top and math.hypot(x, z) <= R + 0.01: out.append((x, y, z))
    return out


def test_light():
    bad = []
    for f in FLOORS:
        if f[5] < 6.5: continue
        d = dark_cells(f[0])
        if d: bad.append(f'{f[3]}: {len(d)} dark walkable cells, e.g. {d[:6]}')
        print(f'  {f[0]:3d} {f[3]:22s} dark walkable cells: {len(d)}')
    assert not bad, '\n'.join(bad)
    print('light ok')


def test_timing():
    TI.build_plan.cache_clear()
    feats = all_features(CTX)
    worst = 0.0
    for tx in range(-2, 2):
        for tz in range(-2, 2):
            box = E.tile_bounds(CTX, tx, tz)
            fs = [f for f in feats if f.name.startswith('tower_interior') and E.feature_intersects(f, box)]
            from atlantis.volume import Volume
            TI.build_plan.cache_clear()                      # cold: includes building every floor plan
            t0 = time.perf_counter()
            vol = Volume(*box)
            for f in sorted(fs, key=lambda f: f.name): f.build(vol, CTX)
            dt = time.perf_counter() - t0
            worst = max(worst, dt)
    print(f'timing: worst cold tile (all 15 interior plans built) {worst * 1000:.0f} ms')
    assert worst < 0.3, f'too slow: {worst:.3f}s'
    TI.build_plan.cache_clear()
    t0 = time.perf_counter()
    for tx in range(-2, 2):
        for tz in range(-2, 2):
            box = E.tile_bounds(CTX, tx, tz)
            from atlantis.volume import Volume
            vol = Volume(*box)
            for f in feats:
                if f.name.startswith('tower_interior') and E.feature_intersects(f, box): f.build(vol, CTX)
    print(f'timing: 16 tiles in one process (plans cached) {(time.perf_counter() - t0) * 1000:.0f} ms')
    print('timing ok')


def test_features():
    fs = interior_features()
    assert len(fs) == len(FLOORS), (len(fs), len(FLOORS))
    for f, spec in zip(sorted(fs, key=lambda f: f.name), FLOORS):
        assert f.layer == 60 and 'tower' in f.meta['allow_overlap']
        if spec[0] != 5: assert 'entrances' not in f.meta
        if spec[5] >= 6.5:
            assert f.meta['interior'] and f.meta['interior_box'][1] == spec[1]
            assert f.bbox[1] == spec[0]
    print('features ok')


if __name__ == '__main__':
    test_features()
    test_determinism()
    test_limits()
    test_circulation()
    test_light()
    test_timing()
    print('ALL OK')
