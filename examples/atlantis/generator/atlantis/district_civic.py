"""Layer 40: the Commercial & Civic district (H, plan 180) and the Education, Culture & Botanical district (L, plan 240).

Both are inner-ring districts (r 135..295).  A deterministic lot planner scans the free cells of each district (not a
road or plaza, not within 3 blocks of an existing layer 40/50 feature, nor of another lot), places a few anchor
buildings next to the Civic Square / Botanical Court and then fills the remaining frontage with smaller designs.

DESIGNS (name: silhouette)
  H  market_hall    long barrel-vaulted hall (glass crown) with side aisles, stalls and a roof terrace
     library        stepped three-storey reading hall with mezzanine ring, shelves, lecterns, glass lantern
     museum         wide temple-like gallery block, front colonnade, tall central atrium, display pedestals
     cafeteria      round glass drum, two floors, curved counter, tables, planted roof terrace
     admin_tower    tapering stepped office tower with corner fins and a spire
     shop_arcade    low two-storey shop house with a front arcade and a stepped parapet
     corner_shop    shop block with a quarter-round corner
     office_crescent crescent-shaped office slab, tall, with vertical fins
     trade_tower    octagonal tower with a ring crown
     kiosk          small round pavilion with a low glass dome
  L  conservatory   large glass barrel vault full of trees, flowers and paths
     terraced_garden stepped botanical garden hill over a garden hall
     academy        U-shaped school around a courtyard, central clock tower, classrooms
     auditorium     semicircular raked hall with stage and low glass half-dome
     garden_pavilion round open pavilion with glass dome
     greenhouse     gable glass house with rows of planters
     study_hall     colonnaded lecture block with terrace setbacks

CONVENTIONS
  * Every building is a `Plan` (dict of cells) built once per process and copied into the tile column by column.  The
    plan refuses cells outside its bbox / district, on road cells or above ctx.max_building_top (Plan.rejected).
  * Floors: level 0 stands on the deck (floor slab y = 4, walk y = 5); `Lvl.h` is the storey height (slab to slab).
  * Roofs are walkable terraces with railings reachable by an internal stair (the walk validator wants every standable
    surface connected); vaults and domes keep a slope of at most one block per column so they stay climbable.
  * Light: after the shell and furniture are placed, `autolight` runs the validator's light model on the plan and adds
    hidden light blocks wherever a walkable cell would stay dark.
"""
import math
import random
from types import SimpleNamespace

from . import config, planner as PL, roads, palette as P, blocks as B, geometry as G
from . import feature as FT
from .feature import Feature, register
from .util import hash_str

LAYER = 40
DECK = PL.DECK_TOP
WALK = PL.WALK_Y
D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIRN = {'east': (1, 0), 'west': (-1, 0), 'south': (0, 1), 'north': (0, -1)}
FACE_OF = {v: k for k, v in DIRN.items()}

WALL = P.WHITE                      # smooth_quartz
BLOCK = P.WHITE_BLOCK
BRICKS = P.WHITE_BRICKS
PILLAR = 'quartz_pillar[axis=y]'
GLASS = P.GLASS_BLUE
GPANE = P.GLASS_BLUE_PANE
CPANE = P.GLASS_CYAN_PANE
WPANE = 'white_stained_glass_pane'
CYAN = P.CYAN
LAMP = P.LAMP
LIGHT = P.HIDDEN_LIGHT
PRIS = P.PRISMARINE_BRICKS
DARKP = P.DARK_PRISMARINE
LEAVES = 'oak_leaves[distance=1,persistent=true]'
LEAVES2 = 'azalea_leaves[distance=1,persistent=true]'
LEAVES3 = 'flowering_azalea_leaves[distance=1,persistent=true]'
FLOWERS = ('poppy', 'blue_orchid', 'allium', 'azure_bluet', 'cornflower', 'lily_of_the_valley', 'dandelion',
           'oxeye_daisy')

_CTX = config.load()
_SIN, _COS = math.sin, math.cos


def _round(v):
    return int(math.floor(v + 0.5))


def stairs(mat, facing, half='bottom'):
    return B.stairs(mat, facing, half)


def slab(mat, typ='bottom'):
    return B.slab(mat, typ)


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

    def cap(self, x, z):
        v = self.colinfo.get((x, z))
        if v is None:
            ok = PL.district_at(x, z) == self.district and roads.surface(x, z) is None
            v = self.colinfo[(x, z)] = int(self.ctx.max_building_top(x, z)) if ok else -999
        return v

    def inside(self, x, y, z):
        bb = self.bbox
        return bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5]

    def set(self, x, y, z, b):
        if b is None: return
        if not self.inside(x, y, z) or y > self.cap(x, z):
            self.rejected += 1
            if len(self.first_rejects) < 8: self.first_rejects.append((x, y, z, b))
            return
        self.c[(x, y, z)] = b

    def soft(self, x, y, z, b):
        """Set only where legal; silently skip otherwise (decoration at the edge of the lot)."""
        if self.inside(x, y, z) and y <= self.cap(x, z): self.c[(x, y, z)] = b

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
        todo = [(k, b) for k, b in c.items() if 'north=' not in b and
                b.split('[', 1)[0].endswith(('_pane', 'iron_bars', '_fence', '_wall'))]
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
# cell-set shapes (world coordinates)
# ------------------------------------------------------------------------------------------------------------
def rect(x0, z0, x1, z1):
    return {(x, z) for x in range(min(x0, x1), max(x0, x1) + 1) for z in range(min(z0, z1), max(z0, z1) + 1)}


def disc(cx, cz, r):
    return set(G.disc_cells(cx, cz, r))


def edge_of(plate):
    return {c for c in plate if any((c[0] + dx, c[1] + dz) not in plate for dx, dz in D4)}


def grow(plate, n=1):
    s = set(plate)
    for _ in range(n):
        s |= {(x + dx, z + dz) for x, z in s for dx, dz in D4}
    return s


def components(cells):
    cells = set(cells)
    out = []
    while cells:
        s = cells.pop(); comp = {s}; st = [s]
        while st:
            x, z = st.pop()
            for dx, dz in D4:
                n = (x + dx, z + dz)
                if n in cells: cells.discard(n); comp.add(n); st.append(n)
        out.append(comp)
    return out


def dist_to_edge(plate):
    """BFS distance of each plate cell to the nearest cell outside the plate (edge cells = 1)."""
    from collections import deque
    d = {}
    dq = deque()
    for c in edge_of(plate):
        d[c] = 1; dq.append(c)
    while dq:
        x, z = dq.popleft()
        for dx, dz in D4:
            n = (x + dx, z + dz)
            if n in plate and n not in d:
                d[n] = d[(x, z)] + 1; dq.append(n)
    return d


def path_cells(src, dst, allowed):
    """Shortest 4-connected path from src to dst through `allowed` cells (list) or None."""
    from collections import deque
    if src not in allowed or dst not in allowed: return None
    prev = {src: None}
    dq = deque([src])
    while dq:
        c = dq.popleft()
        if c == dst:
            out = []
            while c is not None: out.append(c); c = prev[c]
            return out
        for dx, dz in D4:
            n = (c[0] + dx, c[1] + dz)
            if n in allowed and n not in prev:
                prev[n] = c; dq.append(n)
    return None


def prof_slope(f, cells, hmax=None):
    """Heights f(c) -> dict, with neighbouring columns clamped to differ by at most 1 (climbable)."""
    h = {c: max(0, int(f(c))) for c in cells}
    if hmax is not None:
        for c in h: h[c] = min(h[c], hmax)
    changed = True
    while changed:
        changed = False
        for (x, z), v in list(h.items()):
            for dx, dz in D4:
                n = (x + dx, z + dz)
                if n in h and h[n] > v + 1:
                    h[n] = v + 1; changed = True
    return h


# ------------------------------------------------------------------------------------------------------------
# the building engine
# ------------------------------------------------------------------------------------------------------------
class Lvl:
    def __init__(self, plate, h, F, wall='win', kind='hall', void=None, mat=None):
        self.plate = set(plate)
        self.h, self.F, self.wall, self.kind = h, F, wall, kind
        self.void = set(void or ())
        self.mat = mat or {}
        self.edge = edge_of(self.plate)
        self.inner = self.plate - self.edge
        self.res = set()          # reserved (furniture keeps out)
        self.occ = set()          # occupied by furniture
        self.W = F + 1
        self.holes = set()


class _DryMarker: pass


