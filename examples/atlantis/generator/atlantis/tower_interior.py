"""Layer 60: the interiors of the Central Tower (district A): rooms, circulation, furniture, lighting for each of the
15 levels in tower_spec.FLOORS.  The structure (outer wall, floor slabs, atrium, core, portals, summit) is tower.py's
(layer 50); this module only adds what is INSIDE it and obeys these limits (interior_kit.Plan enforces them):
  * nothing at r <= CORE_R + 1 (the core and its landing stay clear), nothing beyond tower_spec.usable_r of a floor,
  * the floor slab layer is touched only by same-height inlays,
  * every room has a 2+ wide doorway onto a clear circulation ring around the core.
Assumptions about tower.py (see ATLANTIS_BUILD_LOG): the mezzanine (floor 17) is a ring whose open centre has radius
MEZZ_OPEN_R = 10, with a railing at y 18 along that edge; this module cuts gaps in that railing where its bridges and the
grand stair arrive, and adds four 2-wide bridges from the core landing across the light well at y 17.

Each floor is built once per process into a dict (interior_kit.Plan) and the tile build copies the part inside it.
"""
import math
from functools import lru_cache

from . import palette as P
from . import blocks as B
from . import tower_spec as T
from .feature import Feature, register
from .util import rng as _rng
from .interior_floors import FLOORS as UPPER_FLOORS
from .interior_kit import (Plan, ang, angd, pol, toward, opp, perp, console, chair, stool, desk, table_round, bed,
                           barrel, chest, shelf, column_data, bench_lab, case, screen, planter, map_table, _row)

MEZZ_OPEN_R = 10
RAYS = [(a - 90) % 360 for a in T.ENTRANCE_PLAN_ANGLES]            # entrance rays (geometry angles)
QS = 'smooth_quartz'
LAMP = P.LAMP


def _stairs(mat, facing, half='bottom'):
    return B.stairs(mat, facing, half)


def _tangent(a):
    """Cardinal facing of the clockwise (increasing angle) tangent at angle a."""
    t = math.radians(a)
    return B.facing_from_vector(-math.sin(t), math.cos(t))


