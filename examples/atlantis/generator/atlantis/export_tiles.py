"""Tiles: split the area into ctx.tile x ctx.tile full-height columns, build them, write vanilla structure templates.

Tile (tx, tz) covers local x = tx*tile .. tx*tile+tile-1 (same for z) and y = ctx.y_min..ctx.y_max.
File name: t_<tx>_<tz>_<hash8>.nbt, negative numbers as m<abs> (t_m3_12_ab12cd34.nbt).
"""
import gzip, hashlib, json, math, os, struct, sys
from array import array

from . import support
from . import background, nbtw
from .volume import Volume
from .util import parse_state

DEFAULT_DATA_VERSION = 3955


# ---------------------------------------------------------------- tile geometry
def tile_bounds(ctx, tx, tz):
    """(x1, y1, z1, x2, y2, z2) local box of tile (tx, tz)."""
    t = ctx.tile
    return tx * t, ctx.y_min, tz * t, tx * t + t - 1, ctx.y_max, tz * t + t - 1


def tiles_in_box(ctx, x1, z1, x2, z2):
    """Tile indices (tx, tz) covering the local block rectangle, sorted."""
    t = ctx.tile
    x1, x2 = sorted((x1, x2)); z1, z2 = sorted((z1, z2))
    return [(tx, tz) for tz in range(z1 // t, z2 // t + 1) for tx in range(x1 // t, x2 // t + 1)]


def tile_in_area(ctx, tx, tz):
    """True if any column of the tile is inside the work mask (circle of area_radius minus protected boxes)."""
    x1, _, z1, x2, _, z2 = tile_bounds(ctx, tx, tz)
    nx, nz = min(max(0, x1), x2), min(max(0, z1), z2)          # nearest point to the centre
    if nx * nx + nz * nz > ctx.area_radius ** 2: return False
    return any(ctx.in_area(x, z) for x in (x1, x2, (x1 + x2) // 2) for z in (z1, z2, (z1 + z2) // 2)) or \
        any(ctx.in_area(x, z) for x in range(x1, x2 + 1, 4) for z in range(z1, z2 + 1, 4))


def all_tiles(ctx):
    r = int(math.ceil(ctx.area_radius))
    return [t for t in tiles_in_box(ctx, -r, -r, r, r) if tile_in_area(ctx, *t)]


def _n(v):
    return f'm{-v}' if v < 0 else str(v)


def tile_key(tx, tz):
    return f't_{_n(tx)}_{_n(tz)}'


def tile_filename(tx, tz, hash8):
    return f'{tile_key(tx, tz)}_{hash8}.nbt'


def parse_tile_filename(fn):
    """'t_m3_12_ab12cd34.nbt' -> (-3, 12, 'ab12cd34')."""
    parts = os.path.basename(fn)[:-4].split('_')
    f = lambda s: -int(s[1:]) if s.startswith('m') else int(s)
    return f(parts[1]), f(parts[2]), parts[3]


# ---------------------------------------------------------------- building
def feature_intersects(f, box):
    b = f.bbox
    return not (b[3] < box[0] or b[0] > box[3] or b[4] < box[1] or b[1] > box[4] or b[5] < box[2] or b[2] > box[5])


def build_box(ctx, box, features, support_fix=True):
    """Volume over `box` with the intersecting features built in layer order (ties by name), background filled.
    With support_fix, the box is built with a 1-block halo so support.fix can see the neighbours of edge cells
    (blocks Minecraft would break on placement are fixed), then cropped back to `box`."""
    x1, y1, z1, x2, y2, z2 = box
    big = (x1 - 1, y1, z1 - 1, x2 + 1, y2, z2 + 1) if support_fix else box
    vol = Volume(*big)
    for f in sorted((f for f in features if feature_intersects(f, big)), key=lambda f: (f.layer, f.name)):
        f.build(vol, ctx)
    fill_background(ctx, vol)
    if not support_fix: return vol
    support.fix(vol, ctx, (x1, y1, z1, x2, y2, z2))
    out = Volume(*box)
    out.palette, out.index = list(vol.palette), dict(vol.index)
    cells, sx, sz, ox = vol.cells, vol.sx, vol.sz, out.sx
    oc = out.cells
    k = 0
    for y in range(y1, y2 + 1):
        for z in range(z1, z2 + 1):
            b = ((y - y1) * sz + (z - z1 + 1)) * sx + 1
            oc[k:k + ox] = cells[b:b + ox]
            k += ox
    return out


def build_tile(ctx, tx, tz, features):
    return build_box(ctx, tile_bounds(ctx, tx, tz), features)


def dome_column_range(ctx, x, z, ylo, yhi):
    """Inclusive (lo, hi) y range clipped to [ylo, yhi] where ctx.inside_dome(x, y, z) is True, or None.
    Exact: a float estimate is corrected with the real predicate."""
    r = ctx.dome_R - ctx.dome_thickness
    q = r * r - x * x - z * z
    if q <= 0: return None
    s = math.sqrt(q); inside = ctx.inside_dome
    top = int(math.floor(ctx.dome_cy + s)) + 1
    lo = max(1, int(math.floor(ctx.dome_cy - s)) - 1)
    while lo <= top + 1 and not inside(x, lo, z): lo += 1
    if lo > top + 1: return None
    while lo > 1 and inside(x, lo - 1, z): lo -= 1
    hi = top
    while hi >= lo and not inside(x, hi, z): hi -= 1
    while inside(x, hi + 1, z): hi += 1
    if hi < lo: return None
    lo, hi = max(lo, ylo), min(hi, yhi)
    return (lo, hi) if lo <= hi else None


def fill_background(ctx, vol):
    """Replace every unset cell with background(ctx, x, y, z), computed per column (equal to the per-cell result)."""
    pid = vol._pid
    air, water, bedrock = pid('air'), pid('water'), pid('bedrock')
    sx, sy, sz = vol.sx, vol.sy, vol.sz
    y1, y2, cells = vol.y1, vol.y2, vol.cells
    step = sx * sz
    seabed_h = background.seabed_height
    ymin = ctx.y_min
    void = None
    for z in range(vol.z1, vol.z2 + 1):
        for x in range(vol.x1, vol.x2 + 1):
            base = (z - vol.z1) * sx + (x - vol.x1)
            if not ctx.in_area(x, z):                      # outside the work mask: leave the world untouched
                if void is None: void = pid('structure_void')
                cells[base::step] = array('H', [void] * sy)
                continue
            cur = cells[base::step]
            if 0 not in cur: continue
            col = [water] * sy
            s = seabed_h(ctx, x, z)
            for y in range(y1, min(s, y2) + 1):
                col[y - y1] = pid(background._seabed_block(ctx, x, y, z, s))
            if y1 <= ymin <= y2: col[ymin - y1] = bedrock
            r = dome_column_range(ctx, x, z, y1, y2)
            if r: col[r[0] - y1:r[1] - y1 + 1] = [air] * (r[1] - r[0] + 1)
            if cur.count(0) == sy:
                cells[base::step] = array('H', col)
            else:
                cells[base::step] = array('H', [c or d for c, d in zip(cur, col)])


# ---------------------------------------------------------------- NBT
def _canonical(ctx, vol):
    """(states, cells): used states sorted by string, and the cells remapped to indices into that list."""
    if 0 in vol.cells: fill_background(ctx, vol)
    used = sorted(set(vol.cells))
    states = sorted(vol.palette[i] for i in used)
    new = {s: i for i, s in enumerate(states)}
    lut = [0] * len(vol.palette)
    for i in used: lut[i] = new[vol.palette[i]]
    return states, array('H', [lut[c] for c in vol.cells])


def _hash(vol, states, cells):
    h = hashlib.sha1()
    h.update(b'atlantis-tile-v1\n')
    h.update(f'{vol.sx},{vol.sy},{vol.sz}\n'.encode())
    h.update('\n'.join(states).encode() + b'\n')
    c = array('H', cells)
    if sys.byteorder == 'little': c.byteswap()
    h.update(c.tobytes())
    return h.hexdigest()[:8]


def tile_hash(ctx, vol):
    states, cells = _canonical(ctx, vol)
    return _hash(vol, states, cells)


_REC = struct.Struct('>bH3sbi3ibH5sib')   # one block compound: pos list [x,y,z], state int, end tag
assert _REC.size == 36


def _tag(t, name, payload):
    n = name.encode()
    return struct.pack('>bH', t, len(n)) + n + payload


def _nbt_and_hash(ctx, vol):
    states, cells = _canonical(ctx, vol)
    pal = []
    for s in states:
        name, props = parse_state(s)
        e = {'Name': name}
        if props: e['Properties'] = props
        pal.append(e)
    dv = ctx.profile.get('data_version', DEFAULT_DATA_VERSION)
    sx, sy, sz = vol.sx, vol.sy, vol.sz
    skip = states.index('structure_void') if 'structure_void' in states else -1   # omitted = not placed
    n = len(cells) - (cells.count(skip) if skip >= 0 else 0)
    buf = bytearray(_REC.size * n)
    pack = _REC.pack_into
    off = 0; i = 0
    for y in range(sy):
        for z in range(sz):
            for x in range(sx):
                c = cells[i]; i += 1
                if c == skip: continue
                pack(buf, off, 9, 3, b'pos', 3, 3, x, y, z, 3, 5, b'state', c, 0)
                off += 36
    out = [b'\x0a\x00\x00',
           _tag(3, 'DataVersion', struct.pack('>i', dv)),
           _tag(nbtw._payload(nbtw.IntList([sx, sy, sz]))[0], 'size', nbtw._payload(nbtw.IntList([sx, sy, sz]))[1]),
           _tag(9, 'palette', nbtw._payload(pal)[1]),
           _tag(9, 'blocks', struct.pack('>bi', 10, n)), bytes(buf),
           _tag(9, 'entities', struct.pack('>bi', 0, 0)),
           b'\x00']
    return b''.join(out), _hash(vol, states, cells)


def tile_nbt(ctx, vol):
    """Uncompressed NBT bytes of the vanilla structure template (unset cells are filled with the background first)."""
    return _nbt_and_hash(ctx, vol)[0]


def write_tile(ctx, tx, tz, vol, out_dir, skip_existing=True):
    """Write t_<tx>_<tz>_<hash8>.nbt (gzip, mtime 0) into out_dir. Returns (name, hash8, path); the file is not
    rewritten when it already exists (same hash = same content). Older files of the same tile are removed."""
    os.makedirs(out_dir, exist_ok=True)
    data, h = _nbt_and_hash(ctx, vol)
    name = tile_filename(tx, tz, h)
    path = os.path.join(out_dir, name)
    if not (skip_existing and os.path.exists(path)):
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(gzip.compress(data, compresslevel=5, mtime=0))
        os.replace(tmp, path)
    prefix = tile_key(tx, tz) + '_'
    for fn in os.listdir(out_dir):
        if fn.startswith(prefix) and fn.endswith('.nbt') and fn != name and fn.count('_') == 3:
            os.remove(os.path.join(out_dir, fn))
    return name, h, path


def world_corner(ctx, tx, tz):
    """[wx, wy, wz] of the tile's north-west-bottom corner, or None when the profile has no origin yet."""
    p = ctx.profile
    if p.get('origin_x') is None or p.get('origin_z') is None: return None
    b = tile_bounds(ctx, tx, tz)
    return list(ctx.to_world(b[0], b[1], b[2]))


def update_tiles_json(ctx, out_dir, entries):
    """entries: {(tx, tz): (name, hash8)}; merged into out_dir/tiles.json."""
    path = os.path.join(out_dir, 'tiles.json')
    try: data = json.load(open(path))
    except (OSError, ValueError): data = {}
    for (tx, tz), (name, h) in entries.items():
        data[tile_key(tx, tz)] = {'name': name, 'hash': h, 'world_corner': world_corner(ctx, tx, tz)}
    with open(path, 'w') as f:
        json.dump(dict(sorted(data.items())), f, indent=1)
    return path
