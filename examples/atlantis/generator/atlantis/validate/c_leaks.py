"""leaks: water next to dry space, and waterlogged blocks inside the dome (including across tile borders)."""
from .. import export_tiles as E
from . import classify as K
from .core import (Issue, Partial, register_check, tinfo, toint, tobytes, set_positions, neighbor_pairs)
from .c_blocks import water_ok_boxes
from .trace import get_trace


def _wet_dry(ctx, vol, features):
    """(wet, dry, t) with wet/dry as 1-byte-per-cell ints.
    wet: water, water plants (kelp/seagrass/bubble_column) and waterlogged=true blocks, minus water_ok boxes.
    dry: (a) air/cave_air/void_air that is inside the dome OR was written explicitly by a feature;
         (b) `light`, and non-full blocks (torches, signs, carpets, panes, slabs, stairs...) INSIDE the dome only;
         never a wet cell."""
    t = tinfo(ctx, vol)
    tr = get_trace(ctx, vol, features)
    inside = toint(t.inside)
    touched = toint(bytes(tr.touched))
    wet = toint(t.cellmap('wet', lambda b: 1 if b.wet else 0))
    ok = water_ok_boxes(features)
    if ok and wet: wet &= ~toint(t.box_mask(ok))
    airplain = toint(t.cellmap('airplain', lambda b: 1 if b.plain_air else 0))
    nonfull = toint(t.cellmap('nonfull', lambda b: 1 if b.cls in (K.AIR, K.NOCOLL, K.PART) else 0))
    dry = ((airplain & (inside | touched)) | (nonfull & inside)) & ~wet
    return wet, dry, t, inside


def _face(b, t, side):
    sx, sy, sz, stride = t.sx, t.sy, t.sz, t.stride
    if side == 'W': return b[0::sx]
    if side == 'E': return b[sx - 1::sx]
    off = 0 if side == 'N' else (sz - 1) * sx
    return b''.join(b[y * stride + off: y * stride + off + sx] for y in range(sy))


@register_check('leaks')
def leaks(ctx, tile_key, vol, features):
    """Water (or a waterlogged block / water plant) face-adjacent to a dry cell, or a waterlogged block inside the
    dome. Dry = air inside the dome or explicitly set to air by a feature; `light` and non-full blocks (torch, sign,
    carpet, pane, slab, stair...) count as dry only inside the dome. Tile borders are matched in reduce using the
    border faces recorded here. Report: count of adjacent pairs + first 20 positions (the lower cell of the pair).
    Severity: error. Exempt decorative water with meta={'water_ok': True} (bbox) or a list of boxes."""
    wet, dry, t, inside = _wet_dry(ctx, vol, features)
    out = []
    if wet and dry:
        pairs = neighbor_pairs(wet, dry, t.sx, t.sy, t.sz)
        total, ps = 0, []
        for p in pairs:
            if not p: continue
            pos, cnt = set_positions(tobytes(p, t.n))
            total += cnt; ps.extend(pos)
        if total:
            pp = [t.xyz(i) for i in sorted(set(ps))[:20]]
            out.append(Issue('leaks', 'error', 'water adjacent to dry air (leak)', pp[0], total, pp))
    wl = toint(t.cellmap('wl', lambda b: 1 if b.wl else 0)) & inside
    ok = water_ok_boxes(features)
    if ok and wl: wl &= ~toint(t.box_mask(ok))
    if wl:
        pos, cnt = set_positions(tobytes(wl, t.n))
        pp = [t.xyz(i) for i in pos]
        out.append(Issue('leaks', 'error', 'waterlogged block inside the dome', pp[0], cnt, pp))
    # border faces for the cross-tile match: code = wet | dry << 1
    code = tobytes(wet | (dry << 1), t.n)
    out.append(Partial('leaks', tile_key, {s: _face(code, t, s) for s in 'WENS'}))
    return out


def leaks_reduce(partials, ctx, features=None):
    """Match tile border faces: E face of (tx, tz) with W face of (tx+1, tz); S face with N face of (tx, tz+1)."""
    by = {}
    from .core import parse_key
    for p in partials:
        by[parse_key(p.tile)] = p.data
    out = []
    for (tx, tz), a in sorted(by.items()):
        for nb, sa, sb in (((tx + 1, tz), 'E', 'W'), ((tx, tz + 1), 'S', 'N')):
            b = by.get(nb)
            if b is None: continue
            fa, fb = a[sa], b[sb]
            n = len(fa)
            one = toint(b'\x01' * n)
            A, B = toint(fa), toint(fb)
            hit = ((A & one) & ((B >> 1) & one)) | (((A >> 1) & one) & (B & one))
            if not hit: continue
            pos, cnt = set_positions(tobytes(hit, n))
            x1, y1, z1, x2, y2, z2 = E.tile_bounds(ctx, tx, tz)
            if sa == 'E':
                pp = [(x2, y1 + i // ctx.tile, z1 + i % ctx.tile) for i in pos]
            else:
                pp = [(x1 + i % ctx.tile, y1 + i // ctx.tile, z2) for i in pos]
            out.append(Issue('leaks', 'error', 'water adjacent to dry air across a tile border (leak)', pp[0], cnt,
                             pp, tile=f'{_k(tx, tz)}|{_k(*nb)}'))
    return out


def _k(tx, tz):
    return E.tile_key(tx, tz)


from .core import CHECKS
CHECKS['leaks'].reduce = leaks_reduce
