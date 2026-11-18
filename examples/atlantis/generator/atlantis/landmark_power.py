"""Layer 50: District E, Power & Engineering, on the pier at plan angle 240 (r 310..490).

Axis frame (a, c): `a` = distance from the centre along the plan-240 pier axis, `c` = signed offset across it
(+ = clockwise side, PL.axis_coords).  Buildings are circular so they need no rotation.

Features (all layer 50; names sort so that the tunnels build before the buildings that dig stair shafts into them):
  power_conduit_*   flush light strips in the deck (cyan glass over sea lanterns between dark prismarine) running out
                    along the pier on both sides of the avenue, with branches to the hall, the workshop and the towers
                    (allow_overlap: thin diagonal strips whose x/z boxes touch the buildings' boxes)
  power_tower_a/b   two heat-exchange / coolant towers (flared body, cyan slits, lantern coolant column, spiral service stair to a roof deck)
  power_tunnel_*    the service tunnel under the deck: floor block y -5, free cells y -4..-1, ceiling block y 0, inside
                    the solid foundation, deck above. Along the avenue axis from r 312 to the hall, plus two branches
                    ending in landing rooms below the stair shafts (allow_overlap: they lie under other features)
  power_workshop    engineering workshop / substation (round hall, low glass dome), stairs down to the tunnel
  power_crystal_hall the Crystal Hall: round hall, glass dome, raised dais with one orichalcum crystal core, consoles,
                    stairs down
Vertical: deck top y 4 (walk 5).
"""
import math

from .feature import Feature, register
from . import planner as PL
from . import palette as P
from . import blocks as B
from . import geometry as G
from . import architecture as A
from . import roads as R

LAYER = 50
ANG = 240.0
UX, UZ = PL.from_polar(1.0, ANG)
VX, VZ = -UZ, UX
DECK = PL.DECK_TOP
WALK = PL.WALK_Y

QUARTZ, BRICKS, DIORITE = 'smooth_quartz', 'quartz_bricks', 'polished_diorite'
PILLAR = 'quartz_pillar[axis=y]'
GLASS, GLASS_C = 'light_blue_stained_glass', 'cyan_stained_glass'
PANE = 'light_blue_stained_glass_pane'
LAMP = P.LAMP
DARKP, PRISM_B, DEEP = P.DARK_PRISMARINE, P.PRISMARINE_BRICKS, P.DARK
COPPER, COPPER_CUT = P.COPPER, P.COPPER_CUT

# tunnel levels
T_FLOOR, T_LO, T_HI, T_CEIL = -5, -4, -1, 0


def xz(a, c):
    return a * UX + c * VX, a * UZ + c * VZ


def rxz(a, c):
    x, z = xz(a, c)
    return int(round(x)), int(round(z))


def ac(x, z):
    return PL.axis_coords(x, z, ANG)


def _bbox_of(points, pad, y1, y2):
    xs = [p[0] for p in points]; zs = [p[1] for p in points]
    return (int(math.floor(min(xs))) - pad, y1, int(math.floor(min(zs))) - pad,
            int(math.ceil(max(xs))) + pad, y2, int(math.ceil(max(zs))) + pad)


def _rect_corners(a0, a1, c0, c1):
    return [xz(a, c) for a in (a0, a1) for c in (c0, c1)]


# ============================================================================================ layout (all constants)
HALL_A, HALL_C = 395.0, 38.0
HX, HZ = rxz(HALL_A, HALL_C)
WS_A, WS_C = 340.0, -34.0
WX, WZ = rxz(WS_A, WS_C)
TOWER_A = 452.0
TOWERS = [('power_tower_a', rxz(TOWER_A, 32.0)), ('power_tower_b', rxz(TOWER_A, -32.0))]

HALL_RO, HALL_RI = 21.5, 19.5          # wall outer / inner radius
HALL_WALL_TOP = 15
HALL_DOME_H = 8
WS_RO, WS_RI = 16.5, 14.5
WS_WALL_TOP = 12
WS_DOME_H = 6

STAIR_N = 9                             # steps of a service stair (y -4 .. 4)
# stair shafts (xs, zs): landing x in [xs-3, xs], stairs at x = xs+1 .. xs+9 ascending east, z = zs-1..zs+1
HALL_SHAFT = (HX - 10, HZ + 13)
WS_SHAFT = (WX - 9, WZ + 8)


