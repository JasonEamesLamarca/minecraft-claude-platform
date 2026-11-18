#!/usr/bin/env python3
"""Tile pipeline tests (plain asserts). Run from the project root:  python3 tests/test_tiles.py"""
import gzip, json, math, os, struct, sys, tempfile, time, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, background, feature, anvil
from atlantis import export_tiles as E
from atlantis.volume import Volume
from atlantis.util import hash_str, rng, parse_state, format_state

ctx = config.load()


def test_util():
    assert hash_str('a', 1) == hash_str('a', 1) and hash_str('a', 1) != hash_str('a', 2)
    assert hash_str('ab', 'c') != hash_str('a', 'bc')
    assert rng(ctx, 'x').random() == rng(ctx, 'x').random() != rng(ctx, 'y').random()
    assert parse_state('quartz_stairs[facing=north,half=bottom]') == \
        ('minecraft:quartz_stairs', {'facing': 'north', 'half': 'bottom'})
    assert parse_state('air') == ('minecraft:air', {})
    assert parse_state('minecraft:light[level=15]') == ('minecraft:light', {'level': '15'})
    assert format_state('minecraft:a', {'b': '1', 'a': '2'}) == 'a[a=2,b=1]'


def decode_template(path):
    """Minimal independent reader: returns (size, {(x, y, z): 'name[props]'}, dataversion)."""
    raw = gzip.open(path, 'rb').read()
    root, _ = anvil._nbt(raw, 3 + struct.unpack_from('>H', raw, 1)[0], 10)
    pal = [format_state(p['Name'], p.get('Properties')) for p in root['palette']]
    return root['size'], {tuple(b['pos']): pal[b['state']] for b in root['blocks']}, root['DataVersion'], root['entities']


def test_roundtrip():
    v = Volume(10, -2, -5, 14, 3, -1)
    v.fill(10, -2, -5, 14, 3, -1, 'stone')
    v.set(11, 0, -4, 'quartz_stairs[facing=north,half=bottom]')
    v.set(12, 1, -3, 'light[level=15]')
    v.set(13, 2, -2, 'air'); v.set(14, 3, -1, 'water')
    d = tempfile.mkdtemp()
    name, h, path = E.write_tile(ctx, 0, 0, v, d)
    size, cells, dv, ents = decode_template(path)
    assert size == [5, 6, 5] and dv == 3955 and ents == []
    assert len(cells) == 5 * 6 * 5
    for x, y, z, b in v.items():
        assert cells[(x - 10, y + 2, z + 5)] == b, (x, y, z, b, cells[(x - 10, y + 2, z + 5)])
    assert cells[(1, 2, 1)] == 'quartz_stairs[facing=north,half=bottom]'
    # tile_nbt is the same content as the file
    assert gzip.open(path, 'rb').read() == E.tile_nbt(ctx, v)


def _all_cells_equal_background(vol):
    for x, y, z, b in vol.items():
        if not ctx.in_area(x, z):                     # outside the circular work mask: left untouched
            assert b == 'structure_void', (x, y, z, b); continue
        assert b == background.background(ctx, x, y, z), (x, y, z, b, background.background(ctx, x, y, z))
    assert 0 not in vol.cells


