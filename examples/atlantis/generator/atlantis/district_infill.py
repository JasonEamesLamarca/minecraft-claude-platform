"""Layer 40: district infill.  Ordinary city buildings that fill the empty lots around the landmarks of districts
A (core), B (Fountain), K (Command), D (Science), G (Transport) and E (Power).

LOT PLANNER (deterministic, runs at import)
  * Candidate cells: planner.district_at in the six districts, inside the district's radial band
    (A r 40..125, inner ring B/K r 135..295, piers D/G/E r 305..492), not a road/plaza cell (roads.surface).
  * A lot is a rectangular bbox (building + a 1-block apron ring).  It is accepted only if every cell is a candidate,
    the surroundings (3 cells) still have a deck (no pier edge / lagoon), no street cell lies within 1 block, it keeps
    3 blocks from every layer 40/50 feature already registered (landmarks), 1 block from street props, and 3 blocks
    from the other lots.  Lots are found by a first-fit raster scan per district; the door faces the nearest street.
  * Each lot gets a design (see DESIGNS) that matches the district's character.

BUILDING MODEL
  * A building is drawn into a `Plan` (dict of cells) in a local frame: u runs along the street front (left to
    right as seen from the street), v from the back (0) to the front (D-1).  `Bld` rotates the frame to the lot facing.
  * The deck is y 4, walkers stand at y 5.  Interior clear height Hh (rows 5..4+Hh), ceiling at y 5+Hh.
  * Every enclosed building: lit furnished ground-floor interior (ceiling lamps), door on the front, a ladder on the
    left flank up to the roof (the roof is walkable and lit), flush sea-lantern lamps in the apron ring.
"""
import importlib
import math

from . import config, planner as PL, roads, palette as P, blocks as B, geometry as G, architecture as A
from . import feature as FT
from . import interior_kit as IK
from .feature import Feature, register
from .util import hash_str, rng as _rng

LAYER = 40
DECK = PL.DECK_TOP
WALK = PL.WALK_Y

_CTX = config.load()

# ------------------------------------------------------------------------------------------------ materials
QS = P.WHITE                      # smooth_quartz
QB = P.WHITE_BLOCK
QBR = P.WHITE_BRICKS
QP = 'quartz_pillar[axis=y]'
PG = P.PALE_GREY
LAMP = P.LAMP
GLASS = P.GLASS_BLUE
PANE = P.GLASS_BLUE_PANE
CYAN = P.CYAN
LBLUE = P.LIGHT_BLUE
DARKP = P.DARK_PRISMARINE
GLASS_W = P.GLASS_WHITE
LIGHT = P.HIDDEN_LIGHT
SLAB_B = B.slab(P.WHITE_SLAB, 'bottom')
SLAB_T = B.slab(P.WHITE_SLAB, 'top')

THEMES = {
    'A': dict(base=QBR, wall=QS, pillar=QP, accent=CYAN, floor=(QS, PG), roof=QS),
    'B': dict(base=QBR, wall=QS, pillar=QP, accent=LBLUE, floor=(QS, PG), roof=QS),
    'K': dict(base=DARKP, wall=QS, pillar=QP, accent=CYAN, floor=(QS, 'polished_andesite'), roof=QB),
    'D': dict(base=QB, wall=P.WHITE_CONCRETE, pillar=QP, accent=LBLUE, floor=(P.WHITE_CONCRETE, PG), roof=QS),
    'G': dict(base=P.GREY, wall=QS, pillar=QP, accent=CYAN, floor=(P.GREY, PG), roof=QB),
    'E': dict(base=P.DARK_BRICKS, wall=QBR, pillar=QP, accent=P.COPPER_CUT, floor=(DARKP, P.DARK_TILES), roof=QB),
}

DISTRICT_BAND = {'A': (40, 125), 'B': (135, 295), 'K': (135, 295), 'D': (305, 492), 'G': (305, 492), 'E': (305, 492)}
DISTRICT_ORDER = ('A', 'B', 'K', 'D', 'G', 'E')
GAP = 3                           # empty blocks kept between lots and from landmarks
FACINGS = ('south', 'west', 'north', 'east')
_Q = {'south': 0, 'west': 1, 'north': 2, 'east': 3}


