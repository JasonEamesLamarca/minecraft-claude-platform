"""Layer 40: the three residential pier districts.

C1 East Terraces   (pier plan angle 120)  stepped terrace blocks, cascading roof gardens, glass-railed balconies
C2 South Promenade (pier plan angle 180)  slender towers, arcaded townhouses and courtyards along the avenue
C3 West Gardens    (pier plan angle 300)  low villas and pavilions among planted courtyards, hedges, pergolas

18 designs (6 per district).  Every lot is one Feature `res_<c1|c2|c3>_<nn>_<design>`; its bbox is the lot (building,
yard, half of the side lanes).  Lots are planned programmatically (`plan_lots`): candidate rectangles along both sides
of the grand avenue, in rows, rejected if they touch roads/plazas/props/other bboxes/the district edge, with a lane of
6 blocks (3 + 3) between neighbours.  Heights step down from ~45 (r 310) to ~15 (r 490), always under the dome cap.

CONVENTIONS
  * Each lot is built once per process as a landmark_command.Plan (a cell dict that refuses cells outside the bbox, the
    district, on roads or above ctx.max_building_top; `rejected` must stay 0) and copied into the tile column by column.
  * Buildings are designed in a lot-local frame: a runs along the facade, b runs from the street (b = 0) to the back;
    `Loc` rotates this onto the world axes (the front looks towards the avenue).
  * Floors: slab at F = 4 + 5 m, walk y = F + 1, four air rows, next slab at F + 5.  A switchback stair core (6 x 4)
    links every floor and the roof garden; the slab hole is open over the upper three steps.
  * Light: hidden light[level=15] blocks on a skewed lattice in every room, terrace, roof, yard and lane, plus lantern
    posts in the lanes.
"""
import hashlib
import json
import math
import os
import random

from . import config, planner as PL, roads, palette as P, blocks as B, geometry as G
from . import feature as FT
from .feature import Feature, register
from .util import hash_str
from .landmark_command import Plan

LAYER = 40
DECK = PL.DECK_TOP
WALK = PL.WALK_Y
PITCH = 5
LANE = 3                      # half lane width
R_MIN, R_MAX = 306, 491       # lot cells must lie inside this radius band (district asked: 305 .. 492)

_CTX = config.load()

WALL_OK = P.WHITE
LIGHT = P.HIDDEN_LIGHT
LAMP = P.LAMP
DIRS4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
DNAME = {(0, -1): 'north', (1, 0): 'east', (0, 1): 'south', (-1, 0): 'west'}
DVEC = {v: k for k, v in DNAME.items()}


def nb4(c):
    a, b = c
    return ((a + 1, b), (a - 1, b), (a, b + 1), (a, b - 1))


def rect(a0, b0, a1, b1):
    return {(a, b) for a in range(a0, a1) for b in range(b0, b1)}


def disc(cx, cb, r):
    out = set()
    for a in range(int(cx - r) - 1, int(cx + r) + 2):
        for b in range(int(cb - r) - 1, int(cb + r) + 2):
            if (a + 0.5 - cx) ** 2 + (b + 0.5 - cb) ** 2 <= r * r: out.add((a, b))
    return out


# ------------------------------------------------------------------------------------------------------------
# DESIGNS: each function returns a spec dict for n floors (raw coordinates, normalised by the layout step)
# ------------------------------------------------------------------------------------------------------------
def _pick(rng, seq):
    return seq[rng.randrange(len(seq))]


def _style(rng, fam, **kw):
    walls = {'C1': ['smooth_quartz', 'white_concrete', 'quartz_bricks'],
             'C2': ['smooth_quartz', 'calcite', 'quartz_block'],
             'C3': ['white_concrete', 'calcite', 'smooth_quartz']}[fam]
    accents = {'C1': ['light_blue_concrete', 'cyan_terracotta', 'prismarine_bricks'],
               'C2': ['cyan_concrete', 'polished_deepslate', 'prismarine'],
               'C3': ['light_gray_concrete', 'polished_andesite', 'cyan_terracotta']}[fam]
    glass = {'C1': [P.GLASS_BLUE, P.GLASS_BLUE, P.GLASS],
             'C2': [P.GLASS, P.GLASS_BLUE, P.GLASS_CYAN],
             'C3': [P.GLASS, P.GLASS, P.GLASS_WHITE]}[fam]
    s = dict(wall=_pick(rng, walls), accent=_pick(rng, accents), glass=_pick(rng, glass),
             front='ribbon', side='ribbon', back='punched', court='ribbon', ground=None,
             ms=LANE, fm=4, core='back', court_cells=set(), units=None, doors=None, balcony=None,
             crown=False, yard='terrace', ext=0, floor_kind=fam)
    s.update(kw)
    return s


def d_terrace_cascade(rng, n):
    W, D = rng.randint(22, 30), rng.randint(17, 21)
    masks = []
    for f in range(n):
        r0, s0 = min(3 * f, D - 12), f // 3
        masks.append(rect(s0, r0, W - s0, D))
    return dict(masks=masks, **_style(rng, 'C1', front='curtain', side='ribbon', back='punched', yard='terrace'))


def d_terrace_crescent(rng, n):
    W, Rc, T0 = rng.randint(32, 38), rng.randint(22, 28), rng.randint(16, 19)
    cx, cb = W / 2.0, -Rc
    masks = []
    for f in range(n):
        rin = Rc + min(3 * f, T0 - 11)
        masks.append({(a, b) for a in range(W) for b in range(-Rc, T0 + 4)
                      if rin <= math.hypot(a + 0.5 - cx, b + 0.5 - cb) <= Rc + T0})
    return dict(masks=masks, **_style(rng, 'C1', front='curtain', side='ribbon', back='punched', core='center',
                                      yard='terrace'))


