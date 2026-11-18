"""Layer 50: the Observation Tower (district K, inner ring), a slender Atlantean needle with a glazed gallery.

SITE   city-local (147, -30): district K at r ~150, where the dome cap is still ~97.  The tower is as tall as the dome
       allows (roof deck = rim cap - 2, spire up to the centre cap).  Footprint: radius 12 around the centre.
SHAPE  * lobby      r 9 drum, one 5-row storey (floor y 4, ceiling slab y 10), glass wall, reception ring.
       * shaft      r 6.5 slender glass-and-quartz needle from y 11 up to the gallery; six radial fins of stepped heights
                    buttress it and cut the lobby roof into small sealed pockets (no big unreachable roof).
       * spiral     a lit helical stair (16 steps per turn, ring r 2.6..5.5) winds round the lift core from the lobby
                    floor to the gallery floor; the last five steps pass through a railed hole in the gallery floor.
       * lift core  centre 5x5 glass shaft (quartz spine on the north side) with a ladder from the lobby to the roof
                    deck (simulated lift, no water); doors on the south face at the lobby, gallery and roof.
       * gallery    r 9.5 drum, 360 degree glazing (7 air rows) on a stepped corbel; benches, block telescopes, consoles,
                    data columns, lecterns, planters.  A straight stair on the north side leads to the roof.
       * roof       r 10.5 terrace with a glass rail and lantern inlays; the lift head-house and a spire rise from the core.
"""
import math

from . import config, planner as PL, roads, palette as P, blocks as B, geometry as G
from . import interior_kit as IK
from . import landmark_command as LC
from .feature import Feature, register
from .util import rng as _rng

LAYER = 50
DECK = PL.DECK_TOP
WALK = PL.WALK_Y
CX, CZ = 147, -30
HALF = 12
N_STEP = 16                    # steps per turn of the spiral
RING_IN, RING_OUT = 2.6, 5.5   # spiral ring
SHAFT_OUT = 6.5
LOBBY_R = 9.0
GAL_R = 9.5
ROOF_R = 10.5

WALL, BLOCK, BRICKS, PILLAR = LC.WALL, LC.BLOCK, LC.BRICKS, LC.PILLAR
GLASS, CYAN, LAMP, RAIL, LIGHT = LC.GLASS, LC.CYAN, LC.LAMP, LC.RAIL, LC.LIGHT
GREY = P.PALE_GREY

_CTX = config.load()
_COS, _SIN = math.cos, math.sin


def _gang(dx, dz):
    return math.degrees(math.atan2(dz, dx)) % 360.0


def _pol(r, a):
    return LC.pol(CX, CZ, r, a)


def _caps():
    rim = int(min(_CTX.max_building_top(CX + 11.5 * _COS(math.radians(a)), CZ + 11.5 * _SIN(math.radians(a)))
                  for a in range(0, 360, 15)))
    centre = int(_CTX.max_building_top(CX, CZ))
    return rim, centre


RIM_CAP, CENTRE_CAP = _caps()
ROOF = min(RIM_CAP - 2, CENTRE_CAP - 6)    # roof deck slab y (head-house + spire above must stay under the cap)
GAL = ROOF - 8                           # gallery floor slab y
STEPS = GAL - DECK                       # steps 1..STEPS, step s is a block at y DECK + s (the last one is flush)
TOP = CENTRE_CAP
BBOX = (CX - HALF, DECK, CZ - HALF, CX + HALF, TOP, CZ + HALF)


def _entrance_angle():
    """Geometry angle (towards the nearest street/plaza cell) of the tower door."""
    best = None
    for a in range(0, 360, 5):
        for d in range(11, 40):
            x, z = _pol(d, a)
            if roads.surface(x, z) is not None:
                if best is None or d < best[0]: best = (d, a)
                break
    return float(best[1] if best else 180)


ENTR = _entrance_angle()
A0 = 90.0 - 4.5 * 360.0 / N_STEP        # phase 0 starts here; the lift door (south, 90) sits on a high-up step


def phase(x, z):
    return int(((_gang(x - CX, z - CZ) - A0) % 360.0) / (360.0 / N_STEP))


def ring_cell(x, z):
    d = math.hypot(x - CX, z - CZ)
    return RING_IN < d <= RING_OUT


def in_core(x, z):
    return abs(x - CX) <= 2 and abs(z - CZ) <= 2


HOLE_PH = {phase_ for phase_ in ((s - 1) % N_STEP for s in range(STEPS - 4, STEPS))}
EXIT_PH = (STEPS - 1) % N_STEP
STAIR_X0, STAIR_Z0 = CX - 4, CZ - 7        # roof stair: 8 steps, 2 wide, climbing east
LIFT_COL = (CX, CZ - 1)                    # ladder cell, attached to the north wall of the core


