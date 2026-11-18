"""Layer 25: the city's base fabric: raised decks, sculpted deck edges, lagoons, airlock halls, deck lighting.

Everything is built from small features (one per pier, lagoon, inner sector, perimeter arc, the core, ...) that loop
only over `vol.clip(bbox)` and ask `planner.zone()` per column.

Vertical convention (see planner.py): foundation floor y 0, deck solid y 1..DECK_TOP (4), players walk at y 5.
Lagoons: bed y -4, water y -3..0, open dome air above.

DOME CLEARANCE (important for later waves): the dome is a very flat cap, so under the outer part of the perimeter
promenade the shell comes down onto the deck.  A deck cell may only exist where two free cells remain above its
walking surface, so the deck top is  deck_top_at(ctx, x, z) = min(DECK_TOP, yi - 2)  with yi = highest dry air y of
the column.  This is DECK_TOP (4) out to r ~577 and ramps down (stair blocks) to y 0 at r ~588, where it meets the
rim walkway (r 588..600, floor y 0).  Roads/buildings must use deck_top_at(), not the constant, for r > 570.

Deck edge footprint: a deck column is an "edge column" (`edge_dirs`) when a 4-neighbour is a lagoon column.  Edge
columns: y0..1 dark_prismarine waterline course, y2..3 quartz/calcite face with a recessed cyan light line (cyan glass
in the face, sea_lantern behind it) every 6 blocks, y4 an upside-down stair cornice.  Railing (y5) stands on every
column that has a lagoon in its 8-neighbourhood (and top y 4): glass panes with quartz posts, explicit connections.
Roads that cross an edge (bridges) must remove the railing cells they use.

Water: lagoon features carry meta water_ok=True (bbox).  All lagoon water lies in x/z columns whose zone is 'lagoon'
and in y -3..0; the walls are deck columns / foundation (tests/test_fabric.py proves containment).

Lights: hidden light[level=15] (meta temporary_light) at y = walking y on a lattice (Manhattan radius 13; 9 on the
perimeter ramp, which is up to 4 blocks sloped) and above the lagoons at y 5 (so deck edges get light).
"""
import math

from .feature import Feature, register
from . import planner as PL
from . import palette as P
from . import blocks as B
from . import dome as D

LAYER = 25
HALL_LAYER = 26
DECK_TOP = PL.DECK_TOP
WALK = PL.WALK_Y
LF, LS = PL.LAGOON_FLOOR, PL.LAGOON_SURFACE
LIGHT = P.HIDDEN_LIGHT
BODY = 'quartz_block'
PANEL_A, PANEL_B = 'smooth_quartz', 'polished_diorite'
GLASS_PANE = 'light_blue_stained_glass_pane'
N_ARCS = 24
LAT_R = 13          # light lattice radius (flat deck)
LAT_R_RAMP = 9      # perimeter (sloped under the dome)
RAMP_R = 566        # below this radius the perimeter deck is flat (top 4)

HALL_U0 = 577.0                             # hall interior along the axis: r 577..588 (11 long)
HALL_HW = 4.5                               # 9 wide
HALL_WALL = 5.7
END_U = 586.7                               # end wall (doorway frame) starts here
STEP_U = 583.0                              # hall floor y 0 for u >= STEP_U, then one step per block


def _h(ix, iz, salt=0):
    n = (ix * 374761393 + iz * 668265263 + salt * 2246822519) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return (n ^ (n >> 16)) / 4294967295.0


# ------------------------------------------------------------------------------------------------ column model
def air_top(ctx, x, z):
    """Highest y of the dry dome air in column (x, z) (-1 if none)."""
    g = D.geo(ctx)
    t = g.top(x, z, g.rr)
    return -1 if t is None else t


def deck_top_at(ctx, x, z):
    """Top block y of the deck at (x, z), or None where there is no deck. DECK_TOP, lowered under the dome edge."""
    if not PL.has_deck(x, z): return None
    if x * x + z * z < RAMP_R * RAMP_R: return DECK_TOP
    return max(0, min(DECK_TOP, air_top(ctx, x, z) - 2))