def _landing_box(sh):
    xs, zs = sh
    return (xs - 3, xs, zs - 1, zs + 1)


TUN_A0 = 312.0
_hall_land = ac(HALL_SHAFT[0] - 1.5, HALL_SHAFT[1])
_ws_land = ac(WS_SHAFT[0] - 1.5, WS_SHAFT[1])
TUN_A1 = math.ceil(max(_hall_land[0], _ws_land[0])) + 3.0
# tunnel interior as union of axis-frame rectangles (a0, a1, c0, c1) and world boxes (x0, x1, z0, z1)
TUN_RECTS = [
    (TUN_A0, TUN_A1, -1.6, 1.6),
    (_hall_land[0] - 1.6, _hall_land[0] + 1.6, 0.0, _hall_land[1]),
    (_ws_land[0] - 1.6, _ws_land[0] + 1.6, _ws_land[1], 0.0),
]
TUN_BOXES = [_landing_box(HALL_SHAFT), _landing_box(WS_SHAFT)]


# ============================================================================================ tunnel cell model
def tunnel_interior(x, z):
    for x0, x1, z0, z1 in TUN_BOXES:
        if x0 <= x <= x1 and z0 <= z <= z1: return True
    a, c = ac(x, z)
    for a0, a1, c0, c1 in TUN_RECTS:
        if a0 <= a <= a1 and c0 <= c <= c1: return True
    return False


def tunnel_kind(x, z):
    """'in' | 'wall' | None for column (x, z)."""
    if tunnel_interior(x, z): return 'in'
    for dx in (-1, 0, 1):
        for dz in (-1, 0, 1):
            if (dx or dz) and tunnel_interior(x + dx, z + dz): return 'wall'
    return None


def tunnel_pieces():
    """[(name, bbox, entrance_feet_cell)]: the tunnel Features."""
    out = []
    n = 4
    step = (TUN_A1 - TUN_A0) / n
    for i in range(n):
        a0, a1 = TUN_A0 + i * step, TUN_A0 + (i + 1) * step
        bb = _bbox_of(_rect_corners(a0 - 2, a1 + 2, -4.0, 4.0), 1, T_FLOOR, T_CEIL)
        mx, mz = rxz((a0 + a1) / 2, 0.0)
        out.append((f'power_tunnel_main_{i}', bb, (mx, T_LO, mz)))
    for nm, (a, cc), sh in (('hall', _hall_land, HALL_SHAFT), ('workshop', _ws_land, WS_SHAFT)):
        pts = _rect_corners(a - 3.5, a + 3.5, min(0.0, cc) - 3.0, max(0.0, cc) + 3.0)
        pts += [(sh[0] - 4, sh[1] - 2), (sh[0] + 1, sh[1] + 2)]
        out.append((f'power_tunnel_{nm}', _bbox_of(pts, 1, T_FLOOR, T_CEIL), (sh[0] - 2, T_LO, sh[1])))
    return out


def _tunnel_build(vol, bb):
    c = vol.clip(*bb)
    if not c: return
    memo = {}
    for z in range(c[2], c[5] + 1):
        for x in range(c[0], c[3] + 1):
            k = memo[(x, z)] = tunnel_kind(x, z)
            if k is None: continue
            if k == 'in':
                a, cc = ac(x, z)
                stripe = abs(cc) <= 0.55 and TUN_A0 <= a <= TUN_A1
                vol.set(x, T_FLOOR, z, QUARTZ if stripe else ('polished_deepslate' if (x + z) % 4 else 'deepslate_tiles'))
                vol.fill(x, T_LO, z, x, T_HI, z, 'air')
                vol.set(x, T_CEIL, z, DEEP)
            else:
                vol.set(x, T_FLOOR, z, DEEP)
                vol.set(x, -4, z, DARKP)
                vol.set(x, -3, z, LAMP if (2 * x + z) % 5 == 0 else GLASS_C)
                vol.set(x, -2, z, PRISM_B)
                vol.set(x, -1, z, PRISM_B)
                vol.set(x, T_CEIL, z, DEEP)


