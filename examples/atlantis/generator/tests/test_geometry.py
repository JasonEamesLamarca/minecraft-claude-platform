"""Plain-assert tests for atlantis.geometry / atlantis.blocks.  Run from the project root:
    python3 tests/test_geometry.py
"""
import math, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TF = float(__import__('os').environ.get('ATLANTIS_TIME_FACTOR', '1'))   # slow CI runners: > 1

from atlantis.volume import Volume
from atlantis import geometry as G, blocks as B

W = 'smooth_quartz'
NB = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _draw(fn, box):
    v = Volume(*box)
    fn(v)
    return v


def clip_equal(draw, big_box, small_box, name):
    """Drawing into a small volume must equal the matching part of the drawing in a big one."""
    big = _draw(draw, big_box)
    small = _draw(draw, small_box)
    for y in range(small.y1, small.y2 + 1):
        for z in range(small.z1, small.z2 + 1):
            for x in range(small.x1, small.x2 + 1):
                a, b = big.get(x, y, z), small.get(x, y, z)
                assert a == b, '%s: clip mismatch at %s: %r vs %r' % (name, (x, y, z), a, b)


def test_clipping():
    BIG = (-60, -5, -60, 60, 50, 60)
    cases = {
        'disc': lambda v: G.disc(v, 3.5, -2, 40, 3, W, 6),
        'ring': lambda v: G.ring(v, 0, 0, 30, 41.5, 2, W, 4),
        'arc': lambda v: G.arc(v, 0, 0, 35, 40, 200, 100, 0, W, 3),
        'outline': lambda v: G.circle_outline(v, 1, 1, 45, 0, W, 2),
        'cylinder': lambda v: G.cylinder(v, 0, 0, 38, 0, 20, W, hollow=True, thickness=2, closed_top=True),
        'frustum_smooth': lambda v: G.frustum(v, 0, 0, 0, 40, 50, 20, W, hollow=True, thickness=2, smooth=True),
        'cone': lambda v: G.cone(v, 0, 0, 0, 40, 50, W, smooth=True),
        'terraces': lambda v: G.terraces(v, 0, 0, 0, [(50, 3), (40, 3), (30, 4)], W, smooth=True),
        'sphere': lambda v: G.sphere(v, 5, 20, -3, 33, W),
        'shell': lambda v: G.shell(v, 5, 20, -3, 40, 2, W),
        'cap_smooth': lambda v: G.cap_shell(v, 0, -300, 0, 340, 3, W, y_min=0, smooth=True),
        'dome': lambda v: G.dome_cap(v, 0, 0, 0, 55, 30, 2, W, smooth=True),
        'polygon': lambda v: G.polygon_prism(v, 0, 0, 7, 45, 0, 5, W, rot=13, hollow=True, thickness=2),
        'line': lambda v: G.line3d(v, (-58, -4, -50), (50, 40, 58), W, 3),
        'ramp': lambda v: G.ramp(v, -55, -20, 55, 30, 0, 14, 7, W, thickness=2, fill_to=-3),
        'bridge': lambda v: G.bridge_deck(v, -58, -10, 58, 25, 4, 5, W, y1=9, pier_every=9, pier_to=-4),
        'stairs': lambda v: G.stairs_run(v, -30, 0, -5, 'east', 30, 5, W, fill_to=-3),
        'spiral': lambda v: G.spiral_stairs(v, 0, 0, 0, 30, 20, 4, W, column='quartz_pillar'),
        'box': lambda v: G.hollow_box(v, -50, 0, -50, 50, 20, 50, W, 'stone', 'glass', thickness=2, clear=True),
        'curved_wall': lambda v: G.curved_wall(v, 0, 0, 50, 10, 300, 0, 12, W, 2),
    }
    windows = [(-60, -5, -60, -29, 50, -29), (-10, 0, 20, 21, 40, 51), (20, -5, 25, 51, 50, 56), (0, 0, 0, 31, 30, 31),
               (28, -5, -60, 59, 50, -29)]
    for name, fn in cases.items():
        for w in windows:
            clip_equal(fn, BIG, w, name)
        # the big drawing must actually contain something, so the comparison is meaningful
        assert sum(1 for _ in _draw(fn, BIG).items()) > 0, name
    print('clipping ok (%d shapes x %d windows)' % (len(cases), len(windows)))