class Bld:
    """Builder of one building: levels (plates), shell, door, stairs, roof, furniture, light."""

    def __init__(self, plan, lot, rng, style=None):
        self.p, self.lot, self.rng = plan, lot, rng
        self.style = dict(style or {})
        self.L = []
        self.roof = {'hv': {}, 'mat': None, 'rail': True, 'posts': True}
        self.door_cells = []
        self.entrances = []
        self.vest = []                       # (x, z) cells just inside the doors
        self.stair_log = []
        self.roof_res = set()
        self.tres = set()
        self.F0 = DECK
        # local frame
        self.fc = lot.facing
        x0, z0, x1, z1 = lot.rect
        self.x0, self.z0, self.x1, self.z1 = x0, z0, x1, z1
        if self.fc in ('north', 'south'): self.Wd, self.Dp = x1 - x0 + 1, z1 - z0 + 1
        else: self.Wd, self.Dp = z1 - z0 + 1, x1 - x0 + 1

    # ---- local frame (u across, v depth from the front wall)
    def T(self, u, v):
        f = self.fc
        if f == 'south': return (self.x0 + u, self.z1 - v)
        if f == 'north': return (self.x1 - u, self.z0 + v)
        if f == 'east': return (self.x1 - v, self.z0 + u)
        return (self.x0 + v, self.z1 - u)

    def UV(self, x, z):
        f = self.fc
        if f == 'south': return (x - self.x0, self.z1 - z)
        if f == 'north': return (self.x1 - x, z - self.z0)
        if f == 'east': return (z - self.z0, self.x1 - x)
        return (self.z1 - z, x - self.x0)

    def lrect(self, u0, v0, u1, v1):
        return {self.T(u, v) for u in range(min(u0, u1), max(u0, u1) + 1) for v in range(min(v0, v1), max(v0, v1) + 1)}

    def ldisc(self, cu, cv, r):
        cx, cz = self.T(cu, cv)
        # T maps integer to integer; for half-integer centres use the mean of the corner images
        if cu != int(cu) or cv != int(cv):
            ax, az = self.T(int(math.floor(cu)), int(math.floor(cv)))
            bx, bz = self.T(int(math.ceil(cu)), int(math.ceil(cv)))
            cx, cz = (ax + bx) / 2.0, (az + bz) / 2.0
        return disc(cx, cz, r)

    def lpt(self, u, v):
        return self.T(u, v)

    def out_vec(self):
        return DIRN[self.fc]

    # ---- levels
    def level(self, plate, h, wall='win', kind='hall', void=None, mat=None):
        F = self.F0 if not self.L else self.L[-1].F + self.L[-1].h
        lv = Lvl(plate, h, F, wall, kind, void, mat)
        self.L.append(lv)
        return lv

    @property
    def n(self):
        return len(self.L)

    def Fy(self, i):
        return self.L[i].F if i < self.n else self.L[-1].F + self.L[-1].h

    # ---- materials
    def m(self, key, default=None):
        return self.style.get(key, default)

    def floor_block(self, x, z, i):
        a = self.m('floor', BLOCK)
        if (x % 4 == 0 and z % 4 == 0): return self.m('floor_acc', 'cyan_terracotta')
        return a if (x + z) % 2 == 0 else self.m('floor2', WALL)

    def pillar_at(self, x, z):
        return (3 * x + 5 * z) % 4 == 0

    def wall_block(self, lv, x, z, r, ceil=False):
        """Block of wall column (x, z) at relative row r (1..h-1)."""
        st = lv.wall
        h = lv.h
        wb = lv.mat.get('wall', self.m('wall', WALL))
        pil = lv.mat.get('pillar', self.m('pillar', PILLAR))
        gp = lv.mat.get('pane', self.m('pane', GPANE))
        pillar = self.pillar_at(x, z)
        if st == 'solid':
            if pillar: return pil
            if r in (2, 3) and (x + z) % 8 == 2: return gp
            return wb
        if st == 'glass':
            if pillar: return pil
            return wb if r == 1 else gp
        if st == 'arcade':
            if pillar: return pil
            return BRICKS if r == h - 1 else None
        if st == 'open':
            if (x + z) % 3 == 0: return pil
            return wb if r == h - 1 else None
        # 'win'
        if pillar: return pil
        if r == 1 or r == h - 1: return wb
        return gp

    # ---- shell
    def build_shell(self):
        p, L, n = self.p, self.L, self.n
        hv = self.roof['hv']
        # walls
        for i, lv in enumerate(L):
            for (x, z) in lv.edge:
                for r in range(1, lv.h):
                    b = self.wall_block(lv, x, z, r)
                    if b: p.set(x, lv.F + r, z, b)
        # floors / roofs: slab at F_i over the plate below
        for i in range(n + 1):
            Fy = self.Fy(i)
            below = L[i - 1] if i >= 1 else L[0]
            up = L[i] if i < n else None
            for c in below.plate:
                x, z = c
                if up is not None and i >= 1 and c in up.void: continue
                if i == n and hv.get(c, 0) > 0:
                    self._roof_cell(c, Fy, hv[c], below)
                    continue
                if i == n:
                    p.set(x, Fy, z, self.roof_block(x, z))
                else:
                    p.set(x, Fy, z, self.floor_block(x, z, i) if (i == 0 or c in up.plate) else self.roof_block(x, z))
        # parapets on every roof (terrace) of level i not covered by the next level
        for i in range(n):
            lv = L[i]
            nxt = L[i + 1].plate if i + 1 < n else set()
            Fy = self.Fy(i + 1)
            for (x, z) in lv.edge:
                if (x, z) in nxt: continue
                if i + 1 == n and hv.get((x, z), 0) > 0: continue
                if i + 1 < n and not any((x + dx, z + dz) in lv.inner and (x + dx, z + dz) not in nxt for dx, dz in D4): continue
                self.rail(x, Fy + 1, z)
        # void rails
        for i in range(1, n):
            lv = L[i]
            for (x, z) in lv.void:
                if any((x + dx, z + dz) in L[i].plate and (x + dx, z + dz) not in lv.void for dx, dz in D4):
                    p.set(x, lv.F, z, self.m('voidrail', GPANE)); p.set(x, lv.F + 1, z, self.m('voidrail', GPANE))

    def roof_block(self, x, z):
        return self.m('roof', WALL) if (x + z) % 2 == 0 else self.m('roof2', BLOCK)

    def rail(self, x, y, z):
        self.p.set(x, y, z, self.m('rail', GPANE))

    def _roof_cell(self, c, Fy, h, below):
        x, z = c
        p = self.p
        mat = self.roof['mat']
        blk = mat(x, z, h) if mat else GLASS
        if c in below.edge:
            gl = self.m('gable', GPANE)
            for y in range(Fy, Fy + h):
                p.set(x, y, z, PILLAR if self.pillar_at(x, z) else (gl if y > Fy else self.m('wall', WALL)))
        p.set(x, Fy + h, z, blk)

    # ---- door
    def door(self, width=2, face=None, lvl=0, rows=3, near=None):
        """Cut the front door (the edge cell with the smallest depth v, near u centre).  Returns the feet cell."""
        lv = self.L[lvl]
        ov = DIRN[face or self.fc]
        cands = []
        for (x, z) in lv.edge:
            o = (x + ov[0], z + ov[1])
            if o in lv.plate: continue
            if near is not None:
                key = abs(x - near[0]) + abs(z - near[1])
            else:
                u, v = self.UV(x, z)
                key = v * 1.0 + 0.35 * abs(u - (self.Wd - 1) / 2.0)
            cands.append((key, x, z))
        cands.sort()
        for key, x, z in cands:
            tx, tz = (1, 0) if ov[0] == 0 else (0, 1)
            cells = [(x, z)]
            for s in (1, -1):
                q = (x + s * tx, z + s * tz)
                if width >= 2 and q in lv.edge and (q[0] + ov[0], q[1] + ov[1]) not in lv.plate and len(cells) < width:
                    cells.append(q)
            if len(cells) < width: continue
            self._cut_door(lv, cells, ov, rows)
            return (x, z)
        raise RuntimeError('no door position for ' + self.p.name)

    def _cut_door(self, lv, cells, ov, rows):
        p = self.p
        F = lv.F
        for (x, z) in cells:
            for r in range(1, rows + 1): p.pop(x, F + r, z)
            self.door_cells.append((x, z))
            if lv.h > rows + 2: p.set(x, F + rows + 1, z, self.m('transom', GPANE))
            for k in (1, 2):
                self.vest.append((x - ov[0] * k, z - ov[1] * k))
        # frame pillars either side
        tx, tz = (1, 0) if ov[0] == 0 else (0, 1)
        sc = sorted(cells)
        lo, hi = sc[0], sc[-1]
        for q in ((lo[0] - tx, lo[1] - tz), (hi[0] + tx, hi[1] + tz)):
            if q in lv.edge:
                for r in range(1, lv.h): p.set(q[0], F + r, q[1], PILLAR)
        e = cells[0]
        self.entrances.append((e[0], F + 1, e[1]))
        lv.res.update(self.vest)
        lv.res.update(cells)

    # ---- stairs
    def stairs_up(self, i, width=2, prefer=None, region=None):
        """Stair flight from level i to level i + 1 (or the roof when i is the top level)."""
        L, n, p = self.L, self.n, self.p
        lv = L[i]
        h = lv.h
        up = L[i + 1] if i + 1 < n else None
        hv = self.roof['hv']
        wd = dist_to_edge(lv.plate)
        door_c = self.door_cells[0] if self.door_cells else (self.x0, self.z0)
        best = None
        for (x0, z0) in sorted(lv.inner):
            for dname, (dx, dz) in DIRN.items():
                px, pz = -dz, dx
                ok = True
                cells = []
                for k in range(-1, h + 2):
                    for j in range(width):
                        c = (x0 + k * dx + j * px, z0 + k * dz + j * pz)
                        cells.append((k, j, c))
                for k, j, c in cells:
                    if k <= h - 1:
                        if c not in lv.inner or c in lv.res or c in lv.occ or c in lv.void or c in lv.holes: ok = False; break
                        if k >= h - 4:           # hole under the upper floor
                            if not self._top_ok(c, up, hv, region): ok = False; break
                    else:                        # exit cells (k == h, h + 1)
                        if c not in lv.inner or c in lv.holes: ok = False; break
                        if not self._top_ok(c, up, hv, region): ok = False; break
                if not ok: continue
                lane0 = [c for k, j, c in cells if j == 0 and 0 <= k < h]
                score = sum(wd[c] for c in lane0) / float(len(lane0))
                far = math.hypot(x0 - door_c[0], z0 - door_c[1])
                score += -0.04 * far
                if prefer is not None: score += 0.05 * math.hypot(x0 - prefer[0], z0 - prefer[1])
                score += ((x0 * 7 + z0 * 13 + i * 5) % 5) * 0.01
                if best is None or score < best[0]: best = (score, x0, z0, dname)
        if best is None:
            return self.spiral_up(i, region)
        _, x0, z0, dname = best
        dx, dz = DIRN[dname]
        px, pz = -dz, dx
        Fy = self.Fy(i + 1)
        mat = self.m('stair', WALL)
        for k in range(h):
            for j in range(width):
                x, z = x0 + k * dx + j * px, z0 + k * dz + j * pz
                p.set(x, lv.F + 1 + k, z, stairs(mat, dname))
                if k >= h - 4 and k < h - 1: p.pop(x, Fy, z)
                lv.res.add((x, z)); lv.occ.add((x, z))
                if k >= h - 4: lv.holes.add((x, z))
        # reserve approach / exit / holes
        for j in range(-1, width + 1):
            for k in (-1, -2, -3):
                lv.res.add((x0 + k * dx + j * px, z0 + k * dz + j * pz))
        for j in range(width):
            for k in range(h - 4, h + 2):
                (up.res if up is not None else self.roof_res).add((x0 + k * dx + j * px, z0 + k * dz + j * pz))
            for k in (h, h + 1):
                lv.res.add((x0 + k * dx + j * px, z0 + k * dz + j * pz))
        self.stair_log.append((i, x0, z0, dname, width))
        foot = (x0 - dx, z0 - dz)
        return foot, (x0 + h * dx, z0 + h * dz)

    @staticmethod
    def _top_ok(c, up, hv, region):
        if region is not None: return c in region
        if up is not None: return c in up.inner and c not in up.void and c not in up.res
        return hv.get(c, 0) == 0

    def spiral_up(self, i, region=None):
        """3x3 spiral stair (centre pillar) from level i to the next level / roof: fallback for small plates."""
        L, n, p = self.L, self.n, self.p
        lv = L[i]
        h = lv.h
        up = L[i + 1] if i + 1 < n else None
        hv = self.roof['hv']
        R = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2), (0, 2), (0, 1)]
        wd = dist_to_edge(lv.plate)

        def lfree(c):
            return c in lv.inner and c not in lv.res and c not in lv.occ and c not in lv.void and c not in lv.holes

        def top_ok(c):
            return self._top_ok(c, up, hv, region)
        best = None
        for (bx, bz) in sorted(lv.inner):
            box = [(bx + a, bz + b) for a, b in R]
            ctr = (bx + 1, bz + 1)
            if not all(lfree(c) for c in box) or ctr not in lv.inner or ctr in lv.res or ctr in lv.occ: continue
            for s in range(8):
                for dr in (1, -1):
                    ring = [box[(s + dr * k) % 8] for k in range(h)]
                    bs = set(box)
                    a0 = [(ring[0][0] + dx, ring[0][1] + dz) for dx, dz in D4 if (ring[0][0] + dx, ring[0][1] + dz) not in bs and (ring[0][0] + dx, ring[0][1] + dz) != ctr]
                    ex = [(ring[-1][0] + dx, ring[-1][1] + dz) for dx, dz in D4 if (ring[-1][0] + dx, ring[-1][1] + dz) not in bs and (ring[-1][0] + dx, ring[-1][1] + dz) != ctr]
                    a0 = [c for c in a0 if lfree(c)]
                    ex = [c for c in ex if lfree(c) and top_ok(c)]
                    if not a0 or not ex: continue
                    if not all(top_ok(c) for c in ring[h - 4:]): continue
                    score = wd[ctr] + ((bx * 7 + bz * 13 + s) % 5) * 0.01
                    if best is None or score < best[0]: best = (score, ring, ctr, a0[0], ex[0], box)
        if best is None:
            raise RuntimeError('no stair position in %s level %d' % (self.p.name, i))
        _, ring, ctr, a0, ex, box = best
        Fy = self.Fy(i + 1)
        mat = self.m('stair', WALL)
        for k, c in enumerate(ring):
            nxt = ring[k + 1] if k + 1 < h else ex
            face = FACE_OF[(nxt[0] - c[0], nxt[1] - c[1])]
            p.set(c[0], lv.F + 1 + k, c[1], stairs(mat, face))
            if k >= h - 4 and k < h - 1: p.pop(c[0], Fy, c[1])
            if k >= h - 4: lv.holes.add(c)
            (up.res if (up is not None and k >= h - 4) else lv.res).add(c)
            lv.occ.add(c); lv.res.add(c)
        for y in range(lv.F + 1, Fy): p.set(ctr[0], y, ctr[1], PILLAR)
        lv.occ.add(ctr); lv.res.add(ctr)
        lv.res.add(a0)
        if up is not None:
            up.res.add(ex)
            up.res.update(ring[h - 4:])
        else:
            self.roof_res.update(ring[h - 4:]); self.roof_res.add(ex)
        lv.res.add(ex)
        self.stair_log.append((i, ctr[0], ctr[1], 'spiral', 3))
        return a0, ex

    def reserve_route(self, i, a, b):
        lv = self.L[i]
        allowed = {c for c in lv.inner if c not in lv.void and c not in lv.holes and c not in lv.occ}
        pth = path_cells(a, b, allowed)
        if pth:
            for c in pth:
                lv.res.add(c)
                for dx, dz in D4:
                    q = (c[0] + dx, c[1] + dz)
                    if q in allowed and len(pth) < 40: pass
        return pth

    def stairs_chain(self, width=2, top=True):
        """Stairs between all levels (and to the roof) plus reserved routes from the door."""
        feet = None
        prev_exit = None
        for i in range(self.n if top else self.n - 1):
            try:
                foot, ex = self.stairs_up(i, width)
            except RuntimeError:
                if i == self.n - 1 and len(self.L[i].plate) <= 44: continue       # tiny roof: nothing to reach
                raise
            lv = self.L[i]
            if i == 0 and self.vest:
                self.reserve_route(0, self.vest[-1], foot)
            elif prev_exit is not None:
                self.reserve_route(i, prev_exit, foot)
            prev_exit = ex
            if i + 1 < self.n: self.L[i + 1].res.update([ex])

    # ---- terrace doors: every setback terrace gets a door from the level above
    def terrace_access(self):
        p = self.p
        self.unserved = []
        for i in range(self.n - 1):
            lv, up = self.L[i], self.L[i + 1]
            terr = (lv.inner - up.plate)
            for comp in components(terr):
                best = None
                for (x, z) in sorted(comp):
                    for dx, dz in D4:
                        e = (x + dx, z + dz)               # candidate wall cell of the upper level
                        if e in up.edge:
                            inside = (e[0] + dx, e[1] + dz)
                            if inside in up.inner and inside not in up.void and inside not in up.holes:
                                # prefer far from the door pillar rhythm
                                key = (self.pillar_at(*e), (e[0] * 3 + e[1] * 5) % 7)
                                if best is None or key < best[0]: best = (key, e, inside, (dx, dz))
                if best is None:
                    if len(comp) > 36: self.unserved.append((i, comp))
                    continue
                _, e, inside, ov = best
                F = up.F
                for r in (1, 2, 3): p.pop(e[0], F + r, e[1])
                up.res.update([e, inside, (inside[0] + ov[0], inside[1] + ov[1])])
                self.door_cells.append(e)
                self.tres.update([(e[0] - ov[0], e[1] - ov[1]), (e[0] - 2 * ov[0], e[1] - 2 * ov[1])])

    # ---- helpers for furniture
    def free(self, i, cells):
        lv = self.L[i]
        for c in cells:
            if c not in lv.inner or c in lv.res or c in lv.occ or c in lv.void or c in lv.holes: return False
        return True

    def put(self, i, x, dy, z, b, occ=True):
        lv = self.L[i]
        self.p.set(x, lv.W + dy, z, b)
        if occ and dy <= 1: lv.occ.add((x, z))

    def terrace_stairs(self):
        for i, comp in getattr(self, 'unserved', []):
            self.stairs_up(i, 2, region=comp)

    def lane_cells(self):
        if getattr(self, '_lanes', None) is None:
            ov = DIRN[self.fc]
            tx, tz = (1, 0) if ov[0] == 0 else (0, 1)
            ls = set()
            for (x, z) in self.door_cells:
                for k in range(0, 6):
                    for s in (-1, 0, 1):
                        ls.add((x + ov[0] * k + tx * s, z + ov[1] * k + tz * s))
            self._lanes = ls
        return self._lanes

    def take(self, i, cells):
        self.L[i].occ.update(cells)