# ------------------------------------------------------------------------------------------------ cap helpers
def bbox_cap(x1, z1, x2, z2):
    pts = set()
    for x in (x1, (x1 + x2) // 2, x2):
        for z in (z1, (z1 + z2) // 2, z2):
            pts.add((x, z))
    return int(min(_CTX.max_building_top(x, z) for x, z in pts))


# ------------------------------------------------------------------------------------------------ Plan
class Plan:
    """Cell store with hard limits: nothing outside the bbox or above the per-column height cap is stored."""

    def __init__(self, name, bbox, district, ctx):
        self.name, self.bbox, self.district, self.ctx = name, bbox, district, ctx
        self.c = {}
        self.cols = {}
        self.rejected = 0
        self.first_rejects = []
        self._cap = {}

    def cap(self, x, z):
        v = self._cap.get((x, z))
        if v is None:
            v = self._cap[(x, z)] = int(self.ctx.max_building_top(x, z))
        return v

    def set(self, x, y, z, b):
        if b is None: return
        bb = self.bbox
        if not (bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5]) or y > self.cap(x, z):
            self.rejected += 1
            if len(self.first_rejects) < 8: self.first_rejects.append((x, y, z, b))
            return
        self.c[(x, y, z)] = b

    def get(self, x, y, z):
        return self.c.get((x, y, z))

    def fill(self, x1, y1, z1, x2, y2, z2, b):
        for y in range(min(y1, y2), max(y1, y2) + 1):
            for z in range(min(z1, z2), max(z1, z2) + 1):
                for x in range(min(x1, x2), max(x1, x2) + 1): self.set(x, y, z, b)

    def intersects(self, *a):
        return True

    def finish(self):
        c = self.c
        todo = [(k, b) for k, b in c.items() if 'north=' not in b and b.split('[', 1)[0].endswith(('_pane', 'iron_bars'))]
        for (x, y, z), b in todo:
            def nb(d, x=x, y=y, z=z):
                if d == 'up': return c.get((x, y + 1, z))
                dx, dz = B.DIRS[d]
                return c.get((x + dx, y, z + dz))
            c[(x, y, z)] = G.connect_state(b, nb)
        cols = {}
        for (x, y, z), b in sorted(c.items()):
            cols.setdefault((x, z), []).append((y, b))
        self.cols = cols
        return self

    def write(self, vol):
        c = vol.clip(*self.bbox)
        if not c: return
        cols = self.cols
        for x in range(c[0], c[3] + 1):
            for z in range(c[2], c[5] + 1):
                for y, b in cols.get((x, z), ()):
                    if c[1] <= y <= c[4]: vol.set(x, y, z, b)


# ------------------------------------------------------------------------------------------------ masks
def rect_mask(u1, v1, u2, v2):
    return {(u, v) for u in range(u1, u2 + 1) for v in range(v1, v2 + 1)}


def rrect_mask(u1, v1, u2, v2, c):
    out = set()
    for u in range(u1, u2 + 1):
        for v in range(v1, v2 + 1):
            dx, dz = min(u - u1, u2 - u), min(v - v1, v2 - v)
            if dx < c and dz < c and math.hypot(c - 1 - dx, c - 1 - dz) > c - 0.4: continue
            out.add((u, v))
    return out


def ellipse_mask(cu, cv, a, b):
    return {(u, v) for u in range(cu - a, cu + a + 1) for v in range(cv - b, cv + b + 1)
            if ((u - cu) / (a + 0.5)) ** 2 + ((v - cv) / (b + 0.5)) ** 2 <= 1.0}


def erode(mask, k=1):
    for _ in range(k):
        mask = {(u, v) for (u, v) in mask if (u + 1, v) in mask and (u - 1, v) in mask and (u, v + 1) in mask
                and (u, v - 1) in mask}
    return mask


def bounds(mask):
    us = [c[0] for c in mask]; vs = [c[1] for c in mask]
    return min(us), min(vs), max(us), max(vs)


# ------------------------------------------------------------------------------------------------ Bld
class Bld:
    """Drawing frame of one lot: local (u, v) -> world (x, z), states rotated to the lot facing."""

    def __init__(self, plan, bbox, facing, theme, rng):
        self.p, self.facing, self.th, self.rng = plan, facing, theme, rng
        self.q = _Q[facing]
        self.x1, self.z1, self.x2, self.z2 = bbox[0], bbox[2], bbox[3], bbox[5]
        if facing in ('south', 'north'):
            self.W, self.D = self.x2 - self.x1 + 1, self.z2 - self.z1 + 1
        else:
            self.W, self.D = self.z2 - self.z1 + 1, self.x2 - self.x1 + 1
        self._rot = {}

    def xz(self, u, v):
        f = self.facing
        if f == 'south': return self.x1 + u, self.z1 + v
        if f == 'north': return self.x2 - u, self.z2 - v
        if f == 'west': return self.x2 - v, self.z1 + u
        return self.x1 + v, self.z2 - u

    def face(self, lf):
        """World facing of a local facing."""
        return B.turn(lf, self.q)

    def blk(self, b):
        if self.q == 0 or '[' not in b: return b
        r = self._rot.get(b)
        if r is None: r = self._rot[b] = B.rotate_state(b, self.q)
        return r

    def set(self, u, y, v, b):
        x, z = self.xz(u, v)
        self.p.set(x, y, z, self.blk(b) if b is not None else None)

    def get(self, u, y, v):
        x, z = self.xz(u, v)
        return self.p.get(x, y, z)

    def air(self, u, y, v):
        x, z = self.xz(u, v)
        self.p.c.pop((x, y, z), None)
        self.p.set(x, y, z, 'air')

    def fill(self, u1, y1, v1, u2, y2, v2, b):
        for y in range(min(y1, y2), max(y1, y2) + 1):
            for v in range(min(v1, v2), max(v1, v2) + 1):
                for u in range(min(u1, u2), max(u1, u2) + 1): self.set(u, y, v, b)

    def clear(self, u1, y1, v1, u2, y2, v2):
        self.fill(u1, y1, v1, u2, y2, v2, 'air')

    def console(self, u, v, lface, width=3):
        x, z = self.xz(u, v)
        A.console(self.p, x, DECK, z, self.face(lface), width)


# ------------------------------------------------------------------------------------------------ building kernel
class Shell:
    """Result of `closed()`: everything the programs and roofs need."""
    pass


def closed(b, mask, Hh, style='glass', ps=4, door_w=3, ladder=True, floor=True, th=None, window_from=7, lamps=True,
           extra_solid=(), fins=True):
    """Floor, walls (ring of the mask), door in the front wall, ceiling with lamps.  Returns Shell."""
    th = th or b.th
    S = Shell()
    S.mask, S.Hh = mask, Hh
    S.inner = erode(mask, 1)
    S.ring = mask - S.inner
    u0, v0, u1, v1 = bounds(mask)
    S.u0, S.v0, S.u1, S.v1 = u0, v0, u1, v1
    S.cu = (u0 + u1) // 2
    S.vf = v1                                   # front wall row
    S.top = DECK + Hh                           # last wall row
    S.Tc = DECK + Hh + 1                        # ceiling row
    # ladder wall cell
    S.ladder = None
    if ladder:
        rows = sorted(v for (u, v) in mask if u == u0)
        vm = rows[len(rows) // 2]
        S.ladder = (u0, vm)
    # door cells
    S.door = set()
    rows = (S.vf, S.vf - 1)
    for u in range(S.cu - door_w // 2, S.cu + door_w // 2 + 1):
        for v in rows:
            if (u, v) in S.ring: S.door.add((u, v))
    S.entrance = (S.cu, S.vf)
    # floor
    if floor:
        f0, f1 = th['floor']
        for (u, v) in mask:
            if abs(u - S.cu) <= 0 and (u, v) in S.inner: blk = CYAN if v % 2 == 0 else LBLUE
            else: blk = f0 if (u // 2 + v // 2) % 2 == 0 else f1
            b.set(u, DECK, v, blk)
    # interior air (clears fabric light blocks)
    for (u, v) in S.inner:
        for y in range(WALK, S.Tc):
            b.air(u, y, v)
    # walls
    for (u, v) in sorted(S.ring):
        ou = (u + 1, v) not in mask or (u - 1, v) not in mask
        ov = (u, v + 1) not in mask or (u, v - 1) not in mask
        corner = ou and ov
        idx = u if (ov and not ou) else v
        pil = corner or idx % ps == 0 or (u, v) == S.ladder
        if (u, v) in S.door: continue
        for y in range(WALK, S.top + 1):
            if style == 'open':
                if pil: blk = th['pillar']
                elif y == S.top: blk = QBR
                else: blk = None
            elif pil:
                blk = LAMP if (y == S.top - 1 and lamps and (u, v) != S.ladder and not corner) else th['pillar']
            elif y < window_from: blk = th['base']
            elif y == S.top: blk = th['accent'] if style in ('glass', 'slit') else th['wall']
            elif style == 'glass': blk = PANE
            elif style == 'slit': blk = PANE if (idx % 2 == 0 and y in (window_from, window_from + 1)) else th['wall']
            else: blk = th['wall']
            if blk is not None: b.set(u, y, v, blk)
    # tapering fins on the street front (outside the wall, in the apron ring)
    if style != 'open' and fins and S.vf + 1 <= b.D - 1:
        for (u, v) in sorted(S.ring):
            if v != S.vf or (u, v) in S.door or abs(u - S.cu) <= door_w // 2 + 2 or (u, v) == S.ladder: continue
            if u % ps != 0 or Hh < 5: continue
            for y in range(WALK, S.top - 1): b.set(u, y, v + 1, th['pillar'])
            b.set(u, S.top - 1, v + 1, B.stairs('quartz', 'north'))
    # door lintel and jamb lamps
    for (u, v) in S.door:
        for y in range(WALK, WALK + 3): b.air(u, y, v)
        b.set(u, WALK + 3, v, th['accent'])
    # ceiling (lamps on a lattice)
    for (u, v) in mask:
        lit = lamps and (u, v) in S.inner and (u - S.cu) % 4 == 0 and (v - S.v0) % 4 == 2
        b.set(u, S.Tc, v, LAMP if lit else th['roof'])
    return S


def porch(b, S, depth=1):
    """Canopy over the door: two pillars in the apron ring and a slab canopy."""
    th = b.th
    v = S.vf + 1
    if v > b.D - 1: return
    for u in (S.cu - 2, S.cu + 2):
        for y in range(WALK, WALK + 4): b.set(u, y, v, th['pillar'])
        b.set(u, WALK + 4, v, LAMP)
    for u in range(S.cu - 2, S.cu + 3):
        b.set(u, WALK + 4, v, SLAB_B) if u not in (S.cu - 2, S.cu + 2) else None
        b.set(u, WALK + 5, v, SLAB_B)


def roof_rim(b, S, skip=()):
    """Cornice: bottom slabs around the roof edge (except the ladder entry)."""
    for (u, v) in S.ring:
        if (u, v) == S.ladder or (u, v) in skip: continue
        b.set(u, S.Tc + 1, v, SLAB_B)


def roof_lamps(b, S, tiers_top, step=5):
    """Flush lamps in the top of the roof tiers: dict {(u,v): y}."""
    for (u, v), y in tiers_top.items():
        if (u - S.cu) % step == 0 and (v - S.v0) % step == 2 and (u, v) not in S.ring:
            b.set(u, y, v, LAMP)


def roof_stepped(b, S, erosions=(2, 4), mat=None):
    """Terraced roof: tier i is the mask eroded erosions[i] times, one block higher each.  Returns {(u,v): top y}."""
    th = b.th
    mat = mat or th['roof']
    top = {c: S.Tc for c in S.mask}
    for i, e in enumerate(erosions):
        m = erode(S.mask, e)
        if not m: break
        y = S.Tc + 1 + i
        for c in m:
            for yy in range(S.Tc + 1, y + 1): b.set(c[0], yy, c[1], QB if yy < y else mat)
            top[c] = y
    roof_rim(b, S)
    roof_lamps(b, S, top)
    S.roof_top = max(top.values())
    return top


def roof_flat(b, S):
    roof_rim(b, S)
    top = {c: S.Tc for c in S.mask}
    roof_lamps(b, S, top)
    S.roof_top = S.Tc + 1
    return top


def attach_ladder(b, S):
    """Ladder on the outside of the left flank wall up to the roof."""
    if S.ladder is None: return
    u, v = S.ladder
    for y in range(WALK, S.Tc + 2):
        b.set(u - 1, y, v, 'ladder[facing=west]')


def apron(b, S, extra=()):
    """Flush lamps in the 1-block apron ring (and a few around the building)."""
    W, D = b.W, b.D
    cells = []
    for u in range(W):
        for v in range(D):
            if u in (0, W - 1) or v in (0, D - 1):
                cells.append((u, v))
    for (u, v) in cells:
        if (u, v) in S.mask: continue
        corner = u in (0, W - 1) and v in (0, D - 1)
        if corner or (u + v) % 5 == 0:
            b.set(u, DECK, v, LAMP)


def entrance_meta(b, S):
    x, z = b.xz(*S.entrance)
    return (x, WALK, z)


# ------------------------------------------------------------------------------------------------ furniture / programs
class Room:
    def __init__(self, b, S, corr=1):
        self.b, self.S, self.corr = b, S, corr
        self.used = set()
        self.inner = S.inner
        self.cu = S.cu
        self.left = {}
        self.right = {}
        for (u, v) in S.inner:
            if v not in self.left or u < self.left[v]: self.left[v] = u
            if v not in self.right or u > self.right[v]: self.right[v] = u

    def ok(self, cells):
        for c in cells:
            if c not in self.inner or c in self.used or abs(c[0] - self.cu) <= self.corr: return False
        return True

    def take(self, cells):
        self.used.update(cells)


def bench(R, u, v, n, along, back):
    """Bench of n stairs; `back` = local side of the tall back."""
    cells = [(u + (i if along == 'u' else 0), v + (i if along == 'v' else 0)) for i in range(n)]
    if not R.ok(cells): return False
    for (cu_, cv_) in cells: R.b.set(cu_, WALK, cv_, B.stairs('quartz', back))
    R.take(cells)
    return True


def desk(R, u, v, n, face='south', lamp=True):
    cells = [(u + i, v) for i in range(n)]
    if not R.ok(cells): return False
    for i, (cu_, cv_) in enumerate(cells):
        R.b.set(cu_, WALK, cv_, SLAB_T)
        if i == 0 and lamp: R.b.set(cu_, WALK + 1, cv_, 'lantern[hanging=false]')
        elif i == n // 2: R.b.set(cu_, WALK + 1, cv_, 'black_stained_glass_pane')
    R.take(cells)
    cx, cz = u + n // 2, v + (1 if face == 'south' else -1)
    if R.ok([(cx, cz)]):
        R.b.set(cx, WALK, cz, B.stairs('quartz', 'north' if face == 'south' else 'south'))
        R.take([(cx, cz)])
    return True


def table(R, u, v, w=2, d=2, lamp=True):
    cells = [(u + i, v + j) for i in range(w) for j in range(d)]
    if not R.ok(cells): return False
    for (a, c) in cells: R.b.set(a, WALK, c, SLAB_T)
    if lamp: R.b.set(u, WALK + 1, v, 'lantern[hanging=false]')
    R.take(cells)
    return True


def bed(R, u, v, head, color='white'):
    """Bed with its head cell at (u, v) and foot one cell opposite `head` (local facing of head)."""
    dx, dz = B.DIRS[head]
    cells = [(u, v), (u - dx, v - dz)]
    if not R.ok(cells): return False
    R.b.set(u, WALK, v, f'{color}_bed[facing={head},part=head]')
    R.b.set(u - dx, WALK, v - dz, f'{color}_bed[facing={head},part=foot]')
    R.take(cells)
    return True


def crate(R, u, v, h=2, kind='barrel'):
    if not R.ok([(u, v)]): return False
    for k in range(h):
        R.b.set(u, WALK + k, v, 'barrel[facing=up]' if (kind == 'barrel' or k == 0) else 'barrel[facing=north]')
    R.take([(u, v)])
    return True


def shelf(R, u, v, h=3):
    if not R.ok([(u, v)]): return False
    for k in range(h): R.b.set(u, WALK + k, v, 'bookshelf')
    R.take([(u, v)])
    return True


def planter(R, u, v, plant='flowering_azalea'):
    if not R.ok([(u, v)]): return False
    R.b.set(u, WALK, v, QB)
    R.b.set(u, WALK + 1, v, plant)
    R.take([(u, v)])
    return True


def crystal(R, u, v, h=3):
    if not R.ok([(u, v)]): return False
    R.b.set(u, WALK, v, LAMP)
    for k in range(1, h): R.b.set(u, WALK + k, v, GLASS)
    R.take([(u, v)])
    return True


def labbench(R, u, v, n):
    cells = [(u + i, v) for i in range(n)]
    if not R.ok(cells): return False
    gear = ['brewing_stand', 'cauldron', 'lantern[hanging=false]', None, 'blue_stained_glass_pane', 'brewing_stand']
    for i, (a, c) in enumerate(cells):
        R.b.set(a, WALK, c, 'smooth_stone_slab[type=top]')
        g = gear[(i * 5 + u + v) % len(gear)]
        if i == 0: g = 'lantern[hanging=false]'
        if g: R.b.set(a, WALK + 1, c, g)
    R.take(cells)
    return True


def console_at(R, u, v, lface='south', width=3):
    hw = width // 2
    cells = [(u + dx, v + dz) for dx in range(-hw, hw + 1) for dz in range(3)]
    if not R.ok(cells): return False
    R.b.console(u, v, lface, width)
    for (a, c) in cells: R.b.set(a, WALK + 3, c, GLASS)
    R.take(cells)
    return True


def prog_lobby(R, n=1):
    S = R.S
    for v in range(S.v0 + 3, S.vf - 2, 5):
        if v in R.left: bench(R, R.left[v], v, 3, 'v', 'west')
        if v in R.right: bench(R, R.right[v], v, 3, 'v', 'east')
    for off in (-4, 4):
        console_at(R, S.cu + off, S.v0 + 1, 'south', 3)
    for (u, v) in ((S.u0 + 2, S.v0 + 2), (S.u1 - 2, S.v0 + 2)):
        planter(R, u, v)
    crystal(R, S.cu + 3, S.v0 + 3, 4)


def prog_offices(R):
    S = R.S
    for v in range(S.v0 + 2, S.vf - 3, 4):
        if v in R.left: desk(R, R.left[v], v, 3)
        if v in R.right: desk(R, R.right[v] - 2, v, 3)
    if S.v0 + 1 in R.left: shelf(R, R.left[S.v0 + 1], S.v0 + 1)
    if S.v0 + 1 in R.right: shelf(R, R.right[S.v0 + 1], S.v0 + 1)
    console_at(R, S.cu + 4, S.v0 + 1, 'south', 3)


def prog_bunks(R):
    S = R.S
    for v in range(S.v0 + 2, S.vf - 1, 3):
        if v in R.left: bed(R, R.left[v], v, 'west')
        if v in R.right: bed(R, R.right[v], v, 'east')
    for v in range(S.v0 + 1, S.vf - 1, 6):
        if v in R.left: crate(R, R.left[v] + 2, v, 2, 'chest')
        if v in R.right: crate(R, R.right[v] - 2, v, 2, 'chest')


def prog_labs(R):
    S = R.S
    for v in range(S.v0 + 2, S.vf - 3, 4):
        if v in R.left: labbench(R, R.left[v], v, 3)
        if v in R.right: labbench(R, R.right[v] - 2, v, 3)
    crystal(R, S.cu + 3, S.v0 + 1, 4)
    if S.v0 + 1 in R.left: shelf(R, R.left[S.v0 + 1], S.v0 + 1)


def prog_crates(R, dense=True):
    S = R.S
    for v in range(S.v0 + 1, S.vf - 1, 2):
        for u in range(S.u0 + 1, S.u1):
            if (u + 2 * v) % 3 == 0 and (R.rng_pick(u, v) if hasattr(R, 'rng_pick') else True):
                crate(R, u, v, 1 + (u + v) % 3, 'barrel' if (u + v) % 2 else 'chest')


def prog_lounge(R):
    S = R.S
    for v in range(S.v0 + 2, S.vf - 2, 4):
        if v in R.left: table(R, R.left[v] + 1, v, 2, 2)
        if v in R.right: table(R, R.right[v] - 2, v, 2, 2)
    for v in range(S.v0 + 2, S.vf - 2, 4):
        if v in R.left: bench(R, R.left[v], v, 2, 'v', 'west')
        if v in R.right: bench(R, R.right[v], v, 2, 'v', 'east')
    planter(R, S.cu + 3, S.v0 + 1)
    planter(R, S.cu - 3, S.v0 + 1)
    crystal(R, S.cu + 3, S.v0 + 3, 3)


def prog_workshop(R):
    S = R.S
    for v in range(S.v0 + 2, S.vf - 2, 4):
        if v in R.left: labbench(R, R.left[v], v, 3)
        if v in R.right: labbench(R, R.right[v] - 2, v, 3)
    for v in range(S.v0 + 1, S.vf - 1, 3):
        if v in R.left: crate(R, R.left[v] + 4, v, 2, 'chest')


def prog_machines(R):
    S = R.S
    for v in range(S.v0 + 2, S.vf - 2, 4):
        for side in ('l', 'r'):
            u = R.left.get(v) if side == 'l' else R.right.get(v)
            if u is None: continue
            u = u if side == 'l' else u - 1
            if R.ok([(u, v), (u + 1, v)]):
                for du in (0, 1):
                    R.b.set(u + du, WALK, v, P.COPPER_CUT)
                    R.b.set(u + du, WALK + 1, v, DARKP)
                R.b.set(u, WALK + 2, v, 'lightning_rod[facing=up]')
                R.b.set(u + 1, WALK + 2, v, 'end_rod[facing=up]')
                R.take([(u, v), (u + 1, v)])
    console_at(R, S.cu + 4, S.v0 + 1, 'south', 3)
    for v in range(S.v0 + 1, S.vf - 1, 5):
        if v in R.left: crate(R, R.left[v] + 3, v, 2)


def run_program(b, S, fn, **kw):
    R = Room(b, S)
    fn(R, **kw)
    return R


# ------------------------------------------------------------------------------------------------ designs
DESIGNS = {}


def design(name, districts, bw, bd, hh, extra, weight=1.0, kind='building', quota=1, fill=True, square=False):
    """Register a design.  bw/bd = (min, max) building width (along the street) / depth; hh = (min, max) interior
    clear height; extra(Hh) = highest y above the ceiling row used by the roof (so total top = DECK+Hh+1+extra)."""
    def deco(fn):
        DESIGNS[name] = dict(name=name, districts=tuple(districts), bw=bw, bd=bd, hh=hh, extra=extra, weight=weight,
                             kind=kind, draw=fn, quota=quota, fill=fill, square=square)
        return fn
    return deco


def _finish(b, S, kind_meta=None, ladder=True):
    if ladder: attach_ladder(b, S)
    apron(b, S)
    box = (b.x1, WALK, b.z1, b.x2, S.Tc, b.z2)
    return dict(entrances=[entrance_meta(b, S)], roof_y=S.Tc)


def _banner_pillars(b, S):
    pass


# ---- A -----------------------------------------------------------------------------------------------
@design('plaza_pavilion', 'A', (11, 17), (11, 15), (6, 8), 3, 1.2, 'pavilion')
def draw_pavilion(b, Hh, mw, md):
    u0, v0 = 1, 1
    mask = rect_mask(1, 1, mw, md) if False else rrect_mask(1, 1, mw, md, 3)
    S = closed(b, mask, Hh, style='open', ps=4, door_w=5)
    # glass roof lantern and stepped canopy
    roof_stepped(b, S, (2, 4))
    # fountain-like central column of light
    R = Room(b, S, corr=0)
    cx, cv = S.cu, (S.v0 + S.vf) // 2
    for dv in (-1, 0, 1):
        for du in (-1, 0, 1):
            b.set(S.cu + du, DECK, cv + dv, LAMP if (du == 0 and dv == 0) else CYAN)
    b.set(S.cu, WALK, cv, QP)
    for y in range(WALK + 1, WALK + 4): b.set(S.cu, y, cv, GLASS)
    b.set(S.cu, WALK + 4, cv, 'end_rod[facing=up]')
    R.take([(S.cu, cv)])
    R.corr = 1
    for v in range(S.v0 + 2, S.vf - 1, 4):
        if v in R.left: bench(R, R.left[v], v, 3, 'v', 'west')
        if v in R.right: bench(R, R.right[v], v, 3, 'v', 'east')
    porch(b, S)
    return S


@design('colonnade_hall', 'A', (19, 27), (12, 16), (6, 8), 3, 1.0, 'hall')
def draw_colonnade_hall(b, Hh, mw, md):
    # outer open colonnade ring (mask M) around a glass hall (mask M1)
    M = rect_mask(1, 1, mw, md)
    M1 = erode(M, 1)
    S = closed(b, M1, Hh, style='glass', ps=3, door_w=5, ladder=False)
    # colonnade ring
    ring = M - M1
    th = b.th
    for (u, v) in ring:
        ou = u in (1, mw)
        ov = v in (1, md)
        idx = v if ou and not ov else u
        pil = (ou and ov) or idx % 3 == 0
        b.set(u, DECK, v, QS if (u + v) % 2 == 0 else PG)
        if (u, v) == (1, (1 + md) // 2): pil = True
        for y in range(WALK, S.top + 1):
            if pil: b.set(u, y, v, th['pillar'])
            elif y == S.top: b.set(u, y, v, QBR)
        b.set(u, S.Tc, v, th['roof'])
    # ladder on the colonnade's left pillar
    lad = (1, (1 + md) // 2)
    S.ladder = lad
    for y in range(WALK, S.Tc + 2): b.set(lad[0] - 1, y, lad[1], 'ladder[facing=west]')
    S.mask_all = M
    S.ring = M - erode(M, 1)
    # clerestory: tier on the hall
    top = {c: S.Tc for c in M}
    for (u, v) in erode(M, 3):
        for y in (S.Tc + 1,): b.set(u, y, v, QB)
        top[(u, v)] = S.Tc + 1
    for (u, v) in erode(M, 5):
        b.set(u, S.Tc + 1, v, QB); b.set(u, S.Tc + 2, v, th['roof']); top[(u, v)] = S.Tc + 2
    for (u, v) in ring:
        if (u, v) != lad: b.set(u, S.Tc + 1, v, SLAB_B)
    for (u, v), y in top.items():
        if (u - S.cu) % 5 == 0 and (v - 1) % 5 == 2 and (u, v) not in ring: b.set(u, y, v, LAMP)
    for (u, v) in ring:                      # floodlights inside the colonnade walk
        if (u + v) % 6 == 0 and (u, v) != lad and not (u in (1, mw) and v in (1, md)): b.set(u, S.Tc, v, LAMP)
    S.roof_top = S.Tc + 2
    S.inner_box = S.inner
    run_program(b, S, prog_lobby)
    for v in range(S.v0 + 2, S.vf - 2, 4):         # statues/planters along the hall
        pass
    S.mask = M
    return S


@design('gatehouse', 'A', (21, 27), (9, 11), (6, 6), 9, 0.8, 'gatehouse')
def draw_gatehouse(b, Hh, mw, md):
    th = b.th
    W = mw
    cu = (1 + mw) // 2
    tw = cu - 5                                         # tower width: the tower's inner wall borders the passage
    Hg = Hh + 4                                         # tower interior height
    Tt = DECK + Hg + 1                                  # tower ceiling row
    Tm = DECK + 11                                      # bridge deck row
    lt = (1, 1 + tw)                                    # left tower u range
    rt = (mw - tw, mw)
    # floor
    for u in range(1, mw + 1):
        for v in range(1, md + 1):
            b.set(u, DECK, v, CYAN if abs(u - cu) <= 0 else (QS if (u // 2 + v // 2) % 2 == 0 else PG))
    # solid mass: left/right towers and bridge
    for u in range(1, mw + 1):
        for v in range(1, md + 1):
            tower = u <= lt[1] or u >= rt[0]
            top = Tt if tower else Tm
            pas = abs(u - cu) <= 3
            for y in range(WALK, top + 1):
                if tower:
                    inside = (lt[0] < u < lt[1] or rt[0] < u < rt[1]) and 1 < v < md
                    if inside and y < Tt: b.air(u, y, v); continue
                    edge = (u in (1, mw) or v in (1, md) or u in (lt[1], rt[0]))
                    pil = (u in (1, mw, lt[1], rt[0])) and (v in (1, md) or v % 3 == 0)
                    blk = th['pillar'] if pil else (PANE if (WALK + 2 <= y < Tt - 1 and (u + v) % 2 == 0 and u in (1, mw)) else th['base'] if y < WALK + 2 else th['wall'])
                    if y == Tt: blk = LAMP if (u - cu) % 2 == 0 and (v % 3 == 2) and lt[0] < u < lt[1] + 0 else th['roof']
                    b.set(u, y, v, blk)
                else:
                    if pas:
                        arch = abs(u - cu) * 1.4 + 5.6
                        if y - DECK <= arch - (abs(u - cu) * 0.0) and y <= WALK + 5 + (1 if abs(u - cu) < 2 else 0):
                            b.air(u, y, v); continue
                    if y == Tm: blk = th['roof']
                    else: blk = th['base'] if y < WALK + 1 else (th['wall'])
                    b.set(u, y, v, blk)
    # tunnel lamps in the arch ceiling
    for v in range(2, md, 3):
        b.set(cu, WALK + 6, v, LAMP)
    # doors from the passage into the tower rooms
    ent = []
    for ucell, tuc, rooms in ((lt[1], cu - 4, (lt[0] + 1, lt[1] - 1)), (rt[0], cu + 4, (rt[0] + 1, rt[1] - 1))):
        vv = (1 + md) // 2
        for y in range(WALK, WALK + 3): b.air(ucell, y, vv)
        b.set(ucell, WALK + 3, vv, th['accent'])
        ent.append((ucell, vv))
    # tower ceilings lamps; furniture in rooms
    S = Shell()
    S.mask = rect_mask(1, 1, mw, md)
    S.Tc, S.Hh = Tt, Hg
    S.top = Tt - 1
    S.ladder = None
    S.cu = cu
    S.entrances_uv = ent
    S.inner = {(u, v) for u in list(range(lt[0] + 1, lt[1])) + list(range(rt[0] + 1, rt[1])) for v in range(2, md)}
    S.ring = set()
    S.u0, S.v0, S.u1, S.v1 = 1, 1, mw, md
    S.vf = md
    S.roof_top = Tt + 2
    for (ut0, ut1, ucell) in ((lt[0], lt[1], lt[1]), (rt[0], rt[1], rt[0])):
        # crown on each tower
        for (u, v) in erode(rect_mask(ut0, 1, ut1, md), 1):
            b.set(u, Tt + 1, v, th['roof'])
        for (u, v) in erode(rect_mask(ut0, 1, ut1, md), 2):
            b.set(u, Tt + 1, v, QB); b.set(u, Tt + 2, v, th['roof'])
        uc = (ut0 + ut1) // 2
        vc = (1 + md) // 2
        b.set(uc, Tt + 3, vc, 'end_rod[facing=up]')
        b.set(uc, Tt + 2, vc, LAMP)
        # interior fittings
        for v in range(2, md, 3):
            pass
    # ladders on the outer flanks of both towers
    vm = (1 + md) // 2
    for y in range(WALK, Tt + 2):
        b.set(0, y, vm, 'ladder[facing=west]')
        b.set(mw + 1, y, vm, 'ladder[facing=east]')
    for u, v in ((1, vm), (mw, vm)):
        for y in range(WALK, Tt + 1): b.set(u, y, v, th['pillar'])
    # roof of bridge: rim + lamps
    for u in range(lt[1] + 1, rt[0]):
        for v in (1, md): b.set(u, Tm + 1, v, SLAB_B)
        for v in range(1, md + 1):
            if v % 4 == 2 and (u - cu) % 4 == 0: b.set(u, Tm, v, LAMP)
    # furnishing of tower rooms: benches + crystal
    R = Room(b, S, corr=-1)
    for (a, c) in ((lt[0] + 2, md - 2), (rt[1] - 2, md - 2)):
        pass
    for (rl, rr) in ((lt[0] + 1, lt[1] - 1), (rt[0] + 1, rt[1] - 1)):
        crystal(R, rl + 1, 3, 3)
        bench(R, rl, md - 2, 2, 'u', 'south')
        bench(R, rr - 1, md - 2, 2, 'u', 'south') if False else None
    apron(b, S)
    S.gate = True
    return S


# ---- B -----------------------------------------------------------------------------------------------
@design('visitor_quarters', 'B', (17, 25), (13, 17), (5, 6), 2, 1.3, 'quarters')
def draw_visitor_quarters(b, Hh, mw, md):
    mask = rrect_mask(1, 1, mw, md, 2)
    S = closed(b, mask, Hh, style='glass', ps=4, door_w=3)
    roof_stepped(b, S, (2, 4))
    th = b.th
    # partitions: alcove rows along both side walls (open fronts), bed in each
    R = Room(b, S, corr=1)
    cu = S.cu
    for v in range(S.v0 + 2, S.vf - 1, 4):
        for sgn in (-1, 1):
            u_part = cu + sgn * 4
            for y in (WALK, WALK + 1):
                b.set(u_part, y, v + 2, QBR) if (u_part, v + 2) in S.inner else None
            for u in range(cu + sgn * 2, (S.u0 if sgn < 0 else S.u1), sgn):
                pass
        # beds at the outer walls
        if v in R.left: bed(R, R.left[v], v, 'west')
        if v in R.right: bed(R, R.right[v], v, 'east')
        if v in R.left: crate(R, R.left[v] + 2, v, 1, 'chest')
        if v in R.right: crate(R, R.right[v] - 2, v, 1, 'chest')
        if v in R.left: planter(R, R.left[v] + 3, v)
        if v in R.right: planter(R, R.right[v] - 3, v)
    # partition walls between alcoves, in the 2-cell gaps (cells next to corridor are kept free)
    for v in range(S.v0 + 2 + 2, S.vf - 1, 4):
        for sgn in (-1, 1):
            for k in range(1, 6):
                u = cu + sgn * (1 + k)
                if (u, v) in S.inner and abs(u - cu) > 1 and k >= 2:
                    b.set(u, WALK, v, QBR); b.set(u, WALK + 1, v, PANE)
    porch(b, S)
    return S


@design('customs_office', 'B', (13, 17), (11, 13), (5, 6), 2, 1.3, 'office')
def draw_customs(b, Hh, mw, md):
    mask = rrect_mask(1, 1, mw, md, 4)
    S = closed(b, mask, Hh, style='glass', ps=3, door_w=3)
    roof_stepped(b, S, (2,))
    cu = S.cu
    # scanner arch inside the door (two pillars + glass lintel)
    for dx in (-2, 2):
        for y in range(WALK, WALK + 3): b.set(cu + dx, y, S.vf - 2, QP)
    for dx in range(-2, 3): b.set(cu + dx, WALK + 3, S.vf - 2, GLASS if abs(dx) < 2 else QP)
    for dx in range(-1, 2):
        for dv in range(-1, 2): b.set(cu + dx, DECK, S.vf - 2 + dv, CYAN)
    # counter with staff stools along the back
    R = Room(b, S, corr=1)
    for v in (S.v0 + 3, S.v0 + 6):
        for u in range(S.u0 + 2, S.u1 - 1):
            if abs(u - cu) <= 1 or (u, v) not in S.inner: continue
            b.set(u, WALK, v, QB); b.set(u, WALK + 1, v, SLAB_B) if v == S.v0 + 3 else None
            R.used.add((u, v))
    console_at(R, cu + 3, S.v0 + 1, 'south', 3) if False else None
    for sgn in (-1, 1):
        crate(R, cu + sgn * 4, S.vf - 2, 2, 'chest')
        bench(R, cu + sgn * 3, S.vf - 4, 2, 'u', 'north') if sgn > 0 else bench(R, cu - 4, S.vf - 4, 2, 'u', 'north')
    planter(R, S.u0 + 1, S.vf - 1)
    planter(R, S.u1 - 1, S.vf - 1)
    porch(b, S)
    return S


@design('guard_post', 'B', (9, 11), (9, 11), (6, 7), 5, 0.6, 'guard', quota=0, fill=False)
def draw_guard_post(b, Hh, mw, md):
    mask = ellipse_mask((1 + mw) // 2, (1 + md) // 2, (mw - 1) // 2, (md - 1) // 2)
    S = closed(b, mask, Hh, style='glass', ps=3, door_w=3)
    top = roof_stepped(b, S, (2,))
    cu = S.cu
    cv = (S.v0 + S.vf) // 2
    # watch lantern on a spire
    for y in range(S.Tc + 2, S.Tc + 5): b.set(cu, y, cv, QP)
    b.set(cu, S.Tc + 5, cv, 'end_rod[facing=up]')
    S.roof_top = S.Tc + 5
    R = Room(b, S, corr=1)
    console_at(R, cu, S.v0 + 1, 'south', 3)
    bench(R, S.u0 + 1, cv, 2, 'v', 'west') if (S.u0 + 1, cv) in S.inner else None
    crate(R, S.u1 - 1, cv, 2)
    porch(b, S)
    return S


# ---- K -----------------------------------------------------------------------------------------------
@design('staff_offices', 'K', (21, 31), (11, 15), (5, 6), 3, 1.5, 'offices')
def draw_offices(b, Hh, mw, md):
    mask = rect_mask(1, 1, mw, md)
    S = closed(b, mask, Hh, style='glass', ps=5, door_w=3, window_from=7)
    roof_stepped(b, S, (2, 4))
    run_program(b, S, prog_offices)
    porch(b, S)
    return S


@design('comms_building', 'K', (13, 17), (13, 17), (6, 7), 12, 1.0, 'comms')
def draw_comms(b, Hh, mw, md):
    mask = rrect_mask(1, 1, mw, md, 5)
    S = closed(b, mask, Hh, style='slit', ps=4, door_w=3)
    top = roof_stepped(b, S, (3,))
    cu, cv = S.cu, (S.v0 + S.vf) // 2
    y0 = S.Tc + 2
    # antenna mast: pillar + iron bars + end rod tip, with a dish bowl of stairs on the side
    for y in range(y0, y0 + 5): b.set(cu, y, cv, QP)
    for y in range(y0 + 5, y0 + 9): b.set(cu, y, cv, 'iron_bars')
    b.set(cu, y0 + 9, cv, LAMP)
    b.set(cu, y0 + 10, cv, 'end_rod[facing=up]')
    for (du, dv, f) in ((1, 0, 'west'), (-1, 0, 'east'), (0, 1, 'north'), (0, -1, 'south')):
        b.set(cu + du, y0 + 4, cv + dv, B.stairs('smooth_quartz', f, 'top'))
        b.set(cu + 2 * du, y0 + 5, cv + 2 * dv, B.stairs('smooth_quartz', f, 'bottom'))
    S.roof_top = y0 + 10
    R = Room(b, S, corr=1)
    for v in range(S.v0 + 2, S.vf - 2, 4):
        if v in R.left: desk(R, R.left[v], v, 3)
        if v in R.right: desk(R, R.right[v] - 2, v, 3)
    console_at(R, S.cu + 3, S.v0 + 1, 'south', 3)
    crystal(R, S.cu - 4, S.v0 + 1, 4)
    porch(b, S)
    return S


@design('barracks', 'K', (21, 31), (11, 13), (5, 5), 6, 1.4, 'barracks')
def draw_barracks(b, Hh, mw, md):
    mask = rect_mask(1, 1, mw, md)
    S = closed(b, mask, Hh, style='slit', ps=4, door_w=3)
    # gabled roof: slopes of stairs along the street axis
    top = {}
    for (u, v) in mask:
        d = min(u - 1, mw - u)
        h = min(d, 5)
        for y in range(S.Tc + 1, S.Tc + h): b.set(u, y, v, QB)
        if d == 0: continue
        if h < 5 and (u - 1) < (mw - u):
            b.set(u, S.Tc + h, v, B.stairs('smooth_quartz', 'east')) if u > 1 else None
        elif h < 5:
            b.set(u, S.Tc + h, v, B.stairs('smooth_quartz', 'west'))
        else:
            b.set(u, S.Tc + h, v, SLAB_B)
        top[(u, v)] = S.Tc + h
    for (u, v), y in top.items():
        if y == S.Tc + 5 and v % 5 == 2 and u == (1 + mw) // 2: b.set(u, y + 1, v, 'lantern[hanging=false]')
    S.roof_top = S.Tc + 6
    run_program(b, S, prog_bunks)
    porch(b, S)
    return S


# ---- D -----------------------------------------------------------------------------------------------
@design('lab_block', 'D', (15, 21), (11, 15), (5, 6), 3, 1.6, 'lab')
def draw_lab_block(b, Hh, mw, md):
    mask = rrect_mask(1, 1, mw, md, 3)
    S = closed(b, mask, Hh, style='glass', ps=4, door_w=3)
    roof_stepped(b, S, (2, 4))
    run_program(b, S, prog_labs)
    porch(b, S)
    return S


@design('observatory', 'D', (13, 17), (13, 17), (6, 7), 12, 1.0, 'observatory', square=True)
def draw_observatory(b, Hh, mw, md):
    cu, cv = (1 + mw) // 2, (1 + md) // 2
    a = (min(mw, md) - 1) // 2
    mask = ellipse_mask(cu, cv, a, a)
    S = closed(b, mask, Hh, style='slit', ps=4, door_w=3)
    # stepped glass dome over the inner room (slope <= 1 block per column so the roof can be climbed)
    Rr = a - 0.8
    Hd = 5.0
    ytop = S.Tc
    for (u, v) in S.inner:
        r = math.hypot(u - cu, v - cv)
        if r > Rr: continue
        h = int(round(Hd * math.sqrt(max(0.0, 1.0 - (r / Rr) ** 2))))
        h = min(h, int(Rr - r + 1.0))
        for y in range(S.Tc, S.Tc + h): b.air(u, y, v)
        b.set(u, S.Tc + h, v, GLASS if h < 4 else GLASS_W)
        ytop = max(ytop, S.Tc + h)
    b.set(cu, ytop + 1, cv, LAMP)
    b.set(cu, ytop + 2, cv, 'end_rod[facing=up]')
    S.roof_top = ytop + 2
    for (u, v) in S.ring:
        if (u, v) != S.ladder: b.set(u, S.Tc + 1, v, SLAB_B)
    # telescope: pedestal + barrel of end rods aimed up
    R = Room(b, S, corr=0)
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1): b.set(cu + dx, DECK, cv + dz, CYAN if (dx or dz) else LAMP)
    b.set(cu, WALK, cv, DARKP); b.set(cu, WALK + 1, cv, QB)
    for k in range(2, 6): b.set(cu, WALK + k, cv, 'end_rod[facing=up]' if k == 5 else P.GLASS_CYAN)
    R.take([(cu, cv)])
    R.corr = 1
    ring_c = [(cu + int(round((a - 2) * math.cos(t))), cv + int(round((a - 2) * math.sin(t))))
              for t in [i * math.pi / 3 for i in range(6)]]
    for (u, v) in ring_c:
        if abs(u - cu) > 1: planter(R, u, v) if (u, v) != (cu, v) else None
    for v in range(S.v0 + 2, S.vf - 1, 4):
        if v in R.left: bench(R, R.left[v] + 1, v, 2, 'v', 'west')
        if v in R.right: bench(R, R.right[v] - 1, v, 2, 'v', 'east')
    S.mask = mask
    porch(b, S)
    return S


@design('workshop', 'D', (19, 27), (13, 17), (5, 6), 8, 1.2, 'workshop')
def draw_workshop(b, Hh, mw, md):
    mask = rect_mask(1, 1, mw, md)
    S = closed(b, mask, Hh, style='slit', ps=5, door_w=5)
    # sawtooth roof across the depth: period 4 along v
    top = {}
    for (u, v) in erode(mask, 1):
        k = (v - 2) % 4
        h = (1, 2, 3, 3)[k]
        for y in range(S.Tc + 1, S.Tc + h + 1):
            blk = GLASS if (k == 3 and y == S.Tc + h) else (QB if y < S.Tc + h else S.th_roof if False else b.th['roof'])
            b.set(u, y, v, blk)
        top[(u, v)] = S.Tc + h
    roof_rim(b, S)
    for (u, v), y in top.items():
        if (v - 2) % 4 == 0 and (u - S.cu) % 5 == 0: b.set(u, y, v, LAMP)
    S.roof_top = S.Tc + 3
    run_program(b, S, prog_workshop)
    porch(b, S)
    return S


# ---- G -----------------------------------------------------------------------------------------------
@design('hangar', 'G', (21, 27), (19, 25), (8, 9), 6, 1.8, 'hangar')
def draw_hangar(b, Hh, mw, md):
    th = b.th
    mask = rect_mask(1, 1, mw, md)
    S = closed(b, mask, Hh, style='slit', ps=6, door_w=1, lamps=True)
    # replace the flat ceiling by a barrel vault (profile along u) rising from the wall top
    cu = S.cu
    rise = 5
    half = (mw - 1) / 2.0
    prof = {}
    prev = 0
    for u in range(1, mw + 1):
        t = abs(u - cu) / half
        h = int(round(rise * math.sqrt(max(0.0, 1 - t * t * 0.98))))
        prof[u] = h
    # make monotone steps <= 1 from the edges
    for u in range(2, cu + 1):
        prof[u] = min(prof[u], prof[u - 1] + 1)
    for u in range(mw - 1, cu - 1, -1):
        prof[u] = min(prof[u], prof[u + 1] + 1)
    for u in range(cu, 0, -1): prof[u] = max(prof[u], 0)
    top = {}
    for (u, v) in mask:
        h = prof[u]
        inner = (u, v) in S.inner
        for y in range(S.Tc, S.Tc + 1):
            pass
        if inner:
            for y in range(S.Tc, S.Tc + h): b.air(u, y, v)
        # roof block at Tc + h
        ring = (u, v) in S.ring
        if ring:
            for y in range(S.Tc, S.Tc + h):
                b.set(u, y, v, th['wall'])
        b.set(u, S.Tc + h, v, LAMP if (inner and (u - cu) % 4 == 0 and v % 4 == 2 and h >= 3) else th['roof'])
        top[(u, v)] = S.Tc + h
    # ribs on the roof
    for (u, v) in mask:
        if v % 6 == 1 and (u, v) not in S.ring:
            b.set(u, top[(u, v)] + 1, v, SLAB_B) if False else None
    # big front door: 9 wide, 6 tall + cyan frame
    dw = 4
    for u in range(cu - dw, cu + dw + 1):
        for y in range(WALK, WALK + 6): b.air(u, y, S.vf)
        b.set(u, WALK + 6, S.vf, CYAN)
    for u in (cu - dw - 1, cu + dw + 1):
        for y in range(WALK, WALK + 7): b.set(u, y, S.vf, th['pillar'])
        b.set(u, WALK + 6, S.vf, LAMP)
    S.door = {(u, S.vf) for u in range(cu - dw, cu + dw + 1)}
    S.roof_top = S.Tc + max(prof.values())
    S.hang_prof = prof
    # floor guidance line + landing marks
    for v in range(S.v0 + 1, S.vf):
        b.set(cu, DECK, v, LAMP if (v - S.v0) % 4 == 2 else CYAN)
    for v in range(S.v0 + 2, S.vf, 6):
        for u in (S.u0 + 2, S.u1 - 2): b.set(u, DECK, v, LAMP)
    for v in range(S.v0 + 4, S.vf - 1, 6):
        for u in (cu - 3, cu + 3): b.set(u, DECK, v, LBLUE)
    R = Room(b, S, corr=1)
    for v in range(S.v0 + 1, S.vf - 1, 4):
        if v in R.left: crate(R, R.left[v], v, 2)
        if v in R.right: crate(R, R.right[v], v, 2)
    console_at(R, cu + 5, S.v0 + 1, 'south', 3)
    # small craft silhouette on the floor (dark prismarine plate with fins)
    for du in range(-2, 3):
        for dv in range(0, 6):
            if (S.v0 + 5 + dv) < S.vf - 6 and abs(du) <= 2 - dv // 3:
                pass
    # ceiling vault hanging lamps don't need extra; ladder from left flank reaches the vault edge
    return S


@design('cargo_depot', 'G', (21, 29), (15, 21), (6, 7), 3, 1.5, 'depot')
def draw_depot(b, Hh, mw, md):
    th = b.th
    mask = rect_mask(1, 1, mw, md)
    S = closed(b, mask, Hh, style='slit', ps=5, door_w=5)
    roof_stepped(b, S, (2, 5))
    run_program(b, S, prog_crates)
    # loading dock canopy on the front
    cu = S.cu
    v = S.vf + 1
    if v <= b.D - 1:
        for u in range(S.cu - 4, S.cu + 5):
            b.set(u, WALK + 4, v, SLAB_B)
        for u in (S.cu - 4, S.cu + 4):
            for y in range(WALK, WALK + 4): b.set(u, y, v, th['pillar'])
        b.set(S.cu - 4, WALK + 4, v, LAMP); b.set(S.cu + 4, WALK + 4, v, LAMP)
    return S


@design('crew_lounge', 'G', (15, 21), (13, 17), (5, 6), 4, 1.3, 'lounge')
def draw_lounge(b, Hh, mw, md):
    cu, cv = (1 + mw) // 2, (1 + md) // 2
    mask = ellipse_mask(cu, cv, (mw - 1) // 2, (md - 1) // 2)
    S = closed(b, mask, Hh, style='glass', ps=3, door_w=3)
    roof_stepped(b, S, (2, 4))
    # glass lantern crown
    for (u, v) in erode(mask, 5):
        b.set(u, S.Tc + 3, v, GLASS)
    S.roof_top = S.Tc + 3
    run_program(b, S, prog_lounge)
    porch(b, S)
    return S


# ---- E -----------------------------------------------------------------------------------------------
@design('substation', 'E', (11, 15), (9, 13), (5, 6), 8, 1.4, 'substation')
def draw_substation(b, Hh, mw, md):
    th = b.th
    mask = rect_mask(1, 1, mw, md)
    S = closed(b, mask, Hh, style='slit', ps=3, door_w=3)
    roof_stepped(b, S, (2,))
    cu = S.cu
    # transformer coils on the roof: copper pillars with lightning rods
    for (u, v) in ((S.u0 + 3, S.v0 + 3), (S.u1 - 3, S.v0 + 3), (S.u0 + 3, S.vf - 3), (S.u1 - 3, S.vf - 3)):
        if (u, v) not in S.inner: continue
        for k in range(1, 5): b.set(u, S.Tc + 1 + k, v, P.COPPER_CUT if k % 2 else DARKP)
        b.set(u, S.Tc + 6, v, 'lightning_rod[facing=up]')
    S.roof_top = S.Tc + 6
    run_program(b, S, prog_machines)
    porch(b, S)
    return S


@design('pump_house', 'E', (11, 15), (11, 15), (6, 7), 6, 1.2, 'pump', square=True)
def draw_pump_house(b, Hh, mw, md):
    th = b.th
    cu, cv = (1 + mw) // 2, (1 + md) // 2
    a = (min(mw, md) - 1) // 2
    mask = ellipse_mask(cu, cv, a, a)
    S = closed(b, mask, Hh, style='slit', ps=4, door_w=3)
    top = roof_stepped(b, S, (2,))
    # pipe manifold: four copper stacks up the flanks and a ring main on the roof
    for (du, dv) in ((a - 1, 0), (-(a - 1), 0), (0, -(a - 1))):
        u, v = cu + du, cv + dv
        if (u, v) not in S.inner: continue
        for y in range(S.Tc + 1, S.Tc + 5): b.set(u, y, v, P.COPPER_CUT if y % 2 else DARKP)
        b.set(u, S.Tc + 5, v, LAMP)
    S.roof_top = S.Tc + 5
    # a band of cyan conduit around the shaft
    for (u, v) in S.ring:
        if (u, v) not in S.door and (u, v) != S.ladder and b.get(u, WALK + 1, v) not in (None, 'air'):
            b.set(u, WALK + 1, v, CYAN)
    R = Room(b, S, corr=0)
    # central pump: cylinder of dark prismarine with glowing core
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if abs(dx) + abs(dz) <= 1:
                b.set(cu + dx, DECK, cv + dz, CYAN)
    for y in range(WALK, WALK + 3): b.set(cu, y, cv, DARKP if y != WALK + 1 else LAMP)
    b.set(cu, WALK + 3, cv, P.COPPER_CUT)
    R.take([(cu, cv)])
    R.corr = 1
    for v in range(S.v0 + 2, S.vf - 1, 3):
        if v in R.left: crate(R, R.left[v], v, 2)
        if v in R.right: crate(R, R.right[v], v, 2)
    console_at(R, cu + 3, S.v0 + 1, 'south', 3)
    porch(b, S)
    return S


@design('engineers_quarters', 'E', (19, 25), (13, 17), (5, 6), 3, 1.4, 'quarters')
def draw_engineers_quarters(b, Hh, mw, md):
    # L-shaped block: full-width front wing plus a back wing on the right
    wing = max(5, md // 2)
    front = rect_mask(1, md - wing + 1, mw, md)
    back = rect_mask(mw - (mw // 2), 1, mw, md - wing)
    mask = front | back
    S = closed(b, mask, Hh, style='slit', ps=4, door_w=3)
    roof_stepped(b, S, (2,))
    run_program(b, S, prog_bunks)
    R = Room(b, S, corr=1)
    for v in range(S.v0 + 1, S.vf - 1, 4):
        if v in R.left: planter(R, R.left[v] + 2, v)
    porch(b, S)
    return S


@design('storage_depot', 'E', (15, 21), (11, 15), (5, 6), 3, 1.0, 'storage')
def draw_storage(b, Hh, mw, md):
    mask = rrect_mask(1, 1, mw, md, 2)
    S = closed(b, mask, Hh, style='slit', ps=4, door_w=5)
    roof_stepped(b, S, (2, 4))
    run_program(b, S, prog_crates)
    porch(b, S)
    return S


# ---- small infill, every district -----------------------------------------------------------------------
ALL6 = ('A', 'B', 'K', 'D', 'G', 'E')


@design('service_kiosk', ALL6, (7, 9), (7, 9), (5, 5), 3, 0.35, 'kiosk', square=True)
def draw_kiosk(b, Hh, mw, md):
    a = (min(mw, md) - 1) // 2
    mask = ellipse_mask((1 + mw) // 2, (1 + md) // 2, (mw - 1) // 2, (md - 1) // 2)
    S = closed(b, mask, Hh, style='glass', ps=3, door_w=3)
    roof_stepped(b, S, (2,))
    for (u, v) in erode(mask, 3):
        b.set(u, S.Tc + 2, v, GLASS)
        b.set(u, S.Tc + 3, v, LAMP)
    S.roof_top = S.Tc + 3
    R = Room(b, S, corr=1)
    for v in range(S.v0 + 1, S.vf - 1, 2):
        if v in R.left: bench(R, R.left[v], v, 2, 'v', 'west')
        if v in R.right: bench(R, R.right[v], v, 2, 'v', 'east')
    crystal(R, S.cu + 2, S.v0 + 1, 3)
    return S


@design('relay_hut', ALL6, (7, 9), (7, 9), (5, 6), 8, 0.35, 'relay')
def draw_relay_hut(b, Hh, mw, md):
    mask = rrect_mask(1, 1, mw, md, 2)
    S = closed(b, mask, Hh, style='slit', ps=3, door_w=3)
    roof_flat(b, S)
    cu, cv = S.cu, (S.v0 + S.vf) // 2
    for y in range(S.Tc + 2, S.Tc + 6): b.set(cu, y, cv, 'iron_bars')
    b.set(cu, S.Tc + 1, cv, QP)
    b.set(cu, S.Tc + 6, cv, LAMP)
    b.set(cu, S.Tc + 7, cv, 'end_rod[facing=up]')
    S.roof_top = S.Tc + 7
    R = Room(b, S, corr=1)
    console_at(R, cu + 2, S.v0 + 1, 'south', 3)
    for v in range(S.v0 + 2, S.vf - 1, 2):
        if v in R.left: crate(R, R.left[v], v, 2)
    return S


# ------------------------------------------------------------------------------------------------ lot planner
class Lot:
    def __init__(self, name, design_name, district, bbox, facing, Hh, mw, md):
        self.name, self.design, self.district, self.bbox, self.facing = name, design_name, district, bbox, facing
        self.Hh, self.mw, self.md = Hh, mw, md


def design_top(d, Hh):
    return DECK + Hh + 1 + d['extra']


def _region(D):
    r0, r1 = DISTRICT_BAND[D]
    xs = []
    zs = []
    for x in range(-r1 - 8, r1 + 9, 6):
        for z in range(-r1 - 8, r1 + 9, 6):
            rr = math.hypot(x, z)
            if r0 - 8 <= rr <= r1 + 8 and PL.district_at(x, z) == D:
                xs.append(x); zs.append(z)
    return min(xs) - 12, min(zs) - 12, max(xs) + 12, max(zs) + 12


class Grid:
    """Per-district occupancy: prefix sums of 'bad' cells over a region."""

    def __init__(self, D, obstacles):
        self.D = D
        r0, r1 = DISTRICT_BAND[D]
        x0, z0, x1, z1 = _region(D)
        self.x0, self.z0, self.w, self.h = x0, z0, x1 - x0 + 1, z1 - z0 + 1
        w, h = self.w, self.h
        bad = bytearray(w * h)          # cell cannot hold a building
        road = bytearray(w * h)         # street cell
        nodeck = bytearray(w * h)       # no deck here
        for j in range(h):
            z = z0 + j
            for i in range(w):
                x = x0 + i
                k = j * w + i
                if not PL.has_deck(x, z):
                    nodeck[k] = 1; bad[k] = 1; continue
                if roads.surface(x, z) is not None:
                    road[k] = 1; bad[k] = 1; continue
                rr = math.hypot(x, z)
                if not (r0 <= rr <= r1) or PL.district_at(x, z) != D:
                    bad[k] = 1
        # obstacles (inflated boxes)
        for (ox1, oz1, ox2, oz2) in obstacles:
            for z in range(max(oz1, z0), min(oz2, z1) + 1):
                row = (z - z0) * w
                a, c = max(ox1, x0) - x0, min(ox2, x1) - x0
                if a <= c: bad[row + a:row + c + 1] = b'\x01' * (c - a + 1)
        self.bad, self.road, self.nodeck = bad, road, nodeck
        self.P_bad = self._prefix(bad)
        self.P_road = self._prefix(road)
        self.P_nodeck = self._prefix(nodeck)
        self.taken = bytearray(w * h)

    def is_road(self, x, z):
        i, j = x - self.x0, z - self.z0
        if 0 <= i < self.w and 0 <= j < self.h: return self.road[j * self.w + i] == 1
        return roads.surface(x, z) is not None

    def _prefix(self, arr):
        w, h = self.w, self.h
        P_ = [0] * ((w + 1) * (h + 1))
        for j in range(h):
            run = 0
            base = (j + 1) * (w + 1)
            prev = j * (w + 1)
            for i in range(w):
                run += arr[j * w + i]
                P_[base + i + 1] = P_[prev + i + 1] + run
        return P_

    def _sum(self, P_, x1, z1, x2, z2):
        w = self.w
        x1 -= self.x0; x2 -= self.x0; z1 -= self.z0; z2 -= self.z0
        if x1 < 0 or z1 < 0 or x2 >= w or z2 >= self.h: return 1
        W1 = w + 1
        return P_[(z2 + 1) * W1 + x2 + 1] - P_[z1 * W1 + x2 + 1] - P_[(z2 + 1) * W1 + x1] + P_[z1 * W1 + x1]

    def fits(self, x1, z1, x2, z2):
        if self._sum(self.P_bad, x1, z1, x2, z2): return False
        if self._sum(self.P_road, x1 - 1, z1 - 1, x2 + 1, z2 + 1): return False
        if self._sum(self.P_nodeck, x1 - 3, z1 - 3, x2 + 3, z2 + 3): return False
        w = self.w
        for z in range(z1, z2 + 1):
            row = (z - self.z0) * w
            if self.taken.find(1, row + x1 - self.x0, row + x2 - self.x0 + 1) != -1: return False
        return True

    def take(self, x1, z1, x2, z2, gap=GAP):
        w = self.w
        for z in range(max(z1 - gap, self.z0), min(z2 + gap, self.z0 + self.h - 1) + 1):
            row = (z - self.z0) * w
            a = max(x1 - gap, self.x0) - self.x0
            c = min(x2 + gap, self.x0 + w - 1) - self.x0
            if a <= c: self.taken[row + a:row + c + 1] = b'\x01' * (c - a + 1)


_NEAR = {}


def _h(x, z, salt=0):
    """Cheap deterministic integer hash of a cell (not Python's hash())."""
    v = (x * 73856093 ^ z * 19349663 ^ salt * 83492791 ^ _CTX.seed * 2654435761) & 0xFFFFFFFF
    v ^= v >> 15; v = (v * 0x2C1B3C6D) & 0xFFFFFFFF
    v ^= v >> 12; v = (v * 0x297A2D39) & 0xFFFFFFFF
    v ^= v >> 15
    return v


def street_facing(grid, cx, cz, rv):
    """Facing (toward the nearest street cell along the 4 cardinal rays, within 70 blocks) of a lot centred (cx, cz)."""
    key = (cx // 3, cz // 3)
    f = _NEAR.get(key)
    if f is not None: return f
    best = None
    for fc in FACINGS:
        dx, dz = B.DIRS[fc]
        for d in range(4, 70, 2):
            if grid.is_road(cx + dx * d, cz + dz * d):
                if best is None or d < best[0] - 1: best = (d, fc)
                break
    f = best[1] if best else FACINGS[rv % 4]
    _NEAR[key] = f
    return f


def _obstacles(feats):
    obs = []
    for f in feats:
        bb = f.bbox
        if f.layer in (40, 50):
            g = GAP
        elif f.layer == 30 and f.name.startswith('roads_c_'):
            g = 1
        else:
            continue
        obs.append((bb[0] - g, bb[2] - g, bb[3] + g, bb[5] + g))
    return obs


# fixed placements: (design, district, centre x, centre z, facing)
SPECIAL = [
    ('guard_post', 'B', -21, -266, 'south'),
    ('guard_post', 'B', 21, -266, 'south'),
]


def plan_lots(feats):
    obstacles = _obstacles(feats)
    lots = []
    counter = {}

    def new_name(D, dn):
        k = counter.get((D, dn), 0); counter[(D, dn)] = k + 1
        return f'infill_{D.lower()}_{dn}_{k}'

    for D in DISTRICT_ORDER:
        grid = Grid(D, obstacles)
        pool = [d for d in DESIGNS.values() if D in d['districts']]

        def try_place(d, x, z, rv, shrink=True):
            """Place design d with its NW anchor at (x, z); returns a Lot or None."""
            bwr, bdr = d['bw'], d['bd']
            mw = (bwr[0] + rv // 7 % (bwr[1] - bwr[0] + 1)) | 1
            md = (bdr[0] + rv // 131 % (bdr[1] - bdr[0] + 1)) | 1
            if d['square']: md = mw
            sizes = [(mw, md)]
            if shrink:
                if d['square']: sizes += [(mw - 4, mw - 4), (bwr[0] | 1, bwr[0] | 1)]
                else: sizes += [(mw - 4, md), (mw, md - 4), (mw - 4, md - 4), (bwr[0] | 1, bdr[0] | 1)]
            for (tw, td) in sizes:
                if tw < bwr[0] or td < bdr[0]: continue
                if not grid.fits(x, z, x + tw + 1, z + td + 1): continue       # quick test (square orientation)
                fc = street_facing(grid, x + (tw + 2) // 2, z + (td + 2) // 2, rv)
                ww, dd = (tw + 2, td + 2) if fc in ('south', 'north') else (td + 2, tw + 2)
                x1, z1, x2, z2 = x, z, x + ww - 1, z + dd - 1
                if not grid.fits(x1, z1, x2, z2): continue
                cap = bbox_cap(x1, z1, x2, z2)
                hlo, hhi = d['hh']
                Hh = hlo + (rv // 977) % (hhi - hlo + 1)
                Hh = min(Hh, cap - 2 - d['extra'] - DECK - 1)
                if D == 'A': Hh = min(Hh, 40 - d['extra'] - 2)
                if Hh < hlo: continue
                grid.take(x1, z1, x2, z2)
                return Lot(new_name(D, d['name']), d['name'], D, (x1, DECK, z1, x2, design_top(d, Hh), z2), fc, Hh, tw, td)
            return None

        # ---- fixed lots
        for (dn, DD, cx, cz, fc) in SPECIAL:
            if DD != D: continue
            d = DESIGNS[dn]
            mw, md = d['bw'][0] | 1, d['bd'][0] | 1
            ww, dd = (mw + 2, md + 2) if fc in ('south', 'north') else (md + 2, mw + 2)
            x1, z1 = cx - ww // 2, cz - dd // 2
            x2, z2 = x1 + ww - 1, z1 + dd - 1
            cap = bbox_cap(x1, z1, x2, z2)
            Hh = min(d['hh'][1], cap - 2 - d['extra'] - DECK - 1)
            if Hh < d['hh'][0] or not grid.fits(x1, z1, x2, z2): continue
            grid.take(x1, z1, x2, z2)
            lots.append(Lot(new_name(D, dn), dn, D, (x1, DECK, z1, x2, design_top(d, Hh), z2), fc, Hh, mw, md))
        # ---- candidate anchors (non-bad cells), raster order
        x0, z0, wd, ht = grid.x0, grid.z0, grid.w, grid.h
        pos = [(x0 + i, z0 + j) for j in range(4, ht - 4) for i in range(4, wd - 4) if not grid.bad[j * wd + i]]
        free_area = len(pos)
        shuffled = sorted(pos, key=lambda p: _h(p[0], p[1], 11))
        # ---- design passes: big designs first, scattered, up to a quota of the district area
        feat = [d for d in pool if d.get('quota', 1) > 0]
        feat.sort(key=lambda d: -(d['bw'][0] * d['bd'][0]))
        wsum = sum(d['weight'] for d in feat)
        for d in feat:
            area = ((d['bw'][0] + d['bw'][1]) / 2 + 2 + GAP) * ((d['bd'][0] + d['bd'][1]) / 2 + 2 + GAP)
            quota = int(0.55 * free_area * d['weight'] / wsum / area + 0.5)
            n = 0
            for (x, z) in shuffled:
                if n >= quota: break
                if grid.taken[(z - z0) * wd + (x - x0)]: continue
                lot = try_place(d, x, z, _h(x, z, 5), shrink=False)
                if lot: lots.append(lot); n += 1
        # ---- fill pass: raster order, any design that fits
        fill = [d for d in pool if d.get('fill', True)]
        for (x, z) in pos:
            if grid.taken[(z - z0) * wd + (x - x0)]: continue
            rv = _h(x, z, 3)
            if rv % 23 == 0: continue                         # occasional pocket plaza
            order = sorted(fill, key=lambda d: (_h(x, z, 100 + hash_str(d['name']) % 997) % 1000) / d['weight'])
            for d in order:
                lot = try_place(d, x, z, rv)
                if lot: lots.append(lot); break
    return lots



# ------------------------------------------------------------------------------------------------ feature
_CACHE = {}


def draw_lot(lot, ctx=None):
    """Build the Plan of a lot (cached per process)."""
    ctx = ctx or _CTX
    p = _CACHE.get(lot.name)
    if p is not None: return p
    d = DESIGNS[lot.design]
    plan = Plan(lot.name, lot.bbox, lot.district, ctx)
    rng = _rng(ctx, lot.name)
    b = Bld(plan, lot.bbox, lot.facing, THEMES[lot.district], rng)
    S = d['draw'](b, lot.Hh, lot.mw, lot.md)
    if getattr(S, 'gate', False):
        pass
    else:
        _finish(b, S)
    plan.shell = S
    plan.b = b
    plan.finish()
    _CACHE[lot.name] = plan
    return plan


class Infill(Feature):
    layer = LAYER

    def __init__(self, lot):
        d = DESIGNS[lot.design]
        meta = {'kind': 'building', 'design': lot.design, 'district': lot.district, 'interior': True}
        super().__init__(lot.name, lot.bbox, LAYER, meta)
        self.lot = lot
        self._meta_done = False

    def plan(self, ctx=None):
        return draw_lot(self.lot, ctx)

    def ensure_meta(self):
        """entrances / interior box need the drawn shell; computed from lot geometry only (no drawing)."""
        return self.meta

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


def _entrances_of(lot):
    """City-local feet cells of the doors of a lot (without drawing it)."""
    d = DESIGNS[lot.design]
    b = Bld(Plan('probe', lot.bbox, lot.district, _CTX), lot.bbox, lot.facing, THEMES[lot.district], None)
    if lot.design == 'gatehouse':
        mw = lot.mw
        cu = (1 + mw) // 2
        vv = (1 + lot.md) // 2
        pts = [(cu - 4, vv), (cu + 4, vv)]
        return [(b.xz(u, v)[0], WALK, b.xz(u, v)[1]) for (u, v) in pts]
    # general: front-row centre of the mask
    mw, md = lot.mw, lot.md
    cu = (1 + mw) // 2
    vf = md - 1 if lot.design == 'colonnade_hall' else md
    x, z = b.xz(cu, vf)
    return [(x, WALK, z)]


def _interior_box(lot):
    x1, _, z1, x2, _, z2 = lot.bbox
    d = DESIGNS[lot.design]
    return (x1 + 2, WALK, z1 + 2, x2 - 2, DECK + lot.Hh, z2 - 2)


def make_features():
    feats = FT.registry()
    mine = plan_lots([f for f in feats if not f.name.startswith('infill_')])
    out = []
    for lot in mine:
        f = Infill(lot)
        f.meta['entrances'] = _entrances_of(lot)
        f.meta['interior_box'] = _interior_box(lot)
        f.meta['roof_y'] = DECK + lot.Hh + 1
        out.append(f)
    return out


def _import_deps():
    from . import generators
    for m in generators.GENERATORS:
        if m != __name__:
            importlib.import_module(m)


_import_deps()
FEATURES = make_features()
register(*FEATURES)


def features():
    return list(FEATURES)
