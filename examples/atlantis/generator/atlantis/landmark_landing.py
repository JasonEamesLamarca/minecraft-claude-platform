"""Layer 50: District G, Transportation & Landing, plus the small landing pads at the other pier tips.

G (pier at plan angle 0 = north; there x = across (east +), z = -along):
  * 4 elevated landing pads (flared Atlantean stems, glass parapets) EAST of the grand avenue at along 350/395/440/485,
    each reached by a 3-wide quartz staircase that starts beside the avenue (x 10) and climbs east to the pad,
  * a skiff hangar WEST of the avenue (along 404..438): enterable, lit, barrel roof, three parked manta skiffs
    (flat wide-winged gliders with a dark-glass canopy) and consoles,
  * a flight-control tower WEST of the avenue (along ~345): lobby with consoles, stair to a glazed control cab,
  * two pier-tip pads at along 531 (x = -21 and +21), the west one with a parked skiff.
Other piers (plan angles 60..300): one low pad (radius 7, +2 blocks above the deck) at along 506, 21 blocks clockwise
of the avenue axis, reached by a short ramp from the avenue edge; only r 495..540 is used.

Every pad has lit edge markers and floor inlays; the pads declare meta['entrances'] at the top of their stair/ramp.
All footprints stay clear of the avenue (|across| <= 8.5), the r 520 promenade ring (517.5..522.5), the root plaza
and the avenue lamp posts (|across| 7.3 / tip pylons at along 538).
"""
import math

from . import geometry as G, blocks as B, palette as P, planner as PL
from .feature import Feature, register
from .util import hash_str
import random

LAYER = 50
DECK = PL.DECK_TOP                                  # 4

WHITE, BLOCK, BRICKS, PALE = P.WHITE, P.WHITE_BLOCK, P.WHITE_BRICKS, P.PALE
GREY, PGREY = P.GREY, P.PALE_GREY
CYAN, LBLUE = P.CYAN, P.LIGHT_BLUE
LAMP = P.LAMP
GLASS, PANE = P.GLASS_BLUE, P.GLASS_BLUE_PANE
DARKGLASS = 'black_stained_glass'
PILLAR = 'quartz_pillar[axis=y]'
DARK = P.DARK
AIR = 'air'

# ---- G layout (world = local coordinates, x east, z south)
ELEV_ALONG = (350, 395, 440, 485)
ELEV_TOP = 13                                       # top block y of the elevated pads (walk y 14)
ELEV_R = 9.0
ELEV_CX = 28                                        # pad centre x: stairs x 10..18, rim from x 19
STAIR_X0 = 10
HANGAR = (-47, -438, -17, -404)                     # x1, z1, x2, z2 (outer)
HANGAR_DOOR_Z = (-427, -415)
TOWER_C = (-24, -345)
TIP_ALONG = 531
TIP_X = 19
TIP_R = 7.0
TIP_TOP = 6                                         # top block of the low pads
PIER_PAD_ALONG = 506
PIER_PAD_ACROSS = 21
PIER_PAD_R = 7.0


def rng(ctx, name):
    return random.Random(hash_str(ctx.seed, name))


def world(plan, along, across):
    ux, uz = PL.from_polar(1.0, plan)
    return along * ux - across * uz, along * uz + across * ux


def _ang(dx, dz):
    return math.degrees(math.atan2(dz, dx)) % 360.0


def _near_angle(ang, step, d, off=0.0, width=0.55):
    """True if the cell at angle `ang`, distance d from a centre, lies on a marker of a `step`-degree series."""
    k = round((ang - off) / step) * step + off
    return abs(ang - k) <= math.degrees(width / max(d, 1.0))


def _clipbox(vol, bbox):
    return vol.clip(*bbox)