# ------------------------------------------------------------------------------------------------------------
# furniture (each takes the builder, a level index and coordinates; returns True when placed)
# ------------------------------------------------------------------------------------------------------------
def f_shelf(b, i, x, z, h=3, block='bookshelf'):
    if not b.free(i, [(x, z)]): return False
    for k in range(h): b.put(i, x, k, z, block)
    return True


def f_lectern(b, i, x, z, face):
    if not b.free(i, [(x, z)]): return False
    b.put(i, x, 0, z, 'lectern[facing=%s,has_book=true,powered=false]' % face)
    return True


def f_table(b, i, x, z, w=2, d=2, top=None, seats=True, lamp=False):
    cells = [(x + a, z + c) for a in range(w) for c in range(d)]
    ring = grow(set(cells)) - set(cells)
    if not b.free(i, cells): return False
    top = top or slab(WALL, 'top')
    for (cx, cz) in cells:
        b.put(i, cx, 0, cz, top)
    if lamp: b.put(i, x, 1, z, 'lantern[hanging=false]', occ=False)
    if seats:
        for (sx, sz) in sorted(ring):
            if (sx in (x - 1, x + w)) == (sz in (z - 1, z + d)): continue       # skip diagonal corners
            if b.free(i, [(sx, sz)]):
                nx = max(x, min(x + w - 1, sx)); nz = max(z, min(z + d - 1, sz))
                face = FACE_OF[(sx - nx, sz - nz)]
                b.put(i, sx, 0, sz, stairs(WALL, face))
    return True


def f_counter(b, i, cells, top='smooth_quartz_slab[type=bottom]', base=BLOCK, deco=None):
    cells = list(cells)
    if not b.free(i, cells): return False
    for k, (x, z) in enumerate(cells):
        b.put(i, x, 0, z, base)
        b.put(i, x, 1, z, top)
        if deco and k % 3 == 1: b.put(i, x, 2, z, deco, occ=False)
    return True


def f_case(b, i, x, z, exhibit=None):
    if not b.free(i, [(x, z)]): return False
    ex = exhibit or b.rng.choice(['gold_block', 'lapis_block', 'amethyst_block', 'prismarine_bricks', 'emerald_block',
                                  'copper_block', 'sea_lantern', 'diamond_block', 'purpur_block'])
    b.put(i, x, 0, z, BLOCK)
    b.put(i, x, 1, z, ex)
    b.put(i, x, 2, z, P.GLASS)
    return True


def f_planter(b, i, x, z, plant=None):
    if not b.free(i, [(x, z)]): return False
    b.put(i, x, 0, z, 'moss_block')
    b.put(i, x, 1, z, plant or b.rng.choice(('azalea', 'flowering_azalea') + FLOWERS), occ=False)
    return True