class TunnelPiece(Feature):
    def __init__(self, name, bbox, entrance):
        # the validator's walk model has no nodes below y 0, so no entrances/interior are declared; the tests walk it
        super().__init__(name, bbox, LAYER, {'district': 'E', 'kind': 'service_tunnel', 'allow_overlap': True})
        self.entrance = entrance

    def build(self, vol, ctx):
        _tunnel_build(vol, self.bbox)


# ============================================================================================ conduits
class Strip:
    """A flush light strip in the deck: rect in the axis frame; `along` 'a' (runs along a) or 'c'."""

    def __init__(self, a0, a1, c0, c1, along):
        self.a0, self.a1, self.c0, self.c1, self.along = a0, a1, c0, c1, along

    def cell(self, x, z):
        """None | 'core' | 'edge'."""
        a, c = ac(x, z)
        if self.along == 'a':
            if not (self.a0 <= a <= self.a1): return None
            d = abs(c - (self.c0 + self.c1) / 2)
        else:
            if not (self.c0 <= c <= self.c1): return None
            d = abs(a - (self.a0 + self.a1) / 2)
        if d <= 0.75: return 'core'
        return 'edge' if d <= 1.75 else None


def _strip_specs():
    out = []
    edges = [335.0, 380.0, 425.0, 470.0]
    for sgn, tag in ((1, 'p'), (-1, 'm')):
        for i in range(3):
            out.append((f'power_conduit_{tag}{i}', Strip(edges[i], edges[i + 1], 12.0 * sgn, 12.0 * sgn, 'a')))
    out.append(('power_conduit_hall', Strip(HALL_A, HALL_A, 12.0, HALL_C - HALL_RO, 'c')))
    out.append(('power_conduit_ta', Strip(TOWER_A, TOWER_A, 12.0, 32.0 - 8.0, 'c')))
    out.append(('power_conduit_tb', Strip(TOWER_A, TOWER_A, -32.0 + 8.0, -12.0, 'c')))
    out.append(('power_conduit_ws', Strip(WS_A, WS_A, WS_C + 16.0, -12.0, 'c')))
    return out


STRIPS = _strip_specs()


def conduit_cell(x, z):
    """'core' | 'edge' | None over all conduit strips (walkways keep them uncut)."""
    for _, s in STRIPS:
        k = s.cell(x, z)
        if k: return k
    return None


class ConduitFeature(Feature):
    def __init__(self, name, strip):
        pts = _rect_corners(strip.a0 - 2, strip.a1 + 2, strip.c0 - 2, strip.c1 + 2) if strip.along == 'a' \
            else _rect_corners(strip.a0 - 2, strip.a1 + 2, strip.c0, strip.c1)
        super().__init__(name, _bbox_of(pts, 1, DECK - 1, DECK), LAYER,
                         {'district': 'E', 'kind': 'conduit', 'allow_overlap': True})
        self.strip = strip

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        for z in range(c[2], c[5] + 1):
            for x in range(c[0], c[3] + 1):
                k = self.strip.cell(x, z)
                if not k or not PL.has_deck(x, z) or R.is_street(x, z): continue
                if k == 'core':
                    vol.set(x, DECK, z, GLASS_C)
                    vol.set(x, DECK - 1, z, LAMP)
                else:
                    vol.set(x, DECK, z, DARKP)


# ============================================================================================ helpers for buildings
def _sector(dx, dz, n):
    f = (math.atan2(dz, dx) % (2 * math.pi)) / (2 * math.pi) * n
    return f - math.floor(f)


def _walkway(x, z0, dz_sign):
    """5-wide approach from (x, z0) in direction dz_sign until a street column is reached: (cells, last z)."""
    cells = []
    z = z0
    for _ in range(60):
        if any(R.is_street(x + dx, z) for dx in range(-2, 3)): break
        cells.extend((x + dx, z) for dx in range(-2, 3))
        z += dz_sign
    return cells, z - dz_sign


def _paint_walkway(vol, cells, cx):
    for x, z in cells:
        if not PL.has_deck(x, z) or conduit_cell(x, z): continue
        vol.set(x, DECK, z, DIORITE if abs(x - cx) == 2 else QUARTZ)