def test_background_equivalence():
    tiles = [(0, 0), (-1, -1), (18, 0), (-19, 3), (9, -18), (24, 24), (0, 18), (-1, 0)]
    for tx, tz in tiles:   # centre, dome rim, outside
        vol = Volume(*E.tile_bounds(ctx, tx, tz))
        E.fill_background(ctx, vol)
        assert len(list(vol.items())) == vol.sx * vol.sy * vol.sz
        _all_cells_equal_background(vol)
    # also with a custom seabed (relief + block function), and with partially set cells
    old = (background._seabed_height, background._seabed_block)
    try:
        background.set_seabed(lambda c, x, z: int(6 * math.sin(x / 9.0) * math.cos(z / 7.0)) if abs(x) + abs(z) > 700 else 0,
                              lambda c, x, y, z, s: 'sand' if (x + z + y) % 5 else 'gravel')
        for tx, tz in [(18, 0), (-19, 3), (24, 24), (30, 0), (-30, -30)]:
            vol = Volume(*E.tile_bounds(ctx, tx, tz))
            b = E.tile_bounds(ctx, tx, tz)
            vol.fill(b[0] + 3, 2, b[2] + 3, b[0] + 9, 60, b[2] + 9, 'stone')      # partially pre-set
            vol.set(b[0], 5, b[2], 'sea_lantern')
            before = {(x, y, z): bl for x, y, z, bl in vol.items()}
            E.fill_background(ctx, vol)
            for x, y, z, bl in vol.items():
                exp = before.get((x, y, z)) or background.background(ctx, x, y, z)
                if not ctx.in_area(x, z): exp = 'structure_void'   # outside the work mask: always blanked
                assert bl == exp, (x, y, z, bl, exp)
    finally:
        background._seabed_height, background._seabed_block = old


def test_dome_column_range():
    for x, z in [(0, 0), (599, 0), (590, 40), (300, 300), (-420, 421), (650, 0), (1, 599)]:
        r = E.dome_column_range(ctx, x, z, ctx.y_min, ctx.y_max)
        ys = [y for y in range(ctx.y_min, ctx.y_max + 1) if ctx.inside_dome(x, y, z)]
        assert (r is None and not ys) or (r == (ys[0], ys[-1]) and len(ys) == r[1] - r[0] + 1), (x, z, r, ys[:2])


def test_naming_and_hash():
    assert E.tile_filename(-3, 12, 'ab12cd34') == 't_m3_12_ab12cd34.nbt'
    assert E.tile_filename(0, -1, 'ff') == 't_0_m1_ff.nbt'
    assert E.parse_tile_filename('/x/t_m3_12_ab12cd34.nbt') == (-3, 12, 'ab12cd34')
    assert E.tile_bounds(ctx, -2, 3) == (-64, ctx.y_min, 96, -33, ctx.y_max, 127)
    tl = E.all_tiles(ctx)
    r = ctx.area_radius
    assert (min(t[0] for t in tl) * ctx.tile <= -r) and (max(t[0] for t in tl) * ctx.tile + ctx.tile - 1 >= r)
    assert E.tiles_in_box(ctx, -64, -64, 63, 63) == [(tx, tz) for tz in (-2, -1, 0, 1) for tx in (-2, -1, 0, 1)]
    feats = feature.registry()
    from atlantis import demo  # noqa: F401  (registers on import)
    feats = feature.registry()
    a = E.build_tile(ctx, 0, 0, feats); b = E.build_tile(ctx, 0, 0, feats)
    d1, d2 = tempfile.mkdtemp(), tempfile.mkdtemp()
    n1, h1, p1 = E.write_tile(ctx, 0, 0, a, d1); n2, h2, p2 = E.write_tile(ctx, 0, 0, b, d2)
    assert h1 == h2 and n1 == n2 and len(h1) == 8
    assert open(p1, 'rb').read() == open(p2, 'rb').read()          # gzip mtime 0 -> byte-identical
    # hash does not depend on palette insertion order
    c = Volume(*E.tile_bounds(ctx, 0, 0)); c.set(0, 0, 0, 'stone'); c.set(1, 0, 0, 'sand')
    e = Volume(*E.tile_bounds(ctx, 0, 0)); e.set(1, 0, 0, 'sand'); e.set(0, 0, 0, 'stone')
    assert E.tile_hash(ctx, c) == E.tile_hash(ctx, e)
    e.set(0, 0, 0, 'gravel')
    assert E.tile_hash(ctx, c) != E.tile_hash(ctx, e)
    # a changed tile replaces the old file
    n3, h3, p3 = E.write_tile(ctx, 0, 0, e, d1)
    assert h3 != h1 and not os.path.exists(p1) and os.path.exists(p3)
    # tiles.json
    E.update_tiles_json(ctx, d1, {(0, 0): (n3, h3)})
    j = json.load(open(os.path.join(d1, 'tiles.json')))
    assert j['t_0_0'] == {'name': n3, 'hash': h3, 'world_corner': E.world_corner(ctx, 0, 0)}
    ctx.profile['origin_x'], ctx.profile['origin_z'] = 1000, -2000
    try:
        assert E.world_corner(ctx, -1, 2) == [1000 - 32, ctx.y_min + ctx.profile['floor_y'], -2000 + 64]
    finally:
        ctx.profile['origin_x'] = ctx.profile['origin_z'] = None


