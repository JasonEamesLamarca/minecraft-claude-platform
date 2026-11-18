"""Layer 50: the Command & Strategic Planning centre (district K) and the Science & Research campus (district D).

DISTRICT K  (inner ring, plan angle 60, r 140..290)
  cmd_command_tower   curved stepped Atlantean tower on the clockwise side of the grand avenue: four tiers (r 26, 20, 14,
                      8), seven furnished floors, terraces with glass rails, fins, crown spire.  Floor 0-1 is the
                      Strategic Planning Hall (double height, holo-table ring, consoles, gallery); 2-3 briefing rooms;
                      4 briefing dais; 5 command bridge; 6 observation lounge + terraces.
  cmd_briefing_rotunda low drum on the other side of the avenue: two floors of briefing rooms around an atrium.
DISTRICT D  (pier at plan angle 60, r 310..490)
  sci_tower_alpha/beta/gamma   three research towers stepping down outward (heights follow the dome clearance),
  sci_wing_1/2                 low connected laboratory wings between the towers,
  sci_antenna                  control hut with an antenna array (masts, dish, beacons).

CONVENTIONS
  * Each landmark is a `Plan` (dict of cells) built once per process and copied into the tile column by column.
    The plan refuses every cell outside the feature bbox, outside its district, on a road cell or above
    ctx.max_building_top; `Plan.rejected` counts those (tests require 0).
  * Floors: slab at F = 4 + 6 i (i = level index; level 0 stands on the deck), walk y = F + 1, five air rows, the next
    slab at F + 6.  Stairs (3 wide, 6 long, east-going) link F to F + 6; the hole in the upper slab is x0+2..x0+5 (the walk model wants head room over the 3rd step too).
  * Light: ceiling sea-lantern floor inlays on terraces and roofs, hidden light[level=15] blocks on a 4-block lattice
    in the top air row of every level.
  * Plan angles come from atlantis.planner; local geometry angles run from east (+x) towards south (+z).
"""
import math

from . import config, planner as PL, roads, palette as P, blocks as B, geometry as G, architecture as A
from . import interior_kit as IK
from .feature import Feature, register
from .util import rng as _rng

LAYER = 50
DECK = PL.DECK_TOP
WALK = PL.WALK_Y
PITCH = 6

WALL = P.WHITE                    # smooth_quartz
BLOCK = P.WHITE_BLOCK
BRICKS = P.WHITE_BRICKS
PILLAR = 'quartz_pillar[axis=y]'
GLASS = P.GLASS_BLUE
CYAN = P.CYAN
LAMP = P.LAMP
RAIL = P.GLASS_BLUE_PANE
LIGHT = P.HIDDEN_LIGHT
DARKP = P.DARK_PRISMARINE

_CTX = config.load()
_SIN = math.sin
_COS = math.cos


# ------------------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------------------
def gang(dx, dz):
    """Geometry angle (0 = east, towards south) of a vector."""
    return math.degrees(math.atan2(dz, dx)) % 360.0


def angd(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def pol(cx, cz, r, a):
    t = math.radians(a)
    return int(math.floor(cx + r * _COS(t) + 0.5)), int(math.floor(cz + r * _SIN(t) + 0.5))


def axis_point(s, v, plan=60):
    """City-local (x, z) at `s` along the plan-angle axis and `v` across it (+ = clockwise side)."""
    ux, uz = PL.from_polar(1.0, plan)
    return s * ux - v * uz, s * uz + v * ux


def cap_min(cx, cz, R):
    pts = [(cx, cz)] + [(cx + R * _COS(math.radians(a)), cz + R * _SIN(math.radians(a))) for a in range(0, 360, 20)]
    return min(_CTX.max_building_top(x, z) for x, z in pts)


def rim_cap(cx, cz, R):
    return int(cap_min(cx, cz, R))


def bbox_cap(x1, z1, x2, z2):
    xm, zm = (x1 + x2) / 2.0, (z1 + z2) / 2.0
    pts = [(x, z) for x in (x1, xm, x2) for z in (z1, zm, z2)]
    return int(min(_CTX.max_building_top(x, z) for x, z in pts))


def seg_dist(x, z, ax, az, bx, bz):
    dx, dz = bx - ax, bz - az
    t = ((x - ax) * dx + (z - az) * dz) / float(dx * dx + dz * dz)
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return math.hypot(x - (ax + t * dx), z - (az + t * dz))


# ------------------------------------------------------------------------------------------------------------
# Plan: the cell store with hard limits
# ------------------------------------------------------------------------------------------------------------
class Plan:
    def __init__(self, name, bbox, district, ctx):
        self.name, self.bbox, self.district, self.ctx = name, bbox, district, ctx
        self.c = {}
        self.cols = {}
        self.rejected = 0
        self.first_rejects = []
        self.colinfo = {}
        self.occ = set()               # (x, F, z)
        self.res = set()               # reserved (x, F, z): furniture keeps out
        self.floors = set()            # (x, F, z) with a floor slab (or the deck)

    # ---- limits
    def cap(self, x, z):
        v = self.colinfo.get((x, z))
        if v is None:
            ok = PL.district_at(x, z) == self.district and roads.surface(x, z) is None
            v = self.colinfo[(x, z)] = int(self.ctx.max_building_top(x, z)) if ok else -999
        return v

    def set(self, x, y, z, b):
        bb = self.bbox
        if b is None: return
        if not (bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5]) or y > self.cap(x, z):
            self.rejected += 1
            if len(self.first_rejects) < 8: self.first_rejects.append((x, y, z, b))
            return
        self.c[(x, y, z)] = b

    def get(self, x, y, z):
        return self.c.get((x, y, z))

    def pop(self, x, y, z):
        self.c.pop((x, y, z), None)

    def fill(self, x1, y1, z1, x2, y2, z2, b):
        for y in range(min(y1, y2), max(y1, y2) + 1):
            for z in range(min(z1, z2), max(z1, z2) + 1):
                for x in range(min(x1, x2), max(x1, x2) + 1): self.set(x, y, z, b)

    # ---- resolve panes, index columns
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
        bb = self.bbox
        c = vol.clip(*bb)
        if not c: return
        cols = self.cols
        for x in range(c[0], c[3] + 1):
            for z in range(c[2], c[5] + 1):
                for y, b in cols.get((x, z), ()):
                    if c[1] <= y <= c[4]: vol.set(x, y, z, b)

    def bbox_of_cells(self):
        xs = [k[0] for k in self.c]; ys = [k[1] for k in self.c]; zs = [k[2] for k in self.c]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


class Lv:
    """A floor of a plan seen through the interior_kit furniture API."""

    def __init__(self, plan, F, cx, cz, rin, rng, style=None):
        self.p, self.F, self.W, self.cx, self.cz, self.rin, self.rng = plan, F, F + 1, cx, cz, rin, rng
        self.style = style or {}
        self.top = F + 5

    def m(self, key, default=None):
        return self.style.get(key, default)

    def free(self, cells):
        p, F, W = self.p, self.F, self.W
        for x, z in cells:
            if math.hypot(x - self.cx, z - self.cz) > self.rin - 0.6: return False
            k = (x, F, z)
            if k in p.occ or k in p.res or k not in p.floors: return False
            if (x, W, z) in p.c or (x, W + 1, z) in p.c: return False
        return True

    def set(self, x, y, z, b, occ=None):
        self.p.set(x, y, z, b)
        if occ is None: occ = self.W <= y <= self.W + 1
        if occ: self.p.occ.add((x, self.F, z))

    def get(self, x, y, z):
        return self.p.get(x, y, z)

    # polar helpers (relative to the tower centre)
    def at(self, r, a):
        return pol(self.cx, self.cz, r, a)

    def face_in(self, x, z):
        return IK.toward(x, z, self.cx, self.cz)

    def reserve(self, cells):
        for x, z in cells: self.p.res.add((x, self.F, z))