def _stair_shaft(vol, sh):
    """Stairs from the landing room (floor y -5) up to the building floor (y 4), ascending east; pane railing at y 5
    around the shaft mouth; lanterns in the shaft walls."""
    xs, zs = sh
    for k in range(STAIR_N):
        x, ys = xs + 1 + k, T_LO + k
        for z in (zs - 1, zs, zs + 1):
            vol.fill(x, ys + 1, z, x, DECK + 2, z, 'air')
            vol.set(x, ys, z, B.stairs('smooth_quartz', 'east'))
            vol.set(x, ys - 1, z, QUARTZ if ys - 1 > T_FLOOR else DEEP)
    for z in (zs - 2, zs + 2):
        for x in range(xs, xs + STAIR_N + 1):
            vol.fill(x, T_CEIL if tunnel_interior(x, z) else T_FLOOR, z, x, DECK, z, QUARTZ)   # keep the corridor open
    for k in (0, 3, 6):
        for z in (zs - 2, zs + 2):
            vol.set(xs + 1 + k, T_LO + k + 2, z, LAMP)
    cells = ({(x, zs - 2) for x in range(xs, xs + STAIR_N + 1)} | {(x, zs + 2) for x in range(xs, xs + STAIR_N + 1)}
             | {(xs, z) for z in (zs - 1, zs, zs + 1)})
    for (x, z) in cells:
        vol.set(x, WALK, z, B.pane(PANE, north=(x, z - 1) in cells, east=(x + 1, z) in cells,
                                   south=(x, z + 1) in cells, west=(x - 1, z) in cells))


def _wrap_ramp(vol, cx, cz, r_in, r_out, s_top, th0, dth):
    """Half-block ramp hugging a round building, rising clockwise from the deck (surface 5) to surface `s_top` over
    `dth` degrees starting at angle th0 (0 = east, 90 = south).  Solid underneath; lanterns in the outer edge."""
    n = 2 * (s_top - WALK)
    for x, z in G.ring_cells(cx, cz, r_in, r_out, (vol.x1, vol.z1, vol.x2, vol.z2)):
        t = ((math.degrees(math.atan2(z - cz, x - cx)) - th0) % 360.0) / dth
        if t >= 1.0: continue
        j = int(t * (n + 1))
        y = DECK + (j + 1) // 2 if j % 2 else DECK + j // 2
        if y > DECK: vol.fill(x, DECK, z, x, y - 1, z, QUARTZ)
        if j % 2: vol.set(x, y, z, B.slab(QUARTZ, 'bottom'))
        elif j and j % 4 == 0 and math.hypot(x - cx, z - cz) > r_out - 1.2: vol.set(x, y, z, LAMP)
        else: vol.set(x, y, z, QUARTZ)


def _ground_lamps(vol, cx, cz, r, angles):
    """Sea-lantern tiles flush in the deck on a circle of radius r at the given angles (degrees, 0 = east, 90 = south):
    the walls of a building shade the deck lights that used to stand there."""
    for g in angles:
        x = cx + int(round(r * math.cos(math.radians(g)))); z = cz + int(round(r * math.sin(math.radians(g))))
        if PL.has_deck(x, z) and not R.is_street(x, z, 1) and not conduit_cell(x, z):
            vol.set(x, DECK, z, LAMP)


def _door(vol, cx, cz, r_in, r_out, sgn):
    """Frame the doorway (z side sgn): pillars and a lintel (the wall callable already left the opening free)."""
    for dz in range(int(r_in), int(r_out) + 1):
        z = cz + sgn * dz
        for y in range(WALK, 10):
            vol.set(cx - 3, y, z, PILLAR); vol.set(cx + 3, y, z, PILLAR)
        for dx in range(-3, 4):
            vol.set(cx + dx, 10, z, BRICKS)


def _wall_fn(cx, cz, n_sec, win_lo, win_hi, top, door_sgn):
    def fn(x, y, z):
        dx, dz = x - cx, z - cz
        if dz * door_sgn > 0 and abs(dx) <= 2 and y <= WALK + 3 and (abs(dx) < 2 or y <= WALK + 2):
            return None
        if y <= WALK + 1: return DEEP
        if y == top: return BRICKS
        f = _sector(dx, dz, n_sec)
        if f < 0.18 or f > 0.82: return PILLAR
        if win_lo <= y <= win_hi: return GLASS
        return QUARTZ
    return fn


