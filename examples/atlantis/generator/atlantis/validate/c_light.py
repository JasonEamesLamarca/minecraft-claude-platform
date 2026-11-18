"""light_coverage: block-light propagation and dark walkable cells.

Model (documented simplification of Minecraft block light):
  * emitters: palette.EMISSION (+ a few vanilla extras in classify.EXTRA_EMISSION) and light[level=N].
  * a cell receives light from a face neighbour, losing 1 per block; it never loses more (water, glass, slabs,
    stairs, panes, leaves, doors, air... all attenuate by exactly 1).
  * opaque cells (full solid cubes other than glass/leaves/ice/slime/honey/barrier/beacon/spawner; tinted glass is
    opaque; structure_void is opaque) receive no light. An emitter that is itself opaque (sea lantern) still emits
    into its neighbours.
  * level >= k sets are computed with bigint bit-parallel flood fill: S_k = (E_k | N(S_k+1)) & (passable | E_k).
Walkable surface cell: the cell and the cell above are non-colliding (air/light/torch/...), the cell below is a full
block or a colliding partial block (slab, stair, carpet...), and the cell is dry (inside the dome, or air written
explicitly by a feature). A walkable cell with block light 0 is a warning.
Tile borders: light from neighbouring tiles reaches up to 14 blocks in. Stage 1 runs on the tile alone (a lower bound
on light). If any dark cell is within 14 blocks (horizontally) of a tile edge and OPTIONS['light_halo'] is true, a
padded volume (tile + 14 on each side) is built from ALL_FEATURES (set by the runner) and the cells are re-evaluated;
with light_halo false those near-edge cells are NOT reported (conservative: no false positives).
"""
from .. import export_tiles as E
from . import classify as K
from .core import (Issue, OPTIONS, register_check, tinfo, toint, tobytes, set_positions, neighbors6)
from . import core

HALO = 14
_GE = {k: bytes(1 if v >= k else 0 for v in range(256)) for k in range(1, 16)}


def propagate(t):
    """Int (1 byte per cell) of cells with block light >= 1 in the volume described by TileInfo t."""
    em = t.cellmap('emit', lambda b: min(b.emit, 15))
    top = max(em)
    if top == 0: return 0
    passable = toint(t.cellmap('lpass', lambda b: 0 if b.opaque else 1))
    S = 0
    for k in range(top, 0, -1):
        Ek = toint(em.translate(_GE[k]))
        nb = neighbors6(S, t.sx, t.sy, t.sz, t.n) if S else 0
        S = (Ek | (nb & passable))
    return S


def _crop(b, pt, core, H):
    """Core-tile bytes out of padded-volume bytes (same y range)."""
    sx, sz = core.sx, core.sz
    out = []
    for y in range(core.sy):
        for z in range(sz):
            s = ((y * pt.sz) + z + H) * pt.sx + H
            out.append(b[s:s + sx])
    return b''.join(out)


def _dark(ctx, vol, features, lit_int):
    t = tinfo(ctx, vol)
    tr = None
    from .trace import get_trace
    tr = get_trace(ctx, vol, features)
    A = toint(t.cellmap('airish', lambda b: 1 if b.cls in (K.AIR, K.NOCOLL) else 0))
    SB = toint(t.cellmap('solidbelow', lambda b: 1 if b.cls in (K.FULL, K.PART) else 0))
    plain = toint(t.cellmap('airplain', lambda b: 1 if b.plain_air else 0))
    dry = (A & toint(t.inside)) | (plain & toint(bytes(tr.touched)))
    st = 8 * t.stride
    walk = A & (A >> st) & (SB << st) & dry & ((1 << (8 * t.n)) - 1)
    return walk, t


@register_check('light_coverage')
def light_coverage(ctx, tile_key, vol, features):
    """Walkable surface cells (solid below, two free cells above, dry) with block light 0: warning with count and the
    first 20 positions. See the module docstring for the light model and the tile-border (halo) handling."""
    t = tinfo(ctx, vol)
    walk, t = _dark(ctx, vol, features, None)
    if not walk: return []
    lit = propagate(t)
    dark = walk & ~lit
    if not dark: return []
    db = tobytes(dark, t.n)
    # dark cells that could still be lit from a neighbouring tile
    near = _near_edge_mask(t, HALO)
    far = dark & ~near
    cand = dark & near
    final = far
    if cand and OPTIONS['light_halo']:
        H = HALO
        box = (vol.x1 - H, vol.y1, vol.z1 - H, vol.x2 + H, vol.y2, vol.z2 + H)
        feats = core.ALL_FEATURES if core.ALL_FEATURES is not None else features
        pv = E.build_box(ctx, box, feats)
        pt = tinfo(ctx, pv)
        plit = propagate(pt)
        lit2 = toint(_crop(tobytes(plit, pt.n), pt, t, H))
        final = far | (cand & ~lit2)
    if not final: return []
    pos, cnt = set_positions(tobytes(final, t.n))
    ps = [t.xyz(i) for i in pos]
    return [Issue('light_coverage', 'warn', 'walkable surface cell with block light 0', ps[0], cnt, ps)]


_NEAR = {}


def _near_edge_mask(t, H):
    key = (t.sx, t.sy, t.sz, H)
    m = _NEAR.get(key)
    if m is None:
        row = bytes(1 if min(x, t.sx - 1 - x) < H else 0 for x in range(t.sx))
        rows = []
        for z in range(t.sz):
            rows.append(b'\x01' * t.sx if min(z, t.sz - 1 - z) < H else row)
        layer = b''.join(rows)
        m = _NEAR[key] = toint(layer * t.sy)
    return m