_DIRS = (('north', 0, -1), ('east', 1, 0), ('south', 0, 1), ('west', -1, 0))


def edge_dirs(x, z):
    """Cardinal directions in which the deck column (x, z) borders a lagoon column (only the lagoon rim zone
    r 297..543 can)."""
    r2 = x * x + z * z
    if r2 < 297 * 297 or r2 > 543 * 543 or not PL.has_deck(x, z): return ()
    return tuple(d for d, dx, dz in _DIRS if PL.zone(x + dx, z + dz) == 'lagoon')


class _Col:
    """Per-feature memo of edge / rail predicates (pure functions of x, z)."""

    def __init__(self):
        self.e4, self.rail, self.lag = {}, {}, {}

    def is_lagoon(self, x, z):
        k = (x, z)
        v = self.lag.get(k)
        if v is None:
            v = self.lag[k] = PL.zone(x, z) == 'lagoon'
        return v

    def edges(self, x, z):
        k = (x, z)
        v = self.e4.get(k)
        if v is None:
            v = self.e4[k] = edge_dirs(x, z)
        return v

    def is_rail(self, x, z):
        k = (x, z)
        v = self.rail.get(k)
        if v is None:
            r2 = x * x + z * z
            v = False
            if 296 * 296 <= r2 <= 544 * 544 and PL.has_deck(x, z):
                v = any(self.is_lagoon(x + dx, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1) if dx or dz)
            self.rail[k] = v
        return v