def _dome_fn(cx, cz, n_sec, y_base, apex):
    def fn(x, y, z):
        dx, dz = x - cx, z - cz
        if dx * dx + dz * dz <= 5 and y >= apex - 1: return LAMP
        f = _sector(dx, dz, n_sec)
        if f < 0.14 or f > 0.86 or y == y_base: return LAMP if (y % 4 == 0 and y != y_base) else QUARTZ
        return GLASS
    return fn


def _floor_fn(cx, cz, rings, sectors=16):
    """Floor top block (y 4): pale quartz / diorite sectors with cyan light rings at `rings` radii."""
    mid = (rings[0] + rings[-1]) / 2

    def fn(x, y, z):
        d = math.hypot(x - cx, z - cz)
        for r in rings:
            if r - 0.5 < d <= r + 0.5: return GLASS_C
        if abs(d - mid) < 0.5: return 'cyan_concrete'
        return DIORITE if int(_sector(x - cx, z - cz, sectors) * 2) % 2 == 0 else QUARTZ
    return fn


def _ring_lamps(cx, cz, rings):
    def fn(x, y, z):
        d = math.hypot(x - cx, z - cz)
        for r in rings:
            if r - 0.5 < d <= r + 0.5: return LAMP
        return None
    return fn


# ============================================================================================ Crystal hall
HALL_WALK, HALL_WALK_END = _walkway(HX, HZ + 22, 1)
HALL_RINGS = (10.0, 16.0)
CORE_H = 12                                          # height of the crystal core above the dais
HALL_CONSOLES = [(HX - 18, HZ, 'east'), (HX + 18, HZ, 'west'), (HX, HZ - 18, 'south')]


class CrystalHall(Feature):
    def __init__(self):
        bb = (HX - 25, T_FLOOR, HZ - 25, HX + 25, HALL_WALL_TOP + HALL_DOME_H + 1, max(HALL_WALK_END, HZ + 25) + 1)
        super().__init__('power_crystal_hall', bb, LAYER, {
            'district': 'E', 'kind': 'crystal_hall', 'entrances': [(HX, WALK, HZ + 21)], 'interior': True,
            'interior_box': (HX - 19, WALK, HZ - 19, HX + 19, HALL_WALL_TOP, HZ + 19),
            'roof_y': HALL_WALL_TOP + HALL_DOME_H})

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        cx, cz = HX, HZ
        G.disc(vol, cx, cz, HALL_RO, DECK, _floor_fn(cx, cz, HALL_RINGS))
        G.disc(vol, cx, cz, HALL_RO, DECK - 1, _ring_lamps(cx, cz, HALL_RINGS))
        G.ring(vol, cx, cz, HALL_RI, HALL_RO, WALK, _wall_fn(cx, cz, 24, 8, 13, HALL_WALL_TOP, 1), y2=HALL_WALL_TOP)
        yb = HALL_WALL_TOP + 1
        G.dome_cap(vol, cx, cz, yb, HALL_RO, HALL_DOME_H, 2, _dome_fn(cx, cz, 24, yb, yb + HALL_DOME_H))
        _door(vol, cx, cz, HALL_RI, HALL_RO, 1)
        _wrap_ramp(vol, cx, cz, HALL_RO, HALL_RO + 2.5, HALL_WALL_TOP + 2, 130.0, 180.0)
        _ground_lamps(vol, cx, cz, HALL_RO + 4.0, range(305, 400, 8))

        def dais(x, y, z):
            d = math.hypot(x - cx, z - cz)
            if d > 7.5: return B.slab(QUARTZ, 'bottom')
            return GLASS_C if 5.5 < d <= 6.5 else QUARTZ
        G.disc(vol, cx, cz, 9.5, WALK, dais)
        G.disc(vol, cx, cz, 1.2, WALK, 'chiseled_quartz_block')
        # the orichalcum core: one tall crystal (sea lanterns and amethyst) in a glass sleeve on a dark plinth
        G.disc(vol, cx, cz, 2.5, WALK + 1, DEEP)
        for y in range(WALK + 2, WALK + 2 + CORE_H):
            vol.set(cx, y, cz, LAMP if y % 3 else 'amethyst_block')
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                vol.set(cx + dx, y, cz + dz, GLASS)
            for dx, dz in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                if y % 4 == 0: vol.set(cx + dx, y, cz + dz, DARKP)
        top = WALK + 2 + CORE_H
        vol.fill(cx - 1, top, cz - 1, cx + 1, top, cz + 1, DEEP)
        vol.set(cx, top + 1, cz, 'end_rod[facing=up]')
        for x, z, f in HALL_CONSOLES:
            A.console(vol, x, DECK, z, f, width=3)
        _stair_shaft(vol, HALL_SHAFT)
        _paint_walkway(vol, HALL_WALK, HX)