# ------------------------------------------------------------------------------------------------------------
# the round tower builder
# ------------------------------------------------------------------------------------------------------------
class Tower:
    """Spec of one tiered round tower.

    tiers   [(outer_r, n_levels)] bottom to top.  levels are numbered from 0; slab index m is at y 4 + 6 m.
    voids   {m: radius}: slab m has a round opening (double-height hall), railed on its first ring.
    stairs  {i: (x0, z0)} local, stair of level i (default pattern if missing).
    doors   [(level, geom_angle, width, height)]
    cuts    [(level, ax, az, bx, bz, w)] stadium interiors (wings) to open in the wall at level 0.
    crown   'spire' | 'mast' | 'dome' | None
    """

    def __init__(self, name, cx, cz, tiers, district, entrance=None, voids=None, stairs=None, doors=(), cuts=(),
                 crown='spire', fins=True, crown_top=None, stair_x=-5, lanterns=True):
        self.name, self.cx, self.cz, self.tiers, self.district = name, cx, cz, tiers, district
        self.entrance = entrance
        self.voids = voids or {}
        self.doors = list(doors)
        self.cuts = list(cuts)
        self.crown, self.fins, self.crown_top, self.lanterns = crown, fins, crown_top, lanterns
        self.levels = []
        for j, (r, n) in enumerate(tiers):
            for _ in range(n): self.levels.append({'i': len(self.levels), 'j': j, 'r': r})
        for lv in self.levels: lv['F'] = DECK + PITCH * lv['i']
        self.nl = len(self.levels)
        self.roof_F = DECK + PITCH * self.nl
        self.stair_x = stair_x
        self.stairs = {}
        for i in range(self.nl):
            if stairs and i in stairs: self.stairs[i] = stairs[i]
            else: self.stairs[i] = (-3, -3, 'east') if i % 2 == 0 else (3, 0, 'west')
        rmax = int(math.ceil(tiers[0][0]))
        top = self.roof_F + {'spire': 20, 'mast': 9, 'dome': 6, None: 1}.get(crown, 1)
        if crown_top: top = crown_top
        self.top = top
        self.bbox = (cx - rmax - 3, DECK, cz - rmax - 3, cx + rmax + 3, top, cz + rmax + 3)
        self.plan = None
        self.feats = []

    # ---- level lookups
    def rin(self, i): return self.levels[i]['r'] - 1.0

    def slab_radius(self, m):
        return self.levels[m - 1]['r'] + (1.5 if m == self.nl else 0.0)

    def stair_geom(self, i):
        """Stair i climbs from level i to i + 1 (3 wide, 6 steps).  Foot x0 (first step), direction east/west.
        Returns dict: steps [(x, s, z)], hole (cells in the upper slab: 3rd..6th step), exit, approach."""
        x0, z0, d = self.stairs[i]
        dx = 1 if d == 'east' else -1
        X0, Z0 = self.cx + x0, self.cz + z0
        steps = [(X0 + dx * s, s, Z0 + q) for s in range(6) for q in range(3)]
        hole = {(X0 + dx * s, Z0 + q) for s in (2, 3, 4, 5) for q in range(3)}
        ex = {(X0 + dx * 6, Z0 + q) for q in range(3)}
        ap = {(X0 - dx, Z0 + q) for q in range(3)}
        return {'dir': d, 'steps': steps, 'hole': hole, 'exit': ex, 'approach': ap, 'X0': X0, 'Z0': Z0, 'dx': dx}

    def hole_cells(self, i):
        return self.stair_geom(i)['hole']

    # ---- build
    def build(self, ctx):
        pl = Plan(self.name, self.bbox, self.district, ctx)
        self.plan = pl
        self._slabs(pl)
        self._walls(pl)
        self._stairs(pl)
        self._cuts_and_doors(pl)
        if self.fins: self._fins(pl)
        self._crown(pl)
        return pl

    def _slabs(self, pl):
        cx, cz = self.cx, self.cz
        holes = {m: set() for m in range(1, self.nl + 1)}
        for i in range(self.nl): holes[i + 1] |= self.hole_cells(i)
        for m in range(0, self.nl + 1):
            F = DECK + PITCH * m
            if m == 0:
                for x, z in G.disc_cells(cx, cz, self.levels[0]['r']): pl.floors.add((x, F, z))
                self._floor_pattern(pl)
                continue
            R = self.slab_radius(m)
            r_next = self.levels[m]['r'] if m < self.nl else 0
            vr = self.voids.get(m)
            for x, z in G.disc_cells(cx, cz, R):
                d = math.hypot(x - cx, z - cz)
                if (x, z) in holes[m]: continue
                if vr and d <= vr: continue
                terrace = d > r_next - 0.5 if m < self.nl else True
                if m < self.nl and d <= r_next - 1.0:
                    b = WALL
                elif terrace and d > r_next + 0.5 and m < self.nl:
                    b = BRICKS if int(d) % 2 else P.PALE_GREY
                else:
                    b = WALL
                pl.set(x, F, z, b)
                pl.floors.add((x, F, z))
            if m < self.nl:
                ring_t = (r_next, R)
                # glowing inlays on the terrace ring
                rm = (r_next + R) / 2.0
                n = max(6, int(2 * math.pi * rm / 4.0))
                for k in range(n):
                    x, z = pol(cx, cz, rm, 360.0 * k / n + 7.0)
                    if r_next < math.hypot(x - cx, z - cz) <= R - 1 and (x, z) not in holes[m] \
                            and self.levels[m - 1]['j'] != self.levels[m]['j']:
                        pl.set(x, F, z, LAMP)
                # glass rail on the outer edge of a terrace
                if self.levels[m - 1]['j'] != self.levels[m]['j']:
                    for x, z in G.ring_cells(cx, cz, R - 1.0, R): pl.set(x, F + 1, z, RAIL)
            else:
                for x, z in G.ring_cells(cx, cz, R - 1.0, R): pl.set(x, F, z, B.slab(WALL, 'top'))
                for x, z in G.ring_cells(cx, cz, R - 2.0, R - 1.0): pl.set(x, F + 1, z, RAIL)
                rl = max(2.5, R - 3.5)
                for k in range(6):
                    x, z = pol(cx, cz, rl, 60 * k + 30)
                    pl.set(x, F, z, LAMP)
            if vr:   # rail around the void
                for x, z in G.ring_cells(cx, cz, vr, vr + 1.0):
                    if (x, F, z) in pl.floors: pl.set(x, F + 1, z, RAIL)
        # accent course on every slab row of the wall, rail around holes
        for m in range(1, self.nl + 1):
            F = DECK + PITCH * m
            r = self.levels[m - 1]['r']
            for x, z in G.ring_cells(cx, cz, r - 1.0, r): pl.set(x, F, z, CYAN)
            if m < self.nl:
                r2 = self.levels[m]['r']
            i = m - 1
            if 0 <= i < self.nl:
                g = self.stair_geom(i)
                keep = set(g['exit'])
                if i + 1 < self.nl:
                    g2 = self.stair_geom(i + 1)
                    keep |= {(x, z) for (x, s, z) in g2['steps']} | g2['approach']
                for (x, z) in g['hole']:
                    for ddx, ddz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        n = (x + ddx, z + ddz)
                        if n in g['hole'] or n in keep or (n[0], F, n[1]) not in pl.floors: continue
                        pl.set(n[0], F + 1, n[1], RAIL)

    def _floor_pattern(self, pl):
        cx, cz = self.cx, self.cz
        r0 = self.levels[0]['r'] - 1.0
        for x, z in G.disc_cells(cx, cz, r0):
            d = math.hypot(x - cx, z - cz)
            a = gang(x - cx, z - cz)
            b = BLOCK
            if abs(d - 0.45 * r0) < 0.7 or abs(d - 0.8 * r0) < 0.6: b = CYAN
            elif d > r0 - 1.2: b = P.PALE_GREY
            elif (int(d) % 2 == 0) and (round(a / 15.0) % 2 == 0 or d < 3): b = BRICKS
            pl.set(x, DECK, z, b)
        n = 12
        for k in range(n):
            x, z = pol(cx, cz, 0.62 * r0, 360.0 * k / n + 15.0)
            pl.set(x, DECK, z, LAMP)

    def _pier(self, x, z, r, n_p):
        a = gang(x - self.cx, z - self.cz)
        seg = 2 * math.pi * r / n_p
        t = (a / 360.0 * n_p) % 1.0
        return min(t, 1.0 - t) * seg < 0.75

    def _walls(self, pl):
        cx, cz = self.cx, self.cz
        for lv in self.levels:
            r, F = lv['r'], lv['F']
            n_p = max(8, int(round(2 * math.pi * r / 6.0)))
            for x, z in G.ring_cells(cx, cz, r - 1.0, r):
                pier = self._pier(x, z, r, n_p)
                pl.set(x, F + 1, z, BLOCK)
                for y in (F + 2, F + 3, F + 4): pl.set(x, y, z, PILLAR if pier else GLASS)
                pl.set(x, F + 5, z, BRICKS)

    def _stairs(self, pl):
        for i in range(self.nl):
            F = DECK + PITCH * i
            g = self.stair_geom(i)
            for (x, st, z) in g['steps']:
                pl.set(x, F + 1 + st, z, B.stairs(WALL, g['dir']))
                for y in range(F + 1, F + 1 + st): pl.set(x, y, z, BLOCK)
            xs = [x for (x, st, z) in g['steps']]
            zs = [z for (x, st, z) in g['steps']]
            box = range(min(xs) - 2, max(xs) + 3), range(min(zs) - 1, max(zs) + 2)
            for x in box[0]:
                for z in box[1]:
                    pl.res.add((x, F, z)); pl.res.add((x, F + PITCH, z))

    def _cut(self, pl, lv, ang, width, height, along0=None, along1=None):
        cx, cz = self.cx, self.cz
        r, F = self.levels[lv]['r'], self.levels[lv]['F']
        ux, uz = _COS(math.radians(ang)), _SIN(math.radians(ang))
        a0 = r - 2.6 if along0 is None else along0
        a1 = r + 1.8 if along1 is None else along1
        n = int(r + 4)
        for x in range(cx - n, cx + n + 1):
            for z in range(cz - n, cz + n + 1):
                dx, dz = x - cx, z - cz
                al = dx * ux + dz * uz
                pp = abs(-dx * uz + dz * ux)
                if a0 <= al <= a1 and pp <= width / 2.0:
                    for y in range(F + 1, F + 1 + height): pl.pop(x, y, z)
                    pl.res.add((x, F, z))

    def _cuts_and_doors(self, pl):
        cx, cz = self.cx, self.cz
        for (lv, ang, w, h) in self.doors:
            self._cut(pl, lv, ang, w, h)
        if self.entrance is not None:
            ang = self.entrance
            r = self.levels[0]['r']
            self._cut(pl, 0, ang, 5, 4)
            ux, uz = _COS(math.radians(ang)), _SIN(math.radians(ang))
            # lane to the door stays free
            for al in range(6, int(r)):
                for pp in range(-3, 4):
                    x, z = int(math.floor(cx + al * ux - pp * uz + 0.5)), int(math.floor(cz + al * uz + pp * ux + 0.5))
                    pl.res.add((x, DECK, z))
            # porch: canopy, two pillars, glowing underside
            F = DECK
            for al in range(int(r) + 1, int(r) + 4):
                for pp in (-3, -2, -1, 0, 1, 2, 3):
                    x, z = int(math.floor(cx + al * ux - pp * uz + 0.5)), int(math.floor(cz + al * uz + pp * ux + 0.5))
                    pl.set(x, F + 5, z, B.slab(WALL, 'bottom'))
                    if abs(pp) == 3 and al in (int(r) + 1, int(r) + 3):
                        for y in range(F + 1, F + 5): pl.set(x, y, z, PILLAR)
                    elif al == int(r) + 2 and pp == 0:
                        pl.set(x, F + 5, z, LAMP)
            self.entrance_cell = (int(math.floor(cx + (r - 0.5) * ux + 0.5)), WALK, int(math.floor(cz + (r - 0.5) * uz + 0.5)))
        for (lv, ax, az, bx, bz, w) in self.cuts:
            r, F = self.levels[lv]['r'], self.levels[lv]['F']
            n = int(r + 3)
            for x in range(cx - n, cx + n + 1):
                for z in range(cz - n, cz + n + 1):
                    d = math.hypot(x - cx, z - cz)
                    if r - 1.0 < d <= r + 0.4 and seg_dist(x, z, ax, az, bx, bz) <= w - 1.0:
                        for y in range(F + 1, F + 5): pl.pop(x, y, z)
                        pl.res.add((x, F, z))

    def _fins(self, pl):
        cx, cz = self.cx, self.cz
        for lv_i, lv in enumerate(self.levels):
            if lv_i + 1 < self.nl and self.levels[lv_i + 1]['j'] == lv['j']: continue      # one run per tier
            j = lv['j']
            first = min(l['i'] for l in self.levels if l['j'] == j)
            r = lv['r']
            if j > 0 and self.tiers[j - 1][0] - r < 4.5: continue          # terrace too narrow for fins
            F0 = DECK + PITCH * first
            F1 = DECK + PITCH * (lv_i + 1)
            nf = max(6, int(round(2 * math.pi * r / 11.0)))
            for k in range(nf):
                a = 360.0 * (k + 0.5) / nf
                if self.entrance is not None and j == 0 and angd(a, self.entrance) < 360.0 / nf * 0.9: continue
                if any(angd(a, d[1]) < 360.0 / nf * 0.9 for d in self.doors if self.levels[d[0]]['j'] == j): continue
                x, z = pol(cx, cz, r + 0.9, a)
                if math.hypot(x - cx, z - cz) <= r + 0.2: continue
                if any(seg_dist(x, z, c[1], c[2], c[3], c[4]) <= c[5] + 1.0 for c in self.cuts): continue
                for y in range(F0 + 1, F1 + 1): pl.set(x, y, z, PILLAR)
                pl.set(x, F1 + 1, z, B.slab(WALL, 'bottom'))

    def _crown(self, pl):
        cx, cz = self.cx, self.cz
        F = self.roof_F
        r = self.levels[-1]['r']
        if self.crown == 'spire':
            for y in range(F + 1, F + 12): pl.set(cx, y, cz, PILLAR)
            for y in range(F + 12, F + 17): pl.set(cx, y, cz, 'end_rod[facing=up]' if y > F + 12 else PILLAR)
            pl.set(cx, F + 12, cz, LAMP)
            for k in range(6):
                a = 60 * k + 30
                x0, z0 = pol(cx, cz, min(6, r - 2), a)
                for (x, y, z) in G.line_cells((x0, F + 1, z0), (cx + (1 if k % 2 else -1) * 0, F + 11, cz), 1):
                    pl.set(x, y, z, BLOCK)
            for x, z in G.ring_cells(cx, cz, 2.2, 3.2):
                pl.set(x, F + 7, z, GLASS)
            for x, z in G.ring_cells(cx, cz, 1.2, 2.2):
                pl.set(x, F + 12, z, P.GLASS_CYAN)
            # arrival beacon ring on the cornice
        elif self.crown == 'mast':
            h = max(4, min(8, self.top - F - 1))
            for y in range(F + 1, F + h): pl.set(cx, y, cz, PILLAR)
            for y in range(F + h, self.top + 1): pl.set(cx, y, cz, 'end_rod[facing=up]')
            for x, z in G.ring_cells(cx, cz, 1.5, 2.5):
                pl.set(x, F + 2, z, B.slab(P.GLASS_CYAN, 'bottom') if False else P.GLASS_CYAN)
        elif self.crown == 'dome':        # ziggurat cap: stepped discs, glowing top
            for k in range(4):
                rr = r - 3 - 3 * k
                if rr < 1: break
                for x, z in G.disc_cells(cx, cz, rr):
                    pl.set(x, F + 1 + k, z, LAMP if (k == 0 and rr - 1 < math.hypot(x - cx, z - cz) and (x + z) % 4 == 0) else BLOCK)
            pl.set(cx, F + 5, cz, LAMP)
            pl.set(cx, F + 6, cz, 'end_rod[facing=up]')

    # ---- interior pass (called by the landmark with its furnishing function)
    def levels_lv(self, rng):
        return [Lv(self.plan, lv['F'], self.cx, self.cz, lv['r'] - 1.0, rng) for lv in self.levels]

    def lights(self, pl, extra_skip=()):
        cx, cz = self.cx, self.cz
        for lv in self.levels:
            F = lv['F']
            top = F + 5
            rin = lv['r'] - 1.0
            n = int(rin) + 1
            for dz in range(-n, n + 1):
                for dx in range(-n, n + 1):
                    if (dx + 2 * lv['i']) % 4 or (dz + lv['i']) % 4: continue
                    x, z = cx + dx, cz + dz
                    if math.hypot(dx, dz) > rin - 0.5: continue
                    if (x, top, z) in pl.c or (x, F + 4, z) in pl.c: continue
                    pl.set(x, top, z, LIGHT)
            # one light by each stair top so the landing is never dark
        # extra: lights next to rails are covered by the lattice