def build(ctx):
    pl = LC.Plan('obs_tower', BBOX, 'K', ctx)
    rng = _rng(ctx, 'obs_tower')
    _lobby(pl, rng)
    _shaft(pl)
    _spiral(pl)
    _gallery(pl, rng)
    _roof(pl)
    _core(pl)
    _lights(pl)
    pl.finish()
    return pl


# ------------------------------------------------------------------------------------------------------------
def _pier(x, z, r, n_p):
    a = _gang(x - CX, z - CZ)
    seg = 360.0 / n_p
    t = (a % seg) / seg
    return min(t, 1 - t) * 2 * math.pi * r / n_p < 0.7


def _lobby(pl, rng):
    F = DECK
    # floor pattern
    for x, z in G.disc_cells(CX, CZ, LOBBY_R):
        d = math.hypot(x - CX, z - CZ)
        b = BLOCK
        if abs(d - 4.0) < 0.6 or abs(d - 7.0) < 0.5: b = CYAN
        elif d > LOBBY_R - 1.2: b = GREY
        elif int(d) % 2 == 0 and int(_gang(x - CX, z - CZ) / 15) % 2 == 0: b = BRICKS
        pl.set(x, F, z, b)
    for k in range(12):
        x, z = _pol(6.6, 30 * k + 15)
        pl.set(x, F, z, LAMP)
    # wall ring r 8..9, 5 rows: knee wall, glass between pillars, cornice
    n_p = 14
    for x, z in G.ring_cells(CX, CZ, LOBBY_R - 1.0, LOBBY_R):
        pier = _pier(x, z, LOBBY_R, n_p)
        pl.set(x, F + 1, z, BLOCK)
        for y in (F + 2, F + 3, F + 4): pl.set(x, y, z, PILLAR if pier else GLASS)
        pl.set(x, F + 5, z, BRICKS)
    # ceiling: annulus only; the spiral ring stays open to the sky
    for x, z in G.ring_cells(CX, CZ, RING_OUT, GAL_R):
        pl.set(x, DECK + 6, z, WALL if math.hypot(x - CX, z - CZ) < LOBBY_R else B.slab(WALL, 'bottom'))
    for x, z in G.ring_cells(CX, CZ, LOBBY_R - 0.5, LOBBY_R + 0.7):
        pl.set(x, DECK + 6, z, CYAN)
    # door
    ux, uz = _COS(math.radians(ENTR)), _SIN(math.radians(ENTR))
    for x in range(CX - 12, CX + 13):
        for z in range(CZ - 12, CZ + 13):
            dx, dz = x - CX, z - CZ
            al, pp = dx * ux + dz * uz, abs(-dx * uz + dz * ux)
            if LOBBY_R - 2.6 <= al <= LOBBY_R + 1.8 and pp <= 2.5:
                for y in range(F + 1, F + 5): pl.pop(x, y, z)
                pl.res.add((x, F, z))
            if LOBBY_R - 2.6 <= al <= LOBBY_R + 0.6 and pp <= 2.5:
                pass
    # porch canopy with two pillars and a lamp
    for al in range(int(LOBBY_R) + 1, int(LOBBY_R) + 4):
        for pp in range(-3, 4):
            x, z = int(math.floor(CX + al * ux - pp * uz + 0.5)), int(math.floor(CZ + al * uz + pp * ux + 0.5))
            if math.hypot(x - CX, z - CZ) <= LOBBY_R: continue
            pl.set(x, F + 5, z, B.slab(WALL, 'bottom'))
            if abs(pp) == 3 and al in (int(LOBBY_R) + 1, int(LOBBY_R) + 3):
                for y in range(F + 1, F + 5): pl.set(x, y, z, PILLAR)
            elif al == int(LOBBY_R) + 2 and pp == 0:
                pl.set(x, F + 5, z, LAMP)
    pl.entrance_cell = (int(math.floor(CX + (LOBBY_R - 0.5) * ux + 0.5)), WALK,
                        int(math.floor(CZ + (LOBBY_R - 0.5) * uz + 0.5)))
    # lobby furniture in the annulus 5.6..7.6 (the ring r<5.5 is the open stair)
    for x, z in G.ring_cells(CX, CZ, RING_OUT, LOBBY_R - 1.0): pl.floors.add((x, F, z))
    for al in range(6, 12):
        for pp in range(-3, 4):
            pl.res.add((int(math.floor(CX + al * ux - pp * uz + 0.5)), F, int(math.floor(CZ + al * uz + pp * ux + 0.5))))
    for dx in range(-3, 4):                          # lane to the lift door (south)
        for dz in range(2, 8): pl.res.add((CX + dx, F, CZ + dz))
    lv = LC.Lv(pl, F, CX, CZ, LOBBY_R - 1.3, rng)
    LC.data_columns(lv, 7.0, 6, ENTR + 30.0, 'cool', 4)
    LC.seat_ring(lv, 6.6, 14, ENTR + 12.0, skip=(ENTR, 90), inward=False)
    LC.planters(lv, 7.3, 8, ENTR + 20.0, skip=(ENTR, 90))
    for a in (ENTR + 75, ENTR - 75, ENTR + 180):             # reception desks / lecterns
        x, z = lv.at(7.0, a)
        IK.desk(lv, x, z, lv.face_in(x, z), 3)
    for a in (ENTR + 110, ENTR - 110):
        x, z = lv.at(6.0, a)
        if lv.free([(x, z)]):
            lv.set(x, F + 1, z, f'lectern[facing={lv.face_in(x, z)},has_book=false,powered=false]')
    # lobby roof: six radial fins cut it into pockets of < 40 cells; lantern inlay in each
    for k in range(6):
        a = 60 * k + 30
        for r10 in range(66, 96):
            r = r10 / 10.0
            x, z = _pol(r, a)
            if not (SHAFT_OUT - 0.5 < math.hypot(x - CX, z - CZ) <= GAL_R): continue
            h = 22 if r < 7.6 else 14 if r < 8.6 else 6
            for y in range(DECK + 7, DECK + 7 + h):
                pl.set(x, y, z, PILLAR)
            pl.set(x, DECK + 7 + h, z, B.slab(WALL, 'bottom'))
            pl.set(x, DECK + 8 + h, z, LIGHT)
        x, z = _pol(7.9, a + 30)
        pl.set(x, DECK + 6, z, LAMP)


