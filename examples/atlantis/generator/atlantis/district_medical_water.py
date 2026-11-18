"""Layer 40: district F (Medical & Life Sciences, plan angle 120) and district W (Water Management & Hydroponics,
plan angle 300), both on the inner ring (r 135..295).

Every building is a `Building`: a footprint rectangle in a local frame (u along the front, v inward), one or more
`Mass`es (floor stacks with walls, slabs, stairs, a flat reachable roof, furniture) and optional extras (glass tanks,
pipes, crowns, fins).  A lot planner places buildings along the street frontage of each district.

DISTRICT F  med_infirmary (U-shaped medical centre: wards, treatment bays, scanner room, stasis pods), med_lifelab
            (tapered life-sciences tower), med_pharmacy (greenhouse lab), med_quarters (staff quarters), med_stasis
            (round stasis-pod tower), med_clinic, med_annex (L-shaped research annex), med_depot (supply depot).
DISTRICT W  wat_plant (treatment plant: glass tanks + pump hall), wat_hydrohall (greenhouse hall with crop rows),
            wat_hydrotower (vertical farm), wat_desal (desalination tower), wat_control, wat_tankfarm,
            wat_filter (filtration works), wat_depot (utility store).

CONVENTIONS (same as landmark_command): floors F = 4 + 6 i, walk y = F + 1, five air rows, the next slab at F + 6.
Stairs are 3 wide, 6 long, with the hole in the upper slab over steps 2..5.  Every roof is a walkable, railed,
lit terrace reached by the last stair; tiered towers have doors onto their terraces.  The cell store `Plan`
refuses cells outside the bbox, outside the district, on roads or above ctx.max_building_top (`rejected` counts them).
Water is never used: tanks are glass shells filled with light-blue stained glass (and sea-lantern cores).
"""
import math

from . import config, planner as PL, roads, palette as P, blocks as B, geometry as G
from . import interior_kit as IK
from .feature import Feature, register, registry
from .util import rng as _rng, hash_str

LAYER = 40
DECK = PL.DECK_TOP
WALK = PL.WALK_Y
PITCH = 6
LAMP = P.LAMP
LIGHT = P.HIDDEN_LIGHT
PILLAR = 'quartz_pillar[axis=y]'
D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
SIDES = ('north', 'east', 'south', 'west')

_CTX = config.load()


# ------------------------------------------------------------------------------------------------------------
# cell store
# ------------------------------------------------------------------------------------------------------------
class Plan:
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
            ok = PL.district_at(x, z) == self.district and roads.surface(x, z) is None
            v = self._cap[(x, z)] = int(self.ctx.max_building_top(x, z)) if ok else -999
        return v

    def ok(self, x, z):
        bb = self.bbox
        return bb[0] <= x <= bb[3] and bb[2] <= z <= bb[5] and self.cap(x, z) > -999

    def set(self, x, y, z, b):
        if b is None: return
        bb = self.bbox
        if not (bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5]) or y > self.cap(x, z):
            self.rejected += 1
            if len(self.first_rejects) < 8: self.first_rejects.append((x, y, z, b))
            return
        self.c[(x, y, z)] = b

    def put(self, x, y, z, b):
        """set only into an empty cell"""
        if (x, y, z) not in self.c: self.set(x, y, z, b)

    def get(self, x, y, z):
        return self.c.get((x, y, z))

    def pop(self, x, y, z):
        self.c.pop((x, y, z), None)

    def fill(self, x1, y1, z1, x2, y2, z2, b):
        for y in range(min(y1, y2), max(y1, y2) + 1):
            for z in range(min(z1, z2), max(z1, z2) + 1):
                for x in range(min(x1, x2), max(x1, x2) + 1): self.set(x, y, z, b)

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


# ------------------------------------------------------------------------------------------------------------
# local frame and footprints
# ------------------------------------------------------------------------------------------------------------
class Frame:
    """Footprint rectangle (world x0,z0..x1,z1) seen from its front side: u along the front, v inward from it."""

    def __init__(self, side, x0, z0, x1, z1):
        self.side, self.x0, self.z0, self.x1, self.z1 = side, x0, z0, x1, z1
        if side in ('north', 'south'): self.W, self.D = x1 - x0 + 1, z1 - z0 + 1
        else: self.W, self.D = z1 - z0 + 1, x1 - x0 + 1

    def w(self, u, v):
        s = self.side
        if s == 'north': return self.x0 + u, self.z0 + v
        if s == 'south': return self.x1 - u, self.z1 - v
        if s == 'west': return self.x0 + v, self.z1 - u
        return self.x1 - v, self.z0 + u

    def out(self):
        """world direction pointing out of the front"""
        return {'north': (0, -1), 'south': (0, 1), 'west': (-1, 0), 'east': (1, 0)}[self.side]


def rect_fp(u0, v0, u1, v1, ch=0):
    def f(ins):
        a0, b0, a1, b1 = u0 + ins, v0 + ins, u1 - ins, v1 - ins
        out = set()
        for u in range(a0, a1 + 1):
            for v in range(b0, b1 + 1):
                if ch and min(u - a0, a1 - u) + min(v - b0, b1 - v) < ch: continue
                out.add((u, v))
        return out
    return f


def ellipse_fp(cu, cv, a, b):
    def f(ins):
        aa, bb = a - ins, b - ins
        out = set()
        for u in range(int(cu - aa) - 1, int(cu + aa) + 2):
            for v in range(int(cv - bb) - 1, int(cv + bb) + 2):
                if ((u - cu) / aa) ** 2 + ((v - cv) / bb) ** 2 <= 1.0 + 1e-9: out.add((u, v))
        return out
    return f


# ------------------------------------------------------------------------------------------------------------
# styles
# ------------------------------------------------------------------------------------------------------------
def style(kind, rng):
    if kind == 'med':
        g = rng.choice([P.GLASS_BLUE, P.GLASS_BLUE, P.GLASS_CYAN, P.GLASS_WHITE])
        return dict(base=P.WHITE_BLOCK, pillar=PILLAR, glass=g, band=P.WHITE_BRICKS, band2='cyan_concrete',
                    floor=P.WHITE, floor2=P.PALE_GREY, trim=P.PRISMARINE_BRICKS, roof=P.WHITE, rail=P.GLASS_BLUE_PANE,
                    win=(2, 3, 4), accent='cyan_concrete')
    if kind == 'green':
        return dict(base=P.PRISMARINE_BRICKS, pillar=PILLAR, glass=P.GLASS_WHITE, band=P.WHITE_BRICKS,
                    band2=P.PRISMARINE_BRICKS, floor=P.WHITE, floor2='moss_block', trim=P.WHITE_BLOCK, roof=P.WHITE,
                    rail=P.GLASS_PANE, win=(1, 2, 3, 4), accent='cyan_concrete')
    if kind == 'store':
        return dict(base=P.PALE, pillar=PILLAR, glass=P.GLASS_BLUE, band=P.WHITE_BRICKS, band2=P.GREY,
                    floor=P.PALE_GREY, floor2=P.GREY, trim=P.PRISMARINE_BRICKS, roof=P.PALE_GREY, rail=P.GLASS_BLUE_PANE,
                    win=(4,), accent='cyan_concrete')
    if kind == 'aqua':
        g = rng.choice([P.GLASS_CYAN, P.GLASS_BLUE])
        return dict(base=P.PRISMARINE_BRICKS, pillar=PILLAR, glass=g, band=P.DARK_PRISMARINE, band2='cyan_concrete',
                    floor=P.WHITE, floor2=P.PRISMARINE, trim=P.DARK_PRISMARINE, roof=P.PALE, rail=P.GLASS_CYAN_PANE,
                    win=(2, 3, 4), accent=P.CYAN)
    if kind == 'works':
        return dict(base=P.PALE, pillar=PILLAR, glass=P.GLASS_CYAN, band=P.PRISMARINE_BRICKS, band2='cyan_concrete',
                    floor=P.PALE_GREY, floor2=P.GREY, trim=P.DARK_PRISMARINE, roof=P.PALE, rail=P.GLASS_CYAN_PANE,
                    win=(3, 4), accent=P.CYAN)
    raise KeyError(kind)