# ------------------------------------------------------------------------------------------------------------
# rooms: ring wall + radial walls with doorways
# ------------------------------------------------------------------------------------------------------------
def rooms(tw, lvl, r_core, wall_angles, door_angles, glass_rows=(2, 3)):
    pl = tw.plan
    lv = tw.levels[lvl]
    F, cx, cz = lv['F'], tw.cx, tw.cz
    rin = lv['r'] - 1.0

    def wall_col(x, z):
        if (x, F, z) not in pl.floors or (x, F, z) in pl.res: return
        if (x, F + 1, z) in pl.c: return
        for k, y in enumerate(range(F + 1, F + 6)):
            pl.set(x, y, z, GLASS if k in glass_rows else (BLOCK if k < 2 else BRICKS))
        pl.occ.add((x, F, z))

    for x, z in G.ring_cells(cx, cz, r_core - 1.0, r_core):
        wall_col(x, z)
    for wa in wall_angles:
        r = r_core
        while r < rin - 0.4:
            x, z = pol(cx, cz, r, wa)
            wall_col(x, z)
            x2, z2 = pol(cx, cz, r + 0.25, wa)
            wall_col(x2, z2)
            r += 0.5
    for da in door_angles:
        ux, uz = _COS(math.radians(da)), _SIN(math.radians(da))
        n = int(r_core + 3)
        for x in range(cx - n, cx + n + 1):
            for z in range(cz - n, cz + n + 1):
                dx, dz = x - cx, z - cz
                al = dx * ux + dz * uz
                pp = abs(-dx * uz + dz * ux)
                if r_core - 2.4 <= al <= r_core + 0.6 and pp <= 1.5:
                    for y in range(F + 1, F + 4): pl.pop(x, y, z)
                    pl.occ.discard((x, F, z))
                    pl.res.add((x, F, z))
                if r_core - 2.4 <= al <= r_core + 3.5 and pp <= 1.5: pl.res.add((x, F, z))
    # boundary check helper for callers
    return True