# ============================================================================================ workshop / substation
WS_WALK, WS_WALK_END = _walkway(WX, WZ - 17, -1)
WS_CONSOLES = [(WX - 14, WZ, 'east'), (WX + 14, WZ, 'west'), (WX + 6, WZ + 14, 'north')]
WS_BENCHES = [(WX + 9, WZ - 2), (WX - 9, WZ - 3), (WX - 6, WZ - 8), (WX + 6, WZ - 8), (WX + 8, WZ + 4)]
BENCH_BLOCKS = ['smithing_table', 'anvil[facing=north]', 'grindstone[face=floor,facing=east]', 'fletching_table',
                'cartography_table']


class Workshop(Feature):
    def __init__(self):
        bb = (WX - 20, T_FLOOR, WS_WALK_END - 1, WX + 20, WS_WALL_TOP + WS_DOME_H + 1, WZ + 20)
        super().__init__('power_workshop', bb, LAYER, {
            'district': 'E', 'kind': 'substation', 'entrances': [(WX, WALK, WZ - 16)], 'interior': True,
            'interior_box': (WX - 14, WALK, WZ - 14, WX + 14, WS_WALL_TOP, WZ + 14),
            'roof_y': WS_WALL_TOP + WS_DOME_H})

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        cx, cz = WX, WZ
        rings = (6.0, 11.0)
        G.disc(vol, cx, cz, WS_RO, DECK, _floor_fn(cx, cz, rings, 12))
        G.disc(vol, cx, cz, WS_RO, DECK - 1, _ring_lamps(cx, cz, rings))
        G.ring(vol, cx, cz, WS_RI, WS_RO, WALK, _wall_fn(cx, cz, 20, 8, 11, WS_WALL_TOP, -1), y2=WS_WALL_TOP)
        yb = WS_WALL_TOP + 1
        G.dome_cap(vol, cx, cz, yb, WS_RO, WS_DOME_H, 2, _dome_fn(cx, cz, 20, yb, yb + WS_DOME_H))
        _door(vol, cx, cz, WS_RI, WS_RO, -1)
        _wrap_ramp(vol, cx, cz, WS_RO, WS_RO + 2.5, WS_WALL_TOP + 2, 0.0, 180.0)
        _ground_lamps(vol, cx, cz, WS_RO + 4.0, [g for g in range(186, 360, 10) if not 245 <= g <= 295])
        # central transformer: copper column with glass windows over a lantern spine
        G.disc(vol, cx, cz, 3.6, WALK, DEEP)
        G.disc(vol, cx, cz, 2.6, WALK + 1, COPPER, y2=WS_WALL_TOP - 1)
        for y in range(WALK + 2, WS_WALL_TOP - 2):
            vol.fill(cx - 1, y, cz, cx + 1, y, cz, LAMP)
            vol.fill(cx, y, cz - 1, cx, y, cz + 1, LAMP)
            for dx, dz in ((2, 0), (-2, 0), (0, 2), (0, -2)):
                vol.set(cx + dx, y, cz + dz, GLASS_C)
        G.disc(vol, cx, cz, 2.6, WS_WALL_TOP - 1, COPPER_CUT)
        vol.set(cx, WS_WALL_TOP, cz, 'end_rod[facing=up]')
        for i, (bx, bz) in enumerate(WS_BENCHES):        # workbenches with a lit iron rack
            vol.set(bx, WALK, bz, BENCH_BLOCKS[i])
            vol.set(bx, WALK, bz + 1, BENCH_BLOCKS[(i + 1) % len(BENCH_BLOCKS)])
            vol.set(bx, WALK, bz - 1, P.METAL)
            vol.set(bx, WALK + 1, bz - 1, 'end_rod[facing=up]')
        for x, z, f in WS_CONSOLES:
            A.console(vol, x, DECK, z, f, width=3)
        _stair_shaft(vol, WS_SHAFT)
        _paint_walkway(vol, WS_WALK, WX)