# ------------------------------------------------------------------------------------------------------------
# pads
# ------------------------------------------------------------------------------------------------------------
def pad_surface(vol, cx, cz, r, ytop, parapet=False, gap=None, lamp_ring=4.8):
    """Circular pad: two solid layers (ytop - 1, ytop), patterned top with lit edge markers and floor inlays.
    gap(x, z) -> True leaves the parapet open there (the stair side)."""
    clip = vol.clip(math.floor(cx - r - 1), ytop - 1, math.floor(cz - r - 1), math.ceil(cx + r + 1), ytop + 1,
                    math.ceil(cz + r + 1))
    if not clip: return
    for x, z in G.disc_cells(cx, cz, r, (clip[0], clip[2], clip[3], clip[5])):
        dx, dz = x - cx, z - cz
        d = math.hypot(dx, dz)
        a = _ang(dx, dz)
        vol.set(x, ytop - 1, z, BLOCK)
        if d > r - 1.0:
            top = LAMP if _near_angle(a, 22.5, d) else CYAN
        elif d > r - 2.0:
            top = WHITE
        elif r - 3.5 < d <= r - 2.5:
            top = CYAN
        elif d <= 1.8:
            top = LBLUE
        elif abs(d - lamp_ring) < 0.7 and _near_angle(a, 45.0, d, 22.5):
            top = LAMP
        else:
            top = PGREY if (x + z) % 2 == 0 else GREY
        vol.set(x, ytop, z, top)
        if parapet and d > r - 1.0 and not (gap and gap(x, z)):
            vol.set(x, ytop + 1, z, GLASS)


def elevated_pad(vol, cx, cz, ytop=ELEV_TOP, r=ELEV_R):
    """Flared stem (a goblet of quartz), glowing underside ring, pad with glass parapet. Stair opening on the west."""
    h = max(1, ytop - 7)
    G.tower(vol, cx, cz, DECK + 1, ytop - 2, lambda y: 2.5 + 6.0 * (max(0, y - 5) / h) ** 2, BLOCK)
    G.disc(vol, cx, cz, 3.6, DECK + 1, DARK)
    G.ring(vol, cx, cz, 3.1, 3.6, DECK + 2, CYAN)
    clip = vol.clip(cx - 10, ytop - 2, cz - 10, cx + 10, ytop - 2, cz + 10)
    if clip:
        for x, z in G.ring_cells(cx, cz, r - 2.5, r - 1.0, (clip[0], clip[2], clip[3], clip[5])):
            if _near_angle(_ang(x - cx, z - cz), 30.0, math.hypot(x - cx, z - cz)):
                vol.set(x, ytop - 2, z, LAMP)
    pad_surface(vol, cx, cz, r, ytop, parapet=True,
                gap=lambda x, z: x < cx - r + 3 and abs(z - cz) <= 2)
    # mooring dishes: four low pylons on the diagonals
    for k in range(4):
        a = math.radians(45 + 90 * k)
        px, pz = cx + round(6.2 * math.cos(a)), cz + round(6.2 * math.sin(a))
        for yy in (ytop + 1, ytop + 2):
            vol.set(px, yy, pz, PILLAR)
        vol.set(px, ytop + 3, pz, P.ROD)


def stair_to_pad(vol, cz, ytop=ELEV_TOP, x0=STAIR_X0):
    rise = ytop - DECK
    G.stairs_run(vol, x0, DECK + 1, cz, 'east', rise, width=3, block=BLOCK, fill_to=DECK + 1)
    for i in range(1, rise, 3):                      # lit curbs
        for dz in (-2, 2):
            vol.set(x0 + i, DECK + 1 + i, cz + dz, LAMP)
            vol.set(x0 + i + 1, DECK + 1 + i, cz + dz, GLASS)


# ------------------------------------------------------------------------------------------------------------
# Manta skiff (block model): a flat, wide-winged glider, original to this project
# ------------------------------------------------------------------------------------------------------------
_WING = (0, 1, 1, 2, 2, 3, 3, 3, 2, 2)              # half-width of the wing at l = 0..9 from the nose
SKIFF_LEN = 10
SKIFF_REACH = 12                                    # cells behind the nose incl. the boarding step