# ------------------------------------------------------------------------------------------------------------
# furnishing helpers
# ------------------------------------------------------------------------------------------------------------
def data_columns(lv, r, n, a0=0.0, kind='cool', h=4):
    for k in range(n):
        x, z = lv.at(r, a0 + 360.0 * k / n)
        IK.column_data(lv, x, z, h, kind)


def seat_ring(lv, r, n, a0=0.0, skip=(), inward=True, mat='quartz'):
    for k in range(n):
        a = a0 + 360.0 * k / n
        if any(angd(a, s) < 12 for s in skip): continue
        x, z = lv.at(r, a)
        f = lv.face_in(x, z)
        IK.chair(lv, x, z, f if inward else IK.opp(f), mat)


def console_ring(lv, r, n, a0=0.0, skip=(), width=3, inward=True):
    for k in range(n):
        a = a0 + 360.0 * k / n
        if any(angd(a, s) < 14 for s in skip): continue
        x, z = lv.at(r, a)
        f = lv.face_in(x, z)
        IK.console(lv, x, z, f if inward else IK.opp(f), width)


def desk_ring(lv, r, n, a0=0.0, skip=()):
    for k in range(n):
        a = a0 + 360.0 * k / n
        if any(angd(a, s) < 12 for s in skip): continue
        x, z = lv.at(r, a)
        f = lv.face_in(x, z)
        if IK.desk(lv, x, z, f, 3):
            dx, dz = B.DIRS[f]
            IK.chair(lv, x + dx * 2, z + dz * 2, IK.opp(f))


def planters(lv, r, n, a0=0.0, skip=()):
    for k in range(n):
        a = a0 + 360.0 * k / n
        if any(angd(a, s) < 10 for s in skip): continue
        x, z = lv.at(r, a)
        IK.planter(lv, x, z, lv.rng.choice(['azalea', 'flowering_azalea']))


def holo_column(lv, h=9):
    """Hologram over a table: glowing core rod, floating rings."""
    W, cx, cz = lv.W, lv.cx, lv.cz
    for y in range(W + 2, W + 2 + h): lv.set(cx, y, cz, GLASS, occ=False)
    lv.set(cx, W + 2 + h, cz, 'end_rod[facing=up]', occ=False)
    for dy, (r0, r1) in ((3, (3.0, 4.0)), (6, (2.0, 3.0))):
        for x, z in G.ring_cells(cx, cz, r0, r1): lv.set(x, W + 2 + dy, z, P.GLASS_CYAN, occ=False)


def lab_level(lv, kind, skip=(), rng=None):
    """Research floor: benches round the wall, central island, cases and data columns."""
    r = lv.rin
    n = max(6, int(2 * math.pi * (r - 2.2) / 5.0))
    for k in range(n):
        a = 360.0 * k / n + 8
        if any(angd(a, s) < 14 for s in skip): continue
        x, z = lv.at(r - 2.0, a)
        f = lv.face_in(x, z)
        if kind == 'server' and k % 2 == 0:
            if lv.free([(x, z)]):
                lv.set(x, lv.W, z, DARKP)
                lv.set(x, lv.W + 1, z, P.GLASS_CYAN)
                lv.set(x, lv.W + 2, z, DARKP)
        elif kind == 'office':
            IK.desk(lv, x, z, f, 3)
            dx, dz = B.DIRS[f]
            IK.chair(lv, x + dx * 2, z + dz * 2, IK.opp(f))
        else:
            IK.bench_lab(lv, x, z, f, 3, lv.rng)
    cx, cz = lv.cx, lv.cz
    if kind == 'lab':
        IK.table_round(lv, cx + 2, cz, 1.6, lamp=True)
        for dx, dz in ((3, -3), (3, 4), (-3, 0)):
            IK.case(lv, cx + dx + 2, cz + dz, 'verdant_froglight')
        data_columns(lv, max(2.5, r * 0.55), 3, 60, 'green', 3)
    elif kind == 'server':
        data_columns(lv, max(2.5, r * 0.5), 4, 45, 'cool', 4)
        IK.map_table(lv, cx + 3, cz, 2.2)
    elif kind == 'office':
        IK.table_round(lv, cx + 2, cz, 2.0, seats=6, lamp=True)
        planters(lv, r - 0.6, 5, 20)
    elif kind == 'lounge':
        seat_ring(lv, max(3.0, r * 0.55), 8, 0, inward=False)
        data_columns(lv, max(2.0, r * 0.3), 3, 90, 'cool', 4)
        planters(lv, r - 0.8, 6, 15)



def fill_level(lv, r0, r1, mix, arc=3.6, ring_step=3.0, a_off=0.0, skip=()):
    """Populate whatever is still free between radius r0 and r1 with a repeating mix of furniture modules, so that no
    room of a command-tower floor stays bare.  Modules refuse to overlap walls, reserved lanes and each other."""
    n_placed = 0
    r = r0
    row = 0
    while r <= r1:
        n = max(4, int(2 * math.pi * r / arc))
        for k in range(n):
            a = 360.0 * (k + 0.5 * (row % 2)) / n + a_off
            if any(angd(a, sk) < 12 for sk in skip): continue
            x, z = lv.at(r, a)
            f = lv.face_in(x, z)
            m = mix[(k + 2 * row) % len(mix)]
            ok = False
            if m == 'desk':
                ok = IK.desk(lv, x, z, f, 3)
                if ok:
                    dx, dz = B.DIRS[f]
                    IK.chair(lv, x + dx * 2, z + dz * 2, IK.opp(f))
            elif m == 'table': ok = IK.table_round(lv, x, z, 1.3, seats=4, lamp=True)
            elif m == 'console': ok = IK.console(lv, x, z, IK.opp(f), 3)
            elif m == 'column': ok = IK.column_data(lv, x, z, 4, 'cool')
            elif m == 'planter': ok = IK.planter(lv, x, z, lv.rng.choice(['azalea', 'flowering_azalea']))
            elif m == 'shelf': ok = IK.shelf(lv, x, z, 3)
            elif m == 'seat': ok = IK.chair(lv, x, z, f, 'quartz')
            elif m == 'case': ok = IK.case(lv, x, z, 'verdant_froglight')
            elif m == 'bench': ok = IK.bench_lab(lv, x, z, f, 3, lv.rng)
            if ok: n_placed += 1
        r += ring_step
        row += 1
    return n_placed


# ------------------------------------------------------------------------------------------------------------
# SPECIALISED RESEARCH LABORATORIES (district D)
#   xeno      Xenobiology: specimen cases (glass + coloured blocks), terrariums, dissection benches
#   energy    Energy research: glowing crystal rigs on copper plinths, copper conduits in the floor, containment cases
#   astro     Astrophysics & cartography: dark star-floor (sea lantern stars), orrery, telescopes, chart tables
#   materials Materials lab: workstations (anvils, smithing tables, furnaces, grindstones) as instruments
#   archive   Atlantean database / archive: bookshelves, lecterns, reading tables, holo-table, data columns
# ------------------------------------------------------------------------------------------------------------
LAB_KINDS = ('xeno', 'energy', 'astro', 'materials', 'archive')


def _hash2(x, z, salt=0):
    v = (x * 73856093 ^ z * 19349663 ^ salt * 83492791) & 0xFFFFFFFF
    v ^= v >> 13
    v = (v * 0x5bd1e995) & 0xFFFFFFFF
    return v ^ (v >> 15)


def _perp_face(f):
    return B.ORDER[(B.ORDER.index(f) + 1) % 4]