def _shaft(pl):
    """Outer needle wall r 5.5..6.5 from the lobby ceiling to the gallery floor, plus the corbel under the gallery."""
    n_p = 12
    for y in range(DECK + 7, GAL):
        for x, z in G.ring_cells(CX, CZ, RING_OUT, SHAFT_OUT):
            pier = _pier(x, z, SHAFT_OUT, n_p)
            band = (y - DECK) % 12 == 0
            b = CYAN if band else (LAMP if pier and (y - DECK) % 6 == 3 else PILLAR if pier else GLASS)
            pl.set(x, y, z, b)
    # corbel: solid annuli widening upward under the gallery
    for k, r in enumerate((7.4, 8.2, 8.9)):
        y = GAL - 3 + k
        for x, z in G.ring_cells(CX, CZ, SHAFT_OUT, r):
            pl.set(x, y, z, WALL if k < 2 else BLOCK)


def _spiral(pl):
    for x, z in G.ring_cells(CX, CZ, RING_IN, RING_OUT):
        ph = phase(x, z)
        d = math.hypot(x - CX, z - CZ)
        for s in range(ph + 1, STEPS + 1, N_STEP):
            y = DECK + s
            if d <= 3.3: b = CYAN
            elif ph % 3 == 0 and d > 4.4: b = LAMP
            else: b = BLOCK
            pl.set(x, y, z, b)
            if s % 3 == 0: pl.set(x, y + 3, z, LIGHT)


