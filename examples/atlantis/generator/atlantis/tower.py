"""Layer 50: the Central Tower of Atlantis (district A): structure and exterior.  Interiors (rooms, furniture,
consoles, room lighting) belong to tower_interior.py (layer 60); this module builds everything they stand inside.

All numbers come from tower_spec (profile TIERS, FLOORS, CORE_R, WALL, ENTRANCE_PLAN_ANGLES).  Tower axis (0, 0).
Plan angles are degrees clockwise from north (see ARCHITECTURE.md); geometry angles ("geom") run from east
towards south.

STRUCTURE CONTRACT (what the interior worker relies on)
  * Hollow tower: outer wall = ring (R(y) - 1, R(y)] of the spec profile (+ fins outside); everything inside is air.
  * Floor slabs: ONE block thick at each tower_spec.FLOORS y, across the whole interior, except
      - the core disc d <= CORE_R (5), which is open from y 5 to the summit (stair + lift, identical on every floor);
      - floor 17 (Operations Hall) is a mezzanine RING: its hole has radius MEZZ_HOLE_R = 10 (cells with d <= 10 are
        air at y 17, d = sqrt(x^2 + z^2)); a glass railing stands on the first ring of floor cells (10 < d <= 11.3) at
        y 18, and a 3-wide gangway (y 17, with railings at y 18) crosses the hole at geom angle 45 deg (plan 135,
        south-east) linking the stair exit to the ring floor;
      - floor 5 (ground) is a full disc (d <= 34) with the atrium open above it (y 6..16 inside the wall).
  * Core (d <= CORE_R): spiral stairs r 2..5 (geometry.spiral_stairs, 12 steps per turn = 6 blocks per turn = one
    FLOOR_H, clockwise on screen, tread j=0 is a slab at y 5, wedge 1 of each turn is a full block flush with the floor
    block) around a 3x3 lift housing of light-blue glass (|x| <= 1, |z| <= 1) whose centre cell (0,0) is a bubble column
    (soul sand at y 5, water-filled y 6..95).  The lift is entered through a warped door at (1,0) on every floor
    (door cells y_f+1, y_f+2, landing block (2,0,y_f)).  The stair exits onto each floor over wedges 0..2 = geom angles
    0..90 deg (the quadrant x >= 0, z >= 0): keep core_cells(y) clear.
  * Every floor has ring(s) of glowing ceiling inlays (sea lantern in the slab cell, light-blue carpet above it), so
    every walkable cell is lit before any interior exists.
  * Summit (y 95): open platform r <= 10 with railing (y 96) and the spire's six ribs rising from it; the needle's
    tip is at TIP_Y.
Doorways cut in the walls (keep 3 cells in front clear): see portal_cells(), balcony_door_cells(), deck_door_cells().
"""
import math

from . import tower_spec as S, geometry as G, blocks as B, palette as P
from .feature import Feature, register

LAYER = 50
TIP_Y = S.TIP_Y
CORE_R = S.CORE_R
MEZZ_HOLE_R = 10
MEZZ_Y = 17
BRIDGE_GEOM = 45.0                 # gangway across the mezzanine hole (geometry angle, 45 = south-east)
APRON_R = 36.6                     # plinth / apron outer radius (bbox r 36 + rounding)
BBOX = (-37, 3, -37, 37, TIP_Y - 1, 37)      # the end-rod tip (y TIP_Y, centre) is the only cell above

# ---- materials
WALL = P.WHITE                       # smooth_quartz
SHAFT = P.PALE                       # calcite: the main shaft is a shade darker than fins and crown
BLOCK = P.WHITE_BLOCK                # quartz_block
BRICKS = P.WHITE_BRICKS              # quartz_bricks
PILLAR = 'quartz_pillar[axis=y]'
DARK = 'dark_prismarine'
GLASS = P.GLASS_BLUE                 # light_blue_stained_glass
CYAN = P.GLASS_CYAN
LAMP = P.LAMP
CARPET = 'light_blue_carpet'
AIR = 'air'
PANE = P.GLASS_BLUE_PANE
MOSS = 'moss_block'
LEAVES = 'flowering_azalea_leaves[persistent=true]'

PORTAL_W = 7
PORTAL_H = 6                          # y 6..11
PORTAL_PROFILE = [3, 3, 3, 3, 3, 2]   # half widths by row (arch)

FINS_MAJOR = tuple(range(0, 360, 60))
FINS_MINOR = tuple(range(30, 360, 60))
BALCONY_AZ = tuple(range(30, 360, 60))       # observation-ring balconies
DECK_DOOR_AZ = tuple(range(0, 360, 60))      # navigation room -> crown roof deck


