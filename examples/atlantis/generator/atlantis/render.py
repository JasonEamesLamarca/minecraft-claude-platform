"""Renderer: top (plan), section (vertical slice) and iso views, built tile by tile.

Only 2D results are kept per tile, so the full 1600 x 1600 x 127 volume never exists in memory. Output: PNG in
out/renders/. Unknown blocks are magenta.
"""
import math, multiprocessing, os, struct, zlib
from array import array

from . import config, export_tiles as E
from .util import base_name

MAGENTA = (255, 0, 255)
WATER_COL = (40, 90, 220)
AIR_COL = (22, 26, 34)
SKY_COL = (150, 195, 235)

COL = {'smooth_stone': (165, 165, 165), 'tuff': (110, 112, 100), 'dripstone_block': (135, 105, 85), 'polished_andesite': (135, 138, 136), 
    # palette.py: white / pale architecture
    'smooth_quartz': (246, 243, 238), 'quartz_block': (242, 238, 232), 'quartz_pillar': (240, 236, 230),
    'quartz_bricks': (236, 232, 226), 'white_concrete': (236, 239, 240), 'calcite': (224, 224, 218),
    'polished_diorite': (190, 190, 192), 'diorite': (190, 190, 192), 'light_gray_concrete': (150, 150, 145),
    'white_terracotta': (210, 178, 161), 'quartz_stairs': (240, 236, 230), 'smooth_quartz_stairs': (246, 243, 238),
    'smooth_quartz_slab': (246, 243, 238), 'quartz_slab': (242, 238, 232), 'diorite_wall': (190, 190, 192),
    # glass and energy
    'glass': (190, 225, 240), 'glass_pane': (190, 225, 240), 'light_blue_stained_glass': (120, 180, 230),
    'light_blue_stained_glass_pane': (120, 180, 230), 'cyan_stained_glass': (60, 160, 175),
    'cyan_stained_glass_pane': (60, 160, 175), 'white_stained_glass': (235, 240, 245),
    'tinted_glass': (50, 40, 60), 'cyan_concrete': (21, 119, 136), 'light_blue_concrete': (35, 137, 198),
    'cyan_terracotta': (86, 91, 91),
    # foundations and contrast
    'polished_deepslate': (72, 72, 73), 'deepslate_bricks': (70, 70, 71), 'deepslate_tiles': (54, 54, 55),
    'deepslate': (80, 80, 82), 'prismarine': (99, 156, 151), 'prismarine_bricks': (99, 171, 158),
    'dark_prismarine': (51, 91, 75), 'iron_block': (205, 205, 205), 'waxed_oxidized_copper': (82, 162, 132),
    'waxed_oxidized_cut_copper': (79, 153, 126), 'waxed_oxidized_copper_grate': (82, 162, 132),
    'oxidized_copper': (82, 162, 132), 'copper_block': (192, 107, 79),
    # light sources
    'sea_lantern': (210, 240, 235), 'end_rod': (240, 235, 255), 'verdant_froglight': (200, 230, 170),
    'pearlescent_froglight': (240, 225, 240), 'ochre_froglight': (250, 235, 170), 'beacon': (150, 235, 230),
    'glowstone': (240, 200, 110), 'shroomlight': (240, 150, 70), 'lantern': (230, 170, 80),
    'soul_lantern': (90, 200, 220), 'redstone_lamp': (180, 110, 60), 'conduit': (150, 140, 120),
    'light_blue_carpet': (60, 150, 210), 'soul_sand': (80, 62, 50), 'bubble_column': (90, 160, 220),
    'warped_door': (40, 140, 130), 'sea_pickle': (110, 150, 50), 'glow_lichen': (110, 170, 130), 'crying_obsidian': (60, 20, 100),
    # natural / seabed
    'stone': (125, 125, 125), 'gravel': (135, 130, 128), 'sand': (220, 210, 150), 'clay': (160, 166, 179),
    'sandstone': (215, 205, 150), 'bedrock': (50, 50, 50), 'seagrass': (50, 140, 60), 'tall_seagrass': (50, 140, 60),
    'kelp_plant': (60, 110, 60), 'kelp': (60, 110, 60), 'water': WATER_COL, 'flowing_water': WATER_COL,
    'tube_coral_block': (49, 87, 206), 'brain_coral_block': (207, 91, 159), 'bubble_coral_block': (165, 26, 162),
    'fire_coral_block': (164, 35, 47), 'horn_coral_block': (216, 199, 66),
    'dirt': (130, 95, 60), 'grass_block': (95, 155, 60), 'mud': (60, 55, 60), 'gold_block': (250, 215, 50),
    'andesite': (130, 130, 130), 'granite': (150, 110, 95), 'moss_block': (90, 130, 50), 'obsidian': (20, 18, 30),
    'black_concrete': (14, 16, 20), 'gray_concrete': (70, 74, 78),
}
COL.update({
    # interiors (tower_interior.py)
    'black_stained_glass': (30, 32, 42), 'blue_stained_glass': (60, 90, 190), 'black_stained_glass_pane': (30, 32, 42),
    'blue_stained_glass_pane': (60, 90, 190), 'red_stained_glass': (200, 50, 50), 'yellow_stained_glass': (230, 210, 80),
    'orange_stained_glass': (230, 140, 50), 'lime_stained_glass': (130, 210, 70), 'purple_stained_glass': (140, 70, 190),
    'gray_stained_glass': (80, 85, 90), 'light_gray_stained_glass': (150, 155, 160),
    'smooth_stone': (165, 165, 165), 'stone_bricks': (122, 122, 122), 'chiseled_quartz_block': (236, 230, 223),
    'barrel': (130, 95, 55), 'chest': (160, 110, 40), 'bookshelf': (140, 105, 65), 'lectern': (150, 110, 60),
    'cauldron': (60, 60, 65), 'brewing_stand': (150, 140, 120), 'white_bed': (235, 235, 235),
    'light_blue_bed': (110, 180, 230), 'cyan_bed': (30, 150, 160), 'gray_bed': (90, 95, 100), 'blue_bed': (50, 70, 180),
    'white_carpet': (235, 235, 235), 'light_blue_carpet': (110, 180, 230), 'cyan_carpet': (30, 150, 160),
    'gray_carpet': (90, 95, 100), 'light_gray_carpet': (150, 150, 145), 'blue_carpet': (50, 70, 180),
    'black_carpet': (20, 20, 24), 'red_carpet': (160, 40, 40), 'redstone_block': (170, 20, 10),
    'redstone_lamp': (200, 60, 40), 'iron_bars': (140, 144, 150), 'lantern': (230, 170, 80),
    'stone_button': (160, 160, 160), 'light_blue_concrete_powder': (110, 180, 230), 'black_concrete': (14, 16, 20),
    'blue_concrete': (45, 47, 143), 'gray_concrete': (55, 58, 62), 'white_wool': (235, 235, 235),
    'light_blue_wool': (110, 180, 230), 'cyan_wool': (21, 138, 145), 'gray_wool': (66, 70, 75),
    'blue_wool': (50, 70, 180), 'light_gray_wool': (150, 150, 145), 'spruce_planks': (110, 80, 45),
    'oak_planks': (170, 140, 85), 'dark_oak_planks': (66, 43, 20), 'birch_planks': (200, 180, 120),
    'stripped_birch_log': (200, 180, 120), 'stripped_dark_oak_log': (66, 43, 20), 'jukebox': (110, 70, 50),
    'smithing_table': (60, 60, 70), 'loom': (170, 130, 80), 'crafting_table': (150, 105, 60),
    'copper_block': (192, 107, 79), 'cut_copper': (192, 107, 79), 'waxed_copper_block': (192, 107, 79),
    'waxed_cut_copper': (192, 107, 79), 'waxed_weathered_copper': (110, 150, 110), 'amethyst_block': (134, 96, 190),
    'amethyst_cluster': (160, 120, 220), 'purple_stained_glass_pane': (140, 70, 190), 'tinted_glass': (50, 40, 60),
    'daylight_detector': (120, 130, 120), 'target': (220, 200, 190), 'observer': (90, 90, 90),
    'composter': (110, 80, 45), 'flower_pot': (140, 80, 60), 'azalea': (80, 130, 50), 'potted_azalea_bush': (80, 130, 50),
    'potted_fern': (60, 130, 60), 'potted_blue_orchid': (60, 130, 170), 'moss_block': (90, 130, 50),
    'smooth_stone_slab': (165, 165, 165), 'bubble_column': (120, 170, 255), 'soul_sand': (80, 60, 50),
    'magma_block': (190, 80, 20), 'end_rod': (240, 235, 255), 'sculk': (14, 40, 50), 'sculk_sensor': (20, 80, 90),
    'iron_block': (205, 205, 205), 'polished_andesite_slab': (135, 138, 136), 'gold_block': (250, 215, 50),
    'light': (255, 255, 120), 'cyan_stained_glass': (60, 160, 175), 'chain': (60, 60, 70),
})
COL['smooth_quartz_stairs'] = COL['smooth_quartz']
SKIP = {'air', 'cave_air', 'void_air', 'light', 'water', 'flowing_water', 'structure_void'}
_SUFFIX = ('_stairs', '_slab', '_wall', '_pillar', '_pane', '_fence', '_fence_gate', '_door', '_trapdoor',
           '_button', '_pressure_plate', '_carpet')