def _gallery(pl, rng):
    F = GAL
    cells = set()
    for x, z in G.disc_cells(CX, CZ, GAL_R):
        if in_core(x, z) and (x, z) != LIFT_COL:
            pl.floors.add((x, F, z))
            pl.set(x, F, z, BLOCK)
            continue
        if (x, z) == LIFT_COL: continue
        if ring_cell(x, z) and phase(x, z) in HOLE_PH and math.hypot(x - CX, z - CZ) <= RING_OUT:
            continue
        d = math.hypot(x - CX, z - CZ)
        b = WALL
        if abs(d - 7.7) < 0.6 or abs(d - 3.6) < 0.5: b = CYAN
        elif d > GAL_R - 1.1: b = GREY
        elif int(d) % 2 == 0 and int(_gang(x - CX, z - CZ) / 15) % 2 == 0: b = BRICKS
        pl.set(x, F, z, b)
        pl.floors.add((x, F, z))
        cells.add((x, z))
    # roof stair hole carved out of the roof later; stair footprint is reserved here
    for dx in range(-9, 6):
        for dz in range(-8, -4): pl.res.add((CX + dx, F, CZ + dz))
    for x, z in G.disc_cells(CX, CZ, RING_OUT + 1.0):
        if phase(x, z) in HOLE_PH or phase(x, z) == EXIT_PH:
            if math.hypot(x - CX, z - CZ) > RING_IN - 0.5: pl.res.add((x, F, z))
    for dx in range(-3, 4):
        for dz in range(2, 6): pl.res.add((CX + dx, F, CZ + dz))
    # rails round the stair hole (not at the exit sector)
    hole = {(x, z) for x, z in G.ring_cells(CX, CZ, RING_IN, RING_OUT) if phase(x, z) in HOLE_PH}
    for (x, z) in hole:
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, z + dz)
            if n in hole or n not in cells: continue
            if ring_cell(*n) and phase(*n) == EXIT_PH: continue
            pl.set(n[0], F + 1, n[1], RAIL)
    # outer wall: knee wall, glazing between pillars, 360 degrees
    n_p = 16
    for x, z in G.ring_cells(CX, CZ, GAL_R - 1.0, GAL_R):
        pier = _pier(x, z, GAL_R, n_p)
        pl.set(x, F + 1, z, BLOCK)
        for y in range(F + 2, F + 8): pl.set(x, y, z, PILLAR if pier else GLASS)
    # furniture
    lv = LC.Lv(pl, F, CX, CZ, GAL_R - 0.6, rng)
    for k in range(8):                                    # block telescopes in the window bays
        a = 22.5 + 45 * k
        x, z = lv.at(7.0, a)
        _telescope(lv, x, z, a)
    placed = []
    for a in range(0, 360, 15):                           # consoles round the core wherever the floor is free
        if any(LC.angd(a, q) < 40 for q in placed): continue
        x, z = lv.at(3.4, a)
        if IK.console(lv, x, z, IK.opp(lv.face_in(x, z)), 3): placed.append(a)
    LC.data_columns(lv, 5.2, 8, 0.0, 'cool', 4)
    LC.seat_ring(lv, 6.5, 24, 7.0, inward=False, mat='quartz')
    LC.planters(lv, 7.6, 16, 0.0)
    for k in range(8):                                    # charting tables between the telescopes
        x, z = lv.at(7.4, 45 * k)
        if lv.free([(x, z)]):
            lv.set(x, F + 1, z, ('cartography_table', 'lectern')[k % 2] if k % 2 == 0 else
                   f'lectern[facing={lv.face_in(x, z)},has_book=false,powered=false]')
    for a in (45, 135, 225, 315):                         # lecterns with a view
        x, z = lv.at(5.9, a + 22)
        if lv.free([(x, z)]):
            lv.set(x, F + 1, z, f'lectern[facing={lv.face_in(x, z)},has_book=false,powered=false]')
    # roof stair: 8 steps climbing east, 2 wide, from the gallery floor to the roof deck
    for q in (0, 1):
        for s in range(8):
            x, z = STAIR_X0 + s, STAIR_Z0 + q
            pl.set(x, F + 1 + s, z, B.stairs(WALL, 'east'))
            for y in range(F + 1, F + 1 + s): pl.set(x, y, z, BLOCK)
        pass


def _telescope(lv, x, z, a):
    """Telescope on a plinth: pillar stand, lightning-rod barrel pointing at the window, a lamp at the eyepiece."""
    f = IK.opp(lv.face_in(x, z))                      # outward
    dx, dz = B.DIRS[f]
    if not lv.free([(x, z), (x + dx, z + dz)]): return False
    W = lv.W
    lv.set(x, W, z, PILLAR)
    lv.set(x, W + 1, z, 'iron_block')
    lv.set(x + dx, W + 1, z + dz, f'lightning_rod[facing={f},powered=false,waterlogged=false]')
    lv.set(x - dx, W + 1, z - dz, f'lightning_rod[facing={f},powered=false,waterlogged=false]') \
        if lv.free([(x - dx, z - dz)]) else None
    lv.p.occ.add((x + dx, lv.F, z + dz))
    return True