# ------------------------------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------------------------------
_SHAFT_STEPS = ((17, 20.0), (29, 19.0), (41, 18.0), (53, 17.0), (65, 16.0))     # y_from, built outer radius
_UPPER_STEPS = ((83, 12.0), (89, 11.0))


def R(y):
    """Built outer radius at y: the spec profile (tower_spec.outer_r), with the gentle taper of the main shaft and
    of the upper shaft built as clean one-block setbacks at floor slabs (deviation from the spec < 1 block)."""
    if 17 <= y < 71:
        steps = _SHAFT_STEPS
    elif 83 <= y < 95:
        steps = _UPPER_STEPS
    else:
        return S.outer_r(y)
    r = steps[0][1]
    for y0, rr in steps:
        if y >= y0: r = rr
    return r


def _rad(a):
    return math.radians(a)


def radial(a):
    """Unit vector (x, z) of plan angle a."""
    return math.sin(_rad(a)), -math.cos(_rad(a))


def plan_of(x, z):
    return math.degrees(math.atan2(x, -z)) % 360.0


def adiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def cardinal(dx, dz):
    if abs(dx) >= abs(dz): return 'east' if dx > 0 else 'west'
    return 'south' if dz > 0 else 'north'


def wall_inner(y):
    """Inner radius of the wall ring at y (cells with wall_inner < d <= R are wall)."""
    return R(y) - S.WALL


def hyp(x, z):
    return math.hypot(x, z)


def outline_cells(r):
    """One-block, 4-connected outline of the disc d <= r (cells inside with an 8-neighbour outside)."""
    r2 = r * r
    out = set()
    for x, z in G.ring_cells(0, 0, r - 2.0, r):
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (x + dx) ** 2 + (z + dz) ** 2 > r2:
                    out.add((x, z)); break
            else:
                continue
            break
    return out


def arc_len_points(r, spacing, offset=0.0):
    n = max(3, int(round(2 * math.pi * r / spacing)))
    return [offset + 360.0 * i / n for i in range(n)]


def cell_at(r, a):
    sx, sz = radial(a)
    return G.rnd(r * sx), G.rnd(r * sz)


class Ops:
    """Ordered sparse block writes (later wins), bucketed by 16x16 columns for fast per-tile replay."""

    def __init__(self):
        self.list = []

    def add(self, x, y, z, b):
        self.list.append((x, y, z, b))

    def fill_col(self, x, z, y1, y2, b):
        for y in range(y1, y2 + 1): self.list.append((x, y, z, b))

    def buckets(self):
        d = {}
        for op in self.list:
            d.setdefault((op[0] >> 4, op[2] >> 4), []).append(op)
        return d


def _pane(cells, x, z, name=PANE):
    return B.pane(name, north=(x, z - 1) in cells, east=(x + 1, z) in cells, south=(x, z + 1) in cells,
                  west=(x - 1, z) in cells)


# ------------------------------------------------------------------------------------------------------------
# public structure helpers
# ------------------------------------------------------------------------------------------------------------
def floor_ys():
    return [f[0] for f in S.FLOORS]


def core_cells(y=None):
    """(x, z) cells the interior worker must keep clear on every floor: the core disc d <= CORE_R + 1 (stair, lift,
    landing) plus the stair exit lane: south-east quadrant lane (geom angle 25..65 deg) out to d <= 12 (the mezzanine
    gangway lies in it on floor 17).  Identical on every floor; y is accepted for API symmetry."""
    out = set(G.disc_cells(0, 0, CORE_R + 1))
    for x, z in G.ring_cells(0, 0, CORE_R, 12):
        a = math.degrees(math.atan2(z, x)) % 360.0
        if 25.0 <= a <= 65.0: out.add((x, z))
    return out


def stair_cells():
    return set(G.ring_cells(0, 0, 2.0, CORE_R))


def portal_cells(i):
    """Air cells (x, y, z) of entrance portal i (plan angle ENTRANCE_PLAN_ANGLES[i])."""
    return [(x, y, z) for (x, y, z, b) in _portal_carve(S.ENTRANCE_PLAN_ANGLES[i]) if b == AIR]


def balcony_door_cells():
    return [(x, y, z) for az in BALCONY_AZ for (x, y, z) in _door_cells(az, 22.5, 72, 74, 1.5)]


def deck_door_cells():
    return [(x, y, z) for az in DECK_DOOR_AZ for (x, y, z) in _door_cells(az, R(84) - 0.5, 84, 86, 1.5)]