def test_shell_watertight():
    # a small cap: every non-shell cell is "inside" (d <= R - t) or "outside" (d > R); no inside cell may be
    # face-adjacent to an outside cell.
    checked = 0
    for R, t in ((9.0, 1), (9.5, 1), (14, 2), (11.3, 1.5)):
        cx, cy, cz, y_min = 0, -4, 0, 0
        e = int(R) + 3
        v = Volume(-e, y_min, -e, e, e, e)
        G.cap_shell(v, cx, cy, cz, R, t, W, y_min=y_min)
        ri = R - t

        def kind(x, y, z):
            if v.get(x, y, z) is not None: return 'shell'
            d2 = (x - cx) ** 2 + (y - cy) ** 2 + (z - cz) ** 2
            if d2 <= ri * ri: return 'in'
            assert d2 > R * R, 'unset cell inside the shell band at %s' % ((x, y, z),)
            return 'out'

        for y in range(v.y1, v.y2 + 1):
            for z in range(v.z1, v.z2 + 1):
                for x in range(v.x1, v.x2 + 1):
                    if kind(x, y, z) != 'in': continue
                    for dx, dy, dz in NB:
                        nx, ny, nz = x + dx, y + dy, z + dz
                        if v.contains(nx, ny, nz):
                            assert kind(nx, ny, nz) != 'out', 'leak at %s -> %s' % ((x, y, z), (nx, ny, nz))
                            checked += 1
    assert checked > 100
    # flood-fill version: water from outside must not reach the inside of closed hollow shapes
    builds = [lambda v: G.shell(v, 0, 0, 0, 10, 1, W),
              lambda v: G.shell(v, 0.5, 0.5, 0.5, 9.7, 1, W),
              lambda v: G.frustum(v, 0, 0, -10, 12, 11, 4, W, hollow=True, thickness=1, closed_top=True,
                                  closed_bottom=True),
              lambda v: G.tower(v, 0, 0, -10, 14, lambda y: 8 + 3 * math.sin(y / 3.0), W, hollow=True,
                                closed_top=True, closed_bottom=True),
              lambda v: G.cylinder(v, 0, 0, 8, -6, 10, W, hollow=True, closed_top=True, closed_bottom=True)]
    for build in builds:
        v = Volume(-16, -16, -16, 16, 16, 16)
        build(v)
        seen = {(-16, -16, -16)}
        stack = [(-16, -16, -16)]
        while stack:
            x, y, z = stack.pop()
            for dx, dy, dz in NB:
                n = (x + dx, y + dy, z + dz)
                if n in seen or not v.contains(*n) or v.get(*n) is not None: continue
                seen.add(n); stack.append(n)
        assert (0, 0, 0) not in seen and (0, 0, -1) not in seen, 'water leaks into a hollow shape'
    print('watertightness ok')


def _components(cells, conn):
    left = set(cells)
    nb = [(1, 0), (-1, 0), (0, 1), (0, -1)] + ([(1, 1), (1, -1), (-1, 1), (-1, -1)] if conn == 8 else [])
    n = 0
    while left:
        n += 1
        stack = [left.pop()]
        while stack:
            x, z = stack.pop()
            for dx, dz in nb:
                c = (x + dx, z + dz)
                if c in left:
                    left.remove(c); stack.append(c)
    return n


def test_circle_connectivity():
    for r in [1, 1.5, 2, 3, 4.5, 5, 7, 10.5, 15, 22.3, 30, 47.5, 60]:
        for c in (0, 0.5):
            thick = list(G.circle_outline_cells(c, c, r))
            thin = list(G.circle_outline_cells(c, c, r, connected=False))
            assert len(set(thick)) == len(thick)
            assert _components(thick, 4) == 1, ('4-connectivity', r, c)
            assert _components(thin, 8) == 1, ('8-connectivity', r, c)
            # closed curve: no interior cell touches the outside, even diagonally
            disc = set(G.disc_cells(c, c, r))
            out = set(thick)
            for (x, z) in disc:
                if (x, z) in out: continue
                for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                    assert (x + dx, z + dz) in disc, ('outline is not closed', r, c, (x, z))
    d = set(G.disc_cells(2, -1, 17)); rg = set(G.ring_cells(2, -1, 11, 17)); inner = set(G.disc_cells(2, -1, 11))
    assert d == rg | inner and not (rg & inner)
    full = set(G.ring_cells(0, 0, 8, 12))
    parts = set(G.arc_cells(0, 0, 8, 12, 0, 120)) | set(G.arc_cells(0, 0, 8, 12, 120, 240)) | \
        set(G.arc_cells(0, 0, 8, 12, 240, 360))
    assert full == parts
    print('circle connectivity ok')


