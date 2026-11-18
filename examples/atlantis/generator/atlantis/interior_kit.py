"""Building kit for the Central Tower interiors (used by tower_interior.py).

A `Plan` holds ONE floor of the tower interior as a plain dict {(x, y, z): block}, built once per process and
independent of any tile (so panes/rails connect identically in every tile and the tile only copies its part).  The
kit offers polar helpers (rings, sectors), walls with doorways, floor inlays, lights and a small furniture set.

Hard limits enforced by `Plan.set` (nothing else can slip through): only cells with CORE_R + 1 < r <= usable_r
are ever written (the core and its landing and the outer wall stay untouched), and the floor slab layer y = F is only
touched by `inlay()` (same-height pattern blocks).

Angles are degrees in the geometry convention: 0 = east (+x), 90 = south (+z), growing clockwise on the map.
Heights: F = floor block y, W = F + 1 = walk (feet) level, the air rows are W .. W + 4 (the next floor slab is F + 6).
"""
import math

from . import palette as P
from . import blocks as B
from . import tower_spec as T
from .geometry import connect_state

CORE_KEEP = T.CORE_R + 1              # cells with r <= CORE_KEEP are never written
AIR_ROWS = 5                          # W .. W + 4
_NEEDS_CONNECT = ('_pane', 'iron_bars', '_fence', '_wall')


def ang(x, z):
    return math.degrees(math.atan2(z, x)) % 360.0


def angd(a, b):
    """Signed difference a - b in (-180, 180]."""
    d = (a - b) % 360.0
    return d - 360.0 if d > 180.0 else d


def pol(r, a):
    t = math.radians(a)
    return int(math.floor(r * math.cos(t) + 0.5)), int(math.floor(r * math.sin(t) + 0.5))


def toward(x, z, tx=0, tz=0):
    """Cardinal direction from (x, z) towards (tx, tz)."""
    return B.facing_from_vector(tx - x, tz - z)


def opp(f):
    return B.OPPOSITE[f]


def perp(f):
    """A unit step perpendicular to cardinal direction f (the 'right' of f as seen on the map)."""
    dx, dz = B.DIRS[f]
    return -dz, dx


