#!/usr/bin/env python3
"""Dome tests (plain asserts). Run from the project root:  python3 tests/test_dome.py"""
import json, math, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TF = float(__import__('os').environ.get('ATLANTIS_TIME_FACTOR', '1'))   # slow CI runners: > 1

from atlantis import config, dome
from atlantis import export_tiles as E
from atlantis.util import parse_state

ctx = config.load()
FEATS = dome.make_features(ctx)            # ONLY the dome features (+ the exporter's background)
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
AIRISH = {'air', 'cave_air', 'void_air'}
TRANSPARENT = {'glass', 'light_blue_stained_glass', 'white_stained_glass', 'cyan_stained_glass'}


def stations():
    return [f for f in FEATS if isinstance(f, dome.Station)]


def tile_at(x, z):
    t = ctx.tile
    return x // t, z // t


def pol(r, deg):
    a = math.radians(deg)
    return int(round(r * math.cos(a))), int(round(r * math.sin(a)))


def points():
    """(label, x, z) points; the tile containing each is tested."""
    pts = [('centre', 0, 0), ('centre2', -1, -1), ('mid300', 300, 0), ('mid_diag', 212, -212),
           ('apex_ring', 25, 0)]
    for deg in (0, 45, 100, 170, 215, 290):
        pts.append((f'rim{deg}_594', *pol(594, deg)))      # inner side of the base ring
        pts.append((f'rim{deg}_600', *pol(600, deg)))      # base ring
        pts.append((f'rim{deg}_606', *pol(606, deg)))      # plinth
    pts.append(('rib_foot', *pol(598, 7.5)))
    pts.append(('band', *pol(dome.geo(ctx).band_r[0], 33)))
    for s in stations():
        for label, u in (('tunnel', 597), ('chamber', 613), ('deck', 632)):
            x, z = s.xz(u, 0)
            pts.append((f'{s.name}_{label}', x, z))
        x, z = s.xz(613, 6); pts.append((f'{s.name}_side', x, z))
    return pts


def build_margin(tx, tz):
    """Tile box grown by 1 block in x/z so adjacency across the tile edge is checked too."""
    b = E.tile_bounds(ctx, tx, tz)
    return E.build_box(ctx, (b[0] - 1, b[1], b[2] - 1, b[3] + 1, b[4], b[5] + 1), FEATS)


def check_watertight(vol, label):
    """No face-adjacent pair air <-> water (air = dry dome air and station air; both are 'air' cells)."""
    pal = vol.palette
    kind = [0] * len(pal)                    # 1 = air, 2 = water
    for i, s in enumerate(pal):
        if s in AIRISH: kind[i] = 1
        elif s == 'water': kind[i] = 2
    cells = vol.cells
    sx, sy, sz = vol.sx, vol.sy, vol.sz
    bad = 0; n_air = n_water = 0
    for y in range(sy):
        for z in range(sz):
            base = (y * sz + z) * sx
            for x in range(sx):
                k = kind[cells[base + x]]
                if not k: continue
                if k == 1: n_air += 1
                else: n_water += 1
                for (dx, dy, dz) in ((1, 0, 0), (0, 1, 0), (0, 0, 1)):
                    xx, yy, zz = x + dx, y + dy, z + dz
                    if xx >= sx or yy >= sy or zz >= sz: continue
                    k2 = kind[cells[(yy * sz + zz) * sx + xx]]
                    if k2 and k2 != k:
                        bad += 1
                        if bad <= 5:
                            print('  LEAK', label, (vol.x1 + x, vol.y1 + y, vol.z1 + z), 'vs',
                                  (vol.x1 + xx, vol.y1 + yy, vol.z1 + zz))
    assert bad == 0, f'{label}: {bad} air/water face contacts'
    return n_air, n_water


def test_watertight():
    seen = set(); done = 0; both = 0
    for label, x, z in points():
        t = tile_at(x, z)
        if t in seen: continue
        seen.add(t)
        a, w = check_watertight(build_margin(*t), f'{label} tile {t}')
        done += 1
        if a and w: both += 1
    assert done >= 8 and both >= 8, (done, both)
    print(f'  watertight: {done} tiles OK ({both} with both air and water)')