def d_ziggurat(rng, n):
    t = (n + 1) // 2
    W, D = 12 + 4 * (t - 1) + rng.randint(0, 2), 10 + 4 * (t - 1) + rng.randint(0, 2)
    masks = [rect(2 * (f // 2), 2 * (f // 2), W - 2 * (f // 2), D - 2 * (f // 2)) for f in range(n)]
    return dict(masks=masks, **_style(rng, 'C1', front='ribbon', side='ribbon', back='ribbon', core='center',
                                      yard='terrace', fm=5))


def d_twin_stagger(rng, n):
    w1, w2 = rng.randint(16, 18), rng.randint(16, 18)
    A = rect(0, 0, w1, 13)
    Bk = rect(w1 - 8, 6, w1 - 8 + w2, 19)
    masks = []
    for f in range(n):
        k = 2 * (f // 3)
        m = set()
        for (a, b) in A | Bk:
            if (a, b) in A and a < k and (a, b) not in Bk: continue
            if (a, b) in Bk and a >= w1 - 8 + w2 - k and (a, b) not in A: continue
            m.add((a, b))
        masks.append(m)
    return dict(masks=masks, **_style(rng, 'C1', front='curtain', side='punched', back='punched', core='back',
                                      yard='terrace'))


def d_l_terrace(rng, n):
    W, L = rng.randint(24, 30), rng.randint(22, 28)
    masks = []
    for f in range(n):
        k = 3 * (f // 2)
        w1 = max(11, W - k); l2 = max(12, L - k)
        masks.append(rect(0, 0, w1, 9) | rect(0, 0, 10, l2))
    return dict(masks=masks, **_style(rng, 'C1', front='curtain', side='ribbon', back='punched', core='back',
                                      yard='terrace', doors=[0.7]))


def d_balcony_slab(rng, n):
    W, D = rng.randint(22, 30), rng.randint(12, 14)
    masks = []
    for f in range(n):
        s0 = 3 if (n >= 4 and f >= n - 2) else 0
        masks.append(rect(s0, 0, W - s0, D))
    bays = []
    k = 3
    while k + 5 <= W - 3:
        bays.append((k, 5)); k += 7
    return dict(masks=masks, **_style(rng, 'C1', front='ribbon', side='punched', back='punched', core='back',
                                      yard='terrace', fm=5, balcony=dict(bays=bays, floors=list(range(1, n)))))


# ---- C2
def d_slender_tower(rng, n):
    S = rng.randint(13, 15)
    masks = []
    for f in range(n):
        k = 0 if f < n / 2.0 else 2
        masks.append(rect(k, k, S - k, S - k))
    return dict(masks=masks, **_style(rng, 'C2', front='curtain', side='curtain', back='curtain', ground=dict(
        front='arcade', side='arcade', back='arcade', court='arcade'), core='center', yard='promenade', fm=5,
        crown=True, ext=4))


def d_round_tower(rng, n):
    R = rng.choice([9.5, 10.0, 10.5])
    masks = []
    for f in range(n):
        r = R - (0 if f < n / 2.0 else 2.0)
        masks.append(disc(R, R, r))
    return dict(masks=masks, **_style(rng, 'C2', front='ribbon', side='ribbon', back='ribbon', ground=dict(
        front='arcade', side='arcade', back='arcade', court='arcade'), core='center', yard='promenade', fm=5,
        crown=True, ext=4))


def d_townhouse_row(rng, n):
    uw = rng.choice([10, 11])
    hs = [max(2, n - 1), n, max(2, n - 2)]
    if rng.random() < 0.5: hs = hs[::-1]
    D = rng.randint(13, 14)
    masks = []
    for f in range(n):
        m = set()
        for u in range(3):
            if hs[u] > f: m |= rect(u * uw, 0, (u + 1) * uw, D)
        masks.append(m)
    acc = ['light_blue_concrete', 'cyan_concrete', 'prismarine_bricks', 'white_concrete']
    walls = ['smooth_quartz', 'quartz_bricks', 'calcite']
    units = [(u * uw, (u + 1) * uw, _pick(rng, walls), _pick(rng, acc)) for u in range(3)]
    st = _style(rng, 'C2', front='ribbon', side='punched', back='punched', ground=dict(
        front='arcade', side='solid', back='punched', court='arcade'), core='center', yard='promenade', fm=5,
        ms=0, units=units, doors=[uw * 0.5 + uw * k for k in range(3)])
    return dict(masks=masks, **st)


def d_arcade_horseshoe(rng, n):
    W, D = rng.randint(26, 32), rng.randint(20, 24)
    cw = rng.choice([8, 10])
    a0 = (W - cw) // 2
    court = rect(a0, 0, a0 + cw, D - 9)
    masks = [rect(0, 0, W, D) - court for f in range(n)]
    return dict(masks=masks, **_style(rng, 'C2', front='ribbon', side='ribbon', back='punched', court='ribbon',
                                      ground=dict(front='arcade', side='arcade', back='punched', court='arcade'),
                                      core='back', yard='promenade', fm=5, court_cells=court, yard2='court'))


def d_podium_tower(rng, n):
    PW, PD = rng.randint(24, 28), rng.randint(17, 19)
    T = rng.randint(13, 15)
    ta, tb = (PW - T) // 2, PD - T - 1
    masks = []
    for f in range(n):
        if f < 2: masks.append(rect(0, 0, PW, PD))
        else:
            k = 0 if f < 2 + (n - 2) / 2.0 else 2
            masks.append(rect(ta + k, tb + k, ta + T - k, tb + T - k))
    return dict(masks=masks, **_style(rng, 'C2', front='curtain', side='curtain', back='ribbon', ground=dict(
        front='arcade', side='arcade', back='punched', court='arcade'), core='center', yard='promenade', fm=5,
        crown=True, ext=4))


def d_bow_mansion(rng, n):
    W, D = rng.randint(20, 24), rng.randint(12, 14)
    bulge = disc(W / 2.0, 0, 7.5)
    masks = []
    for f in range(n):
        k = 2 if (n >= 4 and f == n - 1) else 0
        base = rect(k, 0, W - k, D) | ({c for c in bulge if c[1] <= 0} if f < n - (1 if n >= 4 else 0) else set())
        masks.append(base)
    return dict(masks=masks, **_style(rng, 'C2', front='curtain', side='punched', back='punched', ground=dict(
        front='ribbon', side='punched', back='punched', court='ribbon'), core='back', yard='promenade', fm=5))


# ---- C3
def d_villa_flat(rng, n):
    W, D = rng.randint(17, 22), rng.randint(12, 15)
    masks = []
    for f in range(n):
        k = 3 if f >= 1 else 0
        masks.append(rect(k, 0 if f == 0 else 2, W - k, D))
    return dict(masks=masks, **_style(rng, 'C3', front='curtain', side='punched', back='punched', core='back',
                                      yard='garden', fm=7))


def d_pavilion_colonnade(rng, n):
    W, D = rng.randint(14, 17), rng.randint(12, 14)
    masks = [rect(0, 0, W, D) for f in range(n)]
    return dict(masks=masks, **_style(rng, 'C3', front='ribbon', side='ribbon', back='ribbon', ground=dict(
        front='colonnade', side='colonnade', back='colonnade', court='colonnade'), core='center', yard='garden',
        fm=7))


def d_courtyard_villa(rng, n):
    W, D = rng.randint(24, 28), rng.randint(20, 24)
    court = rect(7, 6, W - 7, D - 6)
    masks = [rect(0, 0, W, D) - court for f in range(n)]
    return dict(masks=masks, **_style(rng, 'C3', front='punched', side='punched', back='punched', court='ribbon',
                                      ground=dict(front='punched', side='punched', back='punched', court='arcade'),
                                      core='back', yard='garden', fm=7, court_cells=court, yard2='court'))


def d_drum_pavilion(rng, n):
    R = rng.choice([9.0, 10.0, 10.5])
    masks = [disc(R, R, R - 1.5 * f) for f in range(n)]
    return dict(masks=masks, **_style(rng, 'C3', front='curtain', side='curtain', back='curtain', core='center',
                                      yard='garden', fm=7))


def d_cottage_pergola(rng, n):
    W, D = rng.randint(12, 14), rng.randint(11, 12)
    masks = [rect(0, 0, W, D) for f in range(n)]
    return dict(masks=masks, **_style(rng, 'C3', front='punched', side='punched', back='punched', core='back',
                                      yard='garden', fm=9, pergola=True))


def d_split_level(rng, n):
    nb = max(1, n - 1)
    A = rect(0, 0, 16, 13)
    Bk = rect(10, 7, 26, 20)
    masks = []
    for f in range(n):
        m = set(A)
        if f < nb: m |= Bk
        masks.append(m)
    return dict(masks=masks, **_style(rng, 'C3', front='curtain', side='punched', back='punched', core='back',
                                      yard='garden', fm=7, pergola=True))


# name: (district, nmin, nmax, fn)
DESIGNS = {
    'terrace_cascade': ('C1', 3, 7, d_terrace_cascade),
    'terrace_crescent': ('C1', 3, 7, d_terrace_crescent),
    'ziggurat_stack': ('C1', 4, 8, d_ziggurat),
    'twin_stagger': ('C1', 3, 8, d_twin_stagger),
    'l_terrace': ('C1', 3, 8, d_l_terrace),
    'balcony_slab': ('C1', 3, 8, d_balcony_slab),
    'slender_tower': ('C2', 4, 9, d_slender_tower),
    'round_tower': ('C2', 4, 9, d_round_tower),
    'townhouse_row': ('C2', 3, 6, d_townhouse_row),
    'arcade_horseshoe': ('C2', 2, 5, d_arcade_horseshoe),
    'podium_tower': ('C2', 4, 9, d_podium_tower),
    'bow_mansion': ('C2', 3, 6, d_bow_mansion),
    'villa_flat': ('C3', 1, 3, d_villa_flat),
    'pavilion_colonnade': ('C3', 1, 2, d_pavilion_colonnade),
    'courtyard_villa': ('C3', 1, 2, d_courtyard_villa),
    'drum_pavilion': ('C3', 2, 3, d_drum_pavilion),
    'cottage_pergola': ('C3', 1, 2, d_cottage_pergola),
    'split_level': ('C3', 2, 3, d_split_level),
}
FAMILY = {'C1': ['terrace_cascade', 'terrace_crescent', 'ziggurat_stack', 'twin_stagger', 'l_terrace',
                 'balcony_slab'],
          'C2': ['slender_tower', 'round_tower', 'townhouse_row', 'arcade_horseshoe', 'podium_tower', 'bow_mansion'],
          'C3': ['villa_flat', 'pavilion_colonnade', 'courtyard_villa', 'drum_pavilion', 'cottage_pergola',
                 'split_level']}


# ------------------------------------------------------------------------------------------------------------
# layout: masks in lot-local coordinates, core, doors
# ------------------------------------------------------------------------------------------------------------
class Layout:
    pass


def make_layout(design, seed, n):
    dist, nmin, nmax, fn = DESIGNS[design]
    rng = random.Random(seed)
    spec = fn(rng, n)
    L = Layout()
    L.design, L.district, L.spec, L.n = design, dist, spec, n
    raw = spec['masks']
    amin = min(c[0] for c in raw[0]); bmin = min(c[1] for c in raw[0])
    amax = max(c[0] for c in raw[0]); bmax = max(c[1] for c in raw[0])
    ms, fm = spec['ms'], spec['fm']
    da, db = ms - amin, fm - bmin
    sh = lambda s: {(a + da, b + db) for (a, b) in s}
    L.M = [sh(m) for m in raw]
    L.W, L.D = amax - amin + 1, bmax - bmin + 1
    L.ms, L.fm = ms, fm
    L.LW, L.LD = L.W + 2 * ms, L.D + fm + LANE
    L.court = sh(spec['court_cells'])
    L.units = None
    if spec['units']:
        L.units = [(u[0] + da, u[1] + da, u[2], u[3]) for u in spec['units']]
    L.balcony = None
    if spec['balcony']:
        bl = spec['balcony']
        L.balcony = dict(bays=[(a0 + da, w) for a0, w in bl['bays']], floors=bl['floors'])
    L.I = [{c for c in m if all(nb in m for nb in nb4(c))} for m in L.M]
    M0, I0 = L.M[0], L.I[0]
    # ---- doors
    targets = spec['doors'] or [0.5]
    L.doors = []
    cols = {}
    for (a, b) in M0: cols.setdefault(a, []).append(b)
    for t in targets:
        at = (amin + (amax - amin) * t if t <= 1.0 else t) + da if not spec['units'] else t + da
        best = None
        for a in sorted(cols):
            bf = min(cols[a])
            if (a + 1) not in cols or min(cols[a + 1]) != bf: continue
            if (a, bf - 1) in M0 or (a + 1, bf - 1) in M0: continue
            if not all(c in I0 for c in ((a, bf + 1), (a + 1, bf + 1), (a, bf + 2), (a + 1, bf + 2))): continue
            if any(abs(a - d[0][0]) < 5 for d in L.doors): continue
            score = abs(a + 0.5 - at) + bf * 0.15
            if best is None or score < best[0]: best = (score, a, bf)
        if best is None: raise ValueError('no door')
        L.doors.append(((best[1], best[2]), (best[1] + 1, best[2])))
    vest = set()
    for (d0, d1) in L.doors:
        for dd in range(1, 4):
            for dx in (-1, 0, 1, 2): vest.add((d0[0] + dx, d0[1] + dd))
    # ---- stair core
    top = L.M[n - 1]; itop = L.I[n - 1]
    cx = sum(c[0] for c in top) / len(top); cb = sum(c[1] for c in top) / len(top)
    if spec['core'] == 'back':
        order = sorted(itop, key=lambda c: (-c[1], abs(c[0] + 2.5 - cx), c[0]))
    else:
        order = sorted(itop, key=lambda c: ((c[0] + 2.5 - cx) ** 2 + (c[1] + 1.5 - cb) ** 2, c[0], c[1]))
    cands = []
    for (a0, b0) in order:
        ok = True
        for a in range(a0 - 1, a0 + 7):
            for b in range(b0 - 1, b0 + 5):
                c = (a, b)
                if c not in top or c in vest or (0 <= a - a0 < 6 and 0 <= b - b0 < 4 and c not in itop):
                    ok = False; break
            if not ok: break
        if ok:
            cands.append((a0, b0)); break
    if not cands: raise ValueError('no core')
    L.core = cands[0]
    for m in range(1, n):
        S, Mm = L.M[m - 1], L.M[m]
        O = S - Mm
        if O:
            E = {c for c in O if any(nb not in S for nb in nb4(c))}
            if not (O - E): raise ValueError('thin terrace')
    L.ytop = DECK + PITCH * n + 1 + spec['ext']
    L.ymax = max(L.ytop, WALK + 8)
    return L


# ------------------------------------------------------------------------------------------------------------
# local frame
# ------------------------------------------------------------------------------------------------------------
_EA = [(1, 0), (0, 1), (-1, 0), (0, -1)]
_EB = [(0, 1), (-1, 0), (0, -1), (1, 0)]
FACINGS = ['north', 'east', 'south', 'west']


class Loc:
    def __init__(self, plan, x0, z0, q, LW, LD):
        self.plan, self.q, self.LW, self.LD = plan, q, LW, LD
        ea, eb = _EA[q], _EB[q]
        self.ea, self.eb = ea, eb
        xs = [a * ea[0] + b * eb[0] for a, b in ((0, 0), (LW - 1, LD - 1))]
        zs = [a * ea[1] + b * eb[1] for a, b in ((0, 0), (LW - 1, LD - 1))]
        self.ox, self.oz = x0 - min(xs), z0 - min(zs)

    def xz(self, a, b):
        ea, eb = self.ea, self.eb
        return self.ox + a * ea[0] + b * eb[0], self.oz + a * ea[1] + b * eb[1]

    def rot(self, blk):
        return B.rotate_state(blk, self.q) if (self.q and '[' in blk) else blk

    def set(self, a, y, b, blk):
        x, z = self.xz(a, b)
        self.plan.set(x, y, z, self.rot(blk))

    def get(self, a, y, b):
        x, z = self.xz(a, b)
        return self.plan.get(x, y, z)

    def pop(self, a, y, b):
        x, z = self.xz(a, b)
        self.plan.pop(x, y, z)

    def has(self, a, y, b):
        return self.get(a, y, b) is not None


def world_dims(q, LW, LD):
    return (LW, LD) if q % 2 == 0 else (LD, LW)


# ------------------------------------------------------------------------------------------------------------
# the builder
# ------------------------------------------------------------------------------------------------------------
def F(m):
    return DECK + PITCH * m


PILLAR = 'quartz_pillar[axis=y]'
FLOWERS = ['poppy', 'blue_orchid', 'allium', 'azure_bluet', 'oxeye_daisy', 'cornflower', 'lily_of_the_valley']
BEDS = ['white_bed', 'light_blue_bed', 'cyan_bed', 'light_gray_bed']
POTS = ['potted_azalea_bush', 'potted_fern', 'potted_blue_orchid', 'potted_allium', 'potted_cornflower']


def leaves(kind='oak_leaves'):
    return f'{kind}[persistent=true]'


class Builder:
    def __init__(self, lay, lot, plan, loc, rng):
        self.L, self.lot, self.plan, self.loc, self.rng = lay, lot, plan, loc, rng
        self.spec = lay.spec
        self.n = lay.n
        self.res = [set() for _ in range(lay.n + 1)]       # reserved cells per floor
        self.occ = [set() for _ in range(lay.n + 1)]
        self.open_cells = [set() for _ in range(lay.n + 1)]  # wall cells with an opening (no furniture against)
        self.door_cells = set()
        self.path = set()
        self.stairs_cells = set()

    # ---- shortcuts
    def put(self, a, y, b, blk):
        self.loc.set(a, y, b, blk)

    def fillcol(self, a, y0, y1, b, blk):
        for y in range(y0, y1 + 1): self.loc.set(a, y, b, blk)

    # ---- main
    def build(self):
        L, n, spec = self.L, self.n, self.spec
        self.ground()
        self.slabs()
        self.walls()
        self.doors()
        self.stair_core()
        self.balconies()
        self.terraces()
        for f in range(n): self.furnish(f)
        self.interior_lights()
        self.crown()
        self.decor_yard()

    # ---- ground (lane, yard) --------------------------------------------------------------------------
    def ground(self):
        L, spec, rng = self.L, self.spec, self.rng
        M0 = L.M[0]
        fam = L.district
        lane_a = ('smooth_stone', 'polished_andesite')
        for a in range(L.LW):
            for b in range(L.LD):
                if (a, b) in M0: continue
                lane = a < L.ms or a >= L.LW - L.ms or b >= L.LD - LANE
                if lane:
                    blk = lane_a[((a // 2) + (b // 2)) & 1]
                    if (a + b) % 6 == 0 and a % 2 == 0: blk = 'light_gray_concrete'
                    self.put(a, DECK, b, blk)

    # ---- slabs --------------------------------------------------------------------------------------------
    def hole(self, m):
        """Cells of slab m that are open for the flight arriving from floor m-1."""
        if m < 1: return set()
        a0, b0 = self.L.core
        if (m - 1) % 2 == 0: return {(a0 + s, b0 + k) for s in (2, 3, 4) for k in (0, 1)}
        return {(a0 + 5 - s, b0 + 2 + k) for s in (2, 3, 4) for k in (0, 1)}

    def floor_block(self, a, b, m, interior=True):
        fam = self.L.district
        if fam == 'C3':
            return 'smooth_stone' if ((a + b) % 4 < 2) else 'polished_andesite'
        if fam == 'C2':
            if (a % 6 == 0 or b % 6 == 0) and interior: return P.LIGHT_BLUE
            return 'smooth_quartz' if ((a // 3 + b // 3) & 1) else 'polished_diorite'
        return 'polished_diorite' if ((a // 2 + b // 2) & 1) else 'smooth_quartz'

    def slabs(self):
        L, n = self.L, self.n
        for m in range(0, n + 1):
            S = L.M[m - 1] if m >= 1 else L.M[0]
            Mm = L.M[m] if m < n else set()
            hole = self.hole(m)
            y = F(m)
            for (a, b) in S:
                if (a, b) in hole: continue
                if m == 0 or (a, b) in Mm:
                    self.put(a, y, b, self.floor_block(a, b, m))
                else:
                    self.put(a, y, b, 'polished_diorite' if (a + b) % 2 else 'smooth_quartz')

    # ---- walls -------------------------------------------------------------------------------------------
    def wall_blocks(self, f, c, N):
        """4 blocks (rows 0..3) of the wall cell c on floor f, or None entries for air."""
        L, spec = self.L, self.spec
        a, b = c
        hasA = any(d[0] != 0 for d in N); hasB = any(d[1] != 0 for d in N)
        wall, accent, glass = spec['wall'], spec['accent'], spec['glass']
        if L.units:
            for (u0, u1, uw, ua) in L.units:
                if u0 <= a < u1:
                    wall, accent = uw, ua
        if any((a + d[0], b + d[1]) in L.court for d in N): kind = 'court'
        elif (0, -1) in N: kind = 'front'
        elif (0, 1) in N: kind = 'back'
        else: kind = 'side'
        sty = spec[kind]
        if f == 0 and spec['ground']: sty = spec['ground'][kind]
        pos = a + b
        if hasA and hasB:
            return [PILLAR] * 4
        unit_edge = L.units and kind in ('front', 'back') and any(a == u[0] or a == u[1] - 1 for u in L.units)
        if unit_edge: return [PILLAR] * 4
        if sty == 'solid': return [wall, wall, wall, accent]
        if sty == 'ribbon':
            if pos % 4 == 0: return [PILLAR] * 4
            return [wall, glass, glass, accent]
        if sty == 'curtain':
            if pos % 4 == 0: return [PILLAR] * 4
            return [glass, glass, glass, accent]
        if sty == 'punched':
            if pos % 3 == 1: return [wall, glass, glass, wall]
            return [wall, wall, wall, accent if pos % 6 == 0 else wall]
        if sty == 'arcade':
            if pos % 4 == 0: return [PILLAR] * 4
            return [None, None, None, wall]
        if sty == 'colonnade':
            if pos % 3 == 0: return [PILLAR] * 4
            return [None, None, None, wall]
        return [wall] * 4

    def walls(self):
        L, n = self.L, self.n
        self.wallcells = []
        for f in range(n):
            M = L.M[f]; I = L.I[f]
            y0 = F(f) + 1
            wc = set()
            for c in M:
                if c in I: continue
                N = [(d[0], d[1]) for d in DIRS4 if (c[0] + d[0], c[1] + d[1]) not in M]
                blks = self.wall_blocks(f, c, N)
                wc.add(c)
                for r in range(4):
                    if blks[r] is not None: self.put(c[0], y0 + r, c[1], blks[r])
                if blks[1] is None: self.open_cells[f].add(c)
            self.wallcells.append(wc)

    # ---- doors ---------------------------------------------------------------------------------------------
    def doors(self):
        L = self.L
        y0 = WALK
        for (d0, d1) in L.doors:
            for (a, b) in (d0, d1):
                for r in range(3): self.loc.pop(a, y0 + r, b)
                self.put(a, y0 + 3, b, self.spec['accent'])
                self.door_cells.add((a, b))
            a, b = d0
            # frame and canopy
            for dd in (-1, 2):
                self.fillcol(a + dd, y0, y0 + 3, b, PILLAR)
            # canopy slab over the doorstep with lanterns
            for dx in range(-1, 3):
                self.put(a + dx, y0 + 3, b - 1, B.slab(P.WHITE, 'bottom'))
            self.put(a, y0 + 2, b - 1, 'lantern[hanging=true]')
            self.put(a + 1, y0 + 2, b - 1, 'lantern[hanging=true]')
            # path
            for bb in range(b - 1, -1, -1):
                for aa in range(a - 1, a + 3):
                    self.path.add((aa, bb))
            for dd in range(1, 4):
                for dx in range(-1, 3): self.res[0].add((a + dx, b + dd))
            self.res[0] |= {(a - 1, b + 1)}

    # ---- stair core -----------------------------------------------------------------------------------------
    def stair_core(self):
        L, n = self.L, self.n
        a0, b0 = L.core
        wall = self.spec['wall']
        for f in range(n):
            y = F(f)
            if f % 2 == 0:
                steps = [((a0 + s, b0 + k), s) for s in range(5) for k in (0, 1)]
                face = 'east'
            else:
                steps = [((a0 + 5 - s, b0 + 2 + k), s) for s in range(5) for k in (0, 1)]
                face = 'west'
            for ((a, b), s) in steps:
                for yy in range(y + 1, y + 1 + s): self.put(a, yy, b, 'quartz_block')
                self.put(a, y + 1 + s, b, B.stairs('smooth_quartz', face))
                self.stairs_cells.add((a, b))
        ring = rect(a0 - 1, b0 - 1, a0 + 7, b0 + 5)
        for m in range(n + 1): self.res[m] |= ring

    # ---- balconies ------------------------------------------------------------------------------------------
    def balconies(self):
        L = self.L
        if not L.balcony: return
        M0 = L.M[0]
        bf = min(b for (a, b) in M0)
        for f in L.balcony['floors']:
            if f >= self.n: continue
            M = L.M[f]
            y = F(f)
            for i, (a0, w) in enumerate(L.balcony['bays']):
                if (i + f) % 2: continue
                if not all((a, bf) in M and (a, bf - 1) not in M for a in range(a0, a0 + w)): continue
                for a in range(a0, a0 + w):
                    for bb in (bf - 1, bf - 2):
                        self.put(a, y, bb, 'smooth_quartz')
                for a in range(a0, a0 + w): self.put(a, y + 1, bf - 2, P.GLASS_BLUE_PANE)
                for bb in (bf - 1,):
                    self.put(a0, y + 1, bb, P.GLASS_BLUE_PANE); self.put(a0 + w - 1, y + 1, bb, P.GLASS_BLUE_PANE)
                self.put(a0 + 2, y, bf - 2, P.LAMP)
                for a in range(a0 + 1, a0 + w - 1):
                    for r in range(3): self.loc.pop(a, y + 1 + r, bf)
                    self.open_cells[f].add((a, bf))
                    self.res[f].add((a, bf + 1)); self.res[f].add((a, bf + 2))
                # soft lights
                self.put(a0 + 1, y + 2, bf - 1, LIGHT)

    # ---- terraces and roofs ---------------------------------------------------------------------------------
    def terraces(self):
        L, n, rng = self.L, self.n, self.rng
        a0, b0 = L.core
        coreset = rect(a0 - 2, b0 - 2, a0 + 8, b0 + 6)
        for m in range(1, n + 1):
            S = L.M[m - 1]
            Mm = L.M[m] if m < n else set()
            O = S - Mm
            if not O: continue
            y = F(m)
            E = {c for c in O if any(nb not in S for nb in nb4(c))}
            for c in E: self.put(c[0], y + 1, c[1], P.GLASS_BLUE_PANE)
            walk = O - E
            if not walk: continue
            if m == n:
                self.roof_garden(m, S, E, walk, coreset)
                continue
            # distance from the wall of M_m
            dist = {}
            fr = []
            for c in walk:
                if any(nb in Mm for nb in nb4(c)): dist[c] = 1; fr.append(c)
            k = 1
            while fr:
                nxt = []
                for c in fr:
                    for nb in nb4(c):
                        if nb in walk and nb not in dist: dist[nb] = k + 1; nxt.append(nb)
                fr = nxt; k += 1
            mx = max(dist.values()) if dist else 1
            wd = 1 if mx < 5 else 2
            way = {c for c in walk if dist.get(c, 99) <= wd}
            # planters beyond the walkway
            for c in walk - way:
                self.planter(c, y, m)
            # components of the walkway -> one arch each
            seen = set()
            for c in sorted(way):
                if c in seen: continue
                comp = []; st = [c]; seen.add(c)
                while st:
                    u = st.pop(); comp.append(u)
                    for nb in nb4(u):
                        if nb in way and nb not in seen: seen.add(nb); st.append(nb)
                self.arch_for(m, comp, Mm)
            # glowing inlays in the walkway paving (flush sea lanterns), hidden lights above the planters
            for c in sorted(walk):
                if (c[0] + 2 * c[1]) % 6 == 0:
                    if c in way: self.put(c[0], y, c[1], P.LAMP)
                    else: self.put(c[0], y + 2, c[1], LIGHT)

    def planter(self, c, y, m):
        a, b = c
        h = (a * 7 + b * 13 + m) % 5
        self.put(a, y, b, 'moss_block')
        if h == 0: self.put(a, y + 1, b, 'flowering_azalea')
        elif h == 1: self.put(a, y + 1, b, 'azalea')
        elif h == 2: self.put(a, y + 1, b, 'short_grass')
        elif h == 3: self.put(a, y + 1, b, leaves('azalea_leaves'))
        else: self.put(a, y + 1, b, leaves('flowering_azalea_leaves'))

    def arch_for(self, m, comp, Mm):
        L = self.L
        I = L.I[m]
        cs = set(comp)
        cx = sum(c[0] for c in comp) / float(len(comp)); cb = sum(c[1] for c in comp) / float(len(comp))
        best = None
        for u in comp:
            for d in DIRS4:
                w = (u[0] + d[0], u[1] + d[1])
                if w not in Mm or w in I: continue
                inner = (w[0] + d[0], w[1] + d[1])
                if inner not in I: continue
                sc = (w[0] - cx) ** 2 + (w[1] - cb) ** 2
                if best is None or sc < best[0]: best = (sc, w, d, u)
        if best is None:
            return
        _, w, d, u = best
        y = F(m)
        ws = [w]
        p = (d[1], d[0])           # perpendicular
        w2 = (w[0] + p[0], w[1] + p[1])
        u2 = (w2[0] - d[0], w2[1] - d[1])
        if w2 in Mm and w2 not in I and u2 in cs and (w2[0] + d[0], w2[1] + d[1]) in I: ws.append(w2)
        for (a, b) in ws:
            for r in range(3): self.loc.pop(a, y + 1 + r, b)
            self.open_cells[m].add((a, b))
            for k in (1, 2):
                self.res[m].add((a + d[0] * k, b + d[1] * k))

    def roof_garden(self, m, S, E, walk, coreset):
        L = self.L
        y = F(m)
        rng = self.rng
        off = rng.randrange(2)
        hole = self.hole(m)
        for c in sorted(walk):
            a, b = c
            if c in hole: continue
            lamp = (a + 2 * b) % 6 == 0
            if c in coreset:
                if lamp: self.put(a, y, b, P.LAMP)
                continue
            patch = ((a // 4) + (b // 4) + off) & 1
            if lamp:
                self.put(a, y, b, P.LAMP)
                continue
            if patch:
                self.put(a, y, b, 'moss_block')
                h = (a * 5 + b * 3) % 7
                if (a % 4, b % 4) in ((1, 1), (2, 2)):
                    self.put(a, y + 1, b, ('flowering_azalea', 'azalea', 'flowering_azalea_leaves[persistent=true]')[h % 3])
                else:
                    self.put(a, y + 1, b, 'moss_carpet')

    def crown(self):
        L = self.L
        if not self.spec['crown']: return
        n = self.n
        y = F(n)
        top = L.M[n - 1]
        a0, b0 = L.core
        core = rect(a0 - 2, b0 - 2, a0 + 8, b0 + 6)
        cells = [c for c in top if c not in L.I[n - 1] or True]
        inner = [c for c in L.I[n - 1] if c not in core and any(nb not in L.I[n - 1] for nb in nb4(c))]
        if not inner: return
        picks = [min(inner, key=lambda c: c[0] + c[1]), max(inner, key=lambda c: c[0] + c[1]),
                 min(inner, key=lambda c: c[0] - c[1]), max(inner, key=lambda c: c[0] - c[1])]
        for (a, b) in set(picks):
            for k in range(1, 3): self.put(a, y + k, b, PILLAR)
            self.put(a, y + 3, b, 'end_rod[facing=up]')
            self.put(a, y, b, 'smooth_quartz')

    # ---- furniture -----------------------------------------------------------------------------------------
    def free(self, f, c):
        return c in self.L.I[f] and c not in self.occ[f] and c not in self.res[f] and c not in self.stairs_cells

    def wall_dirs(self, f, c):
        out = []
        for d in DIRS4:
            w = (c[0] + d[0], c[1] + d[1])
            if w in self.wallcells[f] and w not in self.open_cells[f] and w not in self.door_cells: out.append(d)
        return out

    def slots(self, f):
        out = []
        for c in sorted(self.L.I[f]):
            if not self.free(f, c): continue
            for d in self.wall_dirs(f, c): out.append((c, d))
        self.rng.shuffle(out)
        return out

    def row(self, f, nn, depth=1):
        for (c, d) in self.slots(f):
            p = (d[1], d[0])
            cells = [(c[0] + k * p[0], c[1] + k * p[1]) for k in range(nn)]
            ok = True
            for cc in cells:
                if not self.free(f, cc) or d not in self.wall_dirs(f, cc): ok = False; break
            if not ok: continue
            deep = [(cc[0] - t * d[0], cc[1] - t * d[1]) for cc in cells for t in range(1, depth)]
            if not all(self.free(f, x) for x in deep): continue
            return cells, d
        return None

    def mark(self, f, cells):
        self.occ[f] |= set(cells)

    def furnish(self, f):
        L, rng = self.L, self.rng
        y = F(f) + 1
        I = L.I[f]
        area = len(I)
        pot = lambda: _pick(rng, POTS)
        facing = lambda d: DNAME[(-d[0], -d[1])]      # towards the room

        def kitchen():
            r = self.row(f, 4)
            if not r: return
            cells, d = r
            seq = ['blast_furnace[facing=%s,lit=false]' % facing(d), 'quartz_block', 'cauldron',
                   'smoker[facing=%s,lit=false]' % facing(d)]
            for c, blk in zip(cells, seq): self.put(c[0], y, c[1], blk)
            self.put(cells[1][0], y + 1, cells[1][1], 'potted_fern')
            self.mark(f, cells)
            self.mark(f, [(c[0] - d[0] * -1, c[1] - d[1] * -1) for c in cells])

        def sofa():
            r = self.row(f, 3, depth=4)
            if not r: return
            cells, d = r
            for c in cells: self.put(c[0], y, c[1], B.stairs('smooth_quartz', DNAME[d]))
            mid = cells[1]
            for t in (1, 2, 3):
                for k in (-1, 0, 1):
                    cc = (mid[0] - t * d[0] + k * d[1], mid[1] - t * d[1] + k * d[0])
                    if self.free(f, cc):
                        self.put(cc[0], y, cc[1], 'light_blue_carpet')
            t = (mid[0] - 2 * d[0], mid[1] - 2 * d[1])
            self.put(t[0], y, t[1], B.slab('smooth_quartz', 'bottom'))
            self.put(t[0], y + 1, t[1], 'potted_blue_orchid')
            self.mark(f, cells + [(mid[0] - k * d[0], mid[1] - k * d[1]) for k in (1, 2, 3)])

        def shelf():
            r = self.row(f, 2)
            if not r: return
            cells, d = r
            for c in cells:
                self.put(c[0], y, c[1], 'bookshelf'); self.put(c[0], y + 1, c[1], 'bookshelf')
            self.mark(f, cells)

        def plant():
            r = self.row(f, 1)
            if not r: return
            c = r[0][0]
            self.put(c[0], y, c[1], pot())
            self.mark(f, [c])

        def bed():
            for (c, d) in self.slots(f):
                foot = (c[0] - d[0], c[1] - d[1])
                if not self.free(f, foot): continue
                col = _pick(rng, BEDS)
                self.put(c[0], y, c[1], f'{col}[facing={DNAME[d]},part=head]')
                self.put(foot[0], y, foot[1], f'{col}[facing={DNAME[d]},part=foot]')
                side = (c[0] + d[1], c[1] + d[0])
                self.mark(f, [c, foot])
                if self.free(f, side) and d in self.wall_dirs(f, side):
                    self.put(side[0], y, side[1], 'barrel[facing=up,open=false]')
                    self.put(side[0], y + 1, side[1], 'lantern[hanging=false]')
                    self.mark(f, [side])
                return

        def wardrobe():
            r = self.row(f, 2)
            if not r: return
            cells, d = r
            for c in cells:
                self.put(c[0], y, c[1], 'barrel[facing=up,open=false]')
            self.mark(f, cells)

        def desk():
            r = self.row(f, 2, depth=2)
            if not r: return
            cells, d = r
            for c in cells: self.put(c[0], y, c[1], B.slab('smooth_quartz', 'top'))
            ch = (cells[0][0] - d[0], cells[0][1] - d[1])
            self.put(ch[0], y, ch[1], B.stairs('smooth_quartz', facing(d)))
            self.put(cells[1][0], y + 1, cells[1][1], 'lantern[hanging=false]')
            self.mark(f, cells + [ch])

        def dining():
            for c in sorted(self.L.I[f]):
                cells = [c, (c[0] + 1, c[1]), (c[0], c[1] + 1), (c[0] + 1, c[1] + 1)]
                ring = rect(c[0] - 1, c[1] - 1, c[0] + 3, c[1] + 3)
                if not all(self.free(f, x) for x in cells): continue
                if sum(1 for x in ring if self.free(f, x)) < 12: continue
                if rng.random() < 0.6: continue
                for x in cells: self.put(x[0], y, x[1], B.slab('smooth_quartz', 'bottom'))
                self.put(c[0], y + 1, c[1], 'potted_allium')
                for (ch, fc) in (((c[0] - 1, c[1]), 'east'), ((c[0] + 2, c[1] + 1), 'west'),
                                 ((c[0], c[1] - 1), 'south'), ((c[0] + 1, c[1] + 2), 'north')):
                    if self.free(f, ch):
                        self.put(ch[0], y, ch[1], B.stairs('smooth_quartz', B.opposite(fc)))
                        self.mark(f, [ch])
                self.mark(f, cells)
                return

        if area < 20: return
        if f == 0:
            order = [kitchen, sofa, dining, shelf, plant, plant, desk, plant]
        elif f % 2 == 1:
            order = [bed, wardrobe, bed, desk, plant, shelf, plant]
        else:
            order = [sofa, shelf, bed, dining, plant, plant, wardrobe]
        k = max(4, min(len(order), area // 14))
        for fn in order[:k]: fn()

    def interior_lights(self):
        L = self.L
        for f in range(self.n):
            y = F(f) + 4
            for c in L.I[f]:
                if (c[0] + 2 * c[1]) % 6 == 0 and not self.loc.has(c[0], y, c[1]):
                    self.put(c[0], y, c[1], LIGHT)
            # walls: also cover wall-side gaps with a lower row at the stair core
        a0, b0 = L.core

    # ---- yard ----------------------------------------------------------------------------------------------
    def tree(self, a, b, h, log, leaf):
        y0 = WALK
        for k in range(h): self.put(a, y0 + k, b, log)
        top = y0 + h - 1
        for dy, r in ((-1, 2), (0, 2), (1, 1), (2, 1)):
            for da in range(-r, r + 1):
                for db in range(-r, r + 1):
                    if abs(da) == r and abs(db) == r and r == 2: continue
                    if (da, db) == (0, 0) and dy <= 0: continue
                    y = top + dy
                    if not self.loc.has(a + da, y, b + db): self.put(a + da, y, b + db, leaf)
        self.put(a, top + 3, b, leaf)

    def decor_yard(self):
        L, spec, rng = self.L, self.spec, self.rng
        M0 = L.M[0]
        fam = L.district
        ms = L.ms
        apron = set()
        for (a, b) in M0:
            for da in (-1, 0, 1):
                for db in (-1, 0, 1): apron.add((a + da, b + db))
        ground = []
        for a in range(L.LW):
            for b in range(L.LD):
                if (a, b) in M0: continue
                lane = a < ms or a >= L.LW - ms or b >= L.LD - LANE
                if not lane: ground.append((a, b))
        court = L.court
        for (a, b) in ground:
            incourt = (a, b) in court
            if fam == 'C3':
                blk = 'grass_block'
                if (a, b) in self.path or (a, b) in apron and not incourt: blk = 'polished_andesite' if (a + b) % 2 else 'smooth_stone'
            elif fam == 'C2':
                blk = 'polished_diorite' if ((a // 3 + b // 3) & 1) else 'smooth_quartz'
                if (a % 6 == 0 and b % 6 == 0) and not incourt and (a, b) not in self.path: blk = P.LAMP
            else:
                blk = 'polished_diorite' if ((a // 2 + b // 2) & 1) else 'smooth_quartz'
                if (a, b) in self.path: blk = 'smooth_quartz'
            self.put(a, DECK, b, blk)
        free_ground = [c for c in ground if c not in self.path and c not in apron]
        fg = set(free_ground)
        # ---- decoration by family
        if fam == 'C3':
            for (a, b) in free_ground:
                h = (a * 31 + b * 17) % 23
                if h < 4: self.put(a, WALK, b, FLOWERS[(a + b) % len(FLOWERS)])
                elif h == 4: self.put(a, WALK, b, 'short_grass')
            # hedges: short runs along the yard borders
            for (a, b) in free_ground:
                edge = (b == L.LD - LANE - 1 or b == 0 or a == ms or a == L.LW - ms - 1)
                if edge and (a + b) % 7 not in (0, 1) and (a, b) not in self.path:
                    self.put(a, WALK, b, leaves('azalea_leaves'))
                    if (a * b) % 3: self.put(a, WALK + 1, b, leaves('azalea_leaves'))
            ntrees = 3 + (len(free_ground) > 200) + (len(free_ground) > 400)
            tries = 0
            placed = 0
            cand = [c for c in free_ground if c[0] % 3 == 1 and c[1] % 3 == 1]
            rng.shuffle(cand)
            kinds = [('stripped_cherry_log', 'cherry_leaves[persistent=true]'),
                     ('stripped_birch_log', 'birch_leaves[persistent=true]'),
                     ('oak_log[axis=y]', 'oak_leaves[persistent=true]')]
            for (a, b) in cand:
                if placed >= ntrees + 2: break
                if min(abs(a - x[0]) + abs(b - x[1]) for x in ([(-99, -99)] + getattr(self, 'trees', []))) < 7: continue
                if (a, b) in court and not self.court_ok(a, b): continue
                if not (3 <= a < L.LW - 3 and 3 <= b < L.LD - 3): continue
                if any((a + da, b + db) in apron for da in range(-2, 3) for db in range(-2, 3)): continue
                lg, lf = kinds[(a + b) % 3]
                self.trees = getattr(self, 'trees', []) + [(a, b)]
                self.tree(a, b, 3 + (a % 2), lg, lf)
                placed += 1
            if spec.get('pergola'): self.pergola()
        elif fam == 'C2':
            for (a, b) in free_ground:
                if (a % 6 == 3 and b % 6 == 3):
                    self.put(a, DECK, b, 'quartz_block')
                    self.put(a, WALK, b, 'cherry_log[axis=y]')
                    self.put(a, WALK + 1, b, 'cherry_log[axis=y]')
                    for da in (-1, 0, 1):
                        for db in (-1, 0, 1):
                            if (a + da, b + db) in fg and (da or db) and abs(da) + abs(db) < 2 and \
                                    not self.loc.has(a + da, WALK + 2, b + db):
                                self.put(a + da, WALK + 2, b + db, 'cherry_leaves[persistent=true]')
                    self.put(a, WALK + 2, b, 'cherry_leaves[persistent=true]')
                    self.put(a, WALK + 3, b, 'cherry_leaves[persistent=true]')
                elif b == 1 and a % 6 in (0, 1, 5) and (a, b) not in self.path and ms <= a < L.LW - ms:
                    self.put(a, WALK, b, 'quartz_slab[type=bottom]' if a % 6 else 'quartz_block')
                elif (a % 6 == 0 and b % 6 == 0) and (a, b) not in court:
                    pass
            if spec.get('yard2') == 'court' and court:
                self.court_garden(court, M0)
        else:   # C1 terraces: planters, small trees
            for (a, b) in free_ground:
                if (b == 1 and a % 4 in (1, 2)) or (b == L.LD - LANE - 1 and a % 3 == 1):
                    self.put(a, DECK, b, 'moss_block')
                    self.put(a, WALK, b, ('azalea', 'flowering_azalea', leaves('azalea_leaves'))[(a + b) % 3])
                elif a % 7 == 3 and b % 7 == 3:
                    self.put(a, DECK, b, 'moss_block')
                    self.put(a, WALK, b, 'cherry_log[axis=y]'); self.put(a, WALK + 1, b, 'cherry_log[axis=y]')
                    for da in (-1, 0, 1):
                        for db in (-1, 0, 1):
                            if abs(da) + abs(db) < 2 and (a + da, b + db) in fg and not self.loc.has(a + da, WALK + 2, b + db):
                                self.put(a + da, WALK + 2, b + db, 'cherry_leaves[persistent=true]')
                    self.put(a, WALK + 3, b, 'cherry_leaves[persistent=true]')
        # ---- lamp posts in the lanes
        self.lamps()
        # ---- hidden lights over the yards and lanes
        for a in range(L.LW):
            for b in range(L.LD):
                if (a, b) in M0: continue
                if (a + 3 * b) % 7 == 0 and not self.loc.has(a, WALK + 1, b):
                    self.put(a, WALK + 1, b, LIGHT)
        # court gardens of the other families
        if fam == 'C3' and court: self.court_garden(court, M0)

    def court_ok(self, a, b):
        return all((a + da, b + db) in self.L.court for da in (-2, -1, 0, 1, 2) for db in (-2, -1, 0, 1, 2))

    def court_garden(self, court, M0):
        for (a, b) in court:
            if any(nb in M0 for nb in nb4((a, b))): continue
            if (a, b) in self.path: continue
            if (a + b) % 5 == 0 and not self.loc.has(a, WALK, b):
                self.put(a, DECK, b, 'moss_block')
                self.put(a, WALK, b, _pick(self.rng, ['flowering_azalea', 'azalea', 'fern']))
        for (a, b) in court:
            if (a + 3 * b) % 5 == 0 and not self.loc.has(a, WALK + 1, b): self.put(a, WALK + 1, b, LIGHT)

    def pergola(self):
        L = self.L
        if not L.doors: return
        (d0, d1) = L.doors[0]
        a, b = d0
        for bb in range(b - 2, 0, -3):
            for aa in (a - 1, a + 2):
                for k in range(3): self.put(aa, WALK + k, bb, B.wall(P.DIORITE_WALL, up=True))
            for aa in range(a - 1, a + 3):
                for bj in (bb, bb - 1):
                    if bj >= 0:
                        self.put(aa, WALK + 3, bj, B.slab('spruce_slab' if False else 'smooth_quartz', 'bottom'))
                        if (aa + bj) % 2: self.put(aa, WALK + 4, bj, leaves('azalea_leaves'))

    def lamps(self):
        L = self.L
        ms = L.ms
        pts = []
        for b in range(3, L.LD - 1, 7):
            if ms: pts.append((1, b)); pts.append((L.LW - 2, b))
        for a in range(3, L.LW - 1, 7):
            pts.append((a, L.LD - 2))
        pts.append((ms + 1, 0)); pts.append((L.LW - ms - 2, 0))
        M0 = L.M[0]
        for (a, b) in pts:
            if (a, b) in M0 or (a, b) in self.path: continue
            if self.loc.has(a, WALK, b): continue
            self.put(a, WALK, b, B.wall(P.DIORITE_WALL, up=True))
            self.put(a, WALK + 1, b, B.wall(P.DIORITE_WALL, up=True))
            self.put(a, WALK + 2, b, 'lantern[hanging=false]')


# ------------------------------------------------------------------------------------------------------------
# lot planning
# ------------------------------------------------------------------------------------------------------------
def _axis_frame(plan):
    ux, uz = PL.from_polar(1.0, plan)
    return ux, uz, -uz, ux


def _h_target(r):
    return 45.0 - (r - 310.0) * 30.0 / 180.0


NOMINAL = {'terrace_cascade': (28, 24), 'terrace_crescent': (38, 29), 'ziggurat_stack': (22, 22),
           'twin_stagger': (30, 26), 'l_terrace': (30, 29), 'balcony_slab': (28, 20), 'slender_tower': (19, 21),
           'round_tower': (23, 25), 'townhouse_row': (30, 21), 'arcade_horseshoe': (32, 28),
           'podium_tower': (30, 25), 'bow_mansion': (26, 27), 'villa_flat': (23, 22), 'pavilion_colonnade': (20, 22),
           'courtyard_villa': (30, 30), 'drum_pavilion': (24, 28), 'cottage_pergola': (18, 23),
           'split_level': (32, 30)}


def _rect_at(face, px, pz, wx, wz):
    if face == 'north': X1, Z1 = int(round(px - wx / 2.0)), int(round(pz))
    elif face == 'south': X1, Z1 = int(round(px - wx / 2.0)), int(round(pz)) - wz + 1
    elif face == 'east': X1, Z1 = int(round(px)) - wx + 1, int(round(pz - wz / 2.0))
    else: X1, Z1 = int(round(px)), int(round(pz - wz / 2.0))
    return (X1, Z1, X1 + wx - 1, Z1 + wz - 1)


def _fast_ok(R, dist, ang, placed, others, props):
    x1, z1, x2, z2 = R
    ux, uz, vx, vz = _axis_frame(ang)
    for (x, z) in ((x1, z1), (x2, z1), (x1, z2), (x2, z2)):
        r = math.hypot(x, z)
        if not (R_MIN <= r <= R_MAX): return False
        s = x * ux + z * uz; c = x * vx + z * vz
        if abs(c) < 8.8 or abs(c) > PL.pier_half_width(s) - 2.0: return False
    for (a1, b1, a2, b2) in placed:
        if not (x2 < a1 or a2 < x1 or z2 < b1 or b2 < z1): return False
    for (a1, b1, a2, b2) in others:
        if not (x2 + 3 < a1 or a2 + 3 < x1 or z2 + 3 < b1 or b2 + 3 < z1): return False
    return True


_PIERS = (('C1', 120), ('C2', 180), ('C3', 300))
_CAPS = {}


def cap_at(x, z):
    v = _CAPS.get((x, z))
    if v is None:
        v = _CAPS[(x, z)] = int(_CTX.max_building_top(x, z))
    return v


def _rect_ok(R, dist, plan_ang, others, props, placed):
    x1, z1, x2, z2 = R
    cs = ((x1, z1), (x2, z1), (x1, z2), (x2, z2))
    ux, uz, vx, vz = _axis_frame(plan_ang)
    for (x, z) in cs:
        r = math.hypot(x, z)
        if not (R_MIN <= r <= R_MAX): return False
        s = x * ux + z * uz; c = x * vx + z * vz
        if abs(c) < 8.8 or abs(c) > PL.pier_half_width(s) - 2.0: return False
    for (a1, b1, a2, b2) in placed:
        if not (x2 < a1 or a2 < x1 or z2 < b1 or b2 < z1): return False
    for (a1, b1, a2, b2) in others:
        if not (x2 + 3 < a1 or a2 + 3 < x1 or z2 + 3 < b1 or b2 + 3 < z1): return False
    for (a1, b1, a2, b2) in props:
        if not (x2 < a1 or a2 < x1 or z2 < b1 or b2 < z1): return False
    # border and interior samples
    pts = []
    for x in range(x1, x2 + 1): pts += [(x, z1), (x, z2)]
    for z in range(z1, z2 + 1): pts += [(x1, z), (x2, z)]
    for x in range(x1 + 1, x2, 2):
        for z in range(z1 + 1, z2, 2): pts.append((x, z))
    for (x, z) in pts:
        if PL.district_at(x, z) != dist: return False
        if roads.surface(x, z) is not None: return False
    return True


def _others_and_props():
    others, props = [], []
    for f in FT.registry():
        if f.name.startswith('res_'): continue
        bb = f.bbox
        if f.layer in (40, 50): others.append((bb[0], bb[2], bb[3], bb[5]))
        elif f.layer == 30 and f.name.startswith('roads_c_') and 'plaza' not in f.name:
            if any(f.name.startswith('roads_c_' + k) for k in ('lamp', 'tip', 'pylon', 'bench', 'planter', 'fountain')):
                props.append((bb[0] - 1, bb[2] - 1, bb[3] + 1, bb[5] + 1))
    return others, props


def plan_lots():
    others, props = _others_and_props()
    lots = []
    for (dist, ang) in _PIERS:
        placed = []
        ux, uz, vx, vz = _axis_frame(ang)
        names = FAMILY[dist]
        small = sorted(names, key=lambda nm: NOMINAL[nm][0] * NOMINAL[nm][1])
        counter = 0
        ids = 0
        for sweep in (0, 1, 2):
            streak = {}
            st = 2 if sweep < 2 else 1
            for cf in range(9, 80, st):
                for s in range(306, 492, st):
                    for sg in (1, -1):
                        if sweep == 0:
                            order = [names[counter % len(names)]]
                        else:
                            used = {nm: sum(1 for l in lots if l['design'] == nm and l['district'] == dist)
                                    for nm in names}
                            order = sorted(names, key=lambda nm: (used[nm], NOMINAL[nm][0] * NOMINAL[nm][1]))
                        got = False
                        for name in order:
                            seed = hash_str('res', dist, cf, s, sg, name)
                            res = _try_lot(dist, ang, name, seed, cf, s, sg, placed, others, props, ux, uz, vx, vz)
                            if res is not None:
                                counter += 1
                                ids += 1
                                res['id'] = ids
                                placed.append(res['rect'])
                                lots.append(res)
                                got = True
                                break
                        if sweep == 0:
                            k = (cf, sg)
                            if got: streak[k] = 0
                            else:
                                streak[k] = streak.get(k, 0) + 1
                                if streak[k] > 70: counter += 1; streak[k] = 0
    return lots


def _try_lot(dist, ang, name, seed, cf, s, sg, placed, others, props, ux, uz, vx, vz):
    _, nmin, nmax, fn = DESIGNS[name]
    rng0 = random.Random(seed ^ 0x5bd1)
    cxe = s * ux + sg * (cf + 12) * vx
    cze = s * uz + sg * (cf + 12) * vz
    r0 = math.hypot(cxe, cze)
    if not (R_MIN <= r0 <= R_MAX): return None
    for (a1, b1, a2, b2) in placed:
        if a1 <= cxe <= a2 and b1 <= cze <= b2: return None
    ext = {'slender_tower': 4, 'round_tower': 4, 'podium_tower': 4}.get(name, 0)
    nt = int(round((_h_target(r0) + rng0.randint(-3, 3) - DECK - 1 - ext) / float(PITCH)))
    n = max(nmin, min(nmax, nt))
    px, pz = s * ux + sg * cf * vx, s * uz + sg * cf * vz       # anchor on the avenue-side edge of the lot
    fx, fz = -sg * vx, -sg * vz
    face = B.facing_from_vector(fx, fz)
    q = FACINGS.index(face)
    nw, nd = NOMINAL[name]
    nwx, nwz = world_dims(q, nw, nd)
    dvx, dvz = DVEC[(0, -1)] if False else (0, 0)
    back = {'north': (0, 1), 'south': (0, -1), 'east': (-1, 0), 'west': (1, 0)}[face]
    sh = None
    for k in range(0, 14):
        if _fast_ok(_rect_at(face, px + back[0] * k, pz + back[1] * k, nwx, nwz), dist, ang, placed, others, props):
            sh = k; break
    if sh is None: return None
    px, pz = px + back[0] * sh, pz + back[1] * sh
    for _ in range(3):
        try:
            lay = make_layout(name, seed, n)
        except ValueError:
            return None
        wx, wz = world_dims(q, lay.LW, lay.LD)
        R = _rect_at(face, px, pz, wx, wz)
        if not _rect_ok(R, dist, ang, others, props, placed): return None
        cap = min(cap_at(x, z) for x in (R[0], (R[0] + R[2]) // 2, R[2]) for z in (R[1], (R[1] + R[3]) // 2, R[3]))
        nc = int((cap - DECK - 1 - ext - 2) // PITCH)
        if nc >= n: break
        if nc < nmin: return None
        n = nc
    else:
        return None
    if lay.ymax > cap - 1: return None
    loc = Loc(None, R[0], R[1], q, lay.LW, lay.LD)
    ents = []
    for (d0, d1) in lay.doors:
        x, z = loc.xz(*d0)
        ents.append((x, WALK, z))
    pts = [loc.xz(a, b) for (a, b) in lay.M[0]]
    ibox = (min(p[0] for p in pts), WALK, min(p[1] for p in pts), max(p[0] for p in pts), lay.ytop,
            max(p[1] for p in pts))
    return dict(design=name, district=dist, seed=seed, n=n, rect=R, q=q, entrances=ents, ibox=ibox,
                ytop=lay.ymax, roof_y=F(n) + 2)


# ------------------------------------------------------------------------------------------------------------
# features
# ------------------------------------------------------------------------------------------------------------
_CACHE = {}


def build_lot_plan(lot, ctx=None):
    ctx = ctx or _CTX
    lay = make_layout(lot['design'], lot['seed'], lot['n'])
    R = lot['rect']
    bbox = (R[0], DECK, R[1], R[2], lot['ytop'], R[3])
    plan = Plan(lot['name'], bbox, lot['district'], ctx)
    loc = Loc(plan, R[0], R[1], lot['q'], lay.LW, lay.LD)
    bd = Builder(lay, lot, plan, loc, random.Random(hash_str('resb', lot['seed'])))
    bd.build()
    plan.finish()
    return plan


class ResFeature(Feature):
    layer = LAYER

    def __init__(self, lot):
        R = lot['rect']
        meta = {'kind': 'residential', 'district': lot['district'], 'design': lot['design'],
                'entrances': list(lot['entrances']), 'interior': True, 'interior_box': lot['ibox'],
                'roof_y': lot['roof_y'], 'floors': lot['n']}
        super().__init__(lot['name'], (R[0], DECK, R[1], R[2], lot['ytop'], R[3]), LAYER, meta)
        self.lot = lot

    def plan(self, ctx=None):
        p = _CACHE.get(self.name)
        if p is None:
            p = _CACHE[self.name] = build_lot_plan(self.lot, ctx)
        return p

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


_CACHE_FILE = os.path.join(config.ROOT, 'data', 'residential_lots.json')


def _signature():
    """Hash of this module's source and of the foreign layer 40/50 bboxes in the three residential piers."""
    others, _ = _others_and_props()
    near = sorted(b for b in others if PL.district_at((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) in ('C1', 'C2', 'C3'))
    with open(__file__, 'rb') as fh:
        src = fh.read()
    return hashlib.sha1(src + repr(near).encode()).hexdigest()


def _load_lots():
    sig = _signature()
    try:
        with open(_CACHE_FILE) as fh:
            d = json.load(fh)
        if d.get('sig') == sig:
            lots = d['lots']
            for l in lots:
                l['rect'] = tuple(l['rect']); l['ibox'] = tuple(l['ibox'])
                l['entrances'] = [tuple(e) for e in l['entrances']]
            return lots
    except (OSError, ValueError, KeyError):
        pass
    lots = plan_lots()
    cnt = {}
    for lot in lots:
        d = lot['district'].lower()
        cnt[d] = cnt.get(d, 0) + 1
        lot['name'] = 'res_%s_%02d_%s' % (d, cnt[d], lot['design'])
    try:
        tmp = _CACHE_FILE + '.%d.tmp' % os.getpid()
        with open(tmp, 'w') as fh:
            json.dump({'sig': sig, 'lots': lots}, fh, separators=(',', ':'))
        os.replace(tmp, _CACHE_FILE)
    except OSError:
        pass
    return lots


def _make_all():
    return [ResFeature(lot) for lot in _load_lots()]


FEATURES = _make_all()
register(*FEATURES)


def features():
    return list(FEATURES)