def color(state):
    """RGB for a block state string; magenta if unknown."""
    b = base_name(state)
    c = COL.get(b)
    if c: return c
    s = b[6:] if b.startswith('waxed_') else b
    for suf in _SUFFIX:
        if s.endswith(suf):
            stem = s[:-len(suf)]
            for cand in (stem, stem + '_block', stem + 's', stem + '_planks', stem.replace('_brick', '_bricks')):
                if cand in COL: return COL[cand]
    if s in COL: return COL[s]
    if 'glass' in s: return COL['glass']
    if 'leaves' in s: return (45, 120, 45)
    return MAGENTA


def is_skip(state):
    return state is None or base_name(state) in SKIP


def is_water(state):
    return state is not None and base_name(state) in ('water', 'flowing_water')


def _is_glass(state):
    return state is not None and 'glass' in base_name(state)


# ---------------------------------------------------------------- png
def write_png_rows(path, w, h, rows):
    """rows: iterable of bytes (w*3 each)."""
    raw = b''.join(b'\0' + bytes(r) for r in rows)
    def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


def _scale_rows(rows, w, scale):
    """Nearest-neighbour upscale of byte rows."""
    if scale <= 1:
        yield from rows; return
    for r in rows:
        r = bytes(r)
        big = b''.join(r[i:i + 3] * scale for i in range(0, w * 3, 3))
        for _ in range(scale): yield big