# ============================================================================================ coolant towers
TW_TOP = 26
TW_DOORS = {'power_tower_a': 1, 'power_tower_b': -1}          # door side in z (A faces south, B north)


def _tw_r(y):
    return 5.0 + 4.0 * (max(0.0, y - WALK) / float(TW_TOP - WALK)) ** 1.8      # a flaring chalice: no setback ledges


class CoolantTower(Feature):
    """Flared cooling tower: quartz/copper body with cyan glass slits, a lantern 'coolant' column in the
    middle and a spiral service stair up to the roof deck (so the roof is reachable); one door at the base."""

    def __init__(self, name, pos):
        self.pos = pos
        x, z = pos
        self.sgn = TW_DOORS[name]
        self.walk, wend = _walkway(x, z + self.sgn * 11, self.sgn)
        z1, z2 = (z - 10, max(wend, z + 10) + 1) if self.sgn > 0 else (min(wend, z - 10) - 1, z + 10)
        super().__init__(name, (x - 11, DECK, z1, x + 11, TW_TOP + 5, z2), LAYER, {
            'district': 'E', 'kind': 'coolant_tower', 'entrances': [(x, WALK, z + self.sgn * 6)], 'interior': True,
            'interior_box': (x - 6, WALK, z - 6, x + 6, TW_TOP, z + 6), 'roof_y': TW_TOP + 8})

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        cx, cz = self.pos
        sgn = self.sgn

        def shell(x, y, z):
            if y % 7 == 0: return COPPER_CUT
            f = _sector(x - cx, z - cz, 12)
            if 0.4 < f < 0.6 and y % 7 not in (6, 1) and y > WALK + 2: return GLASS_C     # full-depth cyan slits
            return QUARTZ
        G.ring(vol, cx, cz, 0, _tw_r(WALK) + 1.5, DECK, DEEP)
        G.tower(vol, cx, cz, WALK, TW_TOP, _tw_r, shell)
        G.ring(vol, cx, cz, _tw_r(TW_TOP) - 1.0, _tw_r(TW_TOP), TW_TOP, LAMP)
        # interior: lantern coolant column, spiral service stair, roof hatch
        G.disc(vol, cx, cz, 3.8, WALK, 'air', y2=TW_TOP - 1)
        G.disc(vol, cx, cz, 3.4, TW_TOP, 'air')
        G.cylinder(vol, cx, cz, 1.2, WALK, TW_TOP - 1, LAMP)
        a0 = 80.0 if sgn > 0 else 260.0
        G.spiral_stairs(vol, cx, cz, WALK, TW_TOP + 1, 3.3, 1.4, QUARTZ, steps_per_turn=14, clockwise=True, a0=a0)
        # door through the shell
        for dx in (-1, 0, 1):
            for y in range(WALK, WALK + 3):
                for k in range(4, 13):
                    vol.set(cx + dx, y, cz + sgn * k, 'air')
        for dx in (-2, 2):
            for y in range(WALK, WALK + 3):
                for k in range(6, 11):
                    vol.set(cx + dx, y, cz + sgn * k, DARKP)
        # roof deck pylons
        for g in range(0, 360, 45):
            px = cx + int(round(7.6 * math.cos(math.radians(g)))); pz = cz + int(round(7.6 * math.sin(math.radians(g))))
            vol.fill(px, TW_TOP + 1, pz, px, TW_TOP + 3, pz, PILLAR)
            vol.set(px, TW_TOP + 4, pz, 'end_rod[facing=up]')
        _ground_lamps(vol, cx, cz, 12.0, [g for g in range(0, 360, 30) if abs((g - (90 if sgn > 0 else 270) + 180) % 360 - 180) > 50])
        _paint_walkway(vol, self.walk, cx)


# ============================================================================================ registration
def make_features():
    feats = [CrystalHall(), Workshop()]
    for nm, pos in TOWERS: feats.append(CoolantTower(nm, pos))
    for nm, bb, ent in tunnel_pieces(): feats.append(TunnelPiece(nm, bb, ent))
    for nm, s in STRIPS: feats.append(ConduitFeature(nm, s))
    return feats


FEATURES = make_features()
register(*FEATURES)