def test_states():
    for f in B.ORDER:
        for h in ('bottom', 'top'):
            for sh in ('straight', 'inner_left', 'inner_right', 'outer_left', 'outer_right'):
                s = B.stairs('smooth_quartz', f, h, sh)
                assert B.is_valid_state(s), s
                assert s.startswith('smooth_quartz_stairs[facing=')
    for t in ('bottom', 'top', 'double'):
        assert B.is_valid_state(B.slab('quartz_block', t)) and B.slab('quartz_block', t).startswith('quartz_slab')
    assert B.stairs('polished_deepslate', 'east') == 'polished_deepslate_stairs[facing=east,half=bottom,shape=straight]'
    assert B.stairs('quartz_stairs', 'north').startswith('quartz_stairs')
    assert not B.is_valid_state('minecraft:stone') and not B.is_valid_state('quartz_stairs[facing=up,half=bottom]')
    for s in ('quartz_stairs[facing=north,half=top,shape=inner_left]', 'quartz_pillar[axis=x]',
              B.pane('glass_pane', north=True, west=True), B.wall('diorite_wall', 'low', 'none', 'tall', 'none'),
              'oak_sign[rotation=3]', 'stone'):
        canon = B.fmt(*B.parse(s))
        r = s
        for _ in range(4): r = B.rotate_state(r, 1)
        assert r == canon, (s, r)
        assert B.mirror_state(B.mirror_state(s, 'x'), 'x') == canon
        assert B.mirror_state(B.mirror_state(s, 'z'), 'z') == canon
        assert B.rotate_state(s, 2) == B.rotate_state(B.rotate_state(s, 1), 1)
    assert B.rotate_state('quartz_stairs[facing=north,half=bottom,shape=straight]', 1) == \
        'quartz_stairs[facing=east,half=bottom,shape=straight]'
    assert B.rotate_state('quartz_pillar[axis=x]', 1) == 'quartz_pillar[axis=z]'
    assert B.rotate_state('quartz_pillar[axis=x]', 2) == 'quartz_pillar[axis=x]'
    assert B.rotate_state('glass_pane[east=true,north=false,south=false,west=false]', 1) == \
        'glass_pane[east=false,north=false,south=true,west=false]'
    assert B.mirror_state('quartz_stairs[facing=east,half=bottom,shape=inner_left]', 'x') == \
        'quartz_stairs[facing=west,half=bottom,shape=inner_right]'
    assert B.mirror_state('quartz_stairs[facing=north,half=bottom,shape=straight]', 'x') == \
        'quartz_stairs[facing=north,half=bottom,shape=straight]'
    assert [B.facing_from_vector(*v) for v in ((0, -3), (2, 1), (1, 5), (-4, 1), (0, 0))] == \
        ['north', 'east', 'south', 'west', 'north']
    # everything the geometry draws (smooth towers, caps, stairs, ramps...) holds valid states
    v = Volume(-40, 0, -40, 40, 40, 40)
    G.cone(v, 0, 0, 0, 30, 20, W, smooth=True)
    G.dome_cap(v, 0, 0, 0, 30, 20, 2, W, smooth=True)
    G.stairs_run(v, -30, 0, -30, 'north', 10, 3, 'quartz_block')
    G.spiral_stairs(v, 25, 25, 0, 10, 5, 1, W)
    G.bridge_deck(v, -35, 30, 10, 30, 3, 5, W, y1=6)
    bad = {b for _, _, _, b in v.items() if not B.is_valid_state(b)}
    assert not bad, bad
    assert sum(1 for _, _, _, b in v.items() if '_stairs[' in b) > 20
    print('states ok')