def f_tree(b, i, x, z, hgt=4, leaf=LEAVES):
    lv = b.L[i]
    cells = [(x, z)]
    if not b.free(i, cells): return False
    tr = b.__dict__.setdefault('trees', [])
    if any(max(abs(x - a), abs(z - c)) < 6 for a, c in tr): return False
    top = lv.F + lv.h - 1
    hgt = min(hgt, top - lv.W - 1)
    if hgt < 2: return False
    b.put(i, x, -1, z, 'grass_block')
    for k in range(hgt): b.put(i, x, k, z, 'oak_log[axis=y]')
    y = lv.W + hgt
    for dy, r in ((-1, 2), (0, 2), (1, 1)):
        for ax in range(-r, r + 1):
            for az in range(-r, r + 1):
                if abs(ax) + abs(az) > r + 1 or (ax == 0 and az == 0 and dy < 1): continue
                yy = y + dy
                if yy > top: continue
                q = (x + ax, z + az)
                if q in lv.inner and q not in lv.res and b.p.get(q[0], yy, q[1]) is None:
                    b.p.set(q[0], yy, q[1], leaf)
    b.p.set(x, y + 1 if y + 1 <= top else y, z, leaf)
    tr.append((x, z))
    return True


def f_bench(b, i, x, z, face):
    if not b.free(i, [(x, z)]): return False
    b.put(i, x, 0, z, stairs(WALL, face))
    return True


def f_desks(b, i, x, z, face, n=3):
    dx, dz = DIRN[face]
    px, pz = -dz, dx
    cells = [(x + k * px, z + k * pz) for k in range(n)]
    seats = [(c[0] - dx, c[1] - dz) for c in cells]
    if not b.free(i, cells + seats): return False
    for (cx, cz) in cells: b.put(i, cx, 0, cz, slab(WALL, 'top'))
    b.put(i, cells[0][0], 1, cells[0][1], 'lantern[hanging=false]', occ=False)
    for (sx, sz) in seats: b.put(i, sx, 0, sz, stairs(WALL, face))
    return True


def ring1(b, i):
    lv = b.L[i]
    return sorted(c for c in lv.inner if any((c[0] + dx, c[1] + dz) in lv.edge for dx, dz in D4))


def toward_wall(b, i, c):
    lv = b.L[i]
    for d, (dx, dz) in DIRN.items():
        if (c[0] + dx, c[1] + dz) in lv.edge: return d
    return 'north'


def open_cells(b, i):
    lv = b.L[i]
    r1 = set(ring1(b, i))
    return sorted(c for c in lv.inner if c not in r1 and c not in lv.res and c not in lv.occ and c not in lv.void)


def furnish_level(b, i):
    """Fill level i according to its kind."""
    lv = b.L[i]
    k = lv.kind
    rng = b.rng
    r1 = ring1(b, i)
    op = open_cells(b, i)
    top = lv.h - 3                     # max furniture height
    if k in ('reading', 'library'):
        for n, c in enumerate(r1):
            if n % 4 != 3 and (c[0] + c[1]) % 2 == 0 or n % 4 == 0:
                f_shelf(b, i, c[0], c[1], min(3, top), 'bookshelf')
        for n, c in enumerate(op):
            if c[0] % 6 == 1 and c[1] % 6 == 1: f_table(b, i, c[0], c[1], 2, 2, lamp=True)
            elif c[0] % 6 == 4 and c[1] % 6 == 4: f_lectern(b, i, c[0], c[1], 'south')
    elif k == 'gallery':
        for n, c in enumerate(r1):
            if n % 5 == 2: f_case(b, i, c[0], c[1])
        for c in op:
            if c[0] % 7 == 3 and c[1] % 7 == 3:
                f_case(b, i, c[0], c[1])
            elif c[0] % 7 == 0 and c[1] % 7 == 5:
                f_bench(b, i, c[0], c[1], 'north')
    elif k == 'market':
        for c in op:
            if c[0] % 5 == 2 and c[1] % 6 == 2:
                f_counter(b, i, [(c[0] + a, c[1]) for a in range(3)], deco=rng.choice(('azalea', 'lantern[hanging=false]', 'flowering_azalea')))
        for n, c in enumerate(r1):
            if n % 6 == 1: f_planter(b, i, c[0], c[1])
            elif n % 6 == 4 and top >= 3: f_shelf(b, i, c[0], c[1], 2, 'barrel[facing=up]')
    elif k == 'cafe':
        for c in op:
            if c[0] % 6 == 2 and c[1] % 5 == 2: f_table(b, i, c[0], c[1], 1, 1, lamp=True)
        cs = [c for c in r1 if (c[0] + c[1]) % 5 != 0]
        f_counter(b, i, cs[:min(9, len(cs))]) if cs and False else None
        for n, c in enumerate(r1):
            if n % 5 == 0: f_planter(b, i, c[0], c[1])
    elif k == 'office':
        for c in op:
            if c[0] % 5 == 2 and c[1] % 4 == 1: f_desks(b, i, c[0], c[1], rng.choice(('north', 'south')), 3)
        for n, c in enumerate(r1):
            if n % 5 == 1: f_shelf(b, i, c[0], c[1], min(3, top), 'bookshelf')
            elif n % 7 == 3: f_planter(b, i, c[0], c[1])
    elif k == 'shop':
        for n, c in enumerate(r1):
            if n % 3 != 2: f_counter(b, i, [c], deco=rng.choice(('lantern[hanging=false]', 'azalea', None)))
        for c in op:
            if c[0] % 5 == 2 and c[1] % 5 == 2: f_case(b, i, c[0], c[1])
    elif k == 'classroom':
        for c in op:
            if c[0] % 4 == 1 and c[1] % 3 == 1: f_desks(b, i, c[0], c[1], 'north', 2)
        for n, c in enumerate(r1):
            if n % 4 == 1: f_shelf(b, i, c[0], c[1], min(3, top), 'bookshelf')
            elif n % 4 == 3: f_lectern(b, i, c[0], c[1], toward_wall(b, i, c))
    elif k == 'garden':
        for n, c in enumerate(r1):
            if n % 3 == 0: f_planter(b, i, c[0], c[1])
        for c in op:
            if (c[0] * 5 + c[1] * 3) % 17 == 0: f_tree(b, i, c[0], c[1], 4)
            elif (c[0] + 2 * c[1]) % 5 == 0 and (c[0] % 3 == 0): f_planter(b, i, c[0], c[1])
    elif k == 'lobby':
        for n, c in enumerate(r1):
            if n % 6 == 0: f_planter(b, i, c[0], c[1])
            elif n % 6 == 3: f_bench(b, i, c[0], c[1], toward_wall(b, i, c))
        for c in op:
            if c[0] % 6 == 3 and c[1] % 6 == 3: f_case(b, i, c[0], c[1])


# ------------------------------------------------------------------------------------------------------------
# lighting: run the validator's light model on the plan, add hidden lights where a walkable cell stays dark
# ------------------------------------------------------------------------------------------------------------
def autolight(plan, margin=3):
    from collections import deque
    from .validate import classify as K
    c = plan.c
    bb = plan.bbox
    X0, X1, Z0, Z1 = bb[0] - margin, bb[3] + margin, bb[2] - margin, bb[5] + margin
    Y0, Y1 = DECK, bb[4] + 1
    info = K.info

    def cell(x, y, z):
        b = c.get((x, y, z))
        if b is None:
            return None
        return b

    def opaque(x, y, z):
        b = c.get((x, y, z))
        if b is None:
            return y < DECK + 1 and y <= DECK and False
        return info(b).opaque

    lvl = {}
    dq = deque()

    def add_source(x, y, z, v):
        if lvl.get((x, y, z), 0) >= v: return
        lvl[(x, y, z)] = v
        dq.append((x, y, z))

    for (x, y, z), b in c.items():
        e = info(b).emit
        if e: add_source(x, y, z, e)

    def run():
        while dq:
            x, y, z = dq.popleft()
            v = lvl[(x, y, z)]
            if v <= 1: continue
            for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                nx, ny, nz = x + dx, y + dy, z + dz
                if not (X0 <= nx <= X1 and Z0 <= nz <= Z1 and Y0 <= ny <= Y1): continue
                if lvl.get((nx, ny, nz), 0) >= v - 1: continue
                nb = c.get((nx, ny, nz))
                if nb is not None and info(nb).opaque: continue
                lvl[(nx, ny, nz)] = v - 1
                dq.append((nx, ny, nz))
    run()

    def airish(x, y, z):
        b = c.get((x, y, z))
        if b is None: return True
        return info(b).cls in (K.AIR, K.NOCOLL)

    def solidbelow(x, y, z):
        b = c.get((x, y, z))
        if b is None: return y <= DECK
        return info(b).cls in (K.FULL, K.PART)

    # walkable cells
    cols = {}
    for (x, y, z) in c: cols.setdefault((x, z), []).append(y)
    walk = []
    for x in range(X0, X1 + 1):
        for z in range(Z0, Z1 + 1):
            ys = cols.get((x, z))
            if ys is None:
                walk.append((x, DECK + 1, z))
                continue
            cand = set()
            for y in ys:
                cand.add(y + 1)
            cand.add(DECK + 1)
            for y in sorted(cand):
                if y > Y1: continue
                if airish(x, y, z) and airish(x, y + 1, z) and solidbelow(x, y - 1, z) and y - 1 >= DECK:
                    walk.append((x, y, z))
    walk.sort(key=lambda t: (t[1], t[0], t[2]))
    added = 0
    for (x, y, z) in walk:
        if lvl.get((x, y, z), 0) > 0: continue
        # place a hidden light at the nearest legal cell
        px, pz = min(max(x, bb[0]), bb[3]), min(max(z, bb[2]), bb[5])
        spot = None
        for yy in (y, y + 1, y + 2, y + 3):
            if (px, yy, pz) not in c and plan.inside(px, yy, pz) and plan.cap(px, pz) >= yy:
                spot = (px, yy, pz); break
        if spot is None: continue
        plan.c[spot] = LIGHT
        add_source(spot[0], spot[1], spot[2], 15)
        run()
        added += 1
    return added


# ------------------------------------------------------------------------------------------------------------
# decoration and the standard building pipeline
# ------------------------------------------------------------------------------------------------------------
def lamp_post(p, x, z, y0=DECK + 1, h=3):
    for k in range(h): p.soft(x, y0 + k, z, PILLAR)
    p.soft(x, y0 + h, z, LAMP)