# ---------------------------------------------------------------- top view
_wctx = None
_wfeats = None
_wopts = None


def _winit(opts):
    global _wctx, _wfeats, _wopts
    from .orchestrate import load_generators
    _wctx = config.load(); _wfeats = load_generators(); _wopts = opts


def _top_tile(job):
    """-> (tx, tz, colours bytes (tile*tile*3), heights array('h'), wet bytes) for the plan view of one tile."""
    tx, tz = job
    ctx, no_dome = _wctx, _wopts['no_dome']
    vol = E.build_tile(ctx, tx, tz, _wfeats)
    sx, sy, sz = vol.sx, vol.sy, vol.sz
    step = sx * sz
    pal = vol.palette
    skip = [is_skip(s) for s in pal]
    glass = [_is_glass(s) for s in pal]
    water = [is_water(s) for s in pal]
    cols = [color(s) if s is not None and not skip[i] else None for i, s in enumerate(pal)]
    cells = vol.cells
    out = bytearray(sx * sz * 3); hts = array('h', [0]) * (sx * sz); wet = bytearray(sx * sz)
    for zz in range(sz):
        for xx in range(sx):
            base = zz * sx + xx
            col = cells[base::step]
            inner = ctx.dome_inner_y(vol.x1 + xx, vol.z1 + zz) if no_dome else None
            gy = (inner - 1) if inner is not None else None
            k = sy - 1
            while k >= 0:
                p = col[k]
                if not skip[p] and not (gy is not None and glass[p] and k + vol.y1 >= gy):
                    break
                k -= 1
            if k < 0:
                out[base * 3:base * 3 + 3] = bytes(AIR_COL); continue
            out[base * 3:base * 3 + 3] = bytes(cols[col[k]])
            hts[base] = k + vol.y1
            if k + 1 < sy and water[col[k + 1]]: wet[base] = 1
    return tx, tz, bytes(out), hts, bytes(wet)


