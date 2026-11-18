"""Plain-assert tests for atlantis.architecture.  Run from the project root:
    python3 tests/test_architecture.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atlantis.volume import Volume
from atlantis import architecture as A, blocks as B, geometry as G, palette as P

BIG = (-70, -5, -70, 70, 70, 70)
ANCHOR = (0, 12, 0)
FACINGS = ('north', 'east', 'south', 'west')
SPECIAL = {'floor_pattern': dict(radius=14), 'tower_cap': dict(radius=6, height=20)}


def draw(name, vol, facing, x=ANCHOR[0], y=ANCHOR[1], z=ANCHOR[2], **kw):
    fn = A.COMPONENTS[name][0]
    kw = dict(SPECIAL.get(name, {}), **kw)
    return fn(vol, x, y, z, facing, **kw)


def snap(vol):
    return {(x, y, z): b for x, y, z, b in vol.items()}


def test_all_draw_valid():
    for name in A.COMPONENTS:
        for f in FACINGS:
            v = Volume(*BIG)
            bb = draw(name, v, f)
            assert len(bb) == 6 and bb[0] <= bb[3] and bb[1] <= bb[4] and bb[2] <= bb[5], (name, bb)
            cells = snap(v)
            assert cells, name + ' drew nothing'
            for (x, y, z), b in cells.items():
                assert B.is_valid_state(b), (name, f, b)
                assert bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5], \
                    '%s facing %s: cell %s outside the returned bbox %s' % (name, f, (x, y, z), bb)
    print('all %d components draw valid states inside their bbox' % len(A.COMPONENTS))


def test_clip_independent():
    windows = [(-70, -5, -70, -1, 70, -1), (0, -5, -70, 70, 70, -1), (-70, -5, 0, -1, 70, 70), (0, -5, 0, 70, 70, 70),
               (-12, 0, -12, 19, 60, 19), (-3, 5, -3, 28, 40, 28)]
    for name in A.COMPONENTS:
        for f in ('north', 'east', 'south', 'west'):
            big = Volume(*BIG); draw(name, big, f)
            for w in windows:
                small = Volume(*w); draw(name, small, f)
                for y in range(small.y1, small.y2 + 1):
                    for z in range(small.z1, small.z2 + 1):
                        for x in range(small.x1, small.x2 + 1):
                            assert big.get(x, y, z) == small.get(x, y, z), \
                                '%s %s: tile-dependent at %s (%r vs %r)' % (name, f, (x, y, z), big.get(x, y, z),
                                                                          small.get(x, y, z))
    print('components are clip/tile independent')


def test_rotation():
    # drawing facing east equals the north drawing rotated one quarter turn clockwise about the anchor, etc.
    for name in A.COMPONENTS:
        if name in ('tower_cap', 'floor_pattern'): continue
        ref = {}
        v = Volume(*BIG); draw(name, v, 'north')
        for (x, y, z), b in snap(v).items(): ref[(x - ANCHOR[0], y, z - ANCHOR[2])] = b
        for q, f in enumerate(FACINGS):
            v = Volume(*BIG); draw(name, v, f)
            got = {(x - ANCHOR[0], y, z - ANCHOR[2]): b for (x, y, z), b in snap(v).items()}
            exp = {}
            for (dx, y, dz), b in ref.items():
                for _ in range(q): dx, dz = -dz, dx
                exp[(dx, y, dz)] = B.rotate_state(b, q)
            assert got == exp, '%s: facing %s is not a rotation of north (%d cells differ)' % (
                name, f, len(set(got.items()) ^ set(exp.items())))
    print('rotation ok')


def test_determinism_and_params():
    for name in A.COMPONENTS:
        a = Volume(*BIG); b = Volume(*BIG)
        draw(name, a, 'south'); draw(name, b, 'south')
        assert a.palette == b.palette and a.cells == b.cells, name
    # a component that cannot touch the volume draws nothing and is cheap
    far = Volume(300, 0, 300, 331, 100, 331)
    t = time.time()
    for name in A.COMPONENTS:
        draw(name, far, 'north')
    assert time.time() - t < 0.05
    assert not list(far.items())
    # parameters change the result
    v1 = Volume(*BIG); v2 = Volume(*BIG)
    A.atlantean_column(v1, 0, 0, 0, height=10); A.atlantean_column(v2, 0, 0, 0, height=18)
    assert len(snap(v2)) > len(snap(v1))
    for st in ('spire', 'crown', 'dish', 'stepped'):
        v = Volume(*BIG); A.tower_cap(v, 0, 0, 0, style=st, radius=6, height=20)
        assert snap(v), st
    for st in ('radial', 'rings', 'geometric'):
        v = Volume(*BIG); A.floor_pattern(v, 0, 0, 0, radius=12, style=st)
        assert len(snap(v)) > 100, st
    print('determinism / params ok')


def test_visual_rules():
    # no bare light blocks: every sea lantern / lamp / froglight cell must have at least one non-air neighbour that
    # is glass-like or part of the structure, and lamps never form big bare grids
    emit = ('sea_lantern', 'redstone_lamp', 'glowstone', 'shroomlight')
    for name in A.COMPONENTS:
        v = Volume(*BIG)
        if A.COMPONENTS[name][1] == 'wall':
            v.fill(-70, -5, 0, 70, 70, 60, 'stone')      # the wall the component is mounted on (facing north)
        draw(name, v, 'north')
        cells = snap(v)
        lamps = [c for c, b in cells.items() if b.split('[')[0] in emit]
        # lamps with all 6 neighbours either solid or glass are "hidden"; count fully exposed ones
        for (x, y, z) in lamps:
            exposed = sum(1 for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
                          if (x + dx, y + dy, z + dz) not in cells or cells[(x + dx, y + dy, z + dz)] == 'air')
            assert exposed <= 2, '%s: lamp at %s is exposed on %d sides' % (name, (x, y, z), exposed)
    print('visual rules ok')


if __name__ == '__main__':
    test_all_draw_valid()
    test_clip_independent()
    test_rotation()
    test_determinism_and_params()
    test_visual_rules()
    print('ALL ARCHITECTURE TESTS PASSED')