def toward_wall_cells(lv, x, z):
    for d, (dx, dz) in DIRN.items():
        if (x + dx, z + dz) in lv.edge: return d
    return 'north'


def terrace_decor(b, garden=False):
    """Planters, benches and lamp posts on every flat terrace (setback roofs and the roof)."""
    p, L, n = b.p, b.L, b.n
    hv = b.roof['hv']
    for i, lv in enumerate(L):
        nxt = L[i + 1].plate if i + 1 < n else set()
        Fy = b.Fy(i + 1)
        top = i + 1 == n
        if top and not b.roof.get('decor', True): continue
        d = dist_to_edge(lv.plate)
        for (x, z) in sorted(lv.inner):
            if (x, z) in nxt: continue
            if top and (hv.get((x, z), 0) > 0 or (x, z) in b.roof_res): continue
            if (x, z) in b.tres: continue
            if (x, z) in lv.holes: continue
            if p.get(x, Fy + 1, z) is not None: continue
            dd = d.get((x, z), 9)
            if dd == 3: continue
            if dd == 2 and (x * 5 + z * 3) % 9 == 0:
                p.set(x, Fy + 1, z, stairs(WALL, toward_wall_cells(lv, x, z)))
            elif (x % 4 == 1 and z % 4 == 1):
                p.set(x, Fy + 1, z, 'moss_block' if not garden else 'grass_block')
                p.set(x, Fy + 2, z, b.rng.choice(('azalea', 'flowering_azalea') + FLOWERS))
            elif x % 7 == 3 and z % 7 == 3:
                lamp_post(p, x, z, Fy + 1, 2)
            elif garden and (x + 2 * z) % 3 == 0 and (x * 3 + z) % 5 == 0:
                p.set(x, Fy + 1, z, b.rng.choice(FLOWERS))


def forecourt(b):
    """Benches, planters and lamp posts in the apron around the building; the doorway lanes stay free."""
    p, lot = b.p, b.lot
    plate0 = b.L[0].plate
    ring = grow(plate0, 1) - plate0
    lanes = b.lane_cells()
    corners = {c for c in ring if sum(((c[0] + dx, c[1] + dz) in ring) for dx, dz in D4) <= 2}
    y = DECK + 1
    for (x, z) in sorted(ring):
        if (x, z) in lanes or not p.inside(x, y, z): continue
        if roads.surface(x, z) is not None: continue
        if (x, z) in corners and (x * 3 + z * 7) % 4 == 0:
            lamp_post(p, x, z, y, 3)
        elif (x * 5 + z * 3) % 11 == 0:
            p.soft(x, y, z, 'moss_block'); p.soft(x, y + 1, z, b.rng.choice(('azalea', 'flowering_azalea') + FLOWERS))
        elif (x * 3 + z * 5) % 13 == 1:
            near = [d for d, (dx, dz) in DIRN.items() if (x + dx, z + dz) in plate0]
            if near: p.soft(x, y, z, stairs(WALL, near[0]))
    bb = lot.bbox
    for x in range(bb[0], bb[3] + 1):
        for z in range(bb[2], bb[5] + 1):
            if (x, z) in plate0 or (x, z) in ring or (x, z) in lanes: continue
            if roads.surface(x, z) is not None or not p.inside(x, y, z): continue
            if (x * 7 + z * 5) % 17 == 0:
                lamp_post(p, x, z, y, 3)
            elif (x * 3 + z * 11) % 19 == 2:
                p.soft(x, y, z, 'moss_block'); p.soft(x, y + 1, z, b.rng.choice(('azalea',) + FLOWERS))


def complete(b, door_width=2, roof_access=True, hook=None, garden_roof=False, stair_width=2, furnish=True,
             door_near=None, terrace=True, post=None):
    if getattr(b, 'dry', False):
        b.door(width=door_width, near=door_near)
        raise _Dry()
    b.build_shell()
    b.door(width=door_width, near=door_near)
    if hook: hook(b)
    if terrace: b.terrace_access()
    b.stairs_chain(width=stair_width, top=roof_access)
    b.terrace_stairs()
    if furnish:
        for i in range(b.n): furnish_level(b, i)
    if roof_access: terrace_decor(b, garden=garden_roof)
    if post: post(b)
    forecourt(b)
    autolight(b.p)
    b.p.finish()
    return b


def roof_cone(b, cells, hmax, mat=None, margin=2):
    """Cone roof over `cells` eroded by `margin` (slope 1 from the edge of the set); the band left flat is the terrace."""
    cells = set(cells)
    for _ in range(margin): cells -= edge_of(cells)
    d = dist_to_edge(cells)
    hv = {c: min(hmax, d[c] - 1) for c in cells if d[c] - 1 > 0}
    b.roof['hv'].update(hv)
    if mat: b.roof['mat'] = mat
    return hv


# ------------------------------------------------------------------------------------------------------------
# DESIGNS: params(lot, rng) -> dict (storey heights, room above the roof) ; build(b, prm)
# ------------------------------------------------------------------------------------------------------------
def style_for(district):
    if district == 'H':
        return {'wall': WALL, 'pillar': PILLAR, 'pane': GPANE, 'rail': GPANE, 'floor': BLOCK, 'floor2': WALL,
                'floor_acc': 'cyan_terracotta', 'roof': WALL, 'roof2': BLOCK, 'stair': WALL}
    return {'wall': WALL, 'pillar': PILLAR, 'pane': CPANE, 'rail': CPANE, 'floor': BLOCK, 'floor2': 'calcite',
            'floor_acc': 'prismarine_bricks', 'roof': WALL, 'roof2': 'calcite', 'stair': WALL, 'gable': CPANE,
            'transom': CPANE, 'voidrail': CPANE}


def _levels_by_r(r, lo, hi):
    """Number of storeys between lo and hi: tall near r 150, lower outward."""
    t = max(0.0, min(1.0, (290.0 - r) / 140.0))
    return int(round(lo + (hi - lo) * t))


# ---- H -----------------------------------------------------------------------------------------------------
def p_market_hall(lot, rng): return {'hs': [7], 'extra': 7}


def d_market_hall(b, prm):
    W, D = b.Wd, b.Dp
    b.level(b.lrect(0, 0, W - 1, D - 1), 7, 'win', 'market')
    vc, hw = (D - 1) / 2.0, max(3.0, D / 2.0 - 4.0)

    def f(c):
        u, v = b.UV(*c)
        t = abs(v - vc)
        return 5.0 * (1 - (t / hw) ** 2) if t < hw else 0
    b.roof['hv'] = {c: h for c, h in prof_slope(f, list(b.L[0].inner)).items() if h > 0}
    b.roof['mat'] = lambda x, z, h: BRICKS if b.UV(x, z)[0] % 6 == 0 else (GLASS if h >= 2 else CPANE)
    complete(b, door_width=3)


def p_library(lot, rng): return {'hs': [7, 6, 6], 'extra': 6}


def d_library(b, prm):
    W, D = b.Wd, b.Dp
    base = b.lrect(0, 0, W - 1, D - 1) - {b.T(0, 0), b.T(W - 1, 0), b.T(0, D - 1), b.T(W - 1, D - 1)}
    b.level(base, 7, 'win', 'reading')
    b.level(base, 6, 'win', 'reading', void=b.lrect(9, 7, W - 10, D - 8))
    b.level(b.lrect(4, 3, W - 5, D - 4), 6, 'glass', 'classroom')
    roof_cone(b, b.L[2].inner, 4, lambda x, z, h: GLASS if h > 1 else BRICKS)
    complete(b)


def p_museum(lot, rng): return {'hs': [8, 6], 'extra': 6}


def d_museum(b, prm):
    W, D = b.Wd, b.Dp
    b.level(b.lrect(0, 0, W - 1, D - 1), 8, 'win', 'gallery')
    b.level(b.lrect(7, 3, W - 8, D - 4), 6, 'glass', 'gallery', void=b.lrect(11, 6, W - 12, D - 7))
    roof_cone(b, b.L[1].inner, 5, lambda x, z, h: GLASS if h > 1 else BRICKS)

    def hook(b):
        p = b.p
        lane = b.lane_cells()
        for u in range(2, W - 2, 4):
            x, z = b.T(u, -2)
            if (x, z) in lane: continue
            for y in range(DECK + 1, DECK + 8): p.soft(x, y, z, PILLAR)
        for u in range(2, W - 2):
            x, z = b.T(u, -2)
            if (x, z) in lane: continue
            p.soft(x, DECK + 8, z, BRICKS)
    complete(b, door_width=3, hook=hook)


def p_cafeteria(lot, rng): return {'hs': [6, 6], 'extra': 5}


def d_cafeteria(b, prm):
    W, D = b.Wd, b.Dp
    cu, cv = (W - 1) / 2.0, (D - 1) / 2.0
    r = min(W, D) / 2.0 - 0.6
    b.level(b.ldisc(cu, cv, r), 6, 'glass', 'cafe')
    b.level(b.ldisc(cu, cv, r - 2.2), 6, 'glass', 'cafe')
    roof_cone(b, b.L[1].inner, 3, lambda x, z, h: GLASS if h > 1 else BRICKS)

    def hook(b):
        lv = b.L[0]
        d = dist_to_edge(lv.plate)
        arc = [c for c in sorted(lv.inner) if d[c] == 3 and b.UV(*c)[1] >= D * 0.62 and c not in lv.res]
        if arc: f_counter(b, 0, arc)
    complete(b, hook=hook)


def p_admin_tower(lot, rng):
    n = _levels_by_r(lot.r, 5, 7)
    return {'hs': [8] + [6] * (n - 1), 'extra': 12, 'n': n}


