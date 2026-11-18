"""Validation core: Issue/Partial types, the check registry, and per-tile analysis helpers.

A check is `fn(ctx, tile_key, vol, features) -> list[Issue | Partial]`.
  * vol       the built tile Volume (background already filled; unset cells are real blocks)
  * features  the features that intersect the tile (the same list the builder used)
  * Issue     a finding.  Partial = data for the whole-city `reduce(partials, ctx, features) -> list[Issue]`
              step of the same check (registered with `@register_check(name, reduce=...)`).
Bulk cell work uses bytes/bigint tricks (one byte per cell, shifts by whole bytes) instead of per-cell Python,
which is what keeps a full 32 x 127 x 32 tile around a few tens of ms per check.
"""
import functools, sys
from dataclasses import dataclass, field

from . import classify as K

SEVERITIES = ('error', 'warn', 'info')

# global validator options (set by runner.configure, also in pool workers)
OPTIONS = {'light_halo': True,        # light_coverage: build a 14-block halo around tiles that have dark candidates
           'clearance_margin': 0,     # extra slack (blocks) allowed above ctx.max_building_top
           'dome_tolerance': 0.5,     # dome_geometry: max |centre / radius deviation|
           'walk_seeds': None}        # connectivity: replacement seed areas (tests); None = planner seeds
DEFAULTS = dict(OPTIONS)
ALL_FEATURES = None                   # every registered feature (for halo builds); falls back to `features`


def configure(**kw):
    OPTIONS.update({k: v for k, v in kw.items() if v is not None})


def set_all_features(features):
    global ALL_FEATURES
    ALL_FEATURES = list(features) if features is not None else None


@dataclass
class Issue:
    check: str
    severity: str                 # 'error' | 'warn' | 'info'
    message: str                  # generic text (no coordinates): it is what the summary groups on
    pos: tuple = None             # (x, y, z) of the first/representative cell
    count: int = 1                # number of offending cells / pairs / items
    positions: list = None        # up to 20 example positions
    tile: str = None              # filled in by the runner

    def to_dict(self):
        return {'check': self.check, 'severity': self.severity, 'message': self.message, 'pos': self.pos,
                'count': self.count, 'positions': self.positions, 'tile': self.tile}


@dataclass
class Partial:
    check: str
    tile: str
    data: object = None


@dataclass
class Check:
    name: str
    fn: object
    reduce: object = None
    trace: bool = False           # needs the last-writer trace (runner builds tiles with TracedVolume)
    doc: str = ''


CHECKS = {}


def register_check(name, reduce=None, trace=False):
    def deco(fn):
        CHECKS[name] = Check(name, fn, reduce, trace, (fn.__doc__ or '').strip())
        return fn
    return deco


def resolve_checks(spec):
    """'all' | 'a,b' -> list[Check] in registry order."""
    _load_all()
    if not spec or spec == 'all': return list(CHECKS.values())
    names = [s.strip() for s in spec.split(',') if s.strip()]
    bad = [n for n in names if n not in CHECKS]
    if bad: raise ValueError(f'unknown check(s) {bad}; available: {", ".join(CHECKS)}')
    return [CHECKS[n] for n in CHECKS if n in names]


def _load_all():
    from . import c_blocks, c_leaks, c_dome, c_light, c_features, c_connectivity   # noqa: F401  (registration side effects)


# ---------------------------------------------------------------- tile keys
def parse_key(tile_key):
    """'t_m3_12' -> (-3, 12)."""
    p = tile_key.split('_')
    f = lambda s: -int(s[1:]) if s.startswith('m') else int(s)
    return f(p[1]), f(p[2])


def make_key(tx, tz):
    n = lambda v: f'm{-v}' if v < 0 else str(v)
    return f't_{n(tx)}_{n(tz)}'


# ---------------------------------------------------------------- per-volume analysis
_LITTLE = sys.byteorder == 'little'