# ================================================================================================================
# 5  Grand Atrium
# ================================================================================================================
def floor_atrium(p):
    W, rng = p.W, p.rng
    p.style.update(wall=P.WHITE_BLOCK, desk=P.WHITE_SLAB)
    # ---- floor: sunburst with stained-glass rosette, cyan spokes and floor lamps
    lamp_at = set()
    for k in range(12):
        for rr in (16.6, 23.4):
            x, z = pol(rr, 15 + 30 * k); lamp_at.add((x, z))
    for x, z, r, a in p.cells:
        b = None
        dray = min(abs(angd(a, ra)) * math.pi / 180 * r for ra in RAYS)
        sector = int(a // 30) % 2
        if (x, z) in lamp_at: b = LAMP
        elif r < 9.8: b = P.PALE if sector else None
        elif r < 11.2: b = P.DARK
        elif r < 12.2: b = P.CYAN
        elif r < 13.8: b = P.GLASS_BLUE if int((a + 7.5) // 15) % 2 else P.GLASS_WHITE
        elif r < 14.4: b = P.CYAN
        elif r < 26.4:
            spoke = min(abs(angd(a, s)) * math.pi / 180 * r for s in range(0, 360, 60))
            if r >= 19.4 and r < 20.4: b = P.GLASS_BLUE
            elif spoke < 0.55 and r < 19.4: b = P.GLASS_CYAN
            elif dray < 0.55: b = P.CYAN
            elif dray < 2.6: b = P.WHITE_BRICKS
            else: b = P.WHITE if sector else P.PALE_GREY
        elif r < 27.4: b = P.CYAN
        else: b = P.DARK
        if b: p.inlay(x, z, b)
    # entrance halls: keep the six axes clear
    for ra in RAYS: p.lane(ra, 10.0, 28.6, half=2.6)
    # ---- pylons of the light well under the mezzanine edge
    for k in range(6):
        x, z = pol(11.9, 60 * k)
        p.set(x, W, z, P.DARK); p.set(x, W + 1, z, P.WHITE_PILLAR)
        for y in range(W + 2, 16):
            p.set(x, y, z, LAMP if y in (W + 3, 12, 15) else P.GLASS_BLUE)
        p.set(x, 16, z, P.WHITE_BLOCK)
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            p.set(x + dx, W, z + dz, _stairs(QS, opp(B.facing_from_vector(dx, dz))), occ=False)
    # ---- grand staircase: a sweeping curved flight inside the well, from the floor to the mezzanine
    A0, SPAN, N = 100.0, 100.0, 12
    for x, z, r, a in p.cells:
        if r < 7.0 or r >= 9.9: continue
        off = (a - A0) % 360.0
        if off >= SPAN: continue
        i = int(off / (SPAN / N))
        y = W + i                                                  # treads at y 6 .. 17
        if r < 8.0:                                               # inner stringer: solid parapet with a glass rail
            for yy in range(W, y + 1): p.set(x, yy, z, P.WHITE_BLOCK if yy < y else P.DARK)
            p.set(x, y + 1, z, P.GLASS_BLUE_PANE)
        else:
            for yy in range(W, y): p.set(x, yy, z, P.WHITE_BLOCK)
            p.set(x, y, z, _stairs(QS, _tangent(a)))
    # ---- map table on a stepped dais (a glowing chart of the city under glass), flanking consoles, a curved screen wall
    cx, cz = pol(18.0, 0)
    for x, z, r, a in p.cells:
        d = math.hypot(x - cx, z - cz)
        if d <= 3.7:
            p.set(x, W, z, P.WHITE_BLOCK if d > 2.6 else P.WHITE)
        elif d <= 4.7:
            p.set(x, W, z, _stairs(QS, toward(x, z, cx, cz)))
    for x in range(cx - 1, cx + 2):
        for z in range(cz - 1, cz + 2):
            p.set(x, W + 1, z, P.DARK)
            p.set(x, W + 2, z, 'sea_lantern' if (x, z) == (cx, cz) else P.GLASS_BLUE)
    for k in (-1, 1):
        x, z = pol(25.0, 17 * k)
        console(p, x, z, 'west', 3)
    p.arc_wall(27.2, -28, 28, style='glass', block=P.DARK, glass='blue_stained_glass_pane')
    for x, z in p.sel(26.4, 27.9, -26, 26):
        if (x, z) in p.occ: p.set(x, W + 4, z, LAMP, occ=False)
    for k in (-3, 3):                                               # crystal data columns at the dais
        x, z = pol(20.5, 11 * k); column_data(p, x, z, 4)
    # ---- reception desk (crescent) with receptionists, info columns behind it
    for x, z in p.sel(14.6, 16.0, 44, 76):
        p.set(x, W, z, B.slab(P.WHITE, 'top'))
    for x, z in p.sel(15.2, 16.0, 44, 76):
        p.set(x, W, z, P.WHITE_BLOCK)
    for k in range(5):
        x, z = pol(15.4, 48 + 6.5 * k)
        p.set(x, W + 1, z, 'lantern[hanging=false]' if k % 2 == 0 else 'black_stained_glass_pane')
    for k in range(4):
        x, z = pol(17.3, 51 + 8 * k); chair(p, x, z, toward(x, z))
    for k in range(3):
        x, z = pol(20.0, 52 + 12 * k); column_data(p, x, z, 4, 'white')
    # ---- lounge: seating groups round low tables, planters
    for a in (108, 132):
        x, z = pol(17.5, a); table_round(p, x, z, 1.3, seats=5, seat_mat='quartz', lamp=True)
    for a in (104, 120, 136):
        x, z = pol(24.0, a); planter(p, x, z, 'azalea')
    for a in (116, 128):
        x, z = pol(21.5, a); table_round(p, x, z, 0.8, seats=3, seat_mat='smooth_quartz', lamp=False)
    # ---- security check: two scanner arches and lockers
    for a in (168, 192):
        x, z = pol(21.0, a)
        face = toward(x, z); px, pz = perp(face)
        if p.free([(x + px * k, z + pz * k) for k in (-2, -1, 0, 1, 2)]):
            for k in (-2, 2):
                for y in range(W, W + 4): p.set(x + px * k, y, z + pz * k, P.WHITE_PILLAR)
            for k in (-1, 0, 1):
                p.set(x + px * k, W + 3, z + pz * k, B.slab(P.WHITE, 'bottom') if k else P.GLASS_CYAN, occ=False)
            p.set(x + px * 3, W, z + pz * 3, P.WHITE_BLOCK)
    x, z = pol(24.5, 180)
    for k in range(-3, 4):
        barrel(p, x, z + k)
    desk(p, *pol(18.0, 180), 'east', 3)
    # ---- exhibit gallery: crystal data columns in an arc, display plinths
    for k in range(5):
        x, z = pol(19.0, 232 + 9 * k); column_data(p, x, z, 4, 'cool' if k % 2 == 0 else 'white')
    for a in (224, 256):
        x, z = pol(24.0, a)
        if p.free([(x, z)]):
            p.set(x, W, z, P.WHITE_BLOCK); p.set(x, W + 1, z, 'lectern[facing=%s]' % toward(x, z)); p.set(x, W + 2, z, 'end_rod[facing=up]')   # a cluster needs a full block below
    # ---- Atlantis model in the north-west wedge
    mx, mz = pol(20.0, 300)
    map_table(p, mx, mz, 3.2)
    for k in range(3):
        x, z = pol(23.5, 285 + 15 * k); planter(p, x, z, 'potted_fern')
    # ---- wall-side display panels along the wall (inner face of the usable ring)
    for x, z in p.sel(27.6, 28.6):
        a = ang(x, z)
        if min(abs(angd(a, ra)) * math.pi / 180 * 28 for ra in RAYS) < 4.0: continue
        if int(a // 6) % 3 == 0:
            p.col(x, z, W, W + 3, P.WHITE_BLOCK)
            p.set(x, W + 1, z, P.GLASS_BLUE); p.set(x, W + 2, z, P.GLASS_BLUE)
            p.set(x, W + 3, z, LAMP)
    for ra in RAYS:
        d = pol(27.2, ra)
        p.room('entrance hall', ra, 24.0)


# ================================================================================================================
# 17  Operations Hall (the mezzanine)
# ================================================================================================================
def floor_ops(p):
    W = p.W
    p.style.update(wall=P.WHITE_BLOCK)
    # inlay: ring walkway, cyan lines
    for x, z, r, a in p.cells:
        if r < MEZZ_OPEN_R + 0.5: continue
        b = None
        if r < 11.6: b = P.DARK
        elif r < 12.4: b = P.CYAN
        elif r > 16.0 and r < 17.0: b = P.LIGHT_BLUE if int(a // 7.5) % 2 else P.PALE_GREY
        elif int(a // 15) % 2 == 0 and r < 16: b = P.WHITE if int(r) % 2 else P.PALE_GREY
        if b: p.inlay(x, z, b)
    # the tower's own 3-wide gangway crosses the well at geom 45 (inside the reserved stair-exit lane)
    p.bridges = [45.0]
    sa0, span, n = None, None, None
    # the stair arrives from the ground floor at A0 + SPAN: carve the railing there
    A0, SPAN = 100.0, 100.0
    for x, z, r, a in p.cells:
        if 8.5 < r < MEZZ_OPEN_R + 1.5 and 0 <= (a - (A0 + SPAN - 24)) % 360 <= 26:
            for y in (p.W, p.W + 1): p.c[(x, y, z)] = 'air'
    p.lane(A0 + SPAN - 8, 10.0, 11.8, half=3.0)
    # ---- arcs of consoles facing the light well
    RC = 16.0
    consoles = 0
    for a in range(0, 360, 13):
        if any(abs(angd(a, b)) < 14 for b in p.bridges + [A0 + SPAN - 8]): continue
        x, z = pol(RC + 1.0, a)
        face = toward(x, z)
        if console(p, x, z, face, 3): consoles += 1
    # second, outer arc of wall screens with sea-lantern lines
    for x, z in p.sel(p.R - 0.2, p.R + 0.1):
        a = ang(x, z)
        if any(abs(angd(a, b)) < 7 for b in p.bridges): continue
        for y in (W + 1, W + 2, W + 3): p.set(x, y, z, 'black_stained_glass_pane' if y != W + 2 else 'blue_stained_glass_pane', occ=(y == W + 1))
        p.set(x, W, z, LAMP)
    # duty desks in the aisles, planters at the bridge heads
    for ba in p.bridges:
        x, z = pol(17.0, ba)
        desk(p, x, z, toward(x, z), 3)
        x, z = pol(11.2, ba + 12); planter(p, x, z, 'potted_azalea_bush')
        x, z = pol(11.2, ba - 12); planter(p, x, z, 'potted_azalea_bush')
    # hanging lamps over the walkway
    for k in range(12):
        x, z = pol(11.3, 15 + 30 * k); p.hang(x, z)
    for k in range(18):
        x, z = pol(16.2, 5 + 20 * k); p.lamp(x, z, 'cool')
    p.rooms.append({'name': 'operations hall', 'door': pol(12.5, 65), 'centre': pol(13.0, 65), 'door_ang': 65,
                    'door_r': 11.0})


# ================================================================================================================
# registry
# ================================================================================================================
BUILDERS = {
    5: floor_atrium,
    17: floor_ops,
}
BUILDERS.update(UPPER_FLOORS)
STYLES = {}


def tower_constraints(F):
    """(protect, blocked) columns for floor F from the tower structure (layer 50): ceiling-lamp slab cells that must
    not be inlaid or covered, and the stair exit lane / doorway fronts that stay clear (tower.core_cells etc.)."""
    from . import tower as TW
    protect = {(x, z) for ops in TW._ops().values() for (x, y, z, b) in ops if y == F and b == 'sea_lantern' and x * x + z * z > 36}
    blocked = set(TW.core_cells())
    doors = []
    if F == 5: doors = [(x, z) for i in range(len(T.ENTRANCE_PLAN_ANGLES)) for (x, y, z) in TW.portal_cells(i)]
    elif F == 71: doors = [(x, z) for (x, y, z) in TW.balcony_door_cells()]
    elif F == 83: doors = [(x, z) for (x, y, z) in TW.deck_door_cells()]
    for (dx, dz) in set(doors):
        for ax in range(-3, 4):
            for az in range(-3, 4): blocked.add((dx + ax, dz + az))
    return protect, blocked


@lru_cache(maxsize=64)
def build_plan(seed, F):
    """The interior of the floor whose floor-block y is F as a finished Plan (cached; independent of tiles)."""
    import random
    from .util import hash_str
    info = [f for f in T.floor_list() if f[0] == F][0]
    name = f'tower_interior_{F}'
    top = 16 if F == 5 else None
    protect, blocked = tower_constraints(F)
    p = Plan(F, info[3], random.Random(hash_str(seed, name)), open_r=MEZZ_OPEN_R if F == 17 else None, top=top,
             protect=protect, blocked=blocked)
    fn = BUILDERS.get(F)
    if fn: fn(p)
    return p.finish()


class InteriorFloor(Feature):
    layer = 60

    def __init__(self, info):
        y, walk, ceil, name, prog, R = info
        self.F = y
        self.empty = R < 6.5                      # the summit: usable radius < core, nothing of ours fits inside
        meta = {'district': 'A', 'kind': 'tower_interior', 'floor_name': name, 'allow_overlap': ['tower']}
        if self.empty:
            super().__init__(f'tower_interior_{y:02d}', (0, y, 0, 0, y, 0), 60, meta)
            return
        Rc = int(math.ceil(R))
        top = 17 if y == 5 else ceil              # the grand stair's last tread (y 17) belongs to the atrium
        meta.update(interior=True, interior_box=(-Rc, walk, -Rc, Rc, ceil, Rc))
        if y == 5: meta['entrances'] = T.entrances(); meta['roof_y'] = 17
        super().__init__(f'tower_interior_{y:02d}', (-Rc, y, -Rc, Rc, top, Rc), 60, meta)

    def build(self, vol, ctx):
        if self.empty: return
        c = vol.clip(*self.bbox)
        if not c: return
        p = build_plan(ctx.seed, self.F)
        x1, y1, z1, x2, y2, z2 = c
        for (x, y, z), b in p.c.items():
            if x1 <= x <= x2 and y1 <= y <= y2 and z1 <= z <= z2:
                vol.set(x, y, z, b)


def _register():
    for info in T.floor_list():
        register(InteriorFloor(info))


_register()