def skiff(vol, nx, nz, y_s, nose='west'):
    """Parked skiff: nose tip at column (nx, nz), resting on struts on floor block y_s.  A flat wing (one block
    thick, up to 7 wide) with a raised spine (|w| <= 1, two blocks), a dark-glass canopy over l 2..4, tail fins
    at l 8..9 and a boarding step behind the tail."""
    n = B.DIRS[nose]
    rv = (-n[0], -n[1])                             # towards the rear
    sv = (-n[1], n[0])
    yb = y_s + 2                                    # wing level

    def at(l, w): return nx + l * rv[0] + w * sv[0], nz + l * rv[1] + w * sv[1]

    if not vol.intersects(*_skiff_box(nx, nz, y_s, nose)): return
    for l in range(SKIFF_LEN):
        hw = _WING[l]
        for w in range(-hw, hw + 1):
            x, z = at(l, w)
            edge = abs(w) == hw and hw > 1
            vol.set(x, yb, z, LBLUE if edge and l % 2 else P.WHITE_CONCRETE if abs(w) <= 1 else GREY)
            if abs(w) <= 1 and 1 <= l <= 8:
                canopy = (l == 2 and w == 0) or (l in (3, 4))
                vol.set(x, yb + 1, z, DARKGLASS if canopy else P.WHITE_CONCRETE if w == 0 else P.GREY)
    x, z = at(9, 0); vol.set(x, yb + 1, z, CYAN)    # engine glow
    for l in (8, 9):                                # tail fins
        for w in (-2, 2):
            x, z = at(l, w); vol.set(x, yb + 1, z, P.CYAN_TERRACOTTA)
    for l in (2, 7):                                # struts
        for w in (-1, 1):
            x, z = at(l, w); vol.set(x, y_s + 1, z, DARK)
    x, z = at(6, 0); vol.set(x, y_s + 1, z, LAMP)   # belly light
    for w in (-1, 0, 1):                            # boarding step behind the tail
        x, z = at(10, w); vol.set(x, y_s + 1, z, B.stairs(WHITE, nose))


def _skiff_box(nx, nz, y_s, nose):
    n = B.DIRS[nose]
    ex, ez = nx - n[0] * 11, nz - n[1] * 11
    return (min(nx, ex) - 4, y_s + 1, min(nz, ez) - 4, max(nx, ex) + 4, y_s + 8, max(nz, ez) + 4)


# ------------------------------------------------------------------------------------------------------------
# features
# ------------------------------------------------------------------------------------------------------------
class ElevatedPad(Feature):
    def __init__(self, k, along):
        cz = -along
        self.cz = cz
        bbox = (STAIR_X0 - 1, DECK, cz - 10, ELEV_CX + 10, ELEV_TOP + 4, cz + 10)
        super().__init__(f'landing_g_elevated_pad_{k}', bbox, LAYER,
                         {'district': 'G', 'kind': 'landing_pad', 'elevated': True,
                          'entrances': [(STAIR_X0 + (ELEV_TOP - DECK) - 1, ELEV_TOP + 1, cz)]})

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        elevated_pad(vol, ELEV_CX, self.cz)
        stair_to_pad(vol, self.cz)
        for dz in (-9, 9): vol.set(ELEV_CX - 10, DECK + 1, self.cz + dz, P.HIDDEN_LIGHT)


class TipPad(Feature):
    """G pier-tip pad at x = +-TIP_X; the west one carries a parked skiff."""

    def __init__(self, side):
        self.side = side
        self.cx = side * TIP_X
        self.cz = -TIP_ALONG
        cz = self.cz
        x_in = 9 if side > 0 else -TIP_X - 8
        x_out = TIP_X + 8 if side > 0 else -9
        self.skiff = side < 0
        super().__init__('landing_g_tip_pad_' + ('east' if side > 0 else 'west'),
                         (x_in, DECK, cz - 8, x_out, 13 if self.skiff else DECK + 5, cz + 8), LAYER,
                         {'district': 'G', 'kind': 'landing_pad', 'tip': True,
                          'entrances': [(side * (STAIR_X0 + 1), TIP_TOP + 1, cz)]})

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        s = self.side
        pad_surface(vol, self.cx, self.cz, TIP_R, TIP_TOP)
        # stairs from the avenue side: treads at |x| = 10, 11 (y 5, 6); the pad rim starts at |x| = 12
        if s > 0:
            G.stairs_run(vol, STAIR_X0, DECK + 1, self.cz, 'east', 2, width=3, block=BLOCK, fill_to=DECK + 1)
        else:
            G.stairs_run(vol, -STAIR_X0, DECK + 1, self.cz, 'west', 2, width=3, block=BLOCK, fill_to=DECK + 1)
        vol.set(s * 10, TIP_TOP, self.cz - 2, LAMP)
        vol.set(s * 10, TIP_TOP, self.cz + 2, LAMP)
        if self.skiff:
            skiff(vol, self.cx, self.cz - 5, TIP_TOP, 'north')