def render_top(ctx, box=None, scale=1, no_dome=False, jobs=4, out=None):
    if box is None:
        r = int(math.ceil(ctx.area_radius)); box = (-r, -r, r, r)
    x1, z1, x2, z2 = box[0], box[1], box[2], box[3]
    tiles = E.tiles_in_box(ctx, x1, z1, x2, z2)
    W, H = x2 - x1 + 1, z2 - z1 + 1
    img = [bytearray(SKY_COL) * W for _ in range(H)]
    hmap = [array('h', [ctx.y_min]) * W for _ in range(H)]
    wetm = [bytearray(W) for _ in range(H)]
    t = ctx.tile
    opts = {'no_dome': no_dome}
    if jobs > 1 and len(tiles) > 1:
        from .util import mp_pool
        pool = mp_pool(jobs, initializer=_winit, initargs=(opts,))
        it = pool.imap_unordered(_top_tile, tiles, chunksize=4)
    else:
        _winit(opts); pool = None
        it = map(_top_tile, tiles)
    for tx, tz, col, hts, wet in it:
        for zz in range(t):
            z = tz * t + zz
            if not z1 <= z <= z2: continue
            xa, xb = max(x1, tx * t), min(x2, tx * t + t - 1)
            a, b = xa - tx * t, xb - tx * t + 1
            o = xa - x1
            img[z - z1][o * 3:(o + b - a) * 3] = col[(zz * t + a) * 3:(zz * t + b) * 3]
            hmap[z - z1][o:o + b - a] = hts[zz * t + a:zz * t + b]
            wetm[z - z1][o:o + b - a] = wet[zz * t + a:zz * t + b]
    if pool: pool.close(); pool.join()
    span = float(ctx.y_max - ctx.y_min)
    rows = []
    for j in range(H):
        row = img[j]; hr = hmap[j]; wr = wetm[j]
        hp = hmap[j - 1] if j else hr
        for i in range(W):
            h = hr[i]
            slope = h - (hr[i - 1] if i else h) * 0.5 - hp[i] * 0.5
            f = 0.62 + 0.38 * min(1.0, max(0.0, (h - ctx.y_min) / (span * 0.6))) + 0.05 * max(-3, min(3, slope))
            o = i * 3
            r, g, b = row[o], row[o + 1], row[o + 2]
            if wr[i]:
                r, g, b = (r * 0.5 + 20, g * 0.5 + 50, b * 0.5 + 110)
            row[o] = int(min(255, max(0, r * f))); row[o + 1] = int(min(255, max(0, g * f)))
            row[o + 2] = int(min(255, max(0, b * f)))
        rows.append(row)
    path = out or os.path.join(config.ROOT, 'out', 'renders', 'top.png')
    write_png_rows(path, W * scale, H * scale, _scale_rows(rows, W, scale))
    return path