# ------------------------------------------------------------------------------------------------------------
# stairs geometry
# ------------------------------------------------------------------------------------------------------------
def stair_geom(X0, Z0, d):
    dx, dz = B.DIRS[d]
    px, pz = -dz, dx
    steps = [(X0 + dx * s + px * q, s, Z0 + dz * s + pz * q) for s in range(6) for q in (-1, 0, 1)]
    approach = [(X0 - dx + px * q, Z0 - dz + pz * q) for q in (-1, 0, 1)]
    exit_ = [(X0 + dx * 6 + px * q, Z0 + dz * 6 + pz * q) for q in (-1, 0, 1)]
    hole = {(x, z) for (x, s, z) in steps if s >= 2}
    low = [(x, z) for (x, s, z) in steps if s < 2]
    return dict(dir=d, steps=steps, approach=approach, exit=exit_, hole=hole, low=low, X0=X0, Z0=Z0)


def grow(cells, n=1):
    out = set(cells)
    for _ in range(n):
        out |= {(x + dx, z + dz) for (x, z) in out for dx, dz in D4 + ((1, 1), (-1, -1), (1, -1), (-1, 1))}
    return out


# ------------------------------------------------------------------------------------------------------------
# Mass: a stack of floors
# ------------------------------------------------------------------------------------------------------------
class Mass:
    def __init__(self, name, frame, fp, insets, scenes, st, rng, door_u=None, ornament=True, gap_doors=()):
        insets = list(insets)
        while True:
            try:
                self._init(name, frame, fp, insets, scenes, st, rng, door_u)
                return
            except ValueError as e:
                if 'no stair room' not in str(e) or len(insets) <= 1: raise
                insets = insets[:-1]          # fewer floors until the stairs fit

    def _init(self, name, frame, fp, insets, scenes, st, rng, door_u):
        self.name, self.frame, self.st, self.rng = name, frame, st, rng
        self.insets = list(insets)
        self.n = len(self.insets)
        self.scenes = [scenes[i % len(scenes)] for i in range(self.n)]
        cache = {}
        self.S, self.Wc, self.I, self.SL = [], [], [], []
        for ins in self.insets:
            if ins not in cache: cache[ins] = fp(ins)
            loc = cache[ins]
            s = {frame.w(u, v) for (u, v) in loc}
            w = {c for c in s if any((c[0] + dx, c[1] + dz) not in s for dx, dz in D4)}
            self.SL.append(loc); self.S.append(s); self.Wc.append(w); self.I.append(s - w)
        self.F = [DECK + PITCH * l for l in range(self.n + 1)]
        self.res = [set() for _ in range(self.n + 1)]
        self.holes = [set() for _ in range(self.n + 1)]
        self.stairs = []
        self.cuts = []                       # (level, cells) wall cells opened for doors
        self.door_u = door_u
        self.entrance = None                 # world feet cell (x, WALK, z)
        self.tier_doors = []
        self.top = self.F[self.n]
        self._plan_doors()
        self._place_stairs()

    # ---- doors
    def _plan_doors(self):
        fr = self.frame
        if self.door_u is not None:
            u0 = self.door_u
            cells = []
            vmins = []
            for du in (-1, 0, 1):
                vs = [v for (u, v) in self.SL[0] if u == u0 + du]
                if not vs: raise ValueError('door outside footprint ' + self.name)
                vmin = min(vs)
                vmins.append(vmin)
                for k in range(3):
                    cells.append(fr.w(u0 + du, vmin + k))
            vmin0 = vmins[1]
            self.entrance = (*fr.w(u0, vmin0)[:1], WALK, fr.w(u0, vmin0)[1])
            self.cuts.append((0, [c for c in cells if c in self.Wc[0]]))
            self.door_frame = [fr.w(u0 - 2, min(v for (u, v) in self.SL[0] if u == u0 - 2) if any(u == u0 - 2 for u, v in self.SL[0]) else vmin0),
                               fr.w(u0 + 2, min(v for (u, v) in self.SL[0] if u == u0 + 2) if any(u == u0 + 2 for u, v in self.SL[0]) else vmin0)]
            self.door_front = fr.w(u0, vmin0 - 1)
            self.door_cell = fr.w(u0, vmin0)
            for dd in range(-2, 3):
                for k in range(0, 9):
                    self.res[0].add(fr.w(u0 + dd, vmins[1] + k))
        # tier doors onto terraces
        for l in range(1, self.n):
            if self.insets[l] > self.insets[l - 1]:
                self._tier_door(l)

    def _tier_door(self, l):
        order = SIDES[l % 4:] + SIDES[:l % 4]
        cx = sum(x for x, z in self.S[l]) / len(self.S[l]); cz = sum(z for x, z in self.S[l]) / len(self.S[l])
        for sd in order:
            dx, dz = B.DIRS[sd]
            cand = [(abs((x - cx) * (-dz) + (z - cz) * dx), x, z) for (x, z) in self.Wc[l]
                    if (x + dx, z + dz) not in self.S[l] and (x + dx, z + dz) in self.S[l - 1]
                    and (x - dx, z - dz) in self.I[l] and (x + dx * 2, z + dz * 2) in self.S[l - 1]]
            if not cand: continue
            cand.sort()
            _, x, z = cand[0]
            px, pz = -dz, dx
            cells = [(x + px * q, z + pz * q) for q in (-1, 0, 1)]
            cells = [c for c in cells if c in self.Wc[l] and (c[0] + dx, c[1] + dz) in self.S[l - 1]]
            if not cells: continue
            self.cuts.append((l, cells))
            self.tier_doors.append((l, (x, z), sd))
            for q in (-2, -1, 0, 1, 2):
                for k in range(1, 6):
                    self.res[l].add((x - dx * k + px * q, z - dz * k + pz * q))
                self.res[l].add((x + dx + px * q, z + dz + pz * q))
                self.res[l].add((x + dx * 2 + px * q, z + dz * 2 + pz * q))
            return

    # ---- stairs
    def _place_stairs(self):
        prev = None
        fr = self.frame
        for i in range(self.n):
            j = min(i + 1, self.n - 1)
            Ii, Ij = self.I[i], self.I[j]
            blocked_i = set(self.res[i]) | grow(self.holes[i], 1)
            blocked_j = set(self.res[j]) if j != i else blocked_i
            best = None
            for d in ('east', 'west', 'south', 'north'):
                for (x, z) in sorted(Ii):
                    g = stair_geom(x, z, d)
                    ok = True
                    for c in g['approach'] + g['low']:
                        if c not in Ii or c in blocked_i: ok = False; break
                    if not ok: continue
                    for c in list(g['hole']) + g['exit']:
                        if c not in Ij or c not in Ii or c in blocked_j or c in blocked_i: ok = False; break
                    if not ok: continue
                    if i + 1 < self.n and any(c in grow(self.holes[i + 1], 0) for c in g['exit']): continue
                    cxm, czm = x + B.DIRS[d][0] * 3, z + B.DIRS[d][1] * 3
                    if prev is None:
                        u, v = self._local(cxm, czm)
                        score = v * 2 - abs(u - self.frame.W / 2.0) * 0.3
                    else:
                        score = abs(cxm - prev[0]) + abs(czm - prev[1])
                    score += (hash_str(self.name, i, x, z, d) % 100) / 100.0
                    if best is None or score > best[0]: best = (score, g, (cxm, czm))
            if best is None:
                raise ValueError('no stair room: %s level %d' % (self.name, i))
            _, g, prev = best
            self.stairs.append(g)
            self.holes[i + 1] |= g['hole']
            self.res[i] |= grow(set(g['approach'] + g['low']), 1) | {(x, z) for (x, s, z) in g['steps']}
            self.res[j] |= grow(set(g['exit']), 1) | grow(g['hole'], 1)
            if j == i: self.res[i] |= grow(set(g['exit']), 1)

    def _local(self, x, z):
        fr = self.frame
        # inverse of Frame.w
        s = fr.side
        if s == 'north': return x - fr.x0, z - fr.z0
        if s == 'south': return fr.x1 - x, fr.z1 - z
        if s == 'west': return fr.z1 - z, x - fr.x0
        return z - fr.z0, fr.x1 - x

    # ---- build
    def build(self, pl, rng=None):
        st = self.st
        n = self.n
        # floor of level 0 (the deck row)
        for (x, z) in self.I[0]:
            pl.set(x, DECK, z, self._floor_block(x, z, 0))
        # walls
        for l in range(n):
            F = self.F[l]
            W = self.Wc[l]
            for (x, z) in W:
                corner = ((x + 1, z) in W or (x - 1, z) in W) and ((x, z + 1) in W or (x, z - 1) in W)
                pier = corner or (x + z + l) % 4 == 0
                pl.set(x, F + 1, z, st['base'])
                for k, y in enumerate((F + 2, F + 3, F + 4)):
                    row = k + 2
                    pl.set(x, y, z, st['pillar'] if pier else (st['glass'] if row in st['win'] else st['base']))
                pl.set(x, F + 5, z, st['band'] if l % 2 == 0 else st['band2'])
        # slabs
        for m in range(1, n + 1):
            y = self.F[m]
            inner = self.S[m] if m < n else set()
            for (x, z) in self.S[m - 1]:
                if (x, z) in self.holes[m]: continue
                if (x, z) in self.I[m] if m < n else False:
                    b = self._floor_block(x, z, m)
                elif (x, z) in inner:
                    b = st['band'] if m % 2 else st['band2']
                else:
                    b = st['roof'] if (x + z) % 4 else st['floor2']
                pl.set(x, y, z, b)
            terr = self.S[m - 1] - inner
            for (x, z) in terr:
                if (x, z) in self.holes[m] or (x, z) in self.Wc[m - 1]: continue
                if (x + 2 * z + m) % 5 == 0 and not any((x + dx, z + dz) in self.holes[m] for dx, dz in D4):
                    pl.set(x, y, z, LAMP)
            for (x, z) in self.Wc[m - 1]:
                if (x, z) not in inner:
                    pl.put(x, y + 1, z, st['rail'])
        # interior accent course
        # stairs
        for i, g in enumerate(self.stairs):
            F = self.F[i]
            for (x, s, z) in g['steps']:
                pl.set(x, F + 1 + s, z, B.stairs(P.WHITE, g['dir']))
                for y in range(F + 1, F + 1 + s): pl.set(x, y, z, P.WHITE_BLOCK)
            # rail the hole on the upper floor (not at the exit)
            Fu = self.F[i + 1]
            ex = set(g['exit'])
            for (x, z) in g['hole']:
                for dx, dz in D4:
                    c = (x + dx, z + dz)
                    if c in g['hole'] or c in ex or c not in self.S[i]: continue
                    if (c[0], Fu, c[1]) in pl.c and pl.c[(c[0], Fu, c[1])] is not None and c not in self.holes[i + 1]:
                        pl.put(c[0], Fu + 1, c[1], st['rail'])
        # door cuts
        for (l, cells) in self.cuts:
            F = self.F[l]
            for (x, z) in cells:
                for y in range(F + 1, F + 5): pl.pop(x, y, z)
        if self.entrance is not None:
            F = DECK
            dx, dz = self.frame.out()
            x, z = self.door_cell
            pl.set(x, F + 5, z, LAMP)
            for c in self.door_frame:
                pass
            # flank pillars and front lamps
            for (px, pz) in ((-dz, dx), (dz, -dx)):
                for k in (2,):
                    fx, fz = x + px * k, z + pz * k
                    if (fx, fz) in self.Wc[0]:
                        for y in range(F + 1, F + 5): pl.set(fx, y, fz, PILLAR)
                lx, lz = x + dx * 1 + px * 3, z + dz * 1 + pz * 3
            fx, fz = self.door_front
            for (px, pz) in ((-dz, dx), (dz, -dx)):
                lx, lz = fx + px * 2, fz + pz * 2
                if pl.ok(lx, lz): pl.set(lx, DECK, lz, LAMP)
        # interior lights (hidden) on a lattice in the top air row
        for l in range(n):
            F = self.F[l]
            for (x, z) in self.I[l]:
                if (x + 2 * l) % 4 == 0 and (z + l) % 4 == 0:
                    pl.put(x, F + 5, z, LIGHT)
        # roof: a few hidden lights too (lamps are flush) are not needed; furniture
        for l in range(n):
            L = Lv(self, pl, l)
            SCENES[self.scenes[l]](L)
        return pl

    def _floor_block(self, x, z, l):
        st = self.st
        a = ((x // 4) + (z // 4) + l) % 2
        if (x + z) % 7 == 0 and l % 2 == 0 and False: return LAMP
        return st['floor'] if a == 0 else st['floor2']

    def link(self, other, pl):
        """open a doorway in the wall cells shared with `other` on every level both have"""
        for l in range(min(self.n, other.n)):
            sh = sorted(self.Wc[l] & other.Wc[l])
            if len(sh) < 7: continue
            mid = len(sh) // 2
            cells = sh[mid - 1: mid + 2]
            F = self.F[l]
            for (x, z) in cells:
                for y in range(F + 1, F + 5): pl.pop(x, y, z)
            for m in (self, other):
                m.res[l] |= grow(set(cells), 1)


# ------------------------------------------------------------------------------------------------------------
# a floor seen through the interior_kit furniture API
# ------------------------------------------------------------------------------------------------------------
class Lv:
    def __init__(self, mass, plan, l):
        self.mass, self.p, self.l = mass, plan, l
        self.F = mass.F[l]; self.W = self.F + 1
        self.I, self.WL = mass.I[l], mass.Wc[l]
        self.rng = _rng(plan.ctx, '%s/%d' % (mass.name, l))
        self.occ = set()
        self.cx = sum(x for x, z in self.I) / float(len(self.I))
        self.cz = sum(z for x, z in self.I) / float(len(self.I))
        xs = [x for x, z in self.I]; zs = [z for x, z in self.I]
        self.bx0, self.bx1, self.bz0, self.bz1 = min(xs), max(xs), min(zs), max(zs)

    def m(self, key, default=None):
        return default

    def free(self, cells):
        for (x, z) in cells:
            if (x, z) not in self.I or (x, z) in self.occ or (x, z) in self.mass.res[self.l]: return False
            if (x, z) in self.mass.holes[self.l]: return False
            if (x, self.W, z) in self.p.c or (x, self.W + 1, z) in self.p.c: return False
        return True

    def set(self, x, y, z, b, occ=None):
        self.p.set(x, y, z, b)
        if occ is None: occ = self.W <= y <= self.W + 1
        if occ: self.occ.add((x, z))

    def get(self, x, y, z):
        return self.p.get(x, y, z)

    def put(self, x, y, z, b, occ=True):
        if self.free([(x, z)]) if y == self.W else True:
            self.set(x, y, z, b, occ)

    def perimeter(self):
        out = []
        for (x, z) in self.I:
            for d, (dx, dz) in B.DIRS.items():
                if (x + dx, z + dz) in self.WL:
                    out.append((math.atan2(z - self.cz, x - self.cx), x, z, d))
        out.sort()
        return [(x, z, d) for (_, x, z, d) in out]

    def along(self, fn, gap):
        placed = []
        for (x, z, wd) in self.perimeter():
            if any(abs(x - px) + abs(z - pz) < gap for px, pz in placed): continue
            if fn(x, z, wd): placed.append((x, z))
        return placed


# ------------------------------------------------------------------------------------------------------------
# furniture scenes
# ------------------------------------------------------------------------------------------------------------
BED_COLORS = ('white', 'light_blue', 'cyan', 'white', 'light_gray')


def _center_cells(L, r=1):
    cx, cz = int(round(L.cx)), int(round(L.cz))
    return cx, cz, [(cx + a, cz + b) for a in range(-r, r + 1) for b in range(-r, r + 1)]


def _shelves_wall(L, gap=2, h=3, block='bookshelf'):
    L.along(lambda x, z, wd: IK.shelf(L, x, z, h, block), gap)


def scene_lobby(L):
    cx, cz, cells = _center_cells(L, 2)
    IK.map_table(L, cx, cz, 2.0)
    L.along(lambda x, z, wd: IK.planter(L, x, z, 'flowering_azalea'), 6)
    L.along(lambda x, z, wd: IK.chair(L, x, z, B.OPPOSITE[wd], 'quartz') and IK.chair(L, x + B.DIRS[B.OPPOSITE[wd]][1] * 0 + (1 if wd in ('north', 'south') else 0), z + (1 if wd in ('east', 'west') else 0), B.OPPOSITE[wd], 'quartz'), 5)


def scene_ward(L):
    col = BED_COLORS[L.l % len(BED_COLORS)]
    L.along(lambda x, z, wd: IK.bed(L, x, z, wd, col), 3)
    cx, cz, _ = _center_cells(L, 1)
    IK.desk(L, cx, cz, 'south', 3)
    L.along(lambda x, z, wd: L.free([(x, z)]) and not L.set(x, L.W, z, 'lantern[hanging=false]') and False, 3)


def scene_bays(L):
    def bay(x, z, wd):
        inward = B.OPPOSITE[wd]
        if not IK.console(L, x, z, inward, 3): return False
        px, pz = IK.perp(inward)
        IK.bed(L, x + px * 3, z + pz * 3, wd, 'white')
        return True
    L.along(bay, 7)
    cx, cz, _ = _center_cells(L, 1)
    IK.table_round(L, cx, cz, 1.0, seats=0, lamp=True)


def scene_scanner(L):
    cx, cz = int(round(L.cx)), int(round(L.cz))
    horiz = (L.bx1 - L.bx0) >= (L.bz1 - L.bz0)
    W = L.W
    if horiz:
        bed_cells = [(cx, cz), (cx + 1, cz)]
        arch = [(cx - 1, cz + k) for k in range(-2, 3)]
        pillars = [(cx - 1, cz - 2), (cx - 1, cz + 2)]
    else:
        bed_cells = [(cx, cz), (cx, cz + 1)]
        arch = [(cx + k, cz - 1) for k in range(-2, 3)]
        pillars = [(cx - 2, cz - 1), (cx + 2, cz - 1)]
    allc = set(bed_cells) | set(arch)
    if L.free(sorted(allc)):
        hd = 'east' if horiz else 'south'
        dx, dz = B.DIRS[hd]
        L.set(bed_cells[0][0], W, bed_cells[0][1], 'light_gray_bed[facing=%s,part=foot]' % hd)
        L.set(bed_cells[1][0], W, bed_cells[1][1], 'light_gray_bed[facing=%s,part=head]' % hd)
        for (x, z) in pillars:
            for k in range(0, 3): L.set(x, W + k, z, P.DARK_PRISMARINE)
        for (x, z) in arch:
            if (x, z) in pillars: L.set(x, W + 3, z, P.DARK_PRISMARINE)
            elif (x, z) == arch[2]: L.set(x, W + 3, z, LAMP)
            else: L.set(x, W + 3, z, P.GLASS_CYAN)
        for (x, z) in arch:
            if (x, z) not in pillars:
                L.occ.add((x, z)) if (x, z) in pillars else None
    L.along(lambda x, z, wd: IK.console(L, x, z, B.OPPOSITE[wd], 3), 8)


def _pod(L, x, z, wd):
    dx, dz = B.DIRS[wd]
    ix, iz = x - dx, z - dz
    if not L.free([(x, z), (ix, iz)]): return False
    W = L.W
    for k, (px, pz) in enumerate(((x, z), (ix, iz))):
        L.set(px, W, pz, LAMP if k == 0 else P.WHITE_BLOCK)
        L.set(px, W + 1, pz, P.GLASS_BLUE)
        L.set(px, W + 2, pz, P.GLASS_BLUE)
        L.set(px, W + 3, pz, B.slab(P.WHITE, 'bottom'))
    return True


def scene_stasis(L):
    L.along(lambda x, z, wd: _pod(L, x, z, wd), 3)
    cx, cz, _ = _center_cells(L, 1)
    IK.column_data(L, cx, cz, 4, 'cool')
    L.along(lambda x, z, wd: IK.console(L, x, z, B.OPPOSITE[wd], 3), 10)


def scene_lab(L):
    cx, cz = int(round(L.cx)), int(round(L.cz))
    horiz = (L.bx1 - L.bx0) >= (L.bz1 - L.bz0)
    for off in (-4, 4):
        for k in (-5, 5):
            if horiz: IK.bench_lab(L, cx + k, cz + off, 'east', 4, L.rng)
            else: IK.bench_lab(L, cx + off, cz + k, 'south', 4, L.rng)
    L.along(lambda x, z, wd: IK.case(L, x, z, L.rng.choice(['verdant_froglight', 'pearlescent_froglight'])), 5)
    L.along(lambda x, z, wd: IK.shelf(L, x, z, 3), 3)


def scene_quarters(L):
    col = BED_COLORS[(L.l + 2) % len(BED_COLORS)]
    L.along(lambda x, z, wd: IK.bed(L, x, z, wd, col), 5)
    L.along(lambda x, z, wd: IK.desk(L, x, z, B.OPPOSITE[wd], 3) if L.rng.random() < 0.5 else IK.shelf(L, x, z, 3), 5)
    L.along(lambda x, z, wd: IK.planter(L, x, z, 'azalea'), 7)


def scene_pharm(L):
    L.along(lambda x, z, wd: IK.planter(L, x, z, L.rng.choice(['flowering_azalea', 'azalea', 'fern'])), 2)
    cx, cz = int(round(L.cx)), int(round(L.cz))
    horiz = (L.bx1 - L.bx0) >= (L.bz1 - L.bz0)
    for k in range(-9, 10, 6):
        if horiz: IK.bench_lab(L, cx + k, cz, 'east', 3, L.rng)
        else: IK.bench_lab(L, cx, cz + k, 'south', 3, L.rng)


def scene_hydro(L):
    p = L.p
    W = L.W
    horiz = (L.bx1 - L.bx0) >= (L.bz1 - L.bz0)
    crops = ['wheat[age=7]', 'carrots[age=7]', 'potatoes[age=7]', 'beetroots[age=3]']
    x0, x1, z0, z1 = L.bx0, L.bx1, L.bz0, L.bz1
    a0, a1, c0, c1 = (z0, z1, x0, x1) if horiz else (x0, x1, z0, z1)
    for a in range(a0 + 2, a1 - 1):
        ph = (a - a0) % 5
        if ph not in (2, 3): continue
        crop = crops[((a - a0) // 5 + L.l) % len(crops)]
        for c in range(c0, c1 + 1):
            if (c - c0) % 13 in (6, 7): continue
            x, z = (c, a) if horiz else (a, c)
            if not L.free([(x, z)]): continue
            L.set(x, W, z, 'farmland[moisture=7]')
            L.set(x, W + 1, z, crop)
        # grow lights above the rows
        for c in range(c0 + 2, c1, 5):
            x, z = (c, a) if horiz else (a, c)
            if (x, W + 1, z) in p.c:
                p.put(x, W + 3, z, 'lantern[hanging=true]')
    L.along(lambda x, z, wd: IK.planter(L, x, z, 'azalea'), 9)


def scene_control(L):
    L.along(lambda x, z, wd: IK.console(L, x, z, B.OPPOSITE[wd], 3), 5)
    cx, cz, _ = _center_cells(L, 2)
    IK.map_table(L, cx, cz, 2.5)


def scene_machine(L):
    W = L.W
    def unit(x, z, wd):
        if not L.free([(x, z)]): return False
        for k in range(4):
            L.set(x, W + k, z, 'waxed_oxidized_copper' if k != 2 else P.GLASS_CYAN)
        L.set(x, W + 2, z, LAMP) if L.rng.random() < 0.3 else None
        return True
    L.along(unit, 3)
    cx, cz, cells = _center_cells(L, 1)
    if L.free([(cx, cz), (cx + 1, cz), (cx, cz + 1), (cx + 1, cz + 1)]):
        for (x, z) in [(cx, cz), (cx + 1, cz), (cx, cz + 1), (cx + 1, cz + 1)]:
            L.set(x, W, z, 'iron_block'); L.set(x, W + 1, z, 'waxed_oxidized_cut_copper')
        L.set(cx, W + 2, cz, 'lantern[hanging=false]')
    L.along(lambda x, z, wd: IK.console(L, x, z, B.OPPOSITE[wd], 3), 12)


def scene_storage(L):
    W = L.W
    L.along(lambda x, z, wd: IK.barrel(L, x, z), 2)
    horiz = (L.bx1 - L.bx0) >= (L.bz1 - L.bz0)
    a0, a1, c0, c1 = (L.bz0, L.bz1, L.bx0, L.bx1) if horiz else (L.bx0, L.bx1, L.bz0, L.bz1)
    for a in range(a0 + 3, a1 - 2, 5):
        for c in range(c0 + 2, c1 - 1):
            if (c - c0) % 9 in (4, 5): continue
            x, z = (c, a) if horiz else (a, c)
            if (c + a) % 3 == 0:
                if IK.barrel(L, x, z): L.set(x, W + 1, z, 'barrel[facing=up]')
            else:
                IK.shelf(L, x, z, 2, P.WHITE_BLOCK)


SCENES = dict(lobby=scene_lobby, ward=scene_ward, bays=scene_bays, scanner=scene_scanner, stasis=scene_stasis,
              lab=scene_lab, quarters=scene_quarters, pharm=scene_pharm, hydro=scene_hydro, control=scene_control,
              machine=scene_machine, storage=scene_storage)


# ------------------------------------------------------------------------------------------------------------
# extras: tanks, pipes, pumps, crowns, fins
# ------------------------------------------------------------------------------------------------------------
def pol(cx, cz, r, a):
    t = math.radians(a)
    return int(math.floor(cx + r * math.cos(t) + 0.5)), int(math.floor(cz + r * math.sin(t) + 0.5))


def build_tank(pl, cx, cz, R, h, st, glass=P.GLASS):
    """Glass cylinder filled with light-blue stained glass (no water), stepped cap of rings 2 blocks apart."""
    ytop = DECK + h
    ring = list(G.ring_cells(cx, cz, R - 1.0, R))
    n_p = max(6, int(round(2 * math.pi * R / 5.0)))
    for (x, z) in ring:
        a = math.degrees(math.atan2(z - cz, x - cx)) % 360.0
        t = (a / 360.0 * n_p) % 1.0
        pier = min(t, 1 - t) * (2 * math.pi * R / n_p) < 0.8
        for y in range(DECK + 1, ytop):
            if y <= DECK + 2: pl.set(x, y, z, P.PRISMARINE_BRICKS)
            else: pl.set(x, y, z, PILLAR if pier else glass)
    for (x, z) in G.disc_cells(cx, cz, R - 1.0):
        for y in range(DECK + 1, ytop):
            if (x, z) == (cx, cz) and (y - DECK) % 4 == 0: pl.set(x, y, z, LAMP)
            else: pl.set(x, y, z, P.GLASS_BLUE if (y - DECK) > 2 else P.LIGHT_BLUE)
    K = max(1, min(3, int(R) - 3))
    for k in range(K + 1):
        r_o = R - k
        r_i = R - k - 1.0 if k < K else -1
        y = ytop + 2 * k
        for (x, z) in (G.ring_cells(cx, cz, r_i, r_o) if r_i >= 0 else G.disc_cells(cx, cz, r_o)):
            for yy in range(ytop - 1 if k == 0 else y - 1, y + 1):
                pl.set(x, yy, z, st['roof'] if yy < y else (LAMP if (x * 3 + z * 5) % 5 == 0 else st['trim']))
    # rim course
    for (x, z) in ring:
        pl.set(x, ytop - 1, z, st['accent'])
    return ytop + 2 * K


def pipe_line(pl, p0, p1, y, mat='waxed_oxidized_copper'):
    for (x, yy, z) in G.line_cells((p0[0], y, p0[1]), (p1[0], y, p1[1]), 1):
        pl.put(x, yy, z, mat)


def post(pl, x, z, y0, y1, mat='waxed_oxidized_copper'):
    for y in range(y0, y1 + 1): pl.put(x, y, z, mat)


def pump(pl, x, z):
    """2x2 pump housing: iron base, copper body, lantern on top"""
    for dx in (0, 1):
        for dz in (0, 1):
            pl.set(x + dx, DECK + 1, z + dz, 'iron_block')
            pl.set(x + dx, DECK + 2, z + dz, 'waxed_oxidized_cut_copper')
    pl.set(x, DECK + 3, z, LAMP)
    pl.set(x + 1, DECK + 3, z + 1, 'end_rod[facing=up]')


def crown(pl, kind, cx, cz, y, R, st):
    """y = roof slab y; R = roof half-extent"""
    if kind == 'spire':
        for k in range(1, 8): pl.set(cx, y + k, cz, PILLAR)
        pl.set(cx, y + 8, cz, LAMP)
        for k in range(9, 13): pl.set(cx, y + k, cz, 'end_rod[facing=up]')
        for (dx, dz) in D4:
            for k in range(1, 4): pl.set(cx + dx, y + k, cz + dz, P.WHITE_BLOCK if k < 3 else B.slab(P.WHITE, 'bottom'))
    elif kind == 'crystal':
        for k in range(1, 6):
            for (dx, dz) in ((0, 0),) + (D4 if k < 4 else ()):
                pl.set(cx + dx, y + k, cz + dz, P.GLASS_BLUE if k > 1 else P.PRISMARINE_BRICKS)
        pl.set(cx, y + 3, cz, LAMP)
        for k in range(6, 10): pl.set(cx, y + k, cz, P.GLASS_CYAN if k < 9 else 'end_rod[facing=up]')
    elif kind == 'array':
        for (dx, dz) in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            x, z = cx + dx * max(2, int(R * 0.5)), cz + dz * max(2, int(R * 0.5))
            for k in range(1, 7): pl.set(x, y + k, z, PILLAR)
            pl.set(x, y + 7, z, 'end_rod[facing=up]')
        for (x, z) in G.ring_cells(cx, cz, 1.2, 2.6): pl.set(x, y + 3, z, P.GLASS_CYAN)
        pl.set(cx, y + 1, cz, PILLAR); pl.set(cx, y + 2, cz, PILLAR); pl.set(cx, y + 3, cz, LAMP)
        pl.set(cx, y + 4, cz, 'end_rod[facing=up]')
    elif kind == 'fins':
        for (dx, dz) in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            x, z = cx + dx * (R - 3), cz + dz * (R - 3)
            for k in range(1, 5): pl.set(x, y + k, z, PILLAR)
            pl.set(x, y + 5, z, B.slab(P.WHITE, 'bottom'))
            pl.set(x, y + 6, z, 'end_rod[facing=up]')


def fins(pl, mass, every=4, skip=()):
    """Vertical pilasters outside the front-facing walls (in the bbox margin ring), up to the roof band."""
    n = mass.n
    top = mass.F[n]
    S0 = mass.S[0]
    for (x, z) in sorted(mass.Wc[0]):
        if (x + z) % every: continue
        if (x, z) in skip: continue
        for dx, dz in D4:
            c = (x + dx, z + dz)
            if c in S0: continue
            if sum(1 for ddx, ddz in D4 if (x + ddx, z + ddz) in mass.Wc[0]) != 2: continue    # straight wall only
            if any(abs(c[0] - e[0]) + abs(c[1] - e[1]) <= 3 for e in mass.res[0] if e not in S0): continue
            if c in mass.res[0]: continue
            if mass.entrance and abs(c[0] - mass.entrance[0]) + abs(c[1] - mass.entrance[2]) < 5: continue
            for y in range(DECK + 1, top):
                if y % 6 == 5: pl.put(c[0], y, c[1], P.CYAN if False else 'cyan_concrete')
                else: pl.put(c[0], y, c[1], PILLAR)
            pl.put(c[0], top, c[1], B.slab(P.WHITE, 'bottom'))
            break


# ------------------------------------------------------------------------------------------------------------
# buildings
# ------------------------------------------------------------------------------------------------------------
class Spec:
    def __init__(self, name, design, district, side, x0, z0, x1, z1, hf):
        self.name, self.design, self.district, self.side = name, design, district, side
        self.x0, self.z0, self.x1, self.z1 = x0, z0, x1, z1
        self.hf = hf                       # height factor 0..1 (1 = tall)
        self.frame = Frame(side, x0, z0, x1, z1)
        self.bbox_xz = (x0 - 1, z0 - 1, x1 + 1, z1 + 1)


def _cap_of(spec):
    pts = []
    for x in (spec.x0, (spec.x0 + spec.x1) // 2, spec.x1):
        for z in (spec.z0, (spec.z0 + spec.z1) // 2, spec.z1): pts.append(_CTX.max_building_top(x, z))
    return int(min(pts))


def _floors(spec, lo, hi, extra=14):
    """floors between lo and hi scaled by the height factor, within the dome cap"""
    n = lo + int(round((hi - lo) * spec.hf))
    cap = _cap_of(spec) - extra
    while n > lo and DECK + PITCH * n > cap: n -= 1
    return max(1, n)


def _tiers(n, plan):
    """insets per level from a pattern like [(0,3),(2,2),(4,9)] = (inset, count)"""
    out = []
    for ins, cnt in plan: out += [ins] * cnt
    out = out[:n] + [out[-1]] * max(0, n - len(out))
    return out


class Building:
    """Subclass per design: set self.masses (built in order), self.extras; provides bbox, meta, plan()."""
    top_extra = 14

    def __init__(self, spec):
        self.spec = spec
        self.rng = _rng(_CTX, spec.name)
        self.masses = []
        self.setup()
        top = max(max(m.F[m.n] for m in self.masses) + self.top_extra, getattr(self, 'tank_top', 0))
        cap = _cap_of(spec)
        self.top_y = min(top, cap)
        self.bbox = (spec.x0 - 1, DECK, spec.z0 - 1, spec.x1 + 1, self.top_y, spec.z1 + 1)
        ent = [m.entrance for m in self.masses if m.entrance]
        self.meta = {'kind': spec.design, 'district': spec.district, 'entrances': ent, 'interior': True,
                     'interior_box': (spec.x0, WALK, spec.z0, spec.x1, self.top_y, spec.z1),
                     'roof_y': max(m.F[m.n] for m in self.masses) + 1}
        self._plan = None

    def plan(self, ctx=None):
        if self._plan is None:
            pl = Plan(self.spec.name, self.bbox, self.spec.district, ctx or _CTX)
            for m in self.masses: m.build(pl)
            for (a, b) in getattr(self, 'links', []): self.masses[a].link(self.masses[b], pl)
            self.extras(pl)
            self.lamps(pl)
            pl.finish()
            self._plan = pl
        return self._plan

    def extras(self, pl):
        pass

    def lamps(self, pl):
        """flush sea-lantern paving on the bbox margin ring (every 5th cell) - lights the lanes"""
        x0, z0, x1, z1 = self.bbox[0], self.bbox[2], self.bbox[3], self.bbox[5]
        ring = [(x, z0) for x in range(x0, x1 + 1)] + [(x1, z) for z in range(z0 + 1, z1 + 1)] + \
               [(x, z1) for x in range(x1 - 1, x0 - 1, -1)] + [(x0, z) for z in range(z1 - 1, z0, -1)]
        for i, (x, z) in enumerate(ring):
            if i % 5 == 2 and (x, DECK, z) not in pl.c and pl.ok(x, z) and (x, DECK + 1, z) not in pl.c:
                pl.set(x, DECK, z, LAMP)

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


def _door_u(fr):
    return fr.W // 2


# ---- F designs ---------------------------------------------------------------------------------------------
class Infirmary(Building):
    top_extra = 10

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('med', rng)
        W, D = fr.W, fr.D
        fd = 15                                          # depth of the front block
        n1 = _floors(s, 3, 6, 10)
        n2 = max(2, n1 - 2)
        insets = _tiers(n1, [(0, 3), (3, 9)])
        front = Mass(s.name + '_front', fr, rect_fp(0, 0, W - 1, fd - 1, ch=3), insets,
                     ['lobby', 'bays', 'scanner', 'stasis', 'ward', 'bays'], st, rng, door_u=_door_u(fr))
        wl = Mass(s.name + '_wingL', fr, rect_fp(0, fd - 1, 12, D - 1, ch=2), [0] * n2, ['ward', 'ward', 'stasis'], st, rng)
        wr = Mass(s.name + '_wingR', fr, rect_fp(W - 13, fd - 1, W - 1, D - 1, ch=2), [0] * n2, ['bays', 'ward', 'stasis'], st, rng)
        self.masses = [front, wl, wr]
        self.links = [(0, 1), (0, 2)]
        self.front_n = n1

    def extras(self, pl):
        s = self.spec; fr = s.frame
        front = self.masses[0]
        cu = fr.W // 2
        x, z = fr.w(cu, 7)
        crown(pl, 'crystal', x, z, front.F[front.n], 4, front.st)
        fins(pl, front, 4)
        # courtyard garden between the wings
        for u in range(13, fr.W - 13):
            for v in range(16, fr.D):
                x, z = fr.w(u, v)
                if not pl.ok(x, z): continue
                if (u + v) % 6 == 0: pl.set(x, DECK, z, LAMP)
                elif u in (13, fr.W - 14) and v % 3 == 0: pl.put(x, WALK, z, 'flowering_azalea')


class LifeLab(Building):
    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('med', rng)
        n = _floors(s, 5, 9, 14)
        insets = _tiers(n, [(0, 2), (2, 3), (4, 9)])
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=4), insets, ['lobby', 'lab', 'lab', 'stasis', 'lab'], st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        x, z = fr.w(fr.W // 2, fr.D // 2)
        crown(pl, 'spire', x, z, m.F[m.n], 4, m.st)
        fins(pl, m, 5)


class Pharmacy(Building):
    top_extra = 8

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('green', rng)
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=0), [0, 0], ['pharm', 'lab'], st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        y = m.F[m.n]
        # glass skylights flush in the roof, light-blue ribs
        for (x, z) in m.I[m.n - 1]:
            u, v = m._local(x, z)
            if u % 6 in (2, 3) and v % 6 in (2, 3) and (x, y, z) in pl.c and pl.c[(x, y, z)] != LAMP:
                pl.set(x, y, z, P.GLASS_CYAN)
        for (u, v) in ((3, 3), (fr.W - 4, 3), (3, fr.D - 4), (fr.W - 4, fr.D - 4)):
            x, z = fr.w(u, v)
            crown(pl, 'fins', x, z, y, 1, m.st) if False else None
            for k in range(1, 4): pl.set(x, y + k, z, PILLAR)
            pl.set(x, y + 4, z, 'end_rod[facing=up]')
        fins(pl, m, 4)


class Quarters(Building):
    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('med', rng)
        n = _floors(s, 4, 7, 10)
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=2), [0] * n, ['lobby', 'quarters'], st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        fins(pl, m, 4)
        y = m.F[m.n]
        for u in (4, fr.W - 5):
            x, z = fr.w(u, fr.D // 2)
            crown(pl, 'fins', x, z, y, 2, m.st) if False else None
            for k in range(1, 6): pl.set(x, y + k, z, PILLAR)
            pl.set(x, y + 6, z, LAMP)


class StasisVault(Building):
    top_extra = 14

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('med', rng)
        n = _floors(s, 6, 9, 14)
        a = (fr.W - 1) / 2.0; b = (fr.D - 1) / 2.0
        insets = _tiers(n, [(0, 3), (2, 3), (4, 9)])
        self.masses = [Mass(s.name, fr, ellipse_fp(a, b, a + 0.5, b + 0.5), insets, ['lobby', 'stasis', 'stasis', 'bays'], st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        x, z = fr.w(fr.W // 2, fr.D // 2)
        crown(pl, 'crystal', x, z, m.F[m.n], 4, m.st)


class Clinic(Building):
    top_extra = 8

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('med', rng)
        n = _floors(s, 2, 4, 8)
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=3), [0] * n, ['lobby', 'ward', 'bays'], st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        fins(pl, m, 4)
        crown(pl, 'array', *fr.w(fr.W // 2, fr.D // 2), m.F[m.n], 3, m.st)


class Annex(Building):
    top_extra = 12

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('med', rng)
        n = _floors(s, 3, 5, 10)
        W, D = fr.W, fr.D
        sd = 10
        a = Mass(s.name + '_a', fr, rect_fp(0, 0, W - 1, sd, ch=2), [0] * max(2, n - 1), ['lobby', 'lab', 'ward'], st, rng, door_u=_door_u(fr))
        b = Mass(s.name + '_b', fr, rect_fp(0, sd, 13, D - 1, ch=2), _tiers(n + 1, [(0, 3), (2, 9)]), ['lab', 'stasis', 'lab'], st, rng)
        self.masses = [a, b]
        self.links = [(0, 1)]

    def extras(self, pl):
        b = self.masses[1]; fr = self.spec.frame
        crown(pl, 'spire', *fr.w(6, fr.D - 7), b.F[b.n], 4, b.st)
        fins(pl, self.masses[0], 5)


class Depot(Building):
    top_extra = 8
    kind = 'store'
    scenes = ['storage', 'storage']

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style(self.kind, rng)
        n = _floors(s, 2, 3, 6)
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=1), [0] * n, self.scenes, st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        # loading-bay doors painted on the front: cyan portals
        fins(pl, m, 6)
        crown(pl, 'fins', *fr.w(fr.W // 2, fr.D // 2), m.F[m.n], max(fr.D // 2, 5), m.st) if False else None


# ---- W designs ---------------------------------------------------------------------------------------------
class WaterPlant(Building):
    top_extra = 6

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('aqua', rng)
        W, D = fr.W, fr.D
        self.hall_d = 16
        n = _floors(s, 3, 5, 10)
        self.st = st
        self.masses = [Mass(s.name, fr, rect_fp(1, 0, W - 2, self.hall_d - 1, ch=3), _tiers(n, [(0, 3), (2, 9)]),
                            ['lobby', 'machine', 'control', 'machine'], st, rng, door_u=_door_u(fr))]
        r_t = 6
        self.tanks = []
        k = 3
        for i in range(k):
            u = int((i + 0.5) * W / k)
            h = 20 + (hash_str(s.name, i) % 3) * 4 + int(8 * s.hf)
            h = max(10, min(h, _cap_of(s) - DECK - 9))
            self.tanks.append((u, self.hall_d + 3 + r_t, r_t, h))
        self.tank_top = max(DECK + h + 2 * 3 + 2 for (_, _, _, h) in self.tanks)

    def extras(self, pl):
        s = self.spec; fr = s.frame
        tops = []
        for (u, v, r, h) in self.tanks:
            cx, cz = fr.w(u, v)
            tops.append(build_tank(pl, cx, cz, r, h, self.st))
        # manifold pipe in front of the tanks, supported on posts, with risers into each tank
        vy = self.hall_d + 1
        y = DECK + 8
        p0, p1 = fr.w(self.tanks[0][0], vy), fr.w(self.tanks[-1][0], vy)
        pipe_line(pl, p0, p1, y)
        for (u, v, r, h) in self.tanks:
            x, z = fr.w(u, vy)
            post(pl, x, z, y, y + 2)
            pl.put(x, y + 3, z, LAMP)
            x2, z2 = fr.w(u, self.hall_d + 3)
            pipe_line(pl, (x, z), (x2, z2), y)
            pump(pl, *fr.w(u - 3, vy + 1))
        for u in (self.tanks[0][0] - 8, self.tanks[-1][0] + 8):
            if 1 < u < fr.W - 2:
                x, z = fr.w(u, vy)
                post(pl, x, z, DECK + 1, y); pl.put(x, y + 1, z, LAMP)
        crown(pl, 'array', *fr.w(fr.W // 2, 7), self.masses[0].F[self.masses[0].n], 4, self.masses[0].st)

    def lamps(self, pl):
        super().lamps(pl)


class HydroHall(Building):
    top_extra = 8

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('green', rng)
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=2), [0, 0], ['hydro', 'hydro'], st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        y = m.F[m.n]
        for u in range(4, fr.W - 3, 8):
            for v in (2, fr.D - 3):
                x, z = fr.w(u, v)
                for k in range(1, 4): pl.set(x, y + k, z, PILLAR)
                pl.set(x, y + 4, z, LAMP)
        fins(pl, m, 4)


class HydroTower(Building):
    top_extra = 10

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('green', rng)
        n = _floors(s, 4, 7, 12)
        a = (fr.W - 1) / 2.0; b = (fr.D - 1) / 2.0
        self.masses = [Mass(s.name, fr, ellipse_fp(a, b, a + 0.5, b + 0.5), [0] * n, ['lobby', 'hydro'], st, rng,
                            door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        crown(pl, 'array', *fr.w(fr.W // 2, fr.D // 2), m.F[m.n], fr.W // 2, m.st)


class Desal(Building):
    top_extra = 14

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('aqua', rng)
        n = _floors(s, 6, 9, 16)
        a = (fr.W - 1) / 2.0; b = (fr.D - 1) / 2.0
        self.masses = [Mass(s.name, fr, ellipse_fp(a, b, a + 0.5, b + 0.5), _tiers(n, [(0, 3), (2, 2), (4, 9)]),
                            ['lobby', 'machine', 'control', 'machine'], st, rng, door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        crown(pl, 'spire', *fr.w(fr.W // 2, fr.D // 2), m.F[m.n], 4, m.st)
        # intake pipes up the four corners (outside the round walls, inside the bbox corners)
        for (u, v) in ((1, 1), (fr.W - 2, 1), (1, fr.D - 2), (fr.W - 2, fr.D - 2)):
            x, z = fr.w(u, v)
            h = m.F[min(m.n, 5)]
            for y in range(DECK + 1, h):
                pl.put(x, y, z, 'waxed_oxidized_copper' if y % 5 else LAMP)
            pl.put(x, h, z, B.slab('waxed_oxidized_cut_copper', 'bottom'))


class Control(Building):
    top_extra = 12

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('aqua', rng)
        n = _floors(s, 3, 5, 12)
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=4), _tiers(n, [(0, 2), (2, 9)]),
                            ['lobby', 'control', 'machine', 'control'], st, rng, door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        crown(pl, 'array', *fr.w(fr.W // 2, fr.D // 2), m.F[m.n], fr.W // 2, m.st)
        fins(pl, m, 5)


class TankFarm(Building):
    top_extra = 6

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('works', rng)
        self.st = st
        self.hall_d = 11
        n = _floors(s, 2, 3, 8)
        self.masses = [Mass(s.name, fr, rect_fp(2, 0, fr.W - 3, self.hall_d - 1, ch=2), [0] * n, ['lobby', 'machine', 'control'],
                            st, rng, door_u=_door_u(fr))]
        self.tanks = []
        k = 3 if fr.W >= 30 else 2
        r = 4 if fr.D < 26 else 5
        for i in range(k):
            u = int((i + 0.5) * fr.W / k)
            h = 14 + 4 * (hash_str(s.name, i) % 3) + int(6 * s.hf)
            h = max(8, min(h, _cap_of(s) - DECK - 9))
            self.tanks.append((u, self.hall_d + 3 + r, r, h))
        self.tank_top = max(DECK + h + 2 * 3 + 2 for (_, _, _, h) in self.tanks)

    def extras(self, pl):
        s = self.spec; fr = s.frame
        for (u, v, r, h) in self.tanks:
            cx, cz = fr.w(u, v)
            build_tank(pl, cx, cz, r, h, self.st)
        vy = self.hall_d + 1
        y = DECK + 8
        pipe_line(pl, fr.w(self.tanks[0][0], vy), fr.w(self.tanks[-1][0], vy), y)
        for (u, v, r, h) in self.tanks:
            x, z = fr.w(u, vy)
            post(pl, x, z, y, y + 1)
            pl.put(x, y + 2, z, LAMP)
            pipe_line(pl, (x, z), fr.w(u, self.hall_d + 3), y)
        m = self.masses[0]
        crown(pl, 'array', *fr.w(fr.W // 2, 5), m.F[m.n], 4, m.st)


class Filter(Building):
    top_extra = 12

    def setup(self):
        s = self.spec; fr = s.frame; rng = self.rng
        st = style('works', rng)
        n = _floors(s, 4, 6, 12)
        self.masses = [Mass(s.name, fr, rect_fp(0, 0, fr.W - 1, fr.D - 1, ch=3), _tiers(n, [(0, 2), (2, 2), (4, 9)]),
                            ['lobby', 'machine', 'lab', 'machine'], st, rng, door_u=_door_u(fr))]

    def extras(self, pl):
        m = self.masses[0]; fr = self.spec.frame
        crown(pl, 'crystal', *fr.w(fr.W // 2, fr.D // 2), m.F[m.n], 4, m.st)
        fins(pl, m, 6)


class UtilDepot(Depot):
    kind = 'works'
    scenes = ['storage', 'machine']


DESIGNS = {
    # name: (class, district, size options (W, D), hero)
    'med_infirmary': (Infirmary, 'F', [(46, 32), (42, 30)]),
    'med_lifelab': (LifeLab, 'F', [(24, 24), (26, 22), (22, 26)]),
    'med_pharmacy': (Pharmacy, 'F', [(34, 18), (30, 18), (36, 20)]),
    'med_quarters': (Quarters, 'F', [(32, 16), (28, 15), (34, 16), (24, 14)]),
    'med_stasis': (StasisVault, 'F', [(22, 22), (24, 24), (21, 21)]),
    'med_clinic': (Clinic, 'F', [(20, 16), (18, 15), (19, 17), (16, 14)]),
    'med_annex': (Annex, 'F', [(28, 24), (30, 22)]),
    'med_depot': (Depot, 'F', [(30, 16), (26, 14), (34, 16), (22, 12)]),
    'wat_plant': (WaterPlant, 'W', [(44, 36), (42, 34)]),
    'wat_hydrohall': (HydroHall, 'W', [(52, 18), (46, 18), (40, 17)]),
    'wat_hydrotower': (HydroTower, 'W', [(21, 21), (19, 19), (23, 23), (17, 17)]),
    'wat_desal': (Desal, 'W', [(23, 23), (21, 21), (25, 25)]),
    'wat_control': (Control, 'W', [(26, 22), (24, 20), (22, 20), (19, 17)]),
    'wat_tankfarm': (TankFarm, 'W', [(40, 28), (36, 26), (32, 24)]),
    'wat_filter': (Filter, 'W', [(32, 24), (28, 22), (30, 26)]),
    'wat_depot': (UtilDepot, 'W', [(28, 16), (24, 14), (32, 16), (22, 12)]),
}

# placement schedule per district: first the heroes, then a repeating mix
HEROES = {'F': ['med_infirmary', 'med_lifelab', 'med_stasis', 'med_infirmary'],
          'W': ['wat_plant', 'wat_desal', 'wat_hydrohall', 'wat_control']}
MIX = {'F': ['med_quarters', 'med_pharmacy', 'med_clinic', 'med_annex', 'med_lifelab', 'med_depot', 'med_stasis',
             'med_quarters', 'med_clinic'],
       'W': ['wat_hydrohall', 'wat_filter', 'wat_tankfarm', 'wat_hydrotower', 'wat_control', 'wat_depot',
             'wat_desal', 'wat_plant', 'wat_tankfarm']}
QUOTA = {'med_infirmary': 2, 'med_lifelab': 3, 'med_stasis': 3, 'med_pharmacy': 3, 'med_annex': 3, 'med_quarters': 5,
         'med_clinic': 5, 'med_depot': 3,
         'wat_plant': 2, 'wat_desal': 3, 'wat_hydrohall': 3, 'wat_control': 3, 'wat_filter': 3, 'wat_tankfarm': 3,
         'wat_hydrotower': 4, 'wat_depot': 3}
FILL = {'F': ['med_quarters', 'med_clinic', 'med_depot'], 'W': ['wat_control', 'wat_hydrotower', 'wat_depot']}


# ------------------------------------------------------------------------------------------------------------
# lot planner
# ------------------------------------------------------------------------------------------------------------
GAP = 5            # footprint to footprint (bbox margin 1 each + 3 clear)
EDGE = 2           # footprint to street / district edge


class Grid:
    def __init__(self, district):
        pa = {'F': 120, 'W': 300}[district]
        self.d = district
        pts = [PL.from_polar(r, a) for r in (128, 305) for a in range(pa - 36, pa + 37, 3)]
        self.x0 = int(min(p[0] for p in pts)) - 4
        self.x1 = int(max(p[0] for p in pts)) + 4
        self.z0 = int(min(p[1] for p in pts)) - 4
        self.z1 = int(max(p[1] for p in pts)) + 4
        w, h = self.x1 - self.x0 + 1, self.z1 - self.z0 + 1
        self.w, self.h = w, h
        bad = [[0] * w for _ in range(h)]
        road = [[0] * w for _ in range(h)]
        for j in range(h):
            z = self.z0 + j
            for i in range(w):
                x = self.x0 + i
                if PL.district_at(x, z) != district: bad[j][i] = 1
                elif roads.surface(x, z) is not None: bad[j][i] = 1; road[j][i] = 1
        self.road = road
        self._sd = {}
        sat = [[0] * (w + 1) for _ in range(h + 1)]
        for j in range(h):
            row = 0
            for i in range(w):
                row += bad[j][i]
                sat[j + 1][i + 1] = sat[j][i + 1] + row
        self.sat = sat

    def clear(self, x0, z0, x1, z1):
        i0, i1, j0, j1 = x0 - self.x0, x1 - self.x0, z0 - self.z0, z1 - self.z0
        if i0 < 0 or j0 < 0 or i1 >= self.w or j1 >= self.h: return False
        s = self.sat
        return s[j1 + 1][i1 + 1] - s[j0][i1 + 1] - s[j1 + 1][i0] + s[j0][i0] == 0

    def sdist(self, cx, cz, side):
        key = (cx, cz, side)
        v = self._sd.get(key)
        if v is None:
            dx, dz = {'north': (0, -1), 'south': (0, 1), 'west': (-1, 0), 'east': (1, 0)}[side]
            v = self._sd[key] = self.street_dist(cx, cz, dx, dz)
        return v

    def street_dist(self, cx, cz, dx, dz, limit=70):
        i, j = cx - self.x0, cz - self.z0
        for k in range(1, limit):
            ii, jj = i + dx * k, j + dz * k
            if not (0 <= ii < self.w and 0 <= jj < self.h): return limit
            if self.road[jj][ii]: return k
        return limit


def _foreign_boxes():
    out = []
    for f in registry():
        if f.layer in (40, 50): out.append(f.bbox)
    return out


def _fit(grid, placed, foreign, x0, z0, ex, ez):
    x1, z1 = x0 + ex - 1, z0 + ez - 1
    if not grid.clear(x0 - EDGE, z0 - EDGE, x1 + EDGE, z1 + EDGE): return False
    for (a0, b0, a1, b1) in placed:
        if x0 - GAP <= a1 and x1 + GAP >= a0 and z0 - GAP <= b1 and z1 + GAP >= b0: return False
    for bb in foreign:
        if x0 - 1 - 3 <= bb[3] and x1 + 1 + 3 >= bb[0] and z0 - 1 - 3 <= bb[5] and z1 + 1 + 3 >= bb[2]: return False
    return True


def plan_lots(district):
    grid = Grid(district)
    foreign = _foreign_boxes()
    pa = {'F': 120, 'W': 300}[district]
    placed = []
    lots = []
    rng = _rng(_CTX, 'lots_' + district)
    anchors = []
    for z in range(grid.z0, grid.z1 + 1, 2):
        for x in range(grid.x0, grid.x1 + 1, 2):
            r = math.hypot(x, z)
            if r < 134 or r > 297: continue
            a = PL.ang_diff(PL.plan_angle(x, z), pa)
            anchors.append((round(r / 22.0), a, x, z))
    anchors.sort()
    anchors = [(x, z) for (_, _, x, z) in anchors]

    def try_design(name, x, z):
        cls, dist, sizes = DESIGNS[name]
        opts = list(sizes)
        rng.shuffle(opts)
        for (w, d) in opts:
            for ori in (0, 1):
                ex, ez = (w, d) if ori == 0 else (d, w)
                if not _fit(grid, placed, foreign, x, z, ex, ez): continue
                sides = ('north', 'south') if ori == 0 else ('west', 'east')
                cx, cz = x + ex // 2, z + ez // 2
                best = min(sides, key=lambda sd: (grid.sdist(cx, cz, sd) - (ez if ori == 0 else ex) // 2, sd))
                return best, ex, ez
        return None

    def commit(name, x, z, side, ex, ez):
        placed.append((x, z, x + ex - 1, z + ez - 1))
        lots.append((name, side, x, z, x + ex - 1, z + ez - 1))

    heroes = list(HEROES[district])
    for name in heroes:
        for (x, z) in anchors:
            res = try_design(name, x, z)
            if res:
                commit(name, x, z, *res); break
    mix = MIX[district]
    k = 0
    count = {}
    for l in lots: count[l[0]] = count.get(l[0], 0) + 1
    for (x, z) in anchors:
        for t in range(len(mix)):
            name = mix[(k + t) % len(mix)]
            if count.get(name, 0) >= QUOTA[name]: continue
            res = try_design(name, x, z)
            if res:
                commit(name, x, z, *res)
                count[name] = count.get(name, 0) + 1
                k = (k + t + 1) % len(mix)
                break
    fine = [(x, z) for (x, z) in ((a, b) for a in range(grid.x0, grid.x1 + 1) for b in range(grid.z0, grid.z1 + 1))
            if 134 <= math.hypot(x, z) <= 297]
    fine.sort(key=lambda p: (round(math.hypot(*p) / 22.0), abs(PL.ang_diff(PL.plan_angle(*p), pa)), p))
    for name in FILL[district]:
        for (x, z) in fine:
            res = try_design(name, x, z)
            if res: commit(name, x, z, *res)
    return lots


# ------------------------------------------------------------------------------------------------------------
# features
# ------------------------------------------------------------------------------------------------------------
def _make_all():
    feats = []
    counters = {}
    for district in ('F', 'W'):
        lots = plan_lots(district)
        for (name, side, x0, z0, x1, z1) in lots:
            counters[name] = counters.get(name, 0) + 1
            fname = '%s_%02d' % (name, counters[name])
            r = math.hypot((x0 + x1) / 2.0, (z0 + z1) / 2.0)
            hf = max(0.0, min(1.0, 1.0 - (r - 150.0) / 140.0))
            hf = min(1.0, hf * 0.8 + 0.2 * ((hash_str(fname) % 100) / 100.0))
            cls = DESIGNS[name][0]
            spec = Spec(fname, name, district, side, x0, z0, x1, z1, hf)
            b = cls(spec)
            feats.append(BuildingFeature(b))
    return feats


class BuildingFeature(Feature):
    layer = LAYER

    def __init__(self, building):
        super().__init__(building.spec.name, building.bbox, LAYER, building.meta)
        self.b = building

    def plan(self, ctx=None):
        return self.b.plan(ctx)

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


FEATURES = _make_all()
register(*FEATURES)


def features():
    return list(FEATURES)