class PierPad(Feature):
    """Small pad at the tip of a non-G pier: radius 7, +2 above the deck, short ramp from the avenue edge."""

    def __init__(self, plan):
        self.plan = plan
        cx, cz = world(plan, PIER_PAD_ALONG, PIER_PAD_ACROSS)
        self.cx, self.cz = round(cx), round(cz)
        self.ramp = (world(plan, PIER_PAD_ALONG, 10.0), world(plan, PIER_PAD_ALONG, 14.2))
        pts = [(self.cx - 8, self.cz - 8), (self.cx + 8, self.cz + 8)] + [(round(p[0]) - 3, round(p[1]) - 3) for p in self.ramp] \
            + [(round(p[0]) + 3, round(p[1]) + 3) for p in self.ramp]
        bbox = (min(p[0] for p in pts), DECK, min(p[1] for p in pts), max(p[0] for p in pts), TIP_TOP + 3,
                max(p[1] for p in pts))
        e = world(plan, PIER_PAD_ALONG, 15.5)
        self.entrance = (round(e[0]), TIP_TOP + 1, round(e[1]))
        super().__init__(f'landing_pier_{plan:03d}_tip_pad', bbox, LAYER,
                         {'district': {0: 'G', 60: 'D', 120: 'C1', 180: 'C2', 240: 'E', 300: 'C3'}[plan],
                          'kind': 'landing_pad', 'tip': True, 'entrances': [self.entrance]})

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        pad_surface(vol, self.cx, self.cz, PIER_PAD_R, TIP_TOP)
        (x0, z0), (x1, z1) = self.ramp
        G.ramp(vol, x0, z0, x1, z1, DECK, TIP_TOP, 3, BLOCK, thickness=2, fill_to=DECK + 1)


# ---- hangar ------------------------------------------------------------------------------------------------
HX1, HZ1, HX2, HZ2 = HANGAR
HCX = (HX1 + HX2) // 2                              # -32
HHALF = (HX2 - HX1) / 2.0                           # 15
WALL_TOP = 11
SKIFF_Z = (-411, -421, -431)
SKIFF_NOSE_X = -41


def _roof_y(x):
    u = (x - HCX) / HHALF
    return WALL_TOP + 1 + int(math.floor(5 * math.sqrt(max(0.0, 1 - u * u)) + 1e-9))


def _toward(x):
    return x + (1 if x < HCX else -1 if x > HCX else 0)