def test_orchestrate_gen_and_render():
    from atlantis import orchestrate
    d = tempfile.mkdtemp()
    out = os.path.join(d, 'tiles')
    assert orchestrate.main(['gen', '--tiles', 'box:-64,-64,63,63', '--jobs', '2', '--out', out]) == 0
    files = sorted(f for f in os.listdir(out) if f.endswith('.nbt'))
    assert len(files) == 16, files
    j = json.load(open(os.path.join(out, 'tiles.json')))
    assert len(j) == 16 and j['t_m1_m1']['name'] in files
    mt = {f: os.path.getmtime(os.path.join(out, f)) for f in files}
    orchestrate.main(['gen', '--tiles=-1,-1;0,0', '--jobs', '1', '--out', out])    # same hash -> skipped
    assert {f: os.path.getmtime(os.path.join(out, f)) for f in files} == mt
    # the centre tower tile contains the demo quartz and lamp ring
    size, cells, dv, _ = decode_template(os.path.join(out, j['t_0_0']['name']))
    assert size == [32, ctx.y_max - ctx.y_min + 1, 32] and cells[(0, 9, 0)] == 'air'
    names = set(cells.values())
    assert 'smooth_quartz' in names and 'light_gray_concrete' in names and 'bedrock' in names
    # renders
    rd = os.path.join(d, 'top.png')
    orchestrate.main(['render', 'top', '--box=-100,-100,100,100', '--no-dome', '--jobs', '2', '--out', rd])
    assert open(rd, 'rb').read(8) == b'\x89PNG\r\n\x1a\n'
    w, h = struct.unpack('>II', open(rd, 'rb').read()[16:24])
    assert (w, h) == (201, 201)
    orchestrate.main(['render', 'section', '--axis', 'z', '--at', '0', '--range=-64,64', '--out', os.path.join(d, 's.png')])
    orchestrate.main(['render', 'iso', '--box=-16,-16,16,16', '--y', '0,40', '--out', os.path.join(d, 'i.png')])
    assert os.path.getsize(os.path.join(d, 'i.png')) > 500


def test_render_colors():
    from atlantis import render, palette
    vals = [v for k, v in vars(palette).items() if k.isupper() and isinstance(v, str)]
    vals += [b for k, v in vars(palette).items() if k.isupper() and isinstance(v, list) for b in v]
    vals += list(palette.EMISSION)
    bad = [b for b in vals if not render.is_skip(b) and render.color(b) == render.MAGENTA]
    assert not bad, bad
    assert render.color('definitely_not_a_block') == render.MAGENTA
    assert render.color('stone_brick_stairs[facing=north]') == render.color('stone') or render.color('sand_slab') == render.color('sand')


def test_timing():
    from atlantis import demo  # noqa: F401
    feats = feature.registry()
    d = tempfile.mkdtemp()
    t0 = time.time()
    n = 4
    for k in range(n):
        v = E.build_tile(ctx, k, 0, feats); E.write_tile(ctx, k, 0, v, d)
    per = (time.time() - t0) / n
    print(f'  {per:.2f} s/tile (build + write, single process)')
    assert per < 2.0


if __name__ == '__main__':
    fails = 0
    for name, fn in sorted((n, f) for n, f in globals().items() if n.startswith('test_') and callable(f)):
        try:
            t = time.time(); fn(); print(f'ok   {name} ({time.time() - t:.1f}s)')
        except Exception:
            import traceback; traceback.print_exc(); print(f'FAIL {name}'); fails += 1
    sys.exit(1 if fails else 0)