def _roof(pl):
    F = ROOF
    hole = {(STAIR_X0 + s, STAIR_Z0 + q) for s in range(3, 8) for q in (0, 1)}
    for x, z in G.disc_cells(CX, CZ, ROOF_R):
        d = math.hypot(x - CX, z - CZ)
        if (x, z) in hole or (x, z) == LIFT_COL: continue
        b = WALL
        if d > GAL_R: b = BRICKS if int(d) % 2 else GREY
        pl.set(x, F, z, b)
    for k in range(12):
        x, z = _pol(7.2, 30 * k + 8)
        if (x, z) not in hole: pl.set(x, F, z, LAMP)
    for k in range(12):
        x, z = _pol(10.0, 30 * k + 15)
        pl.set(x, F, z, LAMP)
    for x, z in G.ring_cells(CX, CZ, ROOF_R - 1.0, ROOF_R): pl.set(x, F + 1, z, RAIL)
    for k in range(12):
        x, z = _pol(ROOF_R - 0.5, 30 * k)
        pl.set(x, F + 1, z, PILLAR)
        pl.set(x, F + 2, z, 'end_rod[facing=up]')
    for (x, z) in hole:                                   # rail round the stair hole except the exit mouth
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            n = (x + dx, z + dz)
            if n in hole or n[0] == STAIR_X0 + 8: continue
            pl.set(n[0], F + 1, n[1], RAIL)


def _core(pl):
    """Lift core: 5x5 wall (glass, quartz spine north), ladder, floors at lobby/gallery/roof, doors on the south face,
    head-house and spire above the roof."""
    top_house = ROOF + 3
    for y in range(DECK + 1, top_house + 1):
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                if max(abs(dx), abs(dz)) != 2: continue
                x, z = CX + dx, CZ + dz
                if dz == -2: b = BLOCK
                elif abs(dx) == 2 and abs(dz) == 2: b = PILLAR
                elif (y - DECK) % 8 == 0: b = CYAN
                else: b = GLASS
                pl.set(x, y, z, b)
    # doors (south face, 3 high) at the three landings
    for F0 in (DECK, GAL, ROOF):
        for y in range(F0 + 1, F0 + 4): pl.pop(CX, y, CZ + 2)
    # floors inside the core
    for F0 in (GAL, ROOF):
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                if (CX + dx, CZ + dz) != LIFT_COL: pl.set(CX + dx, F0, CZ + dz, BLOCK)
    # ladder up the north wall: lobby to roof level + 1
    for y in range(DECK + 1, ROOF + 2):
        pl.set(CX, y, CZ - 1, 'ladder[facing=south]')
    # lift cabin furnishing: lamps in the corners of each landing, hidden lights up the shaft
    for y in range(DECK + 3, ROOF + 3, 4): pl.set(CX + 1, y, CZ + 1, LIGHT)
    # head-house roof and spire
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            pl.set(CX + dx, top_house + 1, CZ + dz, BLOCK if max(abs(dx), abs(dz)) == 2 else LAMP)
    for y in range(top_house + 2, TOP - 2): pl.set(CX, y, CZ, PILLAR)
    for y in range(max(TOP - 2, top_house + 2), TOP + 1): pl.set(CX, y, CZ, 'end_rod[facing=up]')


def _lights(pl):
    """Hidden light lattice in the lobby, the gallery and on the terrace."""
    for F0, rin, top_row in ((DECK, LOBBY_R - 1.0, DECK + 5), (GAL, GAL_R - 1.0, GAL + 7)):
        n = int(rin) + 1
        for dz in range(-n, n + 1):
            for dx in range(-n, n + 1):
                if dx % 4 or dz % 4 or math.hypot(dx, dz) > rin - 0.5: continue
                x, z = CX + dx, CZ + dz
                if (x, top_row, z) in pl.c: continue
                pl.set(x, top_row, z, LIGHT)
    for dz in range(-12, 13, 3):
        for dx in range(-12, 13, 3):
            if math.hypot(dx, dz) < ROOF_R - 1.5 and math.hypot(dx, dz) > 3.5:
                if (CX + dx, ROOF + 1, CZ + dz) not in pl.c: pl.set(CX + dx, ROOF + 1, CZ + dz, LIGHT)


# ------------------------------------------------------------------------------------------------------------
_CACHE = {}


def build_tower(ctx):
    return build(ctx)


class ObservationTower(Feature):
    layer = LAYER

    def __init__(self):
        ex, ez = _pol(LOBBY_R - 0.5, ENTR)
        meta = {'kind': 'observation_tower', 'district': 'K', 'interior': True, 'islands_ok': True, 'clearance_ok': True,
                'entrances': [(ex, WALK, ez)],
                'interior_box': (CX - 8, WALK, CZ - 8, CX + 8, ROOF - 1, CZ + 8),
                'roof_y': ROOF + 1}
        super().__init__('obs_tower', BBOX, LAYER, meta)
        self.maker = build_tower

    def plan(self, ctx=None):
        p = _CACHE.get(self.name)
        if p is None:
            p = _CACHE[self.name] = build(ctx or _CTX)
        return p

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


FEATURES = [ObservationTower()]
register(*FEATURES)


def features():
    return list(FEATURES)