class Hangar(Feature):
    def __init__(self):
        super().__init__('landing_g_skiff_hangar', (HX1, DECK, HZ1 - 2, HX2 + 2, 18, HZ2 + 2), LAYER,
                         {'district': 'G', 'kind': 'hangar', 'interior': True,
                          'entrances': [(HX2, DECK + 1, (HANGAR_DOOR_Z[0] + HANGAR_DOOR_Z[1]) // 2)],
                          'interior_box': (HX1 + 1, DECK + 1, HZ1 + 1, HX2 - 1, WALL_TOP, HZ2 - 1),
                          'roof_y': WALL_TOP + 1, 'islands_ok': True})

    def _wall(self, vol, x, z, long_wall):
        dlo, dhi = HANGAR_DOOR_Z
        door = x == HX2 and dlo <= z <= dhi
        corner = x in (HX1, HX2) and z in (HZ1, HZ2)
        k = (z - HZ1) if long_wall else (x - HX1)
        pillar = corner or k % 6 == 0 or (x == HX2 and z in (dlo - 1, dhi + 1))
        for y in range(DECK + 1, WALL_TOP + 1):
            if door:
                if y == WALL_TOP: vol.set(x, y, z, CYAN)
                else: vol.set(x, y, z, AIR)
            elif pillar:
                vol.set(x, y, z, PILLAR)
            elif y == DECK + 1:
                vol.set(x, y, z, DARK)
            elif y in (8, 9, 10):
                vol.set(x, y, z, B.pane(PANE, north=long_wall, south=long_wall, east=not long_wall,
                                        west=not long_wall))
            elif y == WALL_TOP:
                vol.set(x, y, z, CYAN)
            else:
                vol.set(x, y, z, WHITE)

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        for x in range(max(c[0], HX1), min(c[3], HX2) + 1):
            for z in range(max(c[2], HZ1), min(c[5], HZ2) + 1):
                # floor
                dx, dz = x - HCX, z - HZ1
                if (x - HX1) % 7 == 3 and (z - HZ1) % 7 == 3: fl = LAMP
                elif x == HX2 or x == HX1 or z in (HZ1, HZ2): fl = DARK
                else: fl = PGREY if (x + z) % 2 == 0 else GREY
                if HANGAR_DOOR_Z[0] <= z <= HANGAR_DOOR_Z[1] and x >= HX2 - 1: fl = CYAN if x == HX2 else fl
                vol.set(x, DECK, z, fl)
                on_long = x in (HX1, HX2)
                on_end = z in (HZ1, HZ2)
                yc = _roof_y(x)
                if on_long or on_end:
                    self._wall(vol, x, z, on_long and not on_end)
                    if on_end and not on_long:
                        for y in range(WALL_TOP + 1, yc):
                            vol.set(x, y, z, WHITE)
                # roof shell: from the inner surface up to the neighbour's inner surface (smooth outer step)
                top = _roof_y(_toward(x))
                lo = yc
                u = abs(x - HCX) / HHALF
                for y in range(lo, max(lo, top) + 1):
                    if u < 0.14: b = CYAN_GLASS
                    elif (z - HZ1) % 6 == 0 or on_end: b = BRICKS
                    elif y == lo and 0.5 < u < 0.62 and (z - HZ1) % 6 == 3: b = LAMP
                    elif y == max(lo, top) and (x - HX1) % 5 == 2 and (z - HZ1) % 5 == 2: b = LAMP
                    else: b = WHITE
                    vol.set(x, y, z, b)
        for lz in (HZ1 - 2, HZ2 + 2):               # the deck's hidden lamp lattice is cut by the hangar
            vol.set(-38, DECK + 1, lz, P.HIDDEN_LIGHT)
        # roof access stair along the outside of the east wall (the roof is walkable and lit)
        G.stairs_run(vol, HX2 + 1, DECK + 1, HZ2 - 1, 'north', 9, width=1, block=BLOCK, fill_to=DECK + 1)
        for i in (0, 4, 8): vol.set(HX2 + 2, DECK + 1 + i, HZ2 - 1 - i, LAMP)
        # bay markings + parked skiffs + consoles
        if vol.intersects(HX1, DECK, HZ1, HX2, 16, HZ2):
            for zc in SKIFF_Z:
                G.box(vol, SKIFF_NOSE_X - 1, DECK, zc - 3, SKIFF_NOSE_X + 13, DECK, zc - 3, CYAN)
                G.box(vol, SKIFF_NOSE_X - 1, DECK, zc + 3, SKIFF_NOSE_X + 13, DECK, zc + 3, CYAN)
                skiff(vol, SKIFF_NOSE_X, zc, DECK, 'west')
            from . import architecture as A
            for zc in (-416, -426):
                A.console(vol, HX1 + 1, DECK, zc, 'east', width=3)
            for zc in (-409, -433):
                A.console(vol, HX1 + 1, DECK, zc, 'east', width=1)


CYAN_GLASS = P.GLASS_CYAN


# ---- control tower -----------------------------------------------------------------------------------------
TX, TZ = TOWER_C
CAB_Y = 11                                          # cab floor slab (walk y 12)
CAB_TOP = 16                                        # cab ceiling


class ControlTower(Feature):
    def __init__(self):
        super().__init__('landing_g_control_tower', (TX - 8, DECK, TZ - 8, TX + 8, CAB_TOP + 13, TZ + 8), LAYER,
                         {'district': 'G', 'kind': 'control_tower', 'interior': True,
                          'entrances': [(TX + 7, DECK + 1, TZ)],
                          'interior_box': (TX - 6, DECK + 1, TZ - 6, TX + 6, CAB_TOP - 1, TZ + 6),
                          'roof_y': CAB_TOP, 'islands_ok': True})

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        clip2 = (c[0], c[2], c[3], c[5])
        # plinth + floor
        for x, z in G.disc_cells(TX, TZ, 7.5, clip2):
            d = math.hypot(x - TX, z - TZ)
            vol.set(x, DECK, z, LAMP if (d < 4.5 and abs(d - 3.0) < 0.6 and _near_angle(_ang(x - TX, z - TZ), 90, d, 45)
                                         ) else (CYAN if 5.5 < d <= 6.2 else (PGREY if (x + z) % 2 else GREY)))
        # lower wall: ring (6, 7], windows y 7..9, pillars every 45 degrees
        for x, z in G.ring_cells(TX, TZ, 6.0, 7.0, clip2):
            d = math.hypot(x - TX, z - TZ)
            a = _ang(x - TX, z - TZ)
            pil = _near_angle(a, 45.0, d, 22.5, 0.9)
            for y in range(DECK + 1, CAB_Y):
                door = x > TX + 4 and abs(z - TZ) <= 1 and y <= DECK + 3
                if door: vol.set(x, y, z, AIR)
                elif y == DECK + 4 and x > TX + 4 and abs(z - TZ) <= 1: vol.set(x, y, z, CYAN)
                elif pil: vol.set(x, y, z, PILLAR)
                elif 7 <= y <= 9: vol.set(x, y, z, GLASS)
                elif y == DECK + 1: vol.set(x, y, z, DARK)
                else: vol.set(x, y, z, WHITE)
        # cab floor (hole above the stair)
        for x, z in G.disc_cells(TX, TZ, 7.5, clip2):
            d = math.hypot(x - TX, z - TZ)
            hole = TX - 3 <= x <= TX - 1 and TZ - 1 <= z <= TZ + 4
            if hole: continue
            if d > 6.0 and d <= 7.5:
                vol.set(x, CAB_Y, z, CYAN if d > 7.0 else WHITE)
            elif abs(d - 4.0) < 0.6 and _near_angle(_ang(x - TX, z - TZ), 60, d, 30):
                vol.set(x, CAB_Y, z, LAMP)
            else:
                vol.set(x, CAB_Y, z, WHITE if (x + z) % 2 else PGREY)
        # lobby ceiling lamps live in the cab floor (above) -> also lamps in the lobby floor were placed above
        # stair
        G.stairs_run(vol, TX - 2, DECK + 1, TZ + 4, 'north', CAB_Y - DECK, width=3, block=BLOCK, fill_to=DECK + 1)
        # glazed cab: ring (6, 7], y 12..15
        for x, z in G.ring_cells(TX, TZ, 6.0, 7.0, clip2):
            d = math.hypot(x - TX, z - TZ)
            pil = _near_angle(_ang(x - TX, z - TZ), 45.0, d, 22.5, 0.9)
            for y in range(CAB_Y + 1, CAB_TOP):
                vol.set(x, y, z, PILLAR if pil else (CYAN_GLASS if y in (12, 15) else GLASS))
        # roof: stepped cap; each step is 2 high so every exposed ring is its own small island (< 50 nodes)
        for (rr, y0, y1) in ((7.0, CAB_TOP, CAB_TOP), (6.3, CAB_TOP + 1, CAB_TOP + 2),
                             (5.0, CAB_TOP + 3, CAB_TOP + 4), (3.6, CAB_TOP + 5, CAB_TOP + 6)):
            for x, z in G.disc_cells(TX, TZ, rr, clip2):
                d = math.hypot(x - TX, z - TZ)
                for y in range(y0, y1 + 1):
                    vol.set(x, y, z, CYAN if (y == y1 and d > rr - 1.0) else (GLASS if y == y0 and y0 > CAB_TOP
                                                                           and d > rr - 1.0 else WHITE))
        for (rr, ytop) in ((7.0, CAB_TOP), (6.3, CAB_TOP + 2), (5.0, CAB_TOP + 4), (3.6, CAB_TOP + 6)):
            for k in range(4):                      # lit marker on each step
                a_ = math.radians(45 + 90 * k)
                vol.set(TX + round((rr - 0.6) * math.cos(a_)), ytop, TZ + round((rr - 0.6) * math.sin(a_)), LAMP)
        for y in range(CAB_TOP + 7, CAB_TOP + 14):
            vol.set(TX, y, TZ, P.ROD if y > CAB_TOP + 9 else PILLAR)
        # consoles
        from . import architecture as A
        A.console(vol, TX + 3, CAB_Y, TZ - 4, 'south', width=3)
        A.console(vol, TX, DECK, TZ - 5, 'south', width=3)
        A.console(vol, TX + 2, DECK, TZ + 4, 'north', width=3)


# ---- registration ------------------------------------------------------------------------------------------
def make_features():
    fs = [ElevatedPad(k + 1, a) for k, a in enumerate(ELEV_ALONG)]
    fs += [Hangar(), ControlTower(), TipPad(-1), TipPad(1)]
    fs += [PierPad(a) for a in PL.PIER_ANGLES if a != 0]
    return fs


FEATURES = make_features()
register(*FEATURES)