class TileInfo:
    """Cached cell tables for one built volume (cache key = a hash of the cells, so mutating and re-checking works)."""

    def __init__(self, ctx, vol):
        self.ctx, self.vol = ctx, vol
        self.sx, self.sy, self.sz = vol.sx, vol.sy, vol.sz
        self.stride = self.sx * self.sz
        self.n = self.stride * self.sy
        raw = vol.cells.tobytes()
        self.ids8 = (raw[0::2] if _LITTLE else raw[1::2]) if len(vol.palette) <= 256 else None
        self._maps = {}
        self._inside = None

    def cellmap(self, key, fn):
        """bytes, one per cell, = fn(BlockInfo of the cell's state) (0..255). Cached under `key`."""
        m = self._maps.get(key)
        if m is None:
            pal = self.vol.palette
            table = [fn(K.info(s)) for s in pal]
            if self.ids8 is not None:
                m = self.ids8.translate(bytes(table + [0] * (256 - len(table))))
            else:
                m = bytes(map(table.__getitem__, self.vol.cells))
            self._maps[key] = m
        return m

    @property
    def inside(self):
        """bytes, 1 where ctx.inside_dome(x, y, z)."""
        if self._inside is None:
            from .. import export_tiles as E
            vol, ctx = self.vol, self.ctx
            ba = bytearray(self.n)
            r = ctx.dome_R - ctx.dome_thickness
            nx = min(max(0, vol.x1), vol.x2); nz = min(max(0, vol.z1), vol.z2)
            if nx * nx + nz * nz < r * r:
                sx, stride = self.sx, self.stride
                for z in range(vol.z1, vol.z2 + 1):
                    for x in range(vol.x1, vol.x2 + 1):
                        rg = E.dome_column_range(ctx, x, z, vol.y1, vol.y2)
                        if rg:
                            lo, hi = rg
                            off = (z - vol.z1) * sx + (x - vol.x1)
                            ba[off + (lo - vol.y1) * stride: off + (hi - vol.y1) * stride + 1: stride] = \
                                b'\x01' * (hi - lo + 1)
            self._inside = bytes(ba)
        return self._inside

    # ---- coordinates
    def xyz(self, i):
        v = self.vol
        return (v.x1 + i % self.sx, v.y1 + i // self.stride, v.z1 + (i // self.sx) % self.sz)

    def index(self, x, y, z):
        v = self.vol
        return ((y - v.y1) * self.sz + (z - v.z1)) * self.sx + (x - v.x1)

    def box_mask(self, boxes):
        """bytes, 1 inside any of the (x1,y1,z1,x2,y2,z2) boxes (clipped to the volume)."""
        ba = bytearray(self.n)
        v = self.vol
        for b in boxes:
            c = v.clip(*b)
            if not c: continue
            w = c[3] - c[0] + 1
            row = b'\x01' * w
            for y in range(c[1], c[4] + 1):
                for z in range(c[2], c[5] + 1):
                    i = self.index(c[0], y, z)
                    ba[i:i + w] = row
        return bytes(ba)


def tinfo(ctx, vol):
    key = (hash(vol.cells.tobytes()), len(vol.palette), ctx.dome_R, ctx.dome_cy)
    c = getattr(vol, '_tinfo', None)
    if c is None or c[0] != key:
        c = (key, TileInfo(ctx, vol))
        try:
            vol._tinfo = c
        except AttributeError:
            pass
    return c[1]


# ---------------------------------------------------------------- bigint helpers (1 byte per cell)
def toint(b):
    return int.from_bytes(b, 'little')


def tobytes(i, n):
    return i.to_bytes(n, 'little')


def set_positions(b, limit=20):
    """Indices of 1-bytes in `b` (first `limit`), and the total count."""
    out, k = [], b.find(1)
    while k >= 0 and len(out) < limit:
        out.append(k); k = b.find(1, k + 1)
    return out, b.count(1)


@functools.lru_cache(maxsize=8)
def edge_masks(sx, sy, sz):
    """(not_last_x, not_first_x, not_last_z, not_first_z) as ints, 1 byte per cell."""
    rx = bytes([1] * (sx - 1) + [0]); fx = bytes([0] + [1] * (sx - 1))
    nlx = toint(rx * (sz * sy)); nfx = toint(fx * (sz * sy))
    rz = (b'\x01' * sx) * (sz - 1) + b'\x00' * sx
    fz = b'\x00' * sx + (b'\x01' * sx) * (sz - 1)
    return nlx, nfx, toint(rz * sy), toint(fz * sy)


def neighbor_pairs(A, B, sx, sy, sz):
    """Face-adjacent pairs (cell with A, neighbour with B) or (B, A). A, B: ints (1 byte per cell).
    Returns three ints (x, y, z axes) with a 1 at the LOWER cell of every such pair."""
    nlx, _, nlz, _ = edge_masks(sx, sy, sz)
    sh = 8
    px = ((A & (B >> sh)) | (B & (A >> sh))) & nlx
    pz = ((A & (B >> (sh * sx))) | (B & (A >> (sh * sx)))) & nlz
    py = (A & (B >> (sh * sx * sz))) | (B & (A >> (sh * sx * sz)))
    return px, py, pz


def neighbors6(S, sx, sy, sz, n):
    """Cells that are a face-neighbour of a cell in S (1 byte per cell ints), clipped to the volume."""
    nlx, nfx, nlz, nfz = edge_masks(sx, sy, sz)
    full = (1 << (8 * n)) - 1
    st = sx * sz
    r = ((S << 8) & nfx) | ((S >> 8) & nlx) | ((S << (8 * sx)) & nfz) | ((S >> (8 * sx)) & nlz) \
        | (S << (8 * st)) | (S >> (8 * st))
    return r & full