class Plan:
    def __init__(self, F, name, rng, style=None, open_r=None, top=None, protect=(), blocked=()):
        self.F, self.W, self.name, self.rng = F, F + 1, name, rng
        self.top = top or F + AIR_ROWS                          # last air row (ceiling_y)
        self.R = T.usable_r(F + 1)
        self.open_r = open_r                                    # floor 17: radius of the open well (no slab inside)
        self.style = style or {}
        self.c = {}
        self.protect = set(protect)                             # slab cells holding tower ceiling lamps (+ carpet above)
        self.blocked = set(blocked)                             # columns we must never build in (stair lane, doorways)
        self.occ = set()                                        # (x, z) columns blocked at foot/head level
        self.keep = set()                                       # (x, z) lanes that furniture must leave free
        self.rooms = []                                         # dicts: name, door (x, z), centre (x, z)
        self.cells = []                                         # (x, z, r, deg) of every writable column
        R = self.R
        n = int(R) + 1
        for z in range(-n, n + 1):
            for x in range(-n, n + 1):
                r = math.hypot(x, z)
                if CORE_KEEP < r <= R:
                    self.cells.append((x, z, r, ang(x, z)))

    # ------------------------------------------------------------------ low level
    def ok(self, x, z):
        r2 = x * x + z * z
        return CORE_KEEP * CORE_KEEP < r2 <= self.R * self.R

    def set(self, x, y, z, b, occ=None):
        if not self.ok(x, z) or y == self.F or (x, z) in self.blocked: return
        self.c[(x, y, z)] = b
        if occ is None: occ = self.W <= y <= self.W + 1
        if occ: self.occ.add((x, z))

    def get(self, x, y, z):
        return self.c.get((x, y, z))

    def col(self, x, z, y0, y1, b):
        for y in range(y0, y1 + 1): self.set(x, y, z, b)

    def drop(self, x, z):
        """Remove everything at a column (doorway cuts)."""
        for y in range(self.W, self.top + 1): self.c.pop((x, y, z), None)
        self.occ.discard((x, z))

    def inlay(self, x, z, b):
        """Replace the floor slab block with a pattern block of the same height."""
        if not self.ok(x, z): return
        if self.open_r and math.hypot(x, z) < self.open_r + 0.5: return
        if (x, z) in self.protect: return
        self.c[(x, self.F, z)] = b

    def free(self, cells):
        for x, z in cells:
            if not self.ok(x, z) or (x, z) in self.occ or (x, z) in self.keep or (x, z) in self.blocked \
                    or (x, z) in self.protect: return False
        return True

    def sel(self, r0, r1, a0=0.0, a1=360.0):
        """Columns with r0 <= r < r1 and angle in [a0, a1) (degrees, wraps)."""
        full = (a1 - a0) >= 360.0
        out = []
        for x, z, r, a in self.cells:
            if r < r0 or r >= r1: continue
            if full or (a - a0) % 360.0 < (a1 - a0): out.append((x, z))
        return out

    # ------------------------------------------------------------------ materials
    def m(self, key, default=None):
        return self.style.get(key, default)

    # ------------------------------------------------------------------ structure
    def wall_col(self, x, z, style='solid', block=None, glass=None):
        """One wall column over the full room height. style: solid | glass (panes between) | panel."""
        b = block or self.m('wall', P.WHITE_BLOCK)
        W = self.W
        if style == 'solid':
            self.col(x, z, W, W + 4, b)
        elif style == 'glass':
            g = glass or self.m('glass_pane', P.GLASS_BLUE_PANE)
            self.set(x, W, z, b); self.set(x, W + 4, z, b)
            self.col(x, z, W + 1, W + 3, g)
        elif style == 'panel':
            self.col(x, z, W, W + 4, b)
            self.set(x, W + 1, z, self.m('wall2', P.PALE_GREY))
            self.set(x, W + 2, z, self.m('wall2', P.PALE_GREY))
            self.set(x, W + 3, z, self.m('wall2', P.PALE_GREY))
        elif style == 'lit':                              # solid with a glowing band
            self.col(x, z, W, W + 4, b)
            self.set(x, W + 2, z, P.LAMP)
        else:
            raise ValueError(style)

    def arc_wall(self, r, a0, a1, style='solid', block=None, gaps=(), thick=0.62, glass=None, gap_w=1.35):
        """Curved wall of radius r from angle a0 to a1; gaps = [(centre_angle, half_width_in_cells), ...] are doorways
        (air, with a lintel on the top two rows). Returns the wall columns."""
        cols = []
        for x, z, rr, a in self.cells:
            if abs(rr - r) >= thick: continue
            if (a1 - a0) < 360.0 and (a - a0) % 360.0 >= (a1 - a0): continue
            gap = False
            for ga, hw in gaps:
                if abs(angd(a, ga)) * math.pi / 180.0 * rr < hw: gap = True
            if gap:
                self.set(x, self.W + 3, z, block or self.m('wall', P.WHITE_BLOCK)); self.set(x, self.W + 4, z, block or self.m('wall', P.WHITE_BLOCK), occ=False)
                self.occ.discard((x, z))
                continue
            self.wall_col(x, z, style, block, glass)
            cols.append((x, z))
        return cols

    def ray_wall(self, a, r0, r1, style='solid', block=None, gaps=(), thick=0.62, glass=None):
        """Straight radial wall along angle a from r0 to r1; gaps = [(radius, half_width)] doorways along it."""
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        cols = []
        for x, z, rr, aa in self.cells:
            if rr < r0 or rr > r1: continue
            along = x * ca + z * sa
            side = -x * sa + z * ca
            if along <= 0 or abs(side) >= thick: continue
            gap = any(abs(rr - gr) < hw for gr, hw in gaps)
            if gap:
                self.set(x, self.W + 3, z, block or self.m('wall', P.WHITE_BLOCK)); self.set(x, self.W + 4, z, block or self.m('wall', P.WHITE_BLOCK), occ=False)
                self.occ.discard((x, z))
                continue
            self.wall_col(x, z, style, block, glass)
            cols.append((x, z))
        return cols

    def lane(self, a, r0, r1, half=1.9):
        """Reserve a straight corridor (furniture keeps out) along angle a from r0 to r1."""
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        for x, z, rr, aa in self.cells:
            if rr < r0 or rr > r1: continue
            if x * ca + z * sa > 0 and abs(-x * sa + z * ca) <= half: self.keep.add((x, z))

    def room(self, name, door_ang, door_r, centre_r=None, centre_ang=None):
        """Register a room: its doorway (r, angle) and a point 2.6 cells inside it (the 2-wide network must reach it);
        reserves the door lane (furniture keeps out of the first 3.2 cells behind the door)."""
        ca = door_ang if centre_ang is None else centre_ang
        d = pol(door_r, door_ang)
        c = pol(door_r + 2.6, ca)
        self.rooms.append({'name': name, 'door': d, 'centre': c, 'door_ang': door_ang, 'door_r': door_r})
        self.lane(door_ang, door_r - 1.5, door_r + 3.4)

    # ------------------------------------------------------------------ lights
    def lamp(self, x, z, kind='cool'):
        """Ceiling lamp panel in the top air row."""
        b = {'cool': P.LAMP, 'warm': P.FROG_WARM, 'white': P.FROG_WHITE, 'green': P.FROG_BLUE}[kind]
        self.set(x, self.top, z, b, occ=False)

    def lamps(self, cells, kind='cool'):
        for x, z in cells: self.lamp(x, z, kind)

    def hang(self, x, z, kind='lantern'):
        """Chain + hanging lantern from the ceiling."""
        self.set(x, self.top, z, 'chain[axis=y]', occ=False)
        self.set(x, self.top - 1, z, 'lantern[hanging=true]' if kind == 'lantern' else 'soul_lantern[hanging=true]', occ=False)

    # ------------------------------------------------------------------ resolve
    def finish(self):
        """Give panes, bars and fences their connections (from this plan only; the tower wall counts as solid)."""
        c = self.c
        R2 = self.R * self.R
        todo = [(k, b) for k, b in c.items() if 'north=' not in b and b.split('[', 1)[0].endswith(_NEEDS_CONNECT)]
        for (x, y, z), b in todo:
            def nb(d, x=x, y=y, z=z):
                if d == 'up': return c.get((x, y + 1, z))
                dx, dz = B.DIRS[d]
                nx, nz = x + dx, z + dz
                if (nx * nx + nz * nz) > R2: return 'smooth_quartz'          # the outer wall
                return c.get((nx, y, nz))
            c[(x, y, z)] = connect_state(b, nb)
        return self