def d_admin_tower(b, prm):
    W, D = b.Wd, b.Dp
    n = prm['n']
    ins = [0, 0, 2, 2, 4, 4, 6, 6][:n]
    for i, s in enumerate(ins):
        b.level(b.lrect(s, s, W - 1 - s, D - 1 - s), prm['hs'][i], 'glass' if i else 'win', 'lobby' if i == 0 else 'office')
    top = b.L[-1]
    roof_cone(b, top.inner, 3, lambda x, z, h: GLASS if h > 1 else BRICKS)
    cc = b.T((W - 1) // 2, (D - 1) // 2)

    def post(b):
        p = b.p
        y0 = b.Fy(b.n) + 3
        for k in range(8): p.set(cc[0], y0 + k, cc[1], PILLAR if k < 5 else 'end_rod[facing=up]')
        p.set(cc[0], y0 + 8, cc[1], LAMP)
    complete(b, post=post)


def p_shop_arcade(lot, rng):
    big = min(lot.dims) >= 12 and max(lot.dims) >= 18
    return {'hs': [6, 5, 5] if big and rng.random() < 0.5 else [6, 5], 'extra': 5}


def d_shop_arcade(b, prm):
    W, D = b.Wd, b.Dp
    hs = prm['hs']
    b.level(b.lrect(0, 0, W - 1, D - 1), hs[0], 'glass', 'shop')
    b.level(b.lrect(1, 0, W - 2, D - 3), hs[1], 'win', 'office')
    if len(hs) > 2: b.level(b.lrect(3, 1, W - 4, D - 5), hs[2], 'win', 'office')

    def post(b):
        p = b.p
        ov = DIRN[b.fc]
        tx, tz = (1, 0) if ov[0] == 0 else (0, 1)
        for (x, z) in b.door_cells[:2]:
            for k in (1, 2):
                for s in (-1, 0, 1):
                    p.soft(x + ov[0] * k + tx * s, DECK + 4, z + ov[1] * k + tz * s,
                           slab(CYAN, 'bottom') if k == 2 else slab(WALL, 'top'))
    complete(b, post=post)


def p_corner_shop(lot, rng): return {'hs': [6, 5], 'extra': 5}


def d_corner_shop(b, prm):
    W, D = b.Wd, b.Dp
    rc = min(7, W // 2, D // 2)
    base = b.lrect(0, 0, W - 1, D - 1)
    cu, cv = W - 1 - rc, rc

    def outside(c, k):
        u, v = b.UV(*c)
        return u > cu and v < cv and math.hypot(u - cu, v - cv) > rc + k
    b.level({c for c in base if not outside(c, 0.3)}, prm['hs'][0], 'glass', 'shop')
    b.level({c for c in b.lrect(2, 2, W - 3, D - 3) if not outside(c, -1.7)}, prm['hs'][1], 'win', 'office')
    complete(b)


def p_office_crescent(lot, rng):
    n = _levels_by_r(lot.r, 4, 7)
    return {'hs': [7] + [6] * (n - 1), 'extra': 8, 'n': n}


def d_office_crescent(b, prm):
    W, D = b.Wd, b.Dp
    outer = {c for c in b.ldisc((W - 1) / 2.0, D * 0.55, min(W / 2.0 - 0.5, D * 0.5)) if 0 <= b.UV(*c)[1] < D}
    pl = outer - b.ldisc((W - 1) / 2.0, D * 0.05, W * 0.24)
    for i in range(prm['n']):
        b.level(pl, prm['hs'][i], 'glass' if i else 'win', 'lobby' if i == 0 else 'office')
        if i >= 1 and i % 2 == 1: pl = pl - edge_of(pl) - edge_of(pl - edge_of(pl))
    roof_cone(b, b.L[-1].inner, 2, lambda x, z, h: GLASS)

    def post(b):
        p = b.p
        Fy = b.Fy(b.n)
        for (x, z) in sorted(b.L[-1].edge):
            if b.pillar_at(x, z) and (x + z) % 8 == 0:
                for y in range(Fy + 1, Fy + 6): p.soft(x, y, z, PILLAR)
    complete(b, post=post)


def p_trade_tower(lot, rng):
    n = _levels_by_r(lot.r, 5, 8)
    return {'hs': [7] + [6] * (n - 1), 'extra': 13, 'n': n}


def _octagon(b, cu, cv, r):
    out = set()
    for u in range(int(cu - r - 1), int(cu + r + 2)):
        for v in range(int(cv - r - 1), int(cv + r + 2)):
            du, dv = abs(u - cu), abs(v - cv)
            if du <= r and dv <= r and du + dv <= r * 1.45: out.add(b.T(u, v))
    return out


def d_trade_tower(b, prm):
    W, D = b.Wd, b.Dp
    cu, cv = (W - 1) // 2, (D - 1) // 2
    r0 = min(W, D) // 2 - 0.5
    rs = [max(r0 - 2 * (i // 2), 6.5) for i in range(prm['n'])]
    for i, r in enumerate(rs):
        b.level(_octagon(b, cu, cv, r), prm['hs'][i], 'glass' if i else 'win', 'lobby' if i == 0 else 'office')
    roof_cone(b, b.L[-1].inner, 3, lambda x, z, h: GLASS if h > 1 else BRICKS)
    cc = b.T(cu, cv)

    def post(b):
        p = b.p
        y0 = b.Fy(b.n) + 3
        for k in range(9): p.set(cc[0], y0 + k, cc[1], PILLAR if k < 5 else 'end_rod[facing=up]')
    complete(b, post=post)


def p_kiosk(lot, rng): return {'hs': [5], 'extra': 4}


def d_kiosk(b, prm):
    W, D = b.Wd, b.Dp
    cu, cv = (W - 1) / 2.0, (D - 1) / 2.0
    b.level(b.ldisc(cu, cv, min(W, D) / 2.0 - 0.7), 5, 'open', 'cafe')
    roof_cone(b, b.L[0].plate, 3, lambda x, z, h: GLASS if h > 1 else CPANE, margin=0)

    def hook(b):
        lv = b.L[0]
        cc = b.T(int(cu), int(cv))
        for c in sorted(lv.inner):
            if math.hypot(c[0] - cc[0], c[1] - cc[1]) < 1.6 and c not in lv.res:
                b.put(0, c[0], 0, c[1], BLOCK); b.put(0, c[0], 1, c[1], 'smooth_quartz_slab[type=bottom]')
    complete(b, hook=hook, roof_access=False, door_width=2)


# ---- L -----------------------------------------------------------------------------------------------------
def p_conservatory(lot, rng): return {'hs': [6], 'extra': 10}


def d_conservatory(b, prm):
    W, D = b.Wd, b.Dp
    base = b.lrect(0, 0, W - 1, D - 1) - {b.T(u, v) for u in (0, W - 1) for v in (0, D - 1)}
    b.level(base, 6, 'glass', 'garden')
    vc, hw = (D - 1) / 2.0, D / 2.0 - 4.0

    def f(c):
        u, v = b.UV(*c)
        t = abs(v - vc)
        return 9.0 * (1 - (t / hw) ** 2) if t < hw else 0
    b.roof['hv'] = {c: h for c, h in prof_slope(f, list(b.L[0].inner)).items() if h > 0}
    b.roof['mat'] = lambda x, z, h: BRICKS if b.UV(x, z)[0] % 5 == 0 else ('cyan_stained_glass' if h >= 8 else GLASS)
    b.roof['decor'] = False

    def hook(b):
        lv = b.L[0]
        p = b.p
        lane = {c for c in lv.inner if abs(b.UV(*c)[1] - vc) <= 1.0 or b.UV(*c)[0] in (W // 4, 3 * W // 4)}
        for c in lv.inner:
            if c in lane:
                p.set(c[0], lv.F, c[1], PRIS if (c[0] + c[1]) % 2 else BLOCK)
                lv.res.add(c)
            else:
                p.set(c[0], lv.F, c[1], 'grass_block' if (c[0] * 3 + c[1]) % 7 else 'moss_block')

    def post(b):
        lv = b.L[0]
        p = b.p
        hv = b.roof['hv']
        for c in sorted(lv.inner):
            if c in lv.res or c in lv.occ: continue
            h = (c[0] * 7 + c[1] * 13) % 23
            if h == 0 or (c[0] % 6 == 2 and c[1] % 6 == 3 and h % 2 == 1):
                f_tree(b, 0, c[0], c[1], 5 + (h % 3), LEAVES if h % 3 else LEAVES3)
            elif (c[0] * 5 + c[1] * 3) % 4 == 0 and p.get(c[0], lv.W, c[1]) is None:
                p.set(c[0], lv.W, c[1], b.rng.choice(FLOWERS + ('fern', 'azalea', 'flowering_azalea')))
    complete(b, door_width=3, hook=hook, furnish=False, post=post)


def p_terraced_garden(lot, rng): return {'hs': [6, 5, 5, 5], 'extra': 8}


def d_terraced_garden(b, prm):
    W, D = b.Wd, b.Dp
    st = [(0, 0, 0, 0), (3, 0, 3, 2), (6, 0, 6, 4), (9, 1, 9, 6)]
    b.style['roof'] = 'grass_block'; b.style['roof2'] = 'moss_block'
    for i, (a, bb, c, d) in enumerate(st):
        b.level(b.lrect(a, bb, W - 1 - c, D - 1 - d), prm['hs'][i], 'solid' if i < 3 else 'win', 'garden' if i < 2 else 'lobby')
    complete(b, door_width=3, garden_roof=True)


def p_academy(lot, rng): return {'hs': [7, 6, 6, 6], 'extra': 8}


def d_academy(b, prm):
    W, D = b.Wd, b.Dp
    court = b.lrect(11, 9, W - 12, D - 1)
    base = b.lrect(0, 0, W - 1, D - 1) - court
    b.level(base, 7, 'win', 'classroom')
    b.level(base, 6, 'win', 'classroom')
    cu = W // 2
    b.level(b.lrect(cu - 7, 0, cu + 7, 10), 6, 'glass', 'classroom')
    b.level(b.lrect(cu - 5, 1, cu + 5, 9), 6, 'glass', 'office')
    roof_cone(b, b.L[-1].inner, 3, lambda x, z, h: GLASS if h > 1 else BRICKS)

    def hook(b):
        p = b.p
        for c in b.lrect(11, 9, W - 12, D - 1):
            if p.inside(c[0], DECK + 1, c[1]) and roads.surface(*c) is None:
                p.soft(c[0], DECK, c[1], 'grass_block')
                uu, vv = b.UV(*c)
                if (uu + vv) % 5 == 0 and 14 <= uu <= W - 15: lamp_post(p, c[0], c[1], DECK + 1, 3)
                elif (uu * 3 + vv) % 4 == 1 and abs(uu - cu) > 1: p.soft(c[0], DECK + 1, c[1], b.rng.choice(FLOWERS))
                elif (uu == cu - 6 or uu == cu + 6) and vv == 12:
                    for k in range(4): p.soft(c[0], DECK + 1 + k, c[1], 'oak_log[axis=y]')
                    for dx in range(-2, 3):
                        for dz in range(-2, 3):
                            if abs(dx) + abs(dz) <= 3: p.soft(c[0] + dx, DECK + 5, c[1] + dz, LEAVES)
    complete(b, door_width=3, hook=hook)


def p_auditorium(lot, rng): return {'hs': [10], 'extra': 8}


def d_auditorium(b, prm):
    W, D = b.Wd, b.Dp
    cu = (W - 1) / 2.0
    rad = min(W / 2.0 - 0.5, D - 11)
    plate = b.lrect(0, 0, W - 1, 9) | {c for c in b.ldisc(cu, 10, rad) if b.UV(*c)[1] >= 0}
    b.level(plate, 10, 'win', 'lobby')
    lv = b.L[0]

    def f(c):
        u, v = b.UV(*c)
        d = math.hypot(u - cu, v - 10)
        return 7.0 * (1 - (d / rad) ** 2) if d < rad - 2 and v > 9 else 0
    b.roof['hv'] = {c: h for c, h in prof_slope(f, list(lv.inner)).items() if h > 0}
    b.roof['mat'] = lambda x, z, h: GLASS if h >= 2 else BRICKS
    b.roof['decor'] = False

    def hook(b):
        p = b.p
        lv = b.L[0]
        for c in sorted(lv.inner):
            u, v = b.UV(*c)
            if v <= 10: continue
            d = math.hypot(u - cu, v - 10)
            if d > rad - 2: continue
            if d < 3.5:
                p.set(c[0], lv.F + 1, c[1], 'smooth_quartz_slab[type=bottom]' if d > 1.5 else 'sea_lantern')
                lv.occ.add(c); continue
            hgt = 1 + int(d - 3.5) // 2
            for y in range(lv.F + 1, lv.F + hgt): p.set(c[0], y, c[1], BLOCK)
            nx, nz = b.T(int(u), int(v) + 1)
            away = FACE_OF[(nx - c[0], nz - c[1])]
            aisle = abs(u - cu) <= 0.6
            p.set(c[0], lv.F + hgt, c[1], stairs(CYAN if aisle else WALL, away))
            lv.occ.add(c)
            if aisle: lv.res.add(c)
    complete(b, door_width=3, hook=hook)


def p_garden_pavilion(lot, rng): return {'hs': [5], 'extra': 4}


def d_garden_pavilion(b, prm):
    W, D = b.Wd, b.Dp
    cu, cv = (W - 1) / 2.0, (D - 1) / 2.0
    b.level(b.ldisc(cu, cv, min(W, D) / 2.0 - 0.7), 5, 'open', 'garden')
    roof_cone(b, b.L[0].plate, 3, lambda x, z, h: GLASS if h > 1 else CPANE, margin=0)

    def hook(b):
        lv = b.L[0]
        cc = b.T(int(cu), int(cv))
        b.put(0, cc[0], 0, cc[1], 'moss_block'); b.put(0, cc[0], 1, cc[1], 'flowering_azalea')
        for d, (dx, dz) in DIRN.items():
            q = (cc[0] + 2 * dx, cc[1] + 2 * dz)
            if q in lv.inner and q not in lv.res: b.put(0, q[0], 0, q[1], stairs(WALL, FACE_OF[(-dx, -dz)]))
    complete(b, hook=hook, roof_access=False, door_width=2)


def p_greenhouse(lot, rng): return {'hs': [5], 'extra': 5}


def d_greenhouse(b, prm):
    W, D = b.Wd, b.Dp
    b.level(b.lrect(0, 0, W - 1, D - 1), 5, 'glass', 'garden')

    def f(c):
        u, v = b.UV(*c)
        return min(u - 2, W - 3 - u, 3)
    b.roof['hv'] = {c: h for c, h in prof_slope(f, list(b.L[0].inner)).items() if h > 0}
    b.roof['mat'] = lambda x, z, h: GLASS if h > 1 else CPANE
    complete(b, door_width=2)


def p_study_hall(lot, rng): return {'hs': [6, 6, 5], 'extra': 5}


def d_study_hall(b, prm):
    W, D = b.Wd, b.Dp
    b.level(b.lrect(0, 0, W - 1, D - 1), 6, 'win', 'classroom')
    b.level(b.lrect(2, 0, W - 3, D - 4), 6, 'win', 'classroom')
    b.level(b.lrect(5, 0, W - 6, D - 8), 5, 'glass', 'office')

    def hook(b):
        p = b.p
        lane = b.lane_cells()
        for u in range(1, W - 1, 4):
            x, z = b.T(u, -2)
            if (x, z) in lane: continue
            for y in range(DECK + 1, DECK + 6): p.soft(x, y, z, PILLAR)
        for u in range(1, W - 1):
            x, z = b.T(u, -2)
            if (x, z) in lane: continue
            p.soft(x, DECK + 6, z, slab(BRICKS, 'bottom'))
    complete(b, hook=hook)


# ------------------------------------------------------------------------------------------------------------
# the design table and the lot planner
# ------------------------------------------------------------------------------------------------------------
DESIGNS = {
    'market_hall': dict(d='H', sizes=[(40, 22), (44, 24), (48, 24)], params=p_market_hall, build=d_market_hall),
    'library': dict(d='H', sizes=[(24, 22), (26, 24), (28, 24)], params=p_library, build=d_library),
    'museum': dict(d='H', sizes=[(32, 22), (36, 24)], params=p_museum, build=d_museum),
    'cafeteria': dict(d='H', sizes=[(19, 19), (21, 21)], params=p_cafeteria, build=d_cafeteria),
    'admin_tower': dict(d='H', sizes=[(20, 20), (22, 22), (24, 24)], params=p_admin_tower, build=d_admin_tower),
    'shop_arcade': dict(d='H', sizes=[(11, 9), (12, 10), (14, 11), (16, 12), (18, 12), (20, 13), (22, 14)], params=p_shop_arcade,
                        build=d_shop_arcade),
    'corner_shop': dict(d='H', sizes=[(15, 13), (17, 14)], params=p_corner_shop, build=d_corner_shop),
    'office_crescent': dict(d='H', sizes=[(28, 22), (32, 24)], params=p_office_crescent, build=d_office_crescent),
    'trade_tower': dict(d='H', sizes=[(17, 17), (19, 19)], params=p_trade_tower, build=d_trade_tower),
    'kiosk': dict(d='H', sizes=[(8, 8)], params=p_kiosk, build=d_kiosk),
    'conservatory': dict(d='L', sizes=[(46, 26), (50, 28)], params=p_conservatory, build=d_conservatory),
    'terraced_garden': dict(d='L', sizes=[(32, 26), (36, 28)], params=p_terraced_garden, build=d_terraced_garden),
    'academy': dict(d='L', sizes=[(36, 30), (40, 32)], params=p_academy, build=d_academy),
    'auditorium': dict(d='L', sizes=[(28, 25), (30, 26)], params=p_auditorium, build=d_auditorium),
    'garden_pavilion': dict(d='L', sizes=[(8, 8)], params=p_garden_pavilion, build=d_garden_pavilion),
    'greenhouse': dict(d='L', sizes=[(12, 15), (12, 18), (13, 20), (14, 24)], params=p_greenhouse, build=d_greenhouse),
    'study_hall': dict(d='L', sizes=[(16, 14), (18, 16), (22, 20), (26, 22)], params=p_study_hall, build=d_study_hall),
}

# (design, target point as (dx, dz) from the plaza centre) anchors, then the weighted fill
ANCHORS = {
    'H': [('market_hall', (-62, -4)), ('library', (62, -6)), ('museum', (-50, 52)), ('cafeteria', (50, 40)),
          ('admin_tower', (-48, -52)), ('admin_tower', (50, -54)), ('office_crescent', (0, 72)), ('office_crescent', (-100, -20)),
          ('trade_tower', (-96, 20)), ('trade_tower', (96, 14)), ('trade_tower', (-80, 80)), ('kiosk', (-24, -30)), ('kiosk', (24, -30)),
          ('kiosk', (-24, 30)), ('kiosk', (24, 30))],
    'L': [('conservatory', (50, 40)), ('academy', (-10, -62)), ('terraced_garden', (60, -34)),
          ('auditorium', (-62, 30)), ('terraced_garden', (-70, -50)), ('study_hall', (-20, 62)), ('garden_pavilion', (30, -30)),
          ('garden_pavilion', (-30, 30)), ('garden_pavilion', (30, 36)), ('garden_pavilion', (-34, -26))],
}
FILL = {
    'H': [('shop_arcade', 20), ('corner_shop', 14), ('office_crescent', 14), ('trade_tower', 12), ('admin_tower', 8),
          ('cafeteria', 4), ('kiosk', 3)],
    'L': [('study_hall', 28), ('greenhouse', 14), ('terraced_garden', 12), ('academy', 6), ('auditorium', 5),
          ('garden_pavilion', 6)],
}
CAPS = {'greenhouse': 14, 'shop_arcade': 30, 'corner_shop': 16, 'study_hall': 18, 'kiosk': 6, 'garden_pavilion': 8, 'cafeteria': 3, 'admin_tower': 4, 'academy': 2, 'auditorium': 2,
        'terraced_garden': 3, 'office_crescent': 5, 'trade_tower': 7}
CENTER = {'H': (0.0, 215.0), 'L': PL.from_polar(215, 240)}
PLAN_ANGLE = {'H': 180, 'L': 240}
DIST_ID = {'H': 'H', 'L': 'L'}


class Lot:
    def __init__(self, name, district, design, rect, facing, apron, prm, r):
        self.name, self.district, self.design, self.rect, self.facing = name, district, design, rect, facing
        self.apron, self.prm, self.r = apron, prm, r
        x0, z0, x1, z1 = rect
        an, as_, aw, ae = apron
        top = DECK + sum(prm['hs']) + prm['extra']
        self.bbox = (x0 - aw, DECK, z0 - an, x1 + ae, top, z1 + as_)


class Grid:
    """Static free-cell mask of one district with a summed-area table."""

    def __init__(self, did, others):
        a = PLAN_ANGLE[did]
        pts = [PL.from_polar(r, a + k) for r in (128, 215, 302) for k in range(-34, 35, 4)]
        self.x0 = int(min(p[0] for p in pts)) - 6
        self.z0 = int(min(p[1] for p in pts)) - 6
        self.x1 = int(max(p[0] for p in pts)) + 6
        self.z1 = int(max(p[1] for p in pts)) + 6
        self.w, self.h = self.x1 - self.x0 + 1, self.z1 - self.z0 + 1
        bad = [[1] * self.w for _ in range(self.h)]
        self.road = [[0] * self.w for _ in range(self.h)]
        for j in range(self.h):
            z = self.z0 + j
            for i in range(self.w):
                x = self.x0 + i
                if roads.surface(x, z) is not None:
                    self.road[j][i] = 1
                elif PL.district_at(x, z) == did:
                    bad[j][i] = 0
        for bb in others:
            for z in range(max(bb[2] - 3, self.z0), min(bb[5] + 3, self.z1) + 1):
                row = bad[z - self.z0]
                for x in range(max(bb[0] - 3, self.x0), min(bb[3] + 3, self.x1) + 1): row[x - self.x0] = 1
        self.bad = bad
        sat = [[0] * (self.w + 1) for _ in range(self.h + 1)]
        for j in range(self.h):
            run = 0
            for i in range(self.w):
                run += bad[j][i]
                sat[j + 1][i + 1] = sat[j][i + 1] + run
        self.sat = sat

    def clear(self, x0, z0, x1, z1):
        """True when every cell of the rectangle is free."""
        if x0 < self.x0 or z0 < self.z0 or x1 > self.x1 or z1 > self.z1: return False
        a, b, c, d = x0 - self.x0, z0 - self.z0, x1 - self.x0 + 1, z1 - self.z0 + 1
        s = self.sat
        return s[d][c] - s[b][c] - s[d][a] + s[b][a] == 0

    def is_road(self, x, z):
        i, j = x - self.x0, z - self.z0
        return 0 <= i < self.w and 0 <= j < self.h and self.road[j][i] == 1

    def road_dist(self, rect):
        """Distance in each cardinal direction from the rectangle's side to the nearest road/plaza cell."""
        x0, z0, x1, z1 = rect
        xm, zm = (x0 + x1) // 2, (z0 + z1) // 2
        out = {}
        for d, (dx, dz) in DIRN.items():
            if d == 'east': x, z = x1 + 1, zm
            elif d == 'west': x, z = x0 - 1, zm
            elif d == 'south': x, z = xm, z1 + 1
            else: x, z = xm, z0 - 1
            k = 0
            while k < 60 and self.x0 <= x < self.x0 + self.w and self.z0 <= z < self.z0 + self.h:
                if self.road[z - self.z0][x - self.x0]: break
                x += dx; z += dz; k += 1
            else:
                k = 999
            out[d] = k
        return out


def _try_place(grid, placed, W, D, x, z, front=3):
    """Try a lot with its plate corner at (x, z); returns (key, rect, facing, apron, bbox) or None."""
    best = None
    for (w, d, ns) in ((W, D, True), (D, W, False)):
        if W == D and not ns: continue
        rect = (x, z, x + w - 1, z + d - 1)
        rd = grid.road_dist(rect)
        faces = ('north', 'south') if ns else ('east', 'west')
        fc = min(faces, key=lambda k: (rd[k], k))
        if rd[fc] >= 999: continue
        for fr in (front, 2, 1):
            ap = {'north': 1, 'south': 1, 'west': 1, 'east': 1}
            ap[fc] = fr
            bb = (rect[0] - ap['west'], rect[1] - ap['north'], rect[2] + ap['east'], rect[3] + ap['south'])
            if not grid.clear(*bb): continue
            ts = ctx_tile = _CTX.tile
            if any(not (rect[0] // ts <= tx <= rect[2] // ts and rect[1] // ts <= tz <= rect[3] // ts)
                   for tx in range(bb[0] // ts, bb[2] // ts + 1) for tz in range(bb[1] // ts, bb[3] // ts + 1)):
                continue
            if any(bb[0] <= p[2] + 3 and bb[2] >= p[0] - 3 and bb[1] <= p[3] + 3 and bb[3] >= p[1] - 3
                   for p in placed): break
            key = (rd[fc], -fr)
            if best is None or key < best[0]:
                best = (key, rect, fc, (ap['north'], ap['south'], ap['west'], ap['east']), bb)
            break
    return best


def plan_district(did, others, seed):
    grid = Grid(did, others)
    cx, cz = CENTER[did]
    placed = []              # bboxes (x0, z0, x1, z1)
    lots = []
    counts = {}
    blocked = set()
    rng = random.Random(hash_str(seed, 'civic', did))

    def commit(design, res):
        key, rect, fc, ap, bb = res
        n = counts.get(design, 0) + 1
        name = 'civ_%s_%s_%02d' % (did.lower(), design, n)
        r = math.hypot((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)
        prm = DESIGNS[design]['params'](SimpleNamespace(r=r, dims=(rect[2] - rect[0] + 1, rect[3] - rect[1] + 1)), random.Random(hash_str(seed, name, 'p')))
        lot = Lot(name, did, design, rect, fc, ap, prm, r)
        xs = (lot.bbox[0], (lot.bbox[0] + lot.bbox[3]) // 2, lot.bbox[3])
        zs = (lot.bbox[2], (lot.bbox[2] + lot.bbox[5]) // 2, lot.bbox[5])
        if lot.bbox[4] > min(_CTX.max_building_top(a, b) for a in xs for b in zs):
            return False
        try:
            _CACHE[name] = make_building(lot, _CTX)
        except RuntimeError:
            return False
        counts[design] = n
        placed.append(bb)
        lots.append(lot)
        for zz in range(bb[1] - 3, bb[3] + 4):
            for xx in range(bb[0] - 3, bb[2] + 4): blocked.add((xx, zz))
        return True

    positions = [(x, z) for z in range(grid.z0, grid.z1 - 4, 2) for x in range(grid.x0, grid.x1 - 4, 2)]
    for design, (dx, dz) in ANCHORS[did]:
        tx, tz = cx + dx, cz + dz
        sizes = DESIGNS[design]['sizes']
        order = sorted(positions, key=lambda p: (p[0] - tx) ** 2 + (p[1] - tz) ** 2)
        done = False
        for (x, z) in order[:4000]:
            if (x, z) in blocked: continue
            for size in (sizes if design in ('kiosk', 'garden_pavilion') else sizes[::-1]):
                res = _try_place(grid, placed, size[0], size[1], x, z)
                if res and commit(design, res):
                    done = True; break
            if done: break
    weights = FILL[did]
    tot = sum(w for _, w in weights)
    order = sorted(positions, key=lambda p: ((p[0] - cx) ** 2 + (p[1] - cz) ** 2, p[1], p[0]))
    for (x, z) in order:
        if (x, z) in blocked or not grid.clear(x - 1, z - 1, x + 7, z + 7): continue
        t = rng.random() * tot
        acc = 0
        pick = weights[0][0]
        for nm, w in weights:
            acc += w
            if t <= acc: pick = nm; break
        cand = [pick] + [nm for nm, _ in weights if nm != pick][:3]
        cand = [nm for nm in cand if counts.get(nm, 0) < CAPS.get(nm, 99)]
        done = False
        for nm in cand:
            for size in sorted(DESIGNS[nm]['sizes'], key=lambda s: -(s[0] * s[1])):
                res = _try_place(grid, placed, size[0], size[1], x, z)
                if res and commit(nm, res):
                    done = True; break
            if done: break
    # second pass: small designs into the remaining pockets
    small = {'H': ['shop_arcade', 'corner_shop'], 'L': ['study_hall', 'greenhouse']}[did]
    for (x, z) in order:
        if (x, z) in blocked or not grid.clear(x - 1, z - 1, x + 6, z + 6): continue
        done = False
        for nm in small:
            if counts.get(nm, 0) >= 44: continue
            for size in sorted(DESIGNS[nm]['sizes'], key=lambda s: -(s[0] * s[1])):
                res = _try_place(grid, placed, size[0], size[1], x, z)
                if res and commit(nm, res):
                    done = True; break
            if done: break
    return lots


# ------------------------------------------------------------------------------------------------------------
# features
# ------------------------------------------------------------------------------------------------------------
_CACHE = {}


class _Dry(Exception):
    pass


def make_building(lot, ctx, dry=False):
    d = DESIGNS[lot.design]
    plan = Plan(lot.name, lot.bbox, lot.district, ctx)
    b = Bld(plan, lot, random.Random(hash_str(ctx.seed, lot.name, 'build')), style_for(lot.district))
    b.dry = dry
    try:
        d['build'](b, lot.prm)
    except _Dry:
        pass
    return b


def _meta(lot):
    x0, z0, x1, z1 = lot.rect
    b = _CACHE.get(lot.name) or make_building(lot, _CTX, dry=True)
    roof = DECK + sum(lot.prm['hs'])
    return {'kind': lot.design, 'district': lot.district, 'interior': True, 'entrances': list(b.entrances[:1]),
            'interior_box': (x0, WALK, z0, x1, roof, z1), 'roof_y': roof}


class CivicFeature(Feature):
    layer = LAYER

    def __init__(self, lot):
        super().__init__(lot.name, lot.bbox, LAYER, _meta(lot))
        self.lot = lot

    def building(self, ctx=None):
        b = _CACHE.get(self.name)
        if b is None:
            b = _CACHE[self.name] = make_building(self.lot, ctx or _CTX)
        return b

    def plan(self, ctx=None):
        return self.building(ctx).p

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


def _make_all():
    others = [f.bbox for f in FT.registry() if f.layer in (40, 50)]
    feats = []
    for did in ('H', 'L'):
        lots = plan_district(did, others, _CTX.seed)
        others = others + [l.bbox for l in lots]
        feats += [CivicFeature(l) for l in lots]
    return feats


FEATURES = _make_all()
register(*FEATURES)


def features():
    return list(FEATURES)