def tower_feature_meta():
    return {
        'district': 'A', 'kind': 'tower',
        'entrances': [tuple(e) for e in S.entrances()],
        'interior': True,
        'interior_box': (-34, 6, -34, 34, TIP_Y - 1, 34),
        'roof_y': TIP_Y,
        'islands_ok': True,            # stepped ledges of the taper, skirt and fins are decorative (see tests for BFS)
        'water_ok': [(-1, 5, -1, 1, 96, 1)],     # the lift's bubble column
        'mezzanine_hole_r': MEZZ_HOLE_R,
        'core_r': CORE_R,
    }


# ------------------------------------------------------------------------------------------------------------
# sparse ops
# ------------------------------------------------------------------------------------------------------------
def _door_cells(az, r, y1, y2, hw):
    """Wall cells (x, y, z) of a doorway of half-width hw (blocks) at plan angle az, through the wall at radius r."""
    sx, sz = radial(az)
    tx, tz = -sz, sx          # tangent
    out = []
    seen = set()
    k = -hw
    while k <= hw + 1e-9:
        for dr in (-0.5, 0.0, 0.5):
            c = (G.rnd((r + dr) * sx + k * tx), G.rnd((r + dr) * sz + k * tz))
            if c not in seen:
                seen.add(c)
                for y in range(y1, y2 + 1): out.append((c[0], y, c[1]))
        k += 0.5
    return out


def _wall_cell(y, a, bias=-0.5):
    r = R(y)
    ri = wall_inner(y)
    for dr in (bias, bias - 0.3, bias + 0.4, bias + 0.8):
        sx, sz = radial(a)
        c = (G.rnd((r + dr) * sx), G.rnd((r + dr) * sz))
        d = hyp(*c)
        if ri < d <= r: return c
    return None