# ---------------------------------------------------------------- section
def render_section(ctx, axis='x', at=0, rng=None, scale=1, out=None, features=None):
    from .orchestrate import load_generators
    feats = features if features is not None else load_generators()
    r = int(math.ceil(ctx.area_radius))
    a, b = rng if rng else (-r, r)
    t = ctx.tile
    W, H = b - a + 1, ctx.y_max - ctx.y_min + 1
    rows = [bytearray(AIR_COL) * W for _ in range(H)]          # rows[0] = top (y_max)
    ti = at // t
    for u in range(a // t, b // t + 1):
        tx, tz = (ti, u) if axis == 'x' else (u, ti)
        vol = E.build_tile(ctx, tx, tz, feats)
        sx, sy, sz = vol.sx, vol.sy, vol.sz
        pal = vol.palette
        cols = [AIR_COL if s is None else WATER_COL if is_water(s) else AIR_COL if is_skip(s) else color(s) for s in pal]
        cols = [bytes(c) for c in cols]
        cells = vol.cells
        for k in range(sy):
            row = rows[sy - 1 - k]
            for w in range(t):
                c = u * t + w
                if not a <= c <= b: continue
                if axis == 'x': idx = (k * sz + w) * sx + (at - vol.x1)
                else: idx = (k * sz + (at - vol.z1)) * sx + w
                o = (c - a) * 3
                row[o:o + 3] = cols[cells[idx]]
    path = out or os.path.join(config.ROOT, 'out', 'renders', f'section_{axis}{at}.png')
    write_png_rows(path, W * scale, H * scale, _scale_rows(rows, W, scale))
    return path


# ---------------------------------------------------------------- iso
def _sprites(k):
    """Pixel offsets of the top / left(+z) / right(+x) faces of a block drawn in a 2k x 2k cell (2:1 dimetric)."""
    top, left, right = [], [], []
    h = k / 2.0
    for py in range(2 * k):
        for px in range(2 * k):
            cx, cy = px + 0.5, py + 0.5
            if abs(cx - k) / k + abs(cy - h) / h <= 1.0:
                top.append((px, py))
            elif cx < k and h + cx / 2 <= cy <= h + k + cx / 2:
                left.append((px, py))
            elif cx >= k and h + (2 * k - cx) / 2 <= cy <= h + k + (2 * k - cx) / 2:
                right.append((px, py))
    return top, left, right


def render_iso(ctx, box, y_range=None, k=4, no_dome=False, out=None, features=None):
    """Oblique 2:1 view from the south-east (+x, +z) of a local box x1,z1,x2,z2."""
    from .orchestrate import load_generators
    feats = features if features is not None else load_generators()
    x1, z1, x2, z2 = box
    y1, y2 = y_range if y_range else (ctx.y_min, ctx.y_max)
    vol = E.build_box(ctx, (x1, y1, z1, x2, y2, z2), feats)
    sx, sy, sz = vol.sx, vol.sy, vol.sz
    pal = vol.palette
    skip = [is_skip(s) for s in pal]       # water is treated like air: we look at the structure, not the sea
    glass = [_is_glass(s) for s in pal]
    cols = [color(s) if s is not None and not skip[i] else None for i, s in enumerate(pal)]
    cells = vol.cells
    if no_dome:
        for z in range(sz):
            for x in range(sx):
                inner = ctx.dome_inner_y(x1 + x, z1 + z)
                if inner is None: continue
                for y in range(max(y1, int(inner) - 1), y2 + 1):
                    i = ((y - y1) * sz + z) * sx + x
                    if glass[cells[i]]: cells[i] = 0
    # occupancy
    def solid(x, y, z):
        if x < 0 or y < 0 or z < 0 or x >= sx or y >= sy or z >= sz: return False
        return not skip[cells[(y * sz + z) * sx + x]]
    Wp = (sx + sz) * k + 2 * k
    Hp = (sx + sz) * k // 2 + sy * k + 2 * k
    img = bytearray(SKY_COL) * (Wp * Hp)
    top_s, left_s, right_s = _sprites(k)
    shade = ((1.0, top_s), (0.78, left_s), (0.58, right_s))
    for x in range(sx):
        for z in range(sz):
            for y in range(sy):
                p = cells[(y * sz + z) * sx + x]
                if skip[p]: continue
                faces = (not solid(x, y + 1, z), not solid(x, y, z + 1), not solid(x + 1, y, z))
                if not any(faces): continue
                c = cols[p]
                px0 = (x - z + sz) * k
                py0 = (x + z) * k // 2 + (sy - 1 - y) * k + k
                for fi, (f, spr) in enumerate(shade):
                    if not faces[fi]: continue
                    rgb = bytes((int(c[0] * f), int(c[1] * f), int(c[2] * f)))
                    for dx, dy in spr:
                        o = ((py0 + dy) * Wp + px0 + dx) * 3
                        img[o:o + 3] = rgb
    rows = (img[j * Wp * 3:(j + 1) * Wp * 3] for j in range(Hp))
    path = out or os.path.join(config.ROOT, 'out', 'renders', 'iso.png')
    write_png_rows(path, Wp, Hp, rows)
    return path


# ---------------------------------------------------------------- CLI glue
def _ints(s, n):
    v = tuple(int(p) for p in s.split(','))
    if len(v) != n: raise SystemExit(f'expected {n} comma-separated integers, got {s!r}')
    return v


def render(ctx, a):
    """Entry point used by orchestrate (argparse namespace `a`)."""
    if a.view == 'top':
        return render_top(ctx, _ints(a.box, 4) if a.box else None, max(1, a.scale), a.no_dome, a.jobs, a.out)
    if a.view == 'plan':
        if not a.box or a.y is None: raise SystemExit('plan needs --box x1,z1,x2,z2 and --y N')
        return render_plan(ctx, _ints(a.box, 4), int(a.y), a.scale if a.scale > 1 else 4, None, a.out)
    if a.view == 'section':
        return render_section(ctx, a.axis, a.at, _ints(a.range, 2) if a.range else None, max(1, a.scale), a.out)
    if not a.box: raise SystemExit('iso needs --box x1,z1,x2,z2')
    k = a.scale if a.scale > 1 else 4
    return render_iso(ctx, _ints(a.box, 4), _ints(a.y, 2) if a.y else None, k, a.no_dome, a.out)


# ---------------------------------------------------------------- plan (horizontal slice)
def render_plan(ctx, box, y, scale=4, features=None, out=None):
    """Floor-plan view: the blocks at layer y (the feet level of a floor, walk y + 0) in colour, with the layer below
    (the floor) dimmed where layer y is empty.  Doors/air show the floor; light emitters get a bright dot.  box is
    x1,z1,x2,z2 in local coordinates; scale pixels per block (default 4)."""
    from .orchestrate import load_generators
    feats = features if features is not None else load_generators()
    x1, z1, x2, z2 = box
    vol = E.build_box(ctx, (x1, y - 1, z1, x2, y + 1, z2), feats)
    W, H = x2 - x1 + 1, z2 - z1 + 1
    rows = []
    for z in range(z1, z2 + 1):
        row = bytearray()
        for x in range(x1, x2 + 1):
            b = vol.get(x, y, z)
            if is_skip(b) or b is None:
                lo = vol.get(x, y - 1, z)
                if is_skip(lo) or lo is None: c = AIR_COL
                else: c = tuple(int(v * 0.6) for v in color(lo))
                up = vol.get(x, y + 1, z)
                if not is_skip(up) and up is not None and c == AIR_COL: c = tuple(int(v * 0.5) for v in color(up))
            else:
                c = color(b)
                n = base_name(b)
                # tall things (above y as well) are drawn slightly brighter
                if not is_skip(vol.get(x, y + 1, z)) and vol.get(x, y + 1, z) is not None:
                    c = tuple(min(255, int(v * 1.1) + 8) for v in c)
            row += bytes(c)
        rows.append(row)
    path = out or os.path.join(config.ROOT, 'out', 'renders', f'plan_y{y}.png')
    if scale >= 8:                                     # block grid: darken the first pixel row/column of every block
        def grid(rs):
            for j, r in enumerate(rs):
                if j % scale == 0:
                    r = bytearray(int(v * 0.8) for v in r)
                else:
                    r = bytearray(r)
                    for i in range(0, len(r), scale * 3):
                        for k in range(3): r[i + k] = int(r[i + k] * 0.8)
                yield r
        rows_it = grid(_scale_rows(rows, W, scale))
    else:
        rows_it = _scale_rows(rows, W, scale)
    write_png_rows(path, W * scale, H * scale, rows_it)
    return path