def restyle_floor(lv, fn):
    """Replace the floor blocks of a level (inside its wall) by fn(x, z, d, a) (None keeps the block)."""
    p = lv.p
    for x, z in G.disc_cells(lv.cx, lv.cz, lv.rin - 0.4):
        k = (x, lv.F, z)
        if k not in p.floors or k not in p.c: continue
        b = fn(x, z, math.hypot(x - lv.cx, z - lv.cz), gang(x - lv.cx, z - lv.cz))
        if b: p.c[k] = b


def _row3(x, z, f, n=3):
    px, pz = IK.perp(f)
    lo = -((n - 1) // 2)
    return [(x + px * k, z + pz * k) for k in range(lo, lo + n)]


def station(lv, x, z, f, items):
    """A workbench: len(items) instruments side by side along the perpendicular of f (they stand on the floor)."""
    cells = _row3(x, z, f, len(items))
    if not lv.free(cells): return False
    for (cx, cz), b in zip(cells, items):
        lv.set(cx, lv.W, cz, b)
    return True


def ring_stations(lv, r, n, a0, make, skip=()):
    placed = 0
    for k in range(n):
        a = a0 + 360.0 * k / n
        if any(angd(a, s) < 14 for s in skip): continue
        x, z = lv.at(r, a)
        if make(lv, x, z, lv.face_in(x, z), k): placed += 1
    return placed


# ---- xenobiology ----------------------------------------------------------------------------------------------
_SPECIMENS = [('slime_block', 'lime_stained_glass'), ('honey_block', 'orange_stained_glass'),
              ('warped_wart_block', 'cyan_stained_glass'), ('shroomlight', 'yellow_stained_glass'),
              ('purple_concrete', 'purple_stained_glass'), ('moss_block', 'green_stained_glass'),
              ('red_concrete', 'red_stained_glass'), ('pink_concrete', 'pink_stained_glass')]


def specimen_case(lv, x, z, k):
    """Case: dark base, a coloured specimen block, two panes of tinted glass, a lit cap."""
    if not lv.free([(x, z)]): return False
    spec, glass = _SPECIMENS[k % len(_SPECIMENS)]
    W = lv.W
    lv.set(x, W, z, P.DARK_PRISMARINE)
    lv.set(x, W + 1, z, spec)
    lv.set(x, W + 2, z, glass)
    lv.set(x, W + 3, z, glass)
    lv.set(x, W + 4, z, LAMP if k % 3 == 0 else P.WHITE)
    return True


def terrarium(lv, x, z):
    cells = [(x + dx, z + dz) for dx in (0, 1) for dz in (0, 1)]
    if not lv.free(cells): return False
    for i, (cx, cz) in enumerate(cells):
        lv.set(cx, lv.W, cz, 'moss_block')
        lv.set(cx, lv.W + 1, cz, ('azalea', 'flowering_azalea', 'azalea', 'moss_carpet')[i])
    return True


def lab_xeno(lv, skip=()):
    r = lv.rin
    restyle_floor(lv, lambda x, z, d, a: 'prismarine_bricks' if d > r - 1.3 else 'green_concrete' if abs(d - 0.6 * r) < 0.5
                  else 'prismarine' if (x + z) % 4 == 0 else None)
    n = max(8, int(2 * math.pi * (r - 1.6) / 2.4))
    ring_stations(lv, r - 1.4, n, 4.0, lambda l, x, z, f, k: specimen_case(l, x, z, k), skip)
    n2 = max(5, int(2 * math.pi * (r * 0.62) / 4.5))
    ring_stations(lv, r * 0.62, n2, 20.0, lambda l, x, z, f, k: specimen_case(l, x, z, k + 3) if k % 2 == 0 else
                  IK.bench_lab(l, x, z, f, 3, l.rng), skip)
    for a in range(0, 360, 60):
        x, z = lv.at(r - 3.4, a + 30)
        terrarium(lv, x, z)
    data_columns(lv, max(2.5, r * 0.35), 3, 90, 'green', 3)


# ---- energy research ------------------------------------------------------------------------------------------
def crystal_rig(lv, x, z):
    """3x3 copper plinth, amethyst / sea-lantern core up to the ceiling, lightning-rod antennae, an end-rod tip."""
    cells = [(x + dx, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    if not lv.free(cells): return False
    W = lv.W
    for (cx, cz) in cells: lv.set(cx, W, cz, 'waxed_oxidized_cut_copper')
    lv.set(x, W, z, 'waxed_oxidized_copper')
    for i, b in enumerate([LAMP, 'amethyst_block', LAMP, 'amethyst_block']): lv.set(x, W + 1 + i, z, b)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        lv.set(x + dx, W + 1, z + dz, 'waxed_oxidized_cut_copper')
        lv.set(x + dx, W + 2, z + dz, 'lightning_rod[facing=up,powered=false,waterlogged=false]')
    return True


def lab_energy(lv, skip=()):
    r = lv.rin
    def conduits(x, z, d, a):
        if d > 3.5 and (round(a / 45.0) * 45 - a) ** 2 * d * d < 0.8: return 'waxed_oxidized_copper'
        if abs(d - 0.55 * r) < 0.5: return CYAN
        return 'polished_deepslate' if (x + z) % 3 else 'deepslate_tiles'
    restyle_floor(lv, conduits)
    crystals = 0
    for a in (45, 135, 225, 315, 0, 90, 180, 270):
        x, z = lv.at(r * 0.55, a)
        if crystal_rig(lv, x, z): crystals += 1
        if crystals >= 4: break
    n = max(6, int(2 * math.pi * (r - 1.8) / 5.0))
    ring_stations(lv, r - 1.6, n, 8.0, lambda l, x, z, f, k: IK.console(l, x, z, f, 3) if k % 2 == 0 else
                  IK.case(l, x, z, 'ochre_froglight'), skip)
    data_columns(lv, max(2.5, r * 0.3), 3, 60, 'warm', 3)


# ---- astrophysics / cartography -------------------------------------------------------------------------------
def orrery(lv, x, z):
    cells = [(x + dx, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
    if not lv.free(cells): return False
    W = lv.W
    lv.set(x, W, z, 'black_concrete')
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)): lv.set(x + dx, W, z + dz, 'polished_blackstone_slab[type=bottom]')
    lv.set(x, W + 1, z, 'end_rod[facing=up]')
    lv.set(x, W + 2, z, 'end_rod[facing=up]')
    lv.set(x, W + 3, z, LAMP)
    for (dx, dz), b in zip(((1, 0), (-1, 0), (0, 1), (0, -1)),
                           ('orange_concrete', 'light_blue_concrete', P.GLASS_CYAN, P.GLASS_CYAN)):
        lv.set(x + dx, W + 3, z + dz, b)
    for dx, dz in ((1, 1), (-1, -1), (1, -1), (-1, 1)): lv.set(x + dx, W + 3, z + dz, P.GLASS_CYAN)
    lv.set(x, W + 4, z, 'end_rod[facing=up]')
    return True


def telescope(lv, x, z, f_out):
    dx, dz = B.DIRS[f_out]
    if not lv.free([(x, z), (x + dx, z + dz)]): return False
    W = lv.W
    lv.set(x, W, z, PILLAR)
    lv.set(x, W + 1, z, 'iron_block')
    lv.set(x + dx, W + 1, z + dz, f'lightning_rod[facing={f_out},powered=false,waterlogged=false]')
    lv.p.occ.add((x + dx, lv.F, z + dz))
    return True


def lab_astro(lv, skip=()):
    r = lv.rin
    def star_floor(x, z, d, a):
        if d > r - 1.3: return 'polished_blackstone'
        if _hash2(x, z, 7) % 29 == 0: return LAMP
        if abs(d - 0.5 * r) < 0.45: return 'blue_concrete'
        return 'black_concrete'
    restyle_floor(lv, star_floor)
    for a in (35, 215, 125):
        x, z = lv.at(r * 0.58, a)
        orrery(lv, x, z)
    n = max(6, int(2 * math.pi * (r - 1.8) / 4.0))
    ring_stations(lv, r - 1.4, n, 6.0, lambda l, x, z, f, k: telescope(l, x, z, IK.opp(f)) if k % 2 == 0 else
                  station(l, x, z, f, ['cartography_table', 'lectern[facing=%s,has_book=false,powered=false]' % f,
                                       'cartography_table']), skip)
    for a in range(0, 360, 72):                           # constellation poles
        x, z = lv.at(r - 3.2, a + 36)
        if lv.free([(x, z)]):
            lv.set(x, lv.W, z, 'polished_blackstone')
            lv.set(x, lv.W + 1, z, 'end_rod[facing=up]')
            lv.set(x, lv.W + 2, z, 'end_rod[facing=up]')
    data_columns(lv, max(2.5, r * 0.3), 3, 100, 'white', 4)


# ---- materials lab --------------------------------------------------------------------------------------------
def _mat_station(l, x, z, f, k):
    pf = _perp_face(f)
    sets = [['blast_furnace[facing=%s,lit=true]' % f, 'smithing_table', 'anvil[facing=%s]' % pf],
            ['grindstone[face=floor,facing=%s]' % f, 'stonecutter[facing=%s]' % f, 'iron_block'],
            ['furnace[facing=%s,lit=true]' % f, 'cauldron', 'blast_furnace[facing=%s,lit=true]' % f],
            ['anvil[facing=%s]' % pf, 'fletching_table', 'smithing_table']]
    return station(l, x, z, f, sets[k % len(sets)])


def lab_materials(lv, skip=()):
    r = lv.rin
    restyle_floor(lv, lambda x, z, d, a: 'iron_block' if abs(d - 0.5 * r) < 0.5 else
                  'deepslate_tiles' if (x + z) % 2 else 'polished_deepslate')
    n = max(6, int(2 * math.pi * (r - 1.8) / 4.2))
    ring_stations(lv, r - 1.4, n, 6.0, _mat_station, skip)
    n2 = max(4, int(2 * math.pi * (r * 0.58) / 5.0))
    ring_stations(lv, r * 0.58, n2, 28.0, lambda l, x, z, f, k: _mat_station(l, x, z, f, k + 1), skip)
    data_columns(lv, max(2.5, r * 0.3), 3, 90, 'warm', 3)


# ---- Atlantean database / archive -------------------------------------------------------------------------------
def lab_archive(lv, skip=()):
    r = lv.rin
    restyle_floor(lv, lambda x, z, d, a: 'polished_andesite' if int(d) % 2 else 'smooth_stone')
    n = max(10, int(2 * math.pi * (r - 1.0) / 1.2))
    for k in range(n):                                    # wall stacks of bookshelves, 3 high, with gaps
        a = 360.0 * k / n + 3.0
        if any(angd(a, s) < 16 for s in skip): continue
        if k % 6 == 5: continue
        x, z = lv.at(r - 0.9, a)
        if not lv.free([(x, z)]): continue
        for h in range(3):
            lv.set(x, lv.W + h, z, 'chiseled_bookshelf[facing=%s]' % lv.face_in(x, z) if h == 2 and k % 2 else 'bookshelf',
                   occ=(h == 0))
    ring_stations(lv, r - 3.2, max(6, int(2 * math.pi * (r - 3.2) / 4.5)), 14.0,
                  lambda l, x, z, f, k: station(l, x, z, f, ['lectern[facing=%s,has_book=false,powered=false]' % f] * 2)
                  if k % 2 == 0 else IK.table_round(l, x, z, 1.4, seats=4, lamp=True), skip)
    IK.map_table(lv, lv.cx + 3, lv.cz + 4, 1.8)
    data_columns(lv, max(2.5, r * 0.32), 4, 45, 'warm', 4)


LAB_FN = {'xeno': lab_xeno, 'energy': lab_energy, 'astro': lab_astro, 'materials': lab_materials,
          'archive': lab_archive}


# ------------------------------------------------------------------------------------------------------------
# DISTRICT K: command tower + briefing rotunda
# ------------------------------------------------------------------------------------------------------------
_K_U, _K_V = 215.0, 50.0
KC = tuple(int(round(v)) for v in axis_point(_K_U, _K_V))
_ux, _uz = PL.from_polar(1.0, 60)
_VX, _VZ = -_uz, _ux                                                    # unit vector of +v
K_ENTR = gang(-_VX, -_VZ)                                               # geometry angle pointing at the avenue

_K_TOWER_TIERS = [(26, 2), (20, 2), (14, 2), (8, 1)]


def make_command_tower():
    cx, cz = KC
    tw = Tower('cmd_command_tower', cx, cz, _K_TOWER_TIERS, 'K', entrance=K_ENTR, voids={1: 11},
               stairs={0: (-23, -1, 'east'), 1: (-17, 2, 'east'), 2: (-11, 5, 'east'), 3: (-5, 8, 'east'), 4: (1, 5, 'west'),
                       5: (-5, 2, 'east'), 6: (1, -1, 'west')},
               doors=[(2, 0, 5, 4), (4, 0, 5, 4), (6, 0, 5, 4)], crown='spire')
    return tw


def furnish_command(tw, rng):
    pl = tw.plan
    cx, cz = tw.cx, tw.cz
    L = tw.levels_lv(rng)
    # level 0 + 1: Strategic Planning Hall
    h0, h1 = L[0], L[1]
    h0.reserve([])
    IK.map_table(h0, cx, cz, 5.5)
    holo_column(h0, 8)
    console_ring(h0, 10.3, 12, 15.0, skip=(K_ENTR,))
    data_columns(h0, 15.5, 12, 30.0, 'cool', 4)
    desk_ring(h0, 21.0, 16, 11.0, skip=(K_ENTR, 180))
    planters(h0, 23.5, 12, 0.0, skip=(K_ENTR,))
    # gallery: benches on the rim, consoles, lecterns
    seat_ring(h1, 22.8, 36, 0.0, inward=True, mat='quartz')
    console_ring(h1, 16.0, 8, 22.5, width=3)
    data_columns(h1, 13.6, 8, 0.0, 'cool', 4)
    planters(h1, 20.0, 8, 22.5)
    # level 2/3: briefing rooms
    rooms(tw, 2, 8, (90, 210, 330), (30, 150, 270))
    rooms(tw, 3, 8, (45, 135, 225, 315), (0, 90, 180, 270))
    for lvn, centres in ((2, (30, 150, 270)), (3, (0, 90, 180, 270))):
        lv = L[lvn]
        IK.table_round(lv, cx, cz, 2.6, seats=8, lamp=True)
        for a in centres:
            x, z = lv.at(13.5, a)
            IK.table_round(lv, x, z, 2.0, seats=6, lamp=True)
            x, z = lv.at(10.6, a + 40)
            IK.column_data(lv, x, z, 3, 'cool')
            x, z = lv.at(17.0, a - 25)
            IK.planter(lv, x, z)
            x, z = lv.at(11.0, a - 40)
            IK.desk(lv, x, z, lv.face_in(x, z), 3)
    # level 4: briefing dais
    d = L[4]
    IK.map_table(d, cx, cz, 3.2)
    holo_column(d, 5)
    seat_ring(d, 7.2, 14, 5.0)
    data_columns(d, 10.3, 6, 30.0, 'cool', 3)
    planters(d, 11.4, 8, 0.0)
    # level 5: command bridge
    b = L[5]
    for a in (-70, -45, -22, 0, 22, 45, 70):
        x, z = b.at(9.2, 180 + a)           # consoles on the west, operators looking out east through the glass? no:
        IK.console(b, x, z, b.face_in(x, z), 3)
    IK.chair(b, cx + 1, cz + 3, 'west', 'quartz')
    IK.table_round(b, cx + 2, cz - 1, 1.6, lamp=True)
    data_columns(b, 11.2, 5, 70.0, 'cool', 3)
    # level 6: observation lounge
    o = L[6]
    seat_ring(o, 5.0, 14, 0.0, inward=False)
    IK.column_data(o, cx, cz, 4, 'cool')
    planters(o, 6.2, 6, 20.0)
    # no bare rooms: fill what is left of every floor
    entr = (K_ENTR,)
    fill_level(h0, 12.5, 22.5, ['table', 'desk', 'column', 'planter', 'seat', 'console', 'case'], 3.2, 2.5, 5.0, skip=entr)
    fill_level(h1, 12.5, 22.5, ['seat', 'column', 'console', 'planter', 'table', 'desk'], 3.2, 2.5, 9.0)
    for lvn in (2, 3):
        fill_level(L[lvn], 9.8, 17.6, ['desk', 'table', 'shelf', 'column', 'planter', 'console', 'seat', 'case'], 3.2, 2.6, 7.0 * lvn)
    fill_level(L[4], 4.8, 11.4, ['console', 'seat', 'column', 'desk', 'planter', 'table'], 3.2, 2.6, 3.0)
    fill_level(L[5], 3.0, 11.2, ['console', 'desk', 'column', 'seat', 'table', 'planter'], 3.2, 2.6, 11.0)
    fill_level(L[6], 2.5, 5.2, ['seat', 'planter', 'column', 'table'], 3.0, 2.2, 4.0)


def build_command_tower(ctx):
    tw = make_command_tower()
    pl = tw.build(ctx)
    furnish_command(tw, _rng(ctx, tw.name))
    tw.lights(pl)
    pl.finish()
    return tw


def _rot_make():
    cx, cz = (int(round(v)) for v in axis_point(205.0, -46.0))
    tw = Tower('cmd_briefing_rotunda', cx, cz, [(17, 2)], 'K', entrance=gang(_VX, _VZ),
               crown='mast', fins=True)
    return tw


def furnish_rotunda(tw, rng):
    cx, cz = tw.cx, tw.cz
    L = tw.levels_lv(rng)
    rooms(tw, 0, 9, (0, 60, 120, 180, 240, 300), (30, 90, 150, 210, 270, 330))
    rooms(tw, 1, 9, (30, 90, 150, 210, 270, 330), (0, 60, 120, 180, 240, 300))
    for lvn in (0, 1):
        lv = L[lvn]
        for a in ((30, 90, 150, 210, 270, 330) if lvn == 0 else (0, 60, 120, 180, 240, 300)):
            x, z = lv.at(12.5, a)
            IK.table_round(lv, x, z, 1.8, seats=5, lamp=True)
            x, z = lv.at(14.6, a + 18)
            IK.column_data(lv, x, z, 3, 'cool')
            x, z = lv.at(14.6, a - 18)
            IK.planter(lv, x, z)
        IK.table_round(lv, cx + 3, cz, 1.5, seats=4, lamp=True)
        data_columns(lv, 6.2, 3, 150.0, 'cool', 3)
        fill_level(lv, 3.0, 7.2, ['table', 'column', 'seat', 'planter', 'desk'], 3.2, 2.4, 5.0)
        fill_level(lv, 10.2, 15.2, ['desk', 'seat', 'shelf', 'column', 'table', 'planter', 'console'], 3.2, 2.6, 9.0)


def build_rotunda(ctx):
    tw = _rot_make()
    pl = tw.build(ctx)
    furnish_rotunda(tw, _rng(ctx, tw.name))
    tw.lights(pl)
    pl.finish()
    return tw


# ------------------------------------------------------------------------------------------------------------
# DISTRICT D: research campus
# ------------------------------------------------------------------------------------------------------------
def _levels_for(cx, cz, R, crown_h, minimum=2):
    cap = rim_cap(cx, cz, R + 3)
    return max(minimum, (cap - DECK - crown_h - 1) // PITCH)


_D_SPECS = [  # name, s, v, r, upper_r, crown, lab kinds
    ('sci_tower_alpha', 352.0, 40.0, 11.0, 8.5, 'spire'),
    ('sci_tower_beta', 412.0, 34.0, 9.0, 7.0, 'mast'),
    ('sci_tower_gamma', 462.0, 36.0, 7.5, None, 'mast'),
]
D_CENTERS = {}
for _n, _s, _v, _r, _ur, _cr in _D_SPECS:
    D_CENTERS[_n] = tuple(int(round(c)) for c in axis_point(_s, _v))

_D_ENTR = gang(-_VX, -_VZ)           # towers face the avenue


def _d_tiers(name, r, ur, cx, cz, crown):
    ch = {'spire': 18, 'mast': 9}[crown]
    n = _levels_for(cx, cz, r, ch, 3)
    if ur is None: return [(r, n)]
    k = max(1, n // 3)
    return [(r, n - k), (ur, k)]


def make_sci_tower(spec):
    name, s, v, r, ur, crown = spec
    cx, cz = D_CENTERS[name]
    tiers = _d_tiers(name, r, ur, cx, cz, crown)
    nl = sum(n for _, n in tiers)
    doors = []
    if len(tiers) > 1:
        doors.append((tiers[0][1], 0, 5, 4))                      # to the terrace of the lower tier
    cuts = []
    for w in WINGS:
        if name in (w['a'], w['b']):
            a = D_CENTERS[w['a']]; b = D_CENTERS[w['b']]
            cuts.append((0, a[0], a[1], b[0], b[1], w['w']))
            cuts.append((1, a[0], a[1], b[0], b[1], w['w']))
    top = DECK + PITCH * nl + (18 if crown == 'spire' else 9)
    tw = Tower(name, cx, cz, tiers, 'D', entrance=_D_ENTR, doors=doors, cuts=cuts, crown=crown, crown_top=top,
               fins=True)
    return tw


WINGS = [{'name': 'sci_wing_1', 'a': 'sci_tower_alpha', 'b': 'sci_tower_beta', 'w': 5.5},
         {'name': 'sci_wing_2', 'a': 'sci_tower_beta', 'b': 'sci_tower_gamma', 'w': 5.5}]


TOWER_LABS = {
    'sci_tower_alpha': ['lobby', 'xeno', 'energy', 'astro', 'materials', 'archive'],
    'sci_tower_beta': ['lobby', 'materials', 'archive', 'xeno', 'astro'],
    'sci_tower_gamma': ['lobby', 'energy', 'astro'],
}


def furnish_sci_tower(tw, rng):
    L = tw.levels_lv(rng)
    kinds = TOWER_LABS[tw.name]
    for i, lv in enumerate(L):
        kind = kinds[i % len(kinds)]
        skip = (_D_ENTR,) if i == 0 else ()
        if kind == 'lobby':
            lab_level(lv, 'lab', skip, rng)
            IK.map_table(lv, tw.cx + 2, tw.cz, 2.0)           # holo map on the lobby floor
        else:
            LAB_FN[kind](lv, skip)
    tw.lab_kinds = [k for k in kinds if k != 'lobby']


def build_sci_tower(ctx, spec):
    tw = make_sci_tower(spec)
    pl = tw.build(ctx)
    furnish_sci_tower(tw, _rng(ctx, tw.name))
    tw.lights(pl)
    pl.finish()
    return tw


# ---- wings (stadium shaped low labs)
class Wing:
    def __init__(self, spec):
        self.name = spec['name']
        self.a, self.b, self.w = D_CENTERS[spec['a']], D_CENTERS[spec['b']], spec['w']
        self.ra = next(s[3] for s in _D_SPECS if s[0] == spec['a'])
        self.rb = next(s[3] for s in _D_SPECS if s[0] == spec['b'])
        w = int(self.w) + 2
        self.bbox = (min(self.a[0], self.b[0]) - w, DECK, min(self.a[1], self.b[1]) - w,
                     max(self.a[0], self.b[0]) + w, DECK + 7, max(self.a[1], self.b[1]) + w)

    def in_towers(self, x, z):
        return math.hypot(x - self.a[0], z - self.a[1]) <= self.ra or \
            math.hypot(x - self.b[0], z - self.b[1]) <= self.rb

    def build(self, ctx):
        pl = Plan(self.name, self.bbox, 'D', ctx)
        rng = _rng(ctx, self.name)
        ax, az = self.a; bx, bz = self.b
        w = self.w
        F = DECK
        L = math.hypot(bx - ax, bz - az)
        ux, uz = (bx - ax) / L, (bz - az) / L
        x1, z1, x2, z2 = self.bbox[0], self.bbox[2], self.bbox[3], self.bbox[5]
        interior = []
        for x in range(x1, x2 + 1):
            for z in range(z1, z2 + 1):
                if self.in_towers(x, z): continue
                d = seg_dist(x, z, ax, az, bx, bz)
                if d > w: continue
                t = ((x - ax) * ux + (z - az) * uz)
                if d <= w - 1.0:
                    pl.set(x, F + 6, z, WALL)
                    pl.floors.add((x, F, z))
                    pl.floors.add((x, F + 6, z))
                    interior.append((x, z, t, d))
                    b = BLOCK if int(t) % 6 < 3 else BRICKS
                    pl.set(x, DECK, z, BLOCK if (int(t) + int(d)) % 4 else P.PALE_GREY)
                else:
                    pier = int(t) % 6 == 0
                    pl.set(x, F + 1, z, BLOCK)
                    for y in (F + 2, F + 3, F + 4): pl.set(x, y, z, PILLAR if pier else GLASS)
                    pl.set(x, F + 5, z, BRICKS)
                    pl.set(x, F + 6, z, CYAN)
        # roof lanterns + parapet-less cornice
        for (x, z, t, d) in interior:
            if int(t) % 5 == 2 and d < 1.0: pl.set(x, F + 6, z, LAMP)
        # roof glass rail on the outer ring
        for x in range(x1, x2 + 1):
            for z in range(z1, z2 + 1):
                if self.in_towers(x, z): continue
                d = seg_dist(x, z, ax, az, bx, bz)
                if w - 1.0 < d <= w: pl.set(x, F + 7, z, RAIL)
        # lab benches on both sides
        lv = Lv(pl, F, 0, 0, 1e9, rng)
        lv.free = lambda cells, pl=pl, F=F: all((x, F, z) in pl.floors and (x, F, z) not in pl.occ and (x, F, z) not in pl.res
                                                and (x, F + 1, z) not in pl.c and (x, F + 2, z) not in pl.c and
                                                self._clear(x, z) for x, z in cells)
        t = 6.0
        k = 0
        while t < L - 6:
            for side in (-1, 1):
                px, pz = -uz * side, ux * side
                x = int(round(ax + ux * t + px * (w - 3.0)))
                z = int(round(az + uz * t + pz * (w - 3.0)))
                face = IK.toward(x, z, int(round(ax + ux * t)), int(round(az + uz * t)))
                if self.name == 'sci_wing_1':                     # materials workshop wing
                    if k % 2 == 0: _mat_station(lv, x, z, face, k // 2 + (0 if side < 0 else 1))
                    else: IK.column_data(lv, x, z, 3, 'warm')
                else:                                              # xenobiology wing: specimen cases between benches
                    if k % 2 == 0: specimen_case(lv, x, z, k + (0 if side < 0 else 3))
                    else: IK.bench_lab(lv, x, z, face, 2, rng)
            t += 4.0
            k += 1
        # lights
        for (x, z, t, d) in interior:
            if int(t) % 4 == 0 and int(d + 8) % 4 == 0 and (x, F + 5, z) not in pl.c:
                pl.set(x, F + 5, z, LIGHT)
        # keep clear lane in the middle (no furniture there)
        pl.finish()
        return pl

    def _clear(self, x, z):
        ax, az = self.a; bx, bz = self.b
        return seg_dist(x, z, ax, az, bx, bz) >= 1.9


# ---- antenna array
AN_C = tuple(int(round(c)) for c in axis_point(432.0, -38.0))


def build_antenna(ctx):
    cx, cz = AN_C
    hut = Tower('sci_antenna', cx, cz, [(7, 1)], 'D', entrance=gang(_VX, _VZ), crown=None, fins=False,
                lanterns=False)
    cap = bbox_cap(cx - 17, cz - 17, cx + 17, cz + 17)
    hut.bbox = (cx - 17, DECK, cz - 17, cx + 17, max(cap - 2, DECK + 12), cz + 17)
    hut.top = hut.bbox[4]
    pl = hut.build(ctx)
    rng = _rng(ctx, hut.name)
    L = hut.levels_lv(rng)[0]
    # hut interior: radio consoles round the wall, central map table
    IK.map_table(L, cx, cz, 2.0)
    console_ring(L, 4.0, 5, 40.0, skip=(gang(_VX, _VZ),))
    # antenna masts round the hut (varied heights), dish on the tallest
    top_y = hut.top
    masts = [(0, 13.0, top_y), (72, 13.0, top_y - 9), (144, 13.0, top_y - 4), (216, 13.0, top_y - 12), (288, 13.0, top_y - 6)]
    for a, r, ty in masts:
        mx, mz = pol(cx, cz, r, a)
        ty = max(ty, DECK + 8)
        for x in (mx, mx + 1):
            for z in (mz, mz + 1):
                for y in range(DECK, ty - 3): pl.set(x, y, z, PILLAR if (y - DECK) % 6 else CYAN)
        for y in range(ty - 3, ty - 1): pl.set(mx, y, mz, PILLAR)
        for y in range(ty - 1, ty + 1): pl.set(mx, y, mz, 'end_rod[facing=up]')
        # braces down to the base ring
        pl.set(mx, DECK + 1, mz, BLOCK)
        # cross-bars (glass ring platform)
        yb = DECK + 7 + (a // 72) * 2
        yb = min(yb, ty - 5)
        for x, z in G.ring_cells(mx, mz, 1.4, 2.6): pl.set(x, yb, z, LAMP if (x + z) % 3 == 0 else P.GLASS_CYAN)
    # link cables between neighbouring masts at height
    mpts = [pol(cx, cz, 13.0, a) for a, _, _ in masts]
    for i in range(5):
        p0, p1 = mpts[i], mpts[(i + 1) % 5]
        yy = DECK + 9 + (i % 2) * 3
        for (x, y, z) in G.line_cells((p0[0], yy, p0[1]), (p1[0], yy, p1[1]), 1):
            if (x, y, z) not in pl.c: pl.set(x, y, z, LAMP if (x + z) % 4 == 0 else P.CYAN)
    # dish: a bowl of glass above the hut roof on a pillar
    roof = hut.roof_F
    for y in range(roof + 1, roof + 4): pl.set(cx, y, cz, PILLAR)
    for x, z in G.disc_cells(cx, cz, 4):
        d = math.hypot(x - cx, z - cz)
        pl.set(x, roof + 4 + (1 if d > 2.5 else 0), z, P.GLASS_CYAN if d > 1 else LAMP)
    pl.set(cx, roof + 5, cz, 'end_rod[facing=up]')
    hut.lights(pl)
    pl.finish()
    hut.pad = None
    return hut


# ------------------------------------------------------------------------------------------------------------
# features
# ------------------------------------------------------------------------------------------------------------
_CACHE = {}


class LandmarkFeature(Feature):
    layer = LAYER

    def __init__(self, name, bbox, meta, maker, mode='tower'):
        super().__init__(name, bbox, LAYER, meta)
        self.maker, self.mode = maker, mode

    def plan(self, ctx=None):
        p = _CACHE.get(self.name)
        if p is None:
            r = self.maker(ctx or _CTX)
            p = r.plan if hasattr(r, 'plan') and not isinstance(r, Plan) else r
            _CACHE[self.name] = p
        return p

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


def _tower_meta(tw, nm):
    r = tw.levels[0]['r']
    cx, cz = tw.cx, tw.cz
    rmax = tw.tiers[0][0]
    ex = int(math.floor(cx + (r - 0.5) * _COS(math.radians(tw.entrance)) + 0.5))
    ez = int(math.floor(cz + (r - 0.5) * _SIN(math.radians(tw.entrance)) + 0.5))
    m = {'kind': nm, 'district': tw.district, 'entrances': [(ex, WALK, ez)], 'interior': True,
         'interior_box': (cx - int(rmax - 1), WALK, cz - int(rmax - 1), cx + int(rmax - 1), tw.top, cz + int(rmax - 1)),
         'roof_y': tw.top, 'islands_ok': True}
    return m


def _make_all():
    feats = []
    names = []
    # --- K
    t = make_command_tower()
    f = LandmarkFeature(t.name, t.bbox, _tower_meta(t, 'command_tower'), build_command_tower)
    feats.append(f)
    r = _rot_make()
    f = LandmarkFeature(r.name, r.bbox, _tower_meta(r, 'briefing_rotunda'), build_rotunda)
    feats.append(f)
    # --- D
    for spec in _D_SPECS:
        t = make_sci_tower(spec)
        feats.append(LandmarkFeature(t.name, t.bbox, _tower_meta(t, 'research_tower'),
                                     (lambda ctx, spec=spec: build_sci_tower(ctx, spec))))
    for w in WINGS:
        wg = Wing(w)
        ex, ez = int(round(wg.a[0] + (wg.b[0] - wg.a[0]) * 0.5)), int(round(wg.a[1] + (wg.b[1] - wg.a[1]) * 0.5))
        meta = {'kind': 'lab_wing', 'district': 'D', 'interior': True, 'islands_ok': True,
                'entrances': [], 'roof_y': DECK + 7,
                'interior_box': (wg.bbox[0], WALK, wg.bbox[2], wg.bbox[3], DECK + 5, wg.bbox[5])}
        feats.append(LandmarkFeature(wg.name, wg.bbox, meta, lambda ctx, wg=wg: wg.build(ctx)))
    a = Tower('sci_antenna', AN_C[0], AN_C[1], [(7, 1)], 'D', entrance=gang(_VX, _VZ), crown=None)
    cap = bbox_cap(AN_C[0] - 17, AN_C[1] - 17, AN_C[0] + 17, AN_C[1] + 17)
    a.bbox = (AN_C[0] - 17, DECK, AN_C[1] - 17, AN_C[0] + 17, max(cap - 2, DECK + 12), AN_C[1] + 17)
    a.top = a.bbox[4]
    meta = _tower_meta(a, 'antenna_array')
    meta['interior_box'] = (AN_C[0] - 6, WALK, AN_C[1] - 6, AN_C[0] + 6, DECK + 6, AN_C[1] + 6)
    meta['roof_y'] = DECK + 6
    feats.append(LandmarkFeature(a.name, a.bbox, meta, build_antenna))
    allnames = [f.name for f in feats]
    for f in feats:
        f.meta['allow_overlap'] = [n for n in allnames if n != f.name]
    return feats


FEATURES = _make_all()
register(*FEATURES)


def features():
    return list(FEATURES)