def _ops_windows(o):
    fins = FINS_MAJOR + FINS_MINOR
    # (y_first_row, rows, tier radius y for count, skip azimuths, skip half-width)
    specs = []
    for yf, name, _ in S.FLOORS:
        if yf in (5, 71, 77, 95): continue
        if yf <= 65: specs.append((yf, yf + 1, 4, fins, 5.5))
        else: specs.append((yf, yf + 1, 4, (), 0))
    for yf, y0, rows, skip, sw in specs:
        r = R(y0 + 2)
        n = max(6, int(round(2 * math.pi * r / 6.5)))
        if skip: n = (n // 6 + (1 if n % 6 >= 3 else 0)) * 6
        for i in range(n):
            a = 360.0 * i / n
            if skip and min(adiff(a, s) for s in skip) < sw: continue
            for y in range(y0, y0 + rows):
                c = _wall_cell(y, a)
                if c: o.add(c[0], y, c[1], GLASS)
    # podium clerestory slits (tier 2 and tier 3 walls)
    for y0, rows, rr, n in ((10, 2, 29.5, 30), (14, 2, 25.5, 24)):
        for i in range(n):
            a = 360.0 * (i + 0.5) / n
            if min(adiff(a, p) for p in S.ENTRANCE_PLAN_ANGLES) < 9.5: continue
            for y in range(y0, y0 + rows):
                c = _wall_cell(y, a)
                if c: o.add(c[0], y, c[1], GLASS)


def _ops_belts(o):
    """Cyan glass bands with sparse sea lanterns behind them (the tower's restrained energy lines)."""
    def belt(y, step_deg, lamp):
        r = R(y)
        for x, z in G.ring_cells(0, 0, wall_inner(y), r):
            o.add(x, y, z, CYAN)
        if lamp:
            n = int(360 / step_deg)
            for i in range(n):
                a = i * step_deg
                sx, sz = radial(a)
                o.add(G.rnd((r - 1.6) * sx), y, G.rnd((r - 1.6) * sz), LAMP)
    belt(7, 0, False)
    belt(11, 7.5, True)
    belt(15, 7.5, True)
    for yb in (28, 40, 52, 64): belt(yb, 7.5, True)
    belt(77, 6.0, True)
    belt(94, 10.0, True)
    # glowing ring round the crown's flare
    rs, ro = _skirt_bounds(66)
    for i in range(60):
        a = i * 6.0
        sx, sz = radial(a)
        o.add(G.rnd((ro - 0.45) * sx), 66, G.rnd((ro - 0.45) * sz), CYAN)
        o.add(G.rnd((ro - 1.7) * sx), 66, G.rnd((ro - 1.7) * sz), LAMP)


def _fin_cells(a, y0, y1, ext_fn, hw_fn):
    """[(y, x, z, u, v)] cells of one fin at plan angle a: |u| <= hw tangentially, outside the wall, v <= R + ext."""
    sx, sz = radial(a)
    tx, tz = -sz, sx
    out = []
    for y in range(y0, y1 + 1):
        r = S.outer_r(y)
        ext = ext_fn(y)
        if ext < 0.4: continue
        hw = hw_fn(y)
        vmax = r + ext
        # bounding box of the oriented rectangle u in [-hw, hw], v in [r - 1.2, vmax]
        pts = [(v * sx + u * tx, v * sz + u * tz) for v in (r - 1.2, vmax) for u in (-hw, hw)]
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        for x in range(int(math.floor(min(xs) - 0.5)), int(math.ceil(max(xs) + 0.5)) + 1):
            for z in range(int(math.floor(min(zs) - 0.5)), int(math.ceil(max(zs) + 0.5)) + 1):
                v = x * sx + z * sz
                u = x * tx + z * tz
                if abs(u) <= hw + 1e-9 and v <= vmax + 1e-9 and hyp(x, z) > wall_inner(y) + 0.01 and v > 0:
                    out.append((y, x, z, u, v))
    return out


def _ops_fins(o):
    # six major buttresses on the avenue axes: curved sweep from the shaft out to the crown rim
    Y0, Y1 = 19, 70
    for a in FINS_MAJOR:
        ext = lambda y: 0.5 + 7.2 * (((y - Y0) / (Y1 - Y0)) ** 2.0)
        hw = lambda y: 1.5 + 1.4 * ((y - Y0) / (Y1 - Y0)) ** 1.5
        cells = _fin_cells(a, Y0, Y1, ext, hw)
        for (y, x, z, u, v) in cells:
            o.add(x, y, z, BLOCK)
        # energy line: outermost cell on the centre line = cyan glass, the cell behind it = lantern (every 2nd row)
        by_y = {}
        for (y, x, z, u, v) in cells:
            if abs(u) <= 0.55: by_y.setdefault(y, []).append((v, x, z))
        for y, lst in by_y.items():
            lst.sort(reverse=True)
            vmax = lst[0][0]
            for v, x, z in lst:
                if v >= vmax - 0.75:
                    o.add(x, y, z, CYAN)
                elif v >= vmax - 1.75 and y % 2 == 0 and R(y) + ext(y) - R(y) >= 2.0:
                    o.add(x, y, z, LAMP)
    # six slender fins on the wedge axes
    for a in FINS_MINOR:
        Y0m, Y1m = 22, 64
        ext = lambda y: 4.2 * math.sin(math.pi * (y - Y0m) / (Y1m - Y0m)) ** 0.8
        cells = _fin_cells(a, Y0m, Y1m, ext, lambda y: 1.0)
        for (y, x, z, u, v) in cells:
            o.add(x, y, z, WALL)
        by_y = {}
        for (y, x, z, u, v) in cells:
            if abs(u) <= 0.55: by_y.setdefault(y, []).append((v, x, z))
        for y, lst in by_y.items():
            lst.sort(reverse=True)
            if ext(y) < 1.6: continue
            o.add(lst[0][2] and lst[0][1], y, lst[0][2], CYAN)
            if y % 3 == 0 and len(lst) > 1: o.add(lst[1][1], y, lst[1][2], LAMP)


def _skirt_bounds(y):
    """Solid flare under the crown: ring (R(y), rout(y)] for y 62..70."""
    if not 62 <= y <= 70: return None
    rs = R(y)
    t = (y - 61) / 10.0
    return rs, rs + (S.outer_r(71) - rs) * t ** 2.2


def _ops_balconies(o):
    for az in BALCONY_AZ:
        sx, sz = radial(az)
        cx, cz = 24.6 * sx, 24.6 * sz
        r = 3.6
        cells = []
        for x in range(int(cx - r - 1), int(cx + r + 2)):
            for z in range(int(cz - r - 1), int(cz + r + 2)):
                d = math.hypot(x - cx, z - cz)
                if d <= r and hyp(x, z) > 22.6:
                    cells.append((x, z, d))
        cs = {(x, z) for x, z, _ in cells}
        for x, z, d in cells:
            edge = d > r - 1.2
            o.add(x, 71, z, BRICKS if edge else WALL)
            if d <= r - 0.8: o.add(x, 70, z, BLOCK)
            if d <= r - 1.8: o.add(x, 69, z, DARK)
        rail = {(x, z) for x, z, d in cells if d > r - 1.2 and hyp(x, z) > 23.4}
        ring_pts = sorted(rail, key=lambda c: math.atan2(c[1] - cz, c[0] - cx))
        for i, (x, z) in enumerate(ring_pts):
            if i % 3 == 0:
                o.add(x, 72, z, PILLAR)
            else:
                o.add(x, 72, z, _pane(rail, x, z))
        # lamp on the middle rail post
        mx, mz = ring_pts[len(ring_pts) // 2]
        o.add(mx, 72, mz, PILLAR); o.add(mx, 73, mz, PILLAR); o.add(mx, 74, mz, LAMP)


def _ops_crown(o):
    # mullions of the observation ring / command deck glass band
    for a in arc_len_points(22.5, 3.6):
        c = cell_at(22.5, a)
        if hyp(*c) <= 22.0 or hyp(*c) > 23.0: continue
        for y in list(range(72, 77)) + list(range(78, 83)):
            o.add(c[0], y, c[1], PILLAR)
    # balcony doorways (3 cells wide, 3 tall) through the glass band
    for az in BALCONY_AZ:
        for (x, y, z) in _door_cells(az, 22.5, 72, 74, 1.5):
            o.add(x, y, z, AIR)
        for (x, y, z) in _door_cells(az, 22.5, 75, 75, 1.5):
            o.add(x, y, z, WALL)


def _ops_podium_base(o):
    # dark prismarine plinth + base courses; portal pads
    for x, z in G.ring_cells(0, 0, 33.0, 34.0):
        o.add(x, 5, z, DARK); o.add(x, 6, z, DARK)
    for x, z in G.ring_cells(0, 0, 34.0, APRON_R):
        o.add(x, 5, z, DARK)
    # inward stairs on the outermost apron ring, full blocks in front of the portals
    pad = []
    for a in S.ENTRANCE_PLAN_ANGLES:
        sx, sz = radial(a)
        pad.append((sx, sz, -sz, sx))
    for x, z in G.ring_cells(0, 0, 35.5, APRON_R):
        near = False
        for sx, sz, tx, tz in pad:
            if x * sx + z * sz > 20 and abs(x * tx + z * tz) <= 5.2: near = True; break
        if near: continue
        o.add(x, 5, z, B.stairs(DARK, cardinal(-x, -z)))


def _ops_apron_lights(o):
    for i in range(24):
        a = 7.5 + 15.0 * i
        if min(adiff(a, p) for p in S.ENTRANCE_PLAN_ANGLES) < 8: continue
        x, z = cell_at(35.0, a)
        o.add(x, 5, z, LAMP); o.add(x, 6, z, CARPET)


def _ops_terraces(o):
    # (rail y, rail outline radius, free walkway inner radius, flight index of the flight that ENDS on it)
    terr = [(9, 34.0, 30.0), (13, 30.0, 26.0), (17, 26.0, 20.0)]
    flights = [(34.0, 36.4, 6, 3, 16.0, 2.05, 5), (30.0, 32.5, 9, 4, 26.0, 2.3, 8), (26.0, 28.4, 13, 4, 37.0, 2.6, 12)]
    ends = []                                       # (tier index, plan angle of the flight's top)
    for k in range(6):
        base = 60.0 * k
        for ti, (b0, b1, y0, n, st, step, support) in enumerate(flights):
            ends.append((ti, base + st + n * step))
    # flights
    for k in range(6):
        base = 60.0 * k
        for ti, (b0, b1, y0, n, st, step, support) in enumerate(flights):
            for x, z in G.ring_cells(0, 0, b0, b1):
                rel = (plan_of(x, z) - base - st) % 360.0
                if rel >= n * step: continue
                s = int(rel / step)
                a = plan_of(x, z)
                tx, tz = math.cos(_rad(a)), math.sin(_rad(a))
                for y in range(support + 1, y0 + s):
                    o.add(x, y, z, WALL)
                o.add(x, y0 + s, z, B.stairs(WALL, cardinal(tx, tz)))
    # rails, lamps and planters
    for ti, (ry, rr, rin) in enumerate(terr):
        outline = outline_cells(rr)
        gaps = [a for (t, a) in ends if t == ti]
        rail = {c for c in outline if not any(adiff(plan_of(*c), g) < 5.0 for g in gaps)}
        posts = set()
        for a in arc_len_points(rr - 0.5, 4.5):
            sx, sz = radial(a)
            best = min(rail, key=lambda c: (c[0] - (rr - 0.5) * sx) ** 2 + (c[1] - (rr - 0.5) * sz) ** 2) if rail else None
            if best and (best[0] - (rr - .5) * sx) ** 2 + (best[1] - (rr - .5) * sz) ** 2 < 2.5: posts.add(best)
        lamps = set(sorted(posts, key=lambda c: plan_of(*c))[::3])
        for c in rail:
            if c in posts:
                o.add(c[0], ry, c[1], PILLAR)
                if c in lamps:
                    o.add(c[0], ry + 1, c[1], PILLAR); o.add(c[0], ry + 2, c[1], LAMP)
            else:
                o.add(c[0], ry, c[1], _pane(rail, *c))
        # paving inlays (lantern in the paving, carpet on top): they also light the gallery below
        rm = (rin + rr - 1.0) / 2.0 + 0.2
        for a in arc_len_points(rm, 7.0, 3.0):
            if min(adiff(a, p) for p in S.ENTRANCE_PLAN_ANGLES) < 9: continue
            if any(adiff(a, g) < 12 for (t, g) in ends): continue
            x, z = cell_at(rm, a)
            o.add(x, ry - 1, z, LAMP); o.add(x, ry, z, CARPET)
        # planters along the inner wall
        for a in arc_len_points(rin + 1.0, 11.0, 5.0):
            if min(adiff(a, p) for p in S.ENTRANCE_PLAN_ANGLES) < 14: continue
            if any(adiff(a, g) < 14 for (t, g) in ends): continue
            for da in (-2.6, 0.0, 2.6):
                x, z = cell_at(rin + 0.9, a + da)
                if rin < hyp(x, z) <= rin + 1.4:
                    o.add(x, ry - 1, z, MOSS); o.add(x, ry, z, LEAVES)


def _ops_portals(o):
    for a in S.ENTRANCE_PLAN_ANGLES:
        for (x, y, z, b) in _portal_frame(a): o.add(x, y, z, b)
    for a in S.ENTRANCE_PLAN_ANGLES:
        for (x, y, z, b) in _portal_carve(a): o.add(x, y, z, b)


def _portal_uv(a):
    sx, sz = radial(a)
    return sx, sz, -sz, sx


def _portal_frame(a):
    sx, sz, tx, tz = _portal_uv(a)
    op = {(u, ly) for ly in range(PORTAL_H) for u in range(-PORTAL_PROFILE[ly], PORTAL_PROFILE[ly] + 1)}
    dist = {}
    for (u, ly) in op:
        for du in range(-3, 4):
            for dl in range(-3, 4):
                c = (u + du, ly + dl)
                if c in op or c[1] < 0: continue
                d = max(abs(du), abs(dl))
                if d < dist.get(c, 9): dist[c] = d
    out = []
    cx, cz = 33.0 * sx, 33.0 * sz
    for x in range(int(cx - 10), int(cx + 11)):
        for z in range(int(cz - 10), int(cz + 11)):
            v = x * sx + z * sz
            if not 32.5 <= v <= 35.6: continue
            U = G.rnd(x * tx + z * tz)
            for (uu, ly), d in dist.items():
                if uu != U: continue
                y = 6 + ly
                if d == 1:
                    b = LAMP if (U + ly) % 2 == 0 and 33.5 <= v <= 34.6 else CYAN
                elif d == 2:
                    b = BLOCK
                else:
                    if v < 33.4: continue
                    b = DARK
                out.append((x, y, z, b))
    return out


def _portal_carve(a):
    sx, sz, tx, tz = _portal_uv(a)
    out = []
    cx, cz = 32.0 * sx, 32.0 * sz
    for x in range(int(cx - 9), int(cx + 10)):
        for z in range(int(cz - 9), int(cz + 10)):
            v = x * sx + z * sz
            if not 28.4 <= v <= 36.9: continue
            U = G.rnd(x * tx + z * tz)
            for ly in range(PORTAL_H):
                if abs(U) <= PORTAL_PROFILE[ly]:
                    out.append((x, 6 + ly, z, AIR))
    return out


def _ops_inlays(o):
    """Ring(s) of recessed ceiling lights on every floor: sea lantern in the slab cell, carpet on top."""
    def inlay(x, y, z):
        o.add(x, y, z, LAMP); o.add(x, y + 1, z, CARPET)
    # ground floor (atrium): rings out to the gallery
    for r, sp in ((9.0, 7.0), (15.0, 7.5), (21.0, 7.5), (26.5, 8.0)):
        for a in arc_len_points(r, sp, 11.0):
            x, z = cell_at(r, a)
            if hyp(x, z) > CORE_R + 1.5: inlay(x, 5, z)
    # floors above: ring radii depend on the usable radius
    for yf, name, _ in S.FLOORS:
        if yf == 5: continue
        rmax = (R(yf) if yf != 95 else 10.0) - 3.0
        if yf == MEZZ_Y:
            radii = [(14.0, 7.5)]
        else:
            radii = [(8.0, 6.5)] + ([(14.0, 7.5)] if rmax >= 14.5 else []) + ([(19.5, 8.0)] if rmax >= 20 else [])
            if yf == 95: radii = [(7.5, 6.5)]
            if yf == 89: radii = [(7.5, 6.5)]
        for r, sp in radii:
            for a in arc_len_points(r, sp, 5.0 + (yf % 12) * 2.0):
                x, z = cell_at(r, a)
                if hyp(x, z) > CORE_R + 1.5: inlay(x, yf, z)
    # crown roof deck (y 83)
    for a in arc_len_points(17.5, 8.0, 7.0):
        x, z = cell_at(17.5, a)
        if min(adiff(a, p) for p in DECK_DOOR_AZ) > 6: o.add(x, 83, z, LAMP); o.add(x, 84, z, CARPET)


def _ops_core(o):
    top = 95
    for y in range(5, top + 1):
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if dx == 0 and dz == 0: continue
                lit = dx != 0 and dz != 0 and y % 4 == (0 if dx == dz else 2)
                o.add(dx, y, dz, LAMP if lit else GLASS)
    o.add(0, 5, 0, 'soul_sand')
    for y in range(6, top + 1): o.add(0, y, 0, 'bubble_column[drag=false]')
    for yf, name, _ in S.FLOORS:
        if yf >= 95: continue
        o.add(1, yf, 0, WALL)
        o.add(1, yf + 1, 0, 'warped_door[facing=east,half=lower,hinge=left,open=false,powered=false]')
        o.add(1, yf + 2, 0, 'warped_door[facing=east,half=upper,hinge=left,open=false,powered=false]')
        o.add(2, yf, 0, WALL)                       # landing block in front of the lift door


def _ops_mezz(o):
    y = MEZZ_Y
    ca, sa = math.cos(_rad(BRIDGE_GEOM)), math.sin(_rad(BRIDGE_GEOM))
    def along(x, z): return x * ca + z * sa
    def perp(x, z): return abs(-x * sa + z * ca)
    rx = range(-13, 14)
    # gangway across the hole and its railings
    bridge = set(); rails = set()
    for x in rx:
        for z in rx:
            s, p = along(x, z), perp(x, z)
            if CORE_R - 0.5 <= s <= MEZZ_HOLE_R + 1.2:
                if p <= 1.6: bridge.add((x, z))
                elif p <= 2.6 and s >= CORE_R + 0.5: rails.add((x, z))
    for x, z in bridge:
        if hyp(x, z) > CORE_R: o.add(x, y, z, WALL)
    # hole-edge railing
    edge = set()
    for x, z in G.ring_cells(0, 0, MEZZ_HOLE_R, 11.3):
        if perp(x, z) <= 2.6 and along(x, z) > 0: continue
        edge.add((x, z))
    allr = edge | rails
    for c in allr:
        x, z = c
        o.add(x, y + 1, z, PILLAR if (x + z) % 4 == 0 else _pane(allr, x, z))


def _ops_summit(o):
    y = 95
    # platform edge rail (outline of r 10), well rail round the open stair well, with a gap for the stair exit
    outline = outline_cells(10.0)
    well = {c for c in G.ring_cells(0, 0, CORE_R, 6.3)}
    def in_exit(x, z):
        a = math.degrees(math.atan2(z, x)) % 360.0
        return a <= 70.0 or a >= 325.0
    well = {c for c in well if not in_exit(*c)}
    rail = outline | well
    rib_az = FINS_MAJOR
    post = set()
    for a in rib_az:
        sx, sz = radial(a)
        post.add(min(outline, key=lambda c: (c[0] - 9.6 * sx) ** 2 + (c[1] - 9.6 * sz) ** 2))
    for c in rail:
        o.add(c[0], y + 1, c[1], PILLAR if c in post else _pane(rail, *c))
    # the spire: six ribs sweep up from the rail posts and meet at a lantern collar, from which the needle rises
    for a in rib_az:
        sx, sz = radial(a)
        pts = []
        for yy in range(96, 101):
            t = (yy - 96) / 4.0
            d = 9.6 * (1 - t ** 1.8)
            pts.append((G.rnd(d * sx), yy, G.rnd(d * sz)))
        for p0, p1 in zip(pts, pts[1:]):
            for c in G.line_cells(p0, p1, 1):
                o.add(c[0], c[1], c[2], WALL)
        bx, by, bz = pts[3]
        o.add(bx, by, bz, LAMP)                      # bead lantern on the rib
    o.add(0, 100, 0, 'chiseled_quartz_block')
    for yy in (101, 102): o.add(0, yy, 0, PILLAR)
    o.add(0, 103, 0, LAMP)
    o.add(0, TIP_Y, 0, 'end_rod[facing=up]')
    # roof deck rail round the crown (y 84) and navigation-room doors
    deck = outline_cells(22.9)
    for a in DECK_DOOR_AZ: pass
    posts = set()
    for a in arc_len_points(22.4, 4.5):
        sx, sz = radial(a)
        posts.add(min(deck, key=lambda c: (c[0] - 22.4 * sx) ** 2 + (c[1] - 22.4 * sz) ** 2))
    lamps = set(sorted(posts, key=lambda c: plan_of(*c))[::3])
    for c in deck:
        if c in posts:
            o.add(c[0], 84, c[1], PILLAR)
            if c in lamps: o.add(c[0], 85, c[1], PILLAR); o.add(c[0], 86, c[1], LAMP)
        else:
            o.add(c[0], 84, c[1], _pane(deck, *c))
    for az in DECK_DOOR_AZ:
        for (x, y_, z) in _door_cells(az, R(84) - 0.5, 84, 86, 1.5):
            o.add(x, y_, z, AIR)


_OPS = None


def _ops():
    global _OPS
    if _OPS is None:
        o = Ops()
        _ops_podium_base(o)
        _ops_apron_lights(o)
        _ops_windows(o)
        _ops_belts(o)
        _ops_fins(o)
        _ops_crown(o)
        _ops_balconies(o)
        _ops_terraces(o)
        _ops_portals(o)
        _ops_inlays(o)
        _ops_core(o)
        _ops_mezz(o)
        _ops_summit(o)
        _OPS = o.buckets()
    return _OPS


# ------------------------------------------------------------------------------------------------------------
# bulk (span based) passes
# ------------------------------------------------------------------------------------------------------------
def _bulk(vol):
    ring, disc = G.ring, G.disc
    # floors
    for yf, name, _ in S.FLOORS:
        if yf == 5:
            disc(vol, 0, 0, 34.0, yf, WALL)
        elif yf == MEZZ_Y:
            ring(vol, 0, 0, MEZZ_HOLE_R, R(yf), yf, WALL)
        elif yf == 83:
            ring(vol, 0, 0, CORE_R, R(71), yf, WALL)
        else:
            ring(vol, 0, 0, CORE_R, R(yf) if yf != 95 else 10.0, yf, WALL)
    # podium tiers: wall rings + the terrace roof rings (tier top row)
    for y0, y1, r, rn in ((5, 8, 34.0, 30.0), (9, 12, 30.0, 26.0), (13, 16, 26.0, 20.0)):
        for y in range(y0, y1):
            ring(vol, 0, 0, r - 1.0, r, y, WALL)
        ring(vol, 0, 0, rn - 1.0, r, y1, WALL)                       # terrace roof ring
        ring(vol, 0, 0, r - 1.0, r, y1, BRICKS)                      # rim course
    # main shaft (smooth taper) and upper shaft
    for ya, yb in ((17, 70), (83, 94)):
        for y in range(ya, yb + 1):
            ring(vol, 0, 0, R(y) - S.WALL, R(y), y, SHAFT)
            if y > ya and R(y) < R(y - 1):                    # stair ledge on top of each setback
                for x, z in G.ring_cells(0, 0, R(y), R(y - 1), (vol.x1, vol.z1, vol.x2, vol.z2)):
                    vol.set(x, y, z, B.stairs(WALL, cardinal(-x, -z)))
    # flare under the crown
    for y in range(62, 71):
        rs, ro = _skirt_bounds(y)
        ring(vol, 0, 0, rs, ro, y, BLOCK)
    # crown wall + glass bands (observation ring y 72..76, command deck y 78..82)
    for y in range(71, 83):
        ring(vol, 0, 0, R(y) - 1.0, R(y), y, WALL if y in (71, 77) else GLASS)
    ring(vol, 0, 0, R(83) - 1.0, R(71), 83, WALL)
    ring(vol, 0, 0, R(71) - 1.0, R(71), 83, BRICKS)
    ring(vol, 0, 0, 13.5, 14.5, 83, BRICKS)                             # paving rings on the roof deck
    ring(vol, 0, 0, 19.5, 20.5, 83, BRICKS)
    ring(vol, 0, 0, R(71), R(71) + 1.0, 83, B.slab(WALL, 'top'))        # cornice lip of the crown roof
    # summit platform rim
    ring(vol, 0, 0, 9.0, 10.0, 95, BRICKS)
    # spiral stairs (full turn per floor height) from the ground floor to the summit
    G.spiral_stairs(vol, 0, 0, 5, 96, CORE_R, 2.0, WALL, steps_per_turn=12, clockwise=True, a0=0.0)


class TowerFeature(Feature):
    layer = LAYER

    def __init__(self):
        super().__init__('tower_central', BBOX, LAYER, tower_feature_meta())

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        _bulk(vol)
        bk = _ops()
        vx1, vx2, vz1, vz2 = vol.x1, vol.x2, vol.z1, vol.z2
        s = vol.set
        for bx in range(vx1 >> 4, (vx2 >> 4) + 1):
            for bz in range(vz1 >> 4, (vz2 >> 4) + 1):
                for (x, y, z, b) in bk.get((bx, bz), ()):
                    if vx1 <= x <= vx2 and vz1 <= z <= vz2: s(x, y, z, b)


FEATURE = register(TowerFeature())