def _paving(x, z):
    return PANEL_B if _h(x // 32, z // 32, 5) < 0.2 else PANEL_A


def build_deck_column(vol, col, x, z, t):
    """Write one deck column (x, z) with top block y t (t >= 1)."""
    if t >= 2: vol.fill(x, 1, z, x, t - 1, z, BODY)
    ed = col.edges(x, z) if t == DECK_TOP else ()
    if ed:
        d = ed[0]
        vol.set(x, 0, z, 'dark_prismarine')
        vol.set(x, 1, z, 'dark_prismarine')
        vol.set(x, 2, z, 'smooth_quartz')
        vol.set(x, 3, z, 'calcite')
        # recessed cyan light line: cyan glass in the face, sea_lantern behind it (inward neighbour)
        if (x + z) % 6 == 0:
            dx, dz = B.DIRS[d]
            ix, iz = x - dx, z - dz
            if PL.has_deck(ix, iz) and not edge_dirs(ix, iz):
                vol.set(x, 2, z, 'cyan_stained_glass')
                vol.set(ix, 2, iz, 'sea_lantern')
        # cornice: upside-down stair, tall back inward, so the overhang casts a shadow line; flat top at y 4
        vol.set(x, 4, z, B.stairs('smooth_quartz', B.opposite(d), half='top'))
    else:
        vol.set(x, t, z, _paving(x, z))
    if t == DECK_TOP and col.is_rail(x, z):
        _rail(vol, col, x, z)


def _rail(vol, col, x, z):
    if (x + z) % 6 == 0:
        vol.set(x, 5, z, 'quartz_pillar[axis=y]')
        vol.set(x, 6, z, 'end_rod[facing=up]')
        return
    vol.set(x, 5, z, B.pane(GLASS_PANE, north=col.is_rail(x, z - 1), east=col.is_rail(x + 1, z),
                            south=col.is_rail(x, z + 1), west=col.is_rail(x - 1, z)))


def _ramp_top(vol, ctx, x, z, t):
    """On the perimeter ramp, the top block becomes a stair when the outward neighbour is lower."""
    f = B.facing_from_vector(-x, -z)
    dx, dz = B.DIRS[f]
    tn = deck_top_at(ctx, x - dx, z - dz)
    if tn is not None and tn < t:
        vol.set(x, t, z, B.stairs('smooth_quartz', f))


# ------------------------------------------------------------------------------------------------ lattices
def lattice(x1, z1, x2, z2, R):
    """Lattice points (Manhattan balls of radius R tile the plane) inside the rectangle."""
    ax, az, bx, bz = R, R + 1, -(R + 1), R
    det = ax * bz - az * bx
    cs = [((bz * x - bx * z) / det, (-az * x + ax * z) / det) for x in (x1, x2) for z in (z1, z2)]
    out = []
    for a in range(math.floor(min(c[0] for c in cs)) - 1, math.ceil(max(c[0] for c in cs)) + 2):
        for b in range(math.floor(min(c[1] for c in cs)) - 1, math.ceil(max(c[1] for c in cs)) + 2):
            x, z = a * ax + b * bx, a * az + b * bz
            if x1 <= x <= x2 and z1 <= z <= z2: out.append((x, z))
    return out


# ------------------------------------------------------------------------------------------------ bbox helpers
def _bbox_pts(pts, y1, y2, m=3):
    xs = [p[0] for p in pts]; zs = [p[1] for p in pts]
    return (int(math.floor(min(xs))) - m, y1, int(math.floor(min(zs))) - m,
            int(math.ceil(max(xs))) + m, y2, int(math.ceil(max(zs))) + m)


def _arc_pts(r0, r1, a0, a1, nr=3, na=24):
    pts = []
    for i in range(nr + 1):
        r = r0 + (r1 - r0) * i / nr
        for j in range(na + 1):
            pts.append(PL.from_polar(r, a0 + (a1 - a0) * j / na))
    return pts


# ------------------------------------------------------------------------------------------------ deck pieces
class DeckPiece(Feature):
    """A piece of deck: the columns of one zone region (pred) inside bbox."""

    def __init__(self, name, bbox, pred, ramp=False):
        super().__init__(name, bbox, LAYER, {'kind': 'fabric_deck'})
        self.pred = pred
        self.ramp = ramp

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        x1, _, z1, x2, _, z2 = c
        col = _Col()
        pred = self.pred
        ramp = self.ramp
        tops = {}
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                if not pred(x, z): continue
                t = deck_top_at(ctx, x, z) if ramp else DECK_TOP
                tops[(x, z)] = t
                if t >= 1:
                    build_deck_column(vol, col, x, z, t)
                    if ramp and t < DECK_TOP: _ramp_top(vol, ctx, x, z, t)
        # hidden lights (lattice points on this piece, one block above the walking surface's floor)
        pts = lattice(x1, z1, x2, z2, LAT_R)
        if ramp: pts += [q for q in lattice(x1, z1, x2, z2, LAT_R_RAMP) if q[0] ** 2 + q[1] ** 2 >= RAMP_R ** 2]
        for x, z in pts:
            t = tops.get((x, z))
            if t is None: continue
            vol.set(x, t + 1 + (1 if (t == DECK_TOP and col.is_rail(x, z)) else 0), z, LIGHT)


def _core_pred(x, z): return x * x + z * z < PL.CORE_R ** 2


def _inner_pred(ang):
    def f(x, z):
        return PL.zone(x, z) == 'inner' and PL.nearest(PL.PIER_ANGLES, PL.plan_angle(x, z)) == ang
    return f


def _pier_pred(ang):
    def f(x, z):
        return PL.in_pier(x, z, ang) is not None and PL.zone(x, z) == 'pier'
    return f


def _perim_pred(k):
    w = 360.0 / N_ARCS

    def f(x, z):
        r2 = x * x + z * z
        if r2 < PL.PERIM_R0 ** 2 or r2 >= PL.PERIM_R1 ** 2: return False
        return int(PL.plan_angle(x, z) // w) % N_ARCS == k
    return f


# ------------------------------------------------------------------------------------------------ lagoons
CORAL = ('tube_coral', 'brain_coral', 'bubble_coral', 'fire_coral', 'horn_coral')


def bed_block(x, z):
    n = _h(x // 6, z // 6, 11)
    m = _h(x, z, 12)
    if n < 0.45: return 'sand' if m > 0.12 else 'gravel'
    if n < 0.75: return 'gravel' if m > 0.2 else 'sand'
    if n < 0.88: return 'prismarine' if m > 0.35 else 'sand'
    return 'sand'


def bed_lantern(x, z):
    return z % 12 == 0 and (x + 6 * ((z // 12) & 1)) % 12 == 0


CELL = 22


def cluster(x, z):
    """(kind, d2) of the coral cluster covering (x, z) or None; centre and kind are hash-derived."""
    cx, cz = x // CELL, z // CELL
    best = None
    for ix in (cx - 1, cx, cx + 1):
        for iz in (cz - 1, cz, cz + 1):
            if _h(ix, iz, 21) > 0.5: continue
            px = ix * CELL + 4 + int(_h(ix, iz, 22) * (CELL - 8))
            pz = iz * CELL + 4 + int(_h(ix, iz, 23) * (CELL - 8))
            d2 = (x - px) ** 2 + (z - pz) ** 2
            if d2 <= 6 and (best is None or d2 < best[1]):
                best = (int(_h(ix, iz, 24) * 5), d2)
    return best


def build_lagoon_column(vol, x, z):
    vol.fill(x, LF + 1, z, x, LS, z, 'water')
    if bed_lantern(x, z):
        vol.set(x, LF, z, 'sea_lantern')
        return
    vol.set(x, LF, z, bed_block(x, z))
    cl = cluster(x, z)
    h = _h(x, z, 31)
    if cl is not None:
        kind, d2 = cl
        if d2 <= 1:
            if h > 0.5:                      # stacked: the bed block touches no water and would die -> place it dead
                vol.set(x, LF, z, 'dead_' + CORAL[kind] + '_block')
                vol.set(x, LF + 1, z, CORAL[kind] + '_block')
                vol.set(x, LF + 2, z, CORAL[kind] + '[waterlogged=true]')
            else:
                vol.set(x, LF, z, CORAL[kind] + '_block')
                vol.set(x, LF + 1, z, CORAL[kind] + '[waterlogged=true]')
        elif h < 0.45:
            vol.set(x, LF + 1, z, CORAL[(kind + int(h * 40)) % 5] + '[waterlogged=true]')
        elif h < 0.8:
            vol.set(x, LF + 1, z, 'sea_pickle[pickles=%d,waterlogged=true]' % (1 + int(h * 100) % 4))
        else:
            vol.set(x, LF + 1, z, 'seagrass')
    elif _h(x, z, 41) < 0.05:
        vol.set(x, LF + 1, z, 'seagrass')


class Lagoon(Feature):
    def __init__(self, ang, bbox):
        super().__init__(f'fabric_lagoon_{ang:03d}', bbox, LAYER, {'kind': 'fabric_lagoon', 'water_ok': True})
        self.ang = ang

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        x1, _, z1, x2, _, z2 = c
        zone = PL.zone
        cols = set()
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                if zone(x, z) != 'lagoon': continue
                if PL.nearest(PL.WEDGE_ANGLES, PL.plan_angle(x, z)) != self.ang: continue
                cols.add((x, z))
                build_lagoon_column(vol, x, z)
        if vol.y2 >= 5 and vol.y1 <= 5:
            for x, z in lattice(x1, z1, x2, z2, LAT_R):
                if (x, z) in cols: vol.set(x, 5, z, LIGHT)


# ------------------------------------------------------------------------------------------------ airlock halls
class AirlockHall(Feature):
    """Sunken airlock hall in the perimeter deck at a wedge axis. Frame: u along the axis (outward), v lateral,
    exactly the dome.Station convention (u = x cos g + z sin g, v = -x sin g + z cos g, g = plan - 90)."""

    def __init__(self, plan):
        self.plan = plan
        g = math.radians(plan - 90)
        self.cg, self.sg = math.cos(g), math.sin(g)
        pts = [(u * self.cg - v * self.sg, u * self.sg + v * self.cg)
               for u in (HALL_U0 - 2, 594) for v in (-HALL_WALL - 1, HALL_WALL + 1)]
        super().__init__(f'fabric_hall_{plan:03d}', _bbox_pts(pts, -1, 9, 2), HALL_LAYER,
                         {'kind': 'airlock_hall', 'plan_angle': plan})
        self.inward = B.facing_from_vector(-self.cg, -self.sg)

    def uv(self, x, z):
        return x * self.cg + z * self.sg, -x * self.sg + z * self.cg

    def floor_y(self, ctx, x, z):
        """Hall floor block y at an interior column: 0 near the doorway, one step per block towards the deck."""
        u, _ = self.uv(x, z)
        return min(deck_top_at(ctx, x, z) or 0, max(0, math.ceil(STEP_U - 2 - u)))   # one block per step, started 2 blocks inward: head room under the low dome glass

    def classify(self, x, z):
        u, v = self.uv(x, z)
        av = abs(v)
        if u >= D.TUN_U0 and av <= D.TUN_HW: return 'tunnel'
        if u >= D.TUN_U0 or u < HALL_U0 - 1.0 or av > HALL_WALL: return None
        if u >= END_U and av > D.TUN_HW:
            return 'frame' if av <= 2.6 else 'endwall'
        if u >= HALL_U0 and av <= HALL_HW: return 'floor'
        if u < END_U: return 'wall'
        return 'floor'

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        x1, _, z1, x2, _, z2 = c
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                k = self.classify(x, z)
                if k is None or k == 'tunnel': continue
                u, v = self.uv(x, z)
                yi = air_top(ctx, x, z)
                tn = deck_top_at(ctx, x, z) or 0
                if k == 'floor':
                    f = self.floor_y(ctx, x, z)
                    if f == 0:
                        vol.set(x, 0, z, 'cyan_concrete' if abs(v) < 0.6 else 'smooth_quartz')
                    else:
                        if f >= 2: vol.fill(x, 1, z, x, f - 1, z, BODY)
                        step = self._rising(ctx, x, z, f)
                        vol.set(x, f, z, B.stairs('smooth_quartz', self.inward) if step else 'smooth_quartz')
                    for y in range(f + 1, tn + 2):
                        if y <= yi: vol.set(x, y, z, 'air')
                elif k == 'wall':
                    if tn >= 1:
                        vol.fill(x, 1, z, x, tn, z, 'quartz_bricks')
                        vol.set(x, tn, z, 'chiseled_quartz_block')
                        if tn >= 3 and int(u) % 4 == 0 and abs(v) <= HALL_HW + 1.2:
                            vol.set(x, 2, z, 'sea_lantern')
                else:       # doorway frame / end wall
                    for y in range(1, yi + 1):
                        if k == 'frame':
                            vol.set(x, y, z, 'quartz_pillar[axis=y]' if y <= 2 else 'chiseled_quartz_block')
                        else:
                            vol.set(x, y, z, 'quartz_bricks')
        # lintel over the opening (only where there is room below the shell)
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                u, v = self.uv(x, z)
                if END_U <= u < D.TUN_U0 and abs(v) <= D.TUN_HW and air_top(ctx, x, z) >= 3:
                    vol.set(x, 3, z, 'chiseled_quartz_block')
        # hidden lights: hall + the rim walkway at the doorway
        for u, v, fy in ((584.5, 0.0, 2), (580.0, 0.0, None), (590.5, 3.0, 1), (590.5, -3.0, 1)):
            x = int(round(u * self.cg - v * self.sg)); z = int(round(u * self.sg + v * self.cg))
            if fy is None: fy = self.floor_y(ctx, x, z) + 1
            if self.classify(x, z) == 'tunnel' or not ctx.inside_dome(x, fy, z): continue
            vol.set(x, fy, z, LIGHT)

    def _rising(self, ctx, x, z, f):
        """True when the next column towards the doorway (outward) is lower, i.e. this block is a step."""
        dx, dz = B.DIRS[self.inward]
        return self.floor_y(ctx, x - dx, z - dz) < f


class RimLights(Feature):
    """Hidden lights along the rim walkway (r 588..594, floor y 0, the inside of the dome plinth)."""
    N = 220

    def __init__(self):
        super().__init__('fabric_rim_lights', (-594, 1, -594, 594, 1, 594), LAYER,
                         {'kind': 'rim_lighting', 'temporary_light': True})
        self._pts = None

    def points(self, ctx):
        if self._pts is None:
            pts = set()
            for k in range(self.N):
                a = 2 * math.pi * (k + 0.5) / self.N
                for r in (589.0, 590.0, 591.0):
                    x, z = int(round(r * math.cos(a))), int(round(r * math.sin(a)))
                    if ctx.inside_dome(x, 1, z) and PL.zone(x, z) == 'rim' and \
                            not any(_in_tunnel(x, z, p) for p in D.STATION_PLANS):
                        pts.add((x, z)); break
            self._pts = sorted(pts)
        return self._pts

    def build(self, vol, ctx):
        if not (vol.y1 <= 1 <= vol.y2): return
        for x, z in self.points(ctx):
            if vol.x1 <= x <= vol.x2 and vol.z1 <= z <= vol.z2:
                vol.set(x, 1, z, LIGHT)


def _in_tunnel(x, z, plan):
    g = math.radians(plan - 90)
    c, s = math.cos(g), math.sin(g)
    u, v = x * c + z * s, -x * s + z * c
    return u >= D.TUN_U0 - 1 and abs(v) <= D.TUN_HW + 1.5


# ------------------------------------------------------------------------------------------------ registration
def make_features(ctx):
    feats = []
    r = PL.CORE_R + 1
    feats.append(DeckPiece('fabric_core', (-r, 1, -r, r, 7, r), _core_pred))
    for a in PL.PIER_ANGLES:
        pts = _arc_pts(PL.CORE_R, PL.INNER_R, a - 30, a + 30)
        feats.append(DeckPiece(f'fabric_inner_{a:03d}', _bbox_pts(pts, 0, 7, 2), _inner_pred(a)))
        pts = []
        ux, uz = PL.from_polar(1.0, a)
        for i in range(0, 13):
            al = PL.INNER_R + (PL.PIER_TIP_R - PL.INNER_R) * i / 12
            hw = PL.pier_half_width(al)
            for s in (-hw, hw):
                pts.append((al * ux - s * uz, al * uz + s * ux))
        pts += _arc_pts(PL.INNER_R, PL.PIER_TIP_R, a - 17, a + 17, 2, 12)
        feats.append(DeckPiece(f'fabric_pier_{a:03d}', _bbox_pts(pts, 0, 7, 2), _pier_pred(a)))
    w = 360.0 / N_ARCS
    for k in range(N_ARCS):
        pts = _arc_pts(PL.PERIM_R0, PL.PERIM_R1, k * w, (k + 1) * w, 2, 6)
        feats.append(DeckPiece(f'fabric_perim_{k:02d}', _bbox_pts(pts, 0, 7, 2), _perim_pred(k), ramp=True))
    for a in PL.WEDGE_ANGLES:
        pts = _arc_pts(PL.INNER_R, PL.PIER_TIP_R, a - 30, a + 30, 4, 24)
        feats.append(Lagoon(a, _bbox_pts(pts, LF, 6, 2)))
        feats.append(AirlockHall(a))
    feats.append(RimLights())
    return feats


_HALLS = []


def hall_info():
    """[{plan, name, bbox}] of the airlock halls (for roads/validators)."""
    return [{'plan': h.plan, 'name': h.name, 'bbox': h.bbox} for h in _HALLS]


def _register():
    from . import config
    fs = make_features(config.load())
    _HALLS[:] = [f for f in fs if isinstance(f, AirlockHall)]
    register(*fs)


_register()