def test_determinism():
    def build():
        v = Volume(-40, -5, -40, 40, 50, 40)
        G.dome_cap(v, 0, 0, 0, 38, 20, 2, W, smooth=True)
        G.frustum(v, 0, 0, 0, 30, 20, 6, W, hollow=True, smooth=True)
        G.line3d(v, (-30, 0, -30), (30, 40, 30), W, 2)
        G.bridge_deck(v, -38, 30, 38, 30, 3, 5, W, y1=8, pier_every=7, pier_to=0)
        G.spiral_stairs(v, 0, 0, 0, 20, 6, 1, W)
        return v
    a, b = build(), build()
    assert a.palette == b.palette and a.cells == b.cells
    print('determinism ok')


def test_misc():
    pts = G.radial(4, 10, 0)
    assert [(x, z) for x, z, a in pts] == [(10, 0), (0, 10), (-10, 0), (0, -10)]
    assert len(G.radial(9, 5, 90, 3, 3)) == 9
    n = sum(1 for _ in G.polygon_cells(0, 0, 6, 10))
    assert abs(n - 3 * math.sqrt(3) / 2 * 100) < 25, n
    v = Volume(0, 0, 0, 20, 20, 20)
    G.line3d(v, (0, 0, 0), (20, 20, 20), W)
    assert v.get(0, 0, 0) == W and v.get(20, 20, 20) == W and v.get(10, 10, 10) == W
    v = Volume(0, 0, 0, 9, 3, 9)
    G.box(v, 0, 0, 0, 9, 0, 9, 'stone')
    G.disc(v, 4, 4, 3, 0, W, replace={'stone'})
    G.disc(v, 4, 4, 6, 1, 'glass', replace={None})
    assert v.get(4, 0, 4) == W and v.get(0, 0, 0) == 'stone' and v.get(4, 1, 4) == 'glass'
    G.disc(v, 4, 4, 6, 1, 'gold_block', replace={'stone'})
    assert v.get(4, 1, 4) == 'glass'
    v = Volume(0, 0, 0, 20, 20, 10)
    G.stairs_run(v, 2, 0, 5, 'east', 8, 3, 'quartz_block')
    for i in range(8):
        assert v.get(2 + i, i, 5) == 'quartz_stairs[facing=east,half=bottom,shape=straight]'
    v = Volume(0, 0, 0, 9, 0, 9)
    G.disc(v, 4, 4, 4, 0, lambda x, y, z: 'stone' if (x + z) % 2 else None)
    assert v.get(4, 0, 5) == 'stone' and v.get(4, 0, 4) is None
    v = Volume(0, 0, 0, 6, 2, 2)
    v.set(0, 0, 1, 'stone'); v.set(4, 0, 1, 'stone')
    for x in (1, 2, 3): v.set(x, 0, 1, 'glass_pane')
    G.autoconnect(v)
    assert v.get(2, 0, 1) == 'glass_pane[east=true,north=false,south=false,west=true]', v.get(2, 0, 1)
    print('misc ok')


def test_timing():
    t = time.time()
    for tx in (0, 150, 290, 568):          # centre, mid, near the rim, at the rim
        v = Volume(tx, -8, 0, tx + 31, 118, 31)
        G.dome_cap(v, 0, 0, 0, 600, 100, 3, W, smooth=True)
    dt = (time.time() - t) / 4
    assert dt < 0.2 * TF, dt
    t = time.time()
    v = Volume(0, -8, 0, 31, 118, 31)
    G.disc(v, 0, 0, 600, 0, W); G.ring(v, 0, 0, 590, 600, 1, W, 20); G.circle_outline(v, 15, 15, 20, 5, W, 30)
    G.cylinder(v, 0, 0, 600, 0, 100, W, hollow=True, thickness=2)
    G.frustum(v, 0, 0, 0, 100, 600, 560, W, hollow=True, smooth=True)
    assert time.time() - t < 0.5 * TF
    print('timing ok: 600-radius cap shell into one 32x32x127 tile: %.1f ms on average' % (dt * 1000))


if __name__ == '__main__':
    test_states()
    test_clipping()
    test_shell_watertight()
    test_circle_connectivity()
    test_determinism()
    test_misc()
    test_timing()
    print('ALL GEOMETRY TESTS PASSED')