def test_shell_alignment():
    """Glass cells found in generated tiles lie exactly in the shell R-2 <= d < R about (0, cy, 0)."""
    R, cy, rin = ctx.dome_R, ctx.dome_cy, ctx.dome_R - ctx.dome_thickness
    n = 0
    for label, x, z in points():
        b = E.tile_bounds(ctx, *tile_at(x, z))
        vol = E.build_box(ctx, (b[0], 0, b[2], b[3], b[4], b[5]), FEATS)
        for (xx, yy, zz, blk) in vol.items():
            if blk.split('[')[0] not in TRANSPARENT: continue
            if abs(xx) < 590 and abs(zz) < 590 or True:
                d = math.sqrt(xx * xx + (yy - cy) ** 2 + zz * zz)
                if (label.startswith('dome_station')): continue          # station windows are not shell
                assert rin - 1e-6 <= d < R + 1e-6, (label, (xx, yy, zz), d)
                n += 1
    assert n > 5000, n
    g = dome.geo(ctx)
    assert g.run(0, 0)[1] >= ctx.dome_height - 2                      # apex column reaches the cap height
    assert g.run(*pol(599, 33)) is not None and g.run(*pol(603, 33)) is None   # base at r ~ 600
    assert abs(g.base_r - ctx.dome_radius) < 1.0
    # exact thickness: every shell column has the cells between inner and outer sphere, none missing
    for (x, z) in ((0, 0), (100, 77), (-250, 310), (430, -120), (-560, -150), (596, 20)):
        s, e = g.run(x, z)
        for y in range(s, e + 1):
            d = math.sqrt(x * x + (y - cy) ** 2 + z * z)
            assert rin - 1e-6 <= d < R, (x, y, z, d)
        for y in (s - 1, e + 1):
            d = math.sqrt(x * x + (y - cy) ** 2 + z * z)
            if y >= 1: assert d < rin or d >= R, (x, y, z, d)
    print(f'  alignment: {n} glass cells on the shell')


def opaque_stats(stride):
    g = dome.geo(ctx)
    tot = op = 0
    r = int(g.base_r) + 2
    for z in range(-r, r + 1, stride):
        for x in range(-r, r + 1, stride):
            for y, b in g.column(x, z):
                tot += 1
                if b.split('[')[0] not in TRANSPARENT: op += 1
    return op, tot


def test_opaque_fraction():
    op, tot = opaque_stats(3)
    frac = op / tot
    print(f'  opaque fraction (sampled, stride 3): {frac * 100:.2f}% of {tot} shell cells')
    assert frac < 0.06, frac


def test_valid_blocks():
    seen = set()
    for label, x, z in points():
        vol = E.build_tile(ctx, *tile_at(x, z), FEATS)
        seen.update(s for s in vol.palette if s)
    assert len(seen) > 15
    for s in sorted(seen):
        name, props = parse_state(s)
        n = name[10:]
        assert n in VALID, f'unknown block {s}'
        for k, v in props.items():
            assert k in VALID[n], f'{s}: unknown property {k}'
            assert v in VALID[n][k], f'{s}: bad value {k}={v}'
    print(f'  valid blocks: {len(seen)} distinct states')


def test_determinism():
    pts = points()
    for label, x, z in pts[:4] + pts[-4:]:
        t = tile_at(x, z)
        a = E.tile_hash(ctx, E.build_tile(ctx, *t, FEATS))
        ctx2 = config.load()
        b = E.tile_hash(ctx2, E.build_tile(ctx2, *t, dome.make_features(ctx2)))
        assert a == b, (label, a, b)


def test_station_air():
    for s in stations():
        boxes = s.air_boxes(ctx)
        assert boxes
        for b in boxes: assert b[0] <= b[3] and b[1] <= b[4] and b[2] <= b[5]
        C = s.model(ctx)
        air = {k for k, v in C.items() if v == 'air'}
        assert len(air) > 800, len(air)
        n_cells = sum((b[3] - b[0] + 1) * (b[4] - b[1] + 1) * (b[5] - b[2] + 1) for b in boxes)
        assert n_cells == len(air), (n_cells, len(air))
        # connectivity: flood fill from the chamber reaches the dry dome through the 2-high airlock
        sx, sz = s.xz(613, 0)
        start = (sx, 1, sz)
        assert start in air
        seen = {start}; stack = [start]; reach = False
        while stack and not reach:
            x, y, z = stack.pop()
            for d in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                p = (x + d[0], y + d[1], z + d[2])
                if p in seen: continue
                if ctx.inside_dome(*p):
                    reach = True; break
                if p in air:
                    seen.add(p); stack.append(p)
        assert reach, s.name
        b = s.bbox
        for (x, y, z) in C:
            assert b[0] <= x <= b[3] and b[1] <= y <= b[4] and b[2] <= z <= b[5], (s.name, (x, y, z), b)
        # doorway is 2 high: air at y=1 and y=2 on the passage axis, solid at y=3 (inside the shell zone)
        px, pz = s.xz(600, 0)
        assert C.get((px, 1, pz)) == 'air' and C.get((px, 2, pz)) == 'air' and C.get((px, 3, pz)) != 'air'
    print('  stations: air connected to the dome, 2-high airlock, bbox contains all cells')


def test_timing():
    worst = 0.0
    for label, x, z in points():
        t0 = time.time(); E.build_tile(ctx, *tile_at(x, z), FEATS); dt = time.time() - t0
        worst = max(worst, dt)
        assert dt < 0.2 * TF, (label, dt)
    print(f'  timing: worst tile {worst * 1000:.0f} ms')


def test_info():
    info = dome.dome_info(ctx)
    assert info['center'] == (0, 0) and info['floor_y'] == 0 and len(info['stations']) == 6
    assert abs(info['R'] - ctx.dome_R) < 1e-9 and abs(info['inner_R'] - (ctx.dome_R - 2)) < 1e-9
    assert info['station_air_boxes']


if __name__ == '__main__':
    t0 = time.time()
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            print(name); fn()
    print(f'OK ({time.time() - t0:.1f}s)')