class PlanVol:
    """Duck-typed Volume so that architecture.py components (console, ...) draw into a Plan."""

    def __init__(self, plan):
        self.p = plan

    def intersects(self, *a):
        return True

    def set(self, x, y, z, b):
        if b is not None: self.p.set(x, y, z, b)

    def fill(self, x1, y1, z1, x2, y2, z2, b, replace=None):
        for y in range(min(y1, y2), max(y1, y2) + 1):
            for z in range(min(z1, z2), max(z1, z2) + 1):
                for x in range(min(x1, x2), max(x1, x2) + 1): self.p.set(x, y, z, b)

    def get(self, x, y, z):
        return self.p.get(x, y, z)


# ============================================================================================================
# furniture (each returns True if placed; they refuse to overlap walls, lanes or other furniture)
# ============================================================================================================
def _row(x, z, face, n, off=0):
    """n cells along the perpendicular of `face`, centred on (x, z) (+ offset)."""
    px, pz = perp(face)
    lo = -((n - 1) // 2) + off
    return [(x + px * k, z + pz * k) for k in range(lo, lo + n)]


def console(p, x, z, face, width=3):
    """Atlantean console: anchor back-centre at (x, z); the operator stands on the `face` side. Footprint width x 3."""
    from . import architecture as A
    ox, oz = B.DIRS[face]
    px, pz = perp(face)
    hw = width // 2
    fp = [(x + px * lx + ox * lz, z + pz * lx + oz * lz) for lx in range(-hw, hw + 1) for lz in range(3)]
    if not p.free(fp): return False
    A.console(PlanVol(p), x, p.F, z, face, width)
    for cx, cz in fp:                                       # hologram canopy: nobody can stand on top of a console
        p.set(cx, p.W + 3, cz, P.GLASS_BLUE, occ=False)
    return True


def chair(p, x, z, face, mat='quartz'):
    """A seat whose occupant looks towards `face`."""
    if not p.free([(x, z)]): return False
    p.set(x, p.W, z, B.stairs(mat, opp(face)))
    return True


def stool(p, x, z, mat=P.WHITE_SLAB):
    if not p.free([(x, z)]): return False
    p.set(x, p.W, z, B.slab(mat, 'bottom'))
    return True


def desk(p, x, z, face, n=3, top=None, lamp=True, screen=True):
    """Desk of n slabs (top half) along the perpendicular of `face` (the user sits on the `face` side's opposite..:
    the user is at the `face` end - the desk's front is `face`). A lantern and a black screen pane on top."""
    cells = _row(x, z, face, n)
    if not p.free(cells): return False
    top = top or p.m('desk', P.WHITE_SLAB)
    W = p.W
    ox, oz = B.DIRS[face]
    for i, (cx, cz) in enumerate(cells):
        p.set(cx, W, cz, B.slab(top, 'top'))
        if lamp and i == 0:
            p.set(cx, W + 1, cz, 'lantern[hanging=false]')
        elif screen and i == len(cells) // 2:
            p.set(cx - ox * 0, W + 1, cz - oz * 0, 'black_stained_glass_pane')
    return True


def table_round(p, x, z, r=2.0, top=None, seats=0, seat_mat='quartz', lamp=False):
    """Round table (slab tops on a pillar) with `seats` stairs around it."""
    top = top or p.m('desk', P.WHITE_SLAB)
    cells = [(cx, cz) for cx in range(x - int(r) - 1, x + int(r) + 2) for cz in range(z - int(r) - 1, z + int(r) + 2)
             if math.hypot(cx - x, cz - z) <= r + 0.3]
    if not p.free(cells): return False
    for cx, cz in cells:
        p.set(cx, p.W, cz, B.slab(top, 'top'))
    if lamp: p.set(x, p.W + 1, z, 'lantern[hanging=false]')
    if seats:
        rr = r + 1.4
        for k in range(seats):
            a = 360.0 * k / seats
            sx, sz = pol(rr, a)
            sx, sz = x + sx, z + sz
            if p.free([(sx, sz)]):
                p.set(sx, p.W, sz, B.stairs(seat_mat, toward(sx, sz, x, z) if False else opp(toward(sx, sz, x, z))))
    return True


def bed(p, x, z, head, color='white'):
    """Bed with its head at (x, z) and the foot one cell opposite to `head` (head = direction foot -> head)."""
    dx, dz = B.DIRS[head]
    fx, fz = x - dx, z - dz
    if not p.free([(x, z), (fx, fz)]): return False
    p.set(x, p.W, z, f'{color}_bed[facing={head},part=head]')
    p.set(fx, p.W, fz, f'{color}_bed[facing={head},part=foot]')
    return True


def barrel(p, x, z):
    if not p.free([(x, z)]): return False
    p.set(x, p.W, z, 'barrel[facing=up]')
    return True


def chest(p, x, z, face):
    if not p.free([(x, z)]): return False
    p.set(x, p.W, z, f'chest[facing={face},type=single]')
    return True


def shelf(p, x, z, h=3, block='bookshelf'):
    if not p.free([(x, z)]): return False
    for k in range(h): p.set(x, p.W + k, z, block)
    return True


def column_data(p, x, z, h=4, kind='cool'):
    """Atlantean data column: a lit crystal (glass over a sea lantern) on a pillar base, capped under the ceiling."""
    if not p.free([(x, z)]): return False
    W = p.W
    lamp = {'cool': P.LAMP, 'warm': P.FROG_WARM, 'white': P.FROG_WHITE, 'green': P.FROG_BLUE}[kind]
    p.set(x, W, z, lamp)
    for k in range(1, h): p.set(x, W + k, z, P.GLASS_BLUE if kind != 'warm' else 'yellow_stained_glass')
    p.set(x, W + h, z, 'end_rod[facing=up]' if h < 4 else P.WHITE)
    return True


def bench_lab(p, x, z, face, n=4, rng=None, kit=None):
    """Lab bench: smooth stone slab tops with equipment (cauldrons, brewing stands, lanterns, specimen glass)."""
    cells = _row(x, z, face, n)
    if not p.free(cells): return False
    rng = rng or p.rng
    W = p.W
    gear = kit or ['brewing_stand', 'cauldron', 'lantern[hanging=false]', None, 'brewing_stand', 'blue_stained_glass_pane',
                   'cauldron', 'lantern[hanging=false]']
    for i, (cx, cz) in enumerate(cells):
        p.set(cx, W, cz, 'smooth_stone_slab[type=top]')
        g = gear[(rng.randrange(len(gear)) if i % 2 == 0 else (i * 3) % len(gear))]
        if i == 0: g = 'lantern[hanging=false]'
        if g: p.set(cx, W + 1, cz, g)
    return True


def case(p, x, z, glow='verdant_froglight'):
    """Specimen case: a glowing specimen inside glass on a quartz plinth."""
    if not p.free([(x, z)]): return False
    W = p.W
    p.set(x, W, z, P.WHITE_BLOCK)
    p.set(x, W + 1, z, glow)
    p.set(x, W + 2, z, P.GLASS)
    p.set(x, W + 3, z, P.GLASS_WHITE)
    return True


def screen(p, cells, y0=None, h=2, mat='black_stained_glass_pane', glow=None):
    """Wall screen: panes over the given wall-adjacent columns (they connect to the wall and each other)."""
    y0 = p.W + 1 if y0 is None else y0
    for x, z in cells:
        for k in range(h): p.set(x, y0 + k, z, mat, occ=(k == 0 and y0 <= p.W + 1))
        if glow: p.set(x, y0 - 1, z, glow)


def planter(p, x, z, plant='azalea'):
    if not p.free([(x, z)]): return False
    # azaleas need soil under them or they pop off when placed; potted plants do not
    p.set(x, p.W, z, 'moss_block' if plant in ('azalea', 'flowering_azalea') else P.WHITE_BLOCK)
    p.set(x, p.W + 1, z, plant)
    return True


def map_table(p, cx, cz, r=3.0, lamp=P.LAMP, glass=P.GLASS_BLUE):
    """Holographic map table: a sea-lantern bed under light-blue glass in a quartz rim, on a pedestal."""
    cells = [(x, z) for x in range(cx - int(r) - 1, cx + int(r) + 2) for z in range(cz - int(r) - 1, cz + int(r) + 2)
             if math.hypot(x - cx, z - cz) <= r + 0.2]
    if not p.free(cells): return False
    for x, z in cells:
        d = math.hypot(x - cx, z - cz)
        if d > r - 0.9:
            p.set(x, p.W, z, P.WHITE_BLOCK)
        else:
            p.set(x, p.W, z, lamp); p.set(x, p.W + 1, z, glass)
    return True
