"""The thirteen upper-floor interiors of the Central Tower (floors 23 .. 89); floors 5 (Grand Atrium) and 17
(Operations Hall) live in tower_interior.py.  Each `floor_*` function furnishes one interior_kit.Plan.

Common scheme: the core (r <= 6) stays clear, a circulation ring (r 7..9) surrounds it, an arc wall at r = 10 holds the
2+ wide doorways of the rooms (sectors between radial walls) except on the floors that are one open space.
"""
import math

from . import palette as P
from . import blocks as B
from .interior_kit import (ang, angd, pol, toward, opp, perp, console, chair, stool, desk, table_round, bed, barrel,
                           chest, shelf, column_data, bench_lab, case, planter, map_table)

LAMP = P.LAMP
QS = 'smooth_quartz'
HOLO = 'light_blue_stained_glass'


def fin(x, z):
    """Cardinal direction from (x, z) towards the tower axis."""
    return toward(x, z)


def fout(x, z):
    return opp(toward(x, z))


def ring_rooms(p, bounds, ri=10.0, inner='solid', radial='solid', names=None, block=None, glass=None, door_hw=1.4):
    """Partition the floor: an arc wall at radius ri with a doorway at the middle of every sector and radial walls on
    the sector boundaries (bounds = sector start angles, the last sector wraps to the first). Returns room dicts."""
    n = len(bounds)
    rooms, gaps = [], []
    for i in range(n):
        a0 = bounds[i]
        a1 = bounds[i + 1] if i + 1 < n else bounds[0] + 360.0
        ac = (a0 + a1) / 2.0
        rooms.append({'name': (names or [f'room {i}'] * n)[i], 'a0': a0 % 360, 'a1': a1, 'ac': ac % 360, 'span': a1 - a0})
        gaps.append((ac % 360, door_hw))
    p.arc_wall(ri, 0, 360, style=inner, block=block, gaps=gaps, glass=glass)
    for r in rooms:
        p.ray_wall(r['a0'], ri - 0.4, p.R + 0.5, style=radial, block=block, glass=glass)
    for r in rooms:
        p.room(r['name'], r['ac'], ri, (ri + p.R) / 2.0)
    return rooms


def ring_lights(p, kind='cool', r=8.0, n=8, a0=22.5, hang=False):
    for k in range(n):
        x, z = pol(r, a0 + 360.0 * k / n)
        if hang: p.hang(x, z)
        else: p.lamp(x, z, kind)


def ring_inlay(p, r0, r1, block, a0=0.0, a1=360.0):
    for x, z in p.sel(r0, r1, a0, a1): p.inlay(x, z, block)


def halo(p, a=P.CYAN, b=P.DARK):
    """Floor rings round the core landing."""
    ring_inlay(p, 6.0, 7.0, b)
    ring_inlay(p, 7.0, 7.6, a)


def at(room, fr, fa, p):
    """Point in a room: fr 0..1 from the inner arc wall to the outer wall, fa -1..1 across the sector."""
    r = 11.0 + (p.R - 0.9 - 11.0) * fr
    return pol(r, room['ac'] + fa * room['span'] / 2.0 * 0.82)


def floor_inlay(p, rooms, colours, margin=4):
    for r, c in zip(rooms, colours):
        ring_inlay(p, 10.9, p.R - 0.5, c, r['a0'] + margin, r['a0'] + r['span'] - margin)


def lamp_room(p, rooms, kind, spots=((0.3, -0.5), (0.3, 0.5), (0.8, -0.4), (0.8, 0.4))):
    for r in rooms:
        for fr, fa in spots:
            x, z = at(r, fr, fa, p); p.lamp(x, z, kind)



def arc_run(p, r, a0, a1, step, fn):
    """Call fn(x, z, angle) at points spaced about `step` cells along the arc of radius r from a0 to a1 (degrees)."""
    n = max(1, int(abs(a1 - a0) * math.pi / 180.0 * r / step))
    seen = set()
    for k in range(n + 1):
        a = a0 + (a1 - a0) * k / n
        x, z = pol(r, a)
        if (x, z) in seen: continue
        seen.add((x, z)); fn(x, z, a)


def ray_run(p, a, r0, r1, step, side, fn):
    """Points along a radial wall at angle a from r0 to r1, `side` cells to its left (+) or right (-)."""
    ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
    n = max(1, int((r1 - r0) / step))
    seen = set()
    for k in range(n + 1):
        r = r0 + (r1 - r0) * k / n
        x, z = int(math.floor(r * ca - side * sa + 0.5)), int(math.floor(r * sa + side * ca + 0.5))
        if (x, z) in seen: continue
        seen.add((x, z)); fn(x, z, a)


def room_edges(p, room, fn_wall, fn_outer=None, step=1.0, a_margin=2.4, r0=11.5):
    """Furniture along the two radial walls of a sector room (and the outer wall if fn_outer is given)."""
    for k, side in ((0, 1.2), (1, -1.2)):
        a = room['a0'] if k == 0 else (room['a0'] + room['span'])
        ray_run(p, a, r0, p.R - 1.3, step, side, fn_wall)
    if fn_outer:
        w = a_margin / (p.R * math.pi / 180.0)
        arc_run(p, p.R - 0.9, room['a0'] + w, room['a0'] + room['span'] - w, step, fn_outer)


def mk(p, *fs):
    """fn(x, z, a) placing the callables fs(p, x, z) in rotation (a failed placement just leaves a gap)."""
    st = [0]
    def fn(x, z, a):
        fs[st[0] % len(fs)](p, x, z); st[0] += 1
    return fn


def f_shelf(p, x, z): shelf(p, x, z, 3)
def f_barrel(p, x, z): barrel(p, x, z)
def f_chest(p, x, z): chest(p, x, z, fin(x, z))
def f_plant(p, x, z): planter(p, x, z, 'potted_fern')
def f_bench(p, x, z): bench_lab(p, x, z, fin(x, z), 1, p.rng)
def f_cab(p, x, z):
    if p.free([(x, z)]):
        for y in range(p.W, p.W + 3): p.set(x, y, z, P.METAL if y != p.W + 1 else 'redstone_lamp[lit=true]')
def f_data(p, x, z): column_data(p, x, z, 4, 'white')
def f_case(p, x, z): case(p, x, z, 'verdant_froglight')


def seat_desk(p, x, z, face=None, n=3, **kw):
    """Desk whose user sits on the `face` side's opposite... i.e. a desk facing `face` with a chair one cell behind."""
    face = face or fin(x, z)
    if not desk(p, x, z, face, n, **kw): return False
    dx, dz = B.DIRS[opp(face)]
    chair(p, x + dx * 2, z + dz * 2, face)
    return True


# ================================================================================================================
# 23  Command Offices
# ================================================================================================================
def floor_offices(p):
    p.style.update(wall=P.WHITE_BLOCK, wall2=P.PALE_GREY, desk=P.WHITE_SLAB)
    halo(p, P.CYAN, P.DARK)
    ring_inlay(p, 9.0, 9.9, P.PALE_GREY)
    rooms = ring_rooms(p, [-30, 30, 90, 150, 210, 270], inner='panel', radial='solid', block=P.WHITE_BLOCK,
                       names=['briefing room', "commander's office", 'staff office A', 'staff office B',
                              'staff office C', 'secretary and waiting'], glass='white_stained_glass_pane')
    floor_inlay(p, rooms, (P.LIGHT_BLUE, P.DARK_PRISMARINE, P.PALE_GREY, P.PALE_GREY, P.PALE_GREY, P.WHITE_TERRACOTTA))
    # briefing room: round table with ten seats, lit centre
    x, z = pol(14.2, rooms[0]['ac'])
    table_round(p, x, z, 2.7, seats=10, seat_mat='dark_prismarine', lamp=True)
    for fa in (-0.95, 0.95):
        x, z = at(rooms[0], 0.97, fa, p); column_data(p, x, z, 4, 'white')
    # commander's office: big desk and chair, sofa corner, shelves, planters
    co = rooms[1]
    x, z = at(co, 0.62, 0, p)
    seat_desk(p, x, z, fin(x, z), 5, top=P.DARK_PRISMARINE)
    x, z = at(co, 0.25, 0, p); c = (x, z)
    table_round(p, x, z, 1.0, top='smooth_quartz_slab')
    for fa in (-0.5, 0.5):
        x, z = at(co, 0.25, fa * 1.2, p); chair(p, x, z, toward(x, z, *c), 'dark_prismarine')
    for fa in (-0.8, 0.8):
        x, z = at(co, 0.97, fa, p); shelf(p, x, z, 3)
        x, z = at(co, 0.1, fa, p); planter(p, x, z, 'potted_azalea_bush')
    # staff offices
    for r in rooms[2:5]:
        for fr, fa in ((0.35, -0.5), (0.35, 0.5), (0.8, -0.5), (0.8, 0.5)):
            x, z = at(r, fr, fa, p); seat_desk(p, x, z)
        x, z = at(r, 0.97, 0.0, p); shelf(p, x, z, 3)
        x, z = at(r, 0.97, 0.45, p); barrel(p, x, z)
        x, z = at(r, 0.1, 0.9, p); planter(p, x, z)
    # secretary and waiting area
    sw = rooms[5]
    x, z = at(sw, 0.4, 0, p); seat_desk(p, x, z, fout(x, z), 4)
    for fa in (-0.8, 0.0, 0.8):
        x, z = at(sw, 0.95, fa, p); chair(p, x, z, fin(x, z))
    x, z = at(sw, 0.75, 0.0, p); table_round(p, x, z, 0.7, top='smooth_quartz_slab')
    x, z = at(sw, 0.15, 0.8, p); planter(p, x, z)
    for r in rooms[2:5]: room_edges(p, r, mk(p, f_shelf, f_barrel, f_chest), mk(p, f_shelf, f_plant, f_barrel), 1.5)
    room_edges(p, rooms[1], mk(p, f_shelf, f_plant), mk(p, f_shelf, f_plant), 2.0)
    room_edges(p, rooms[0], mk(p, f_data, f_plant), None, 2.5)
    ring_lights(p, 'white', 8.0, 8)
    lamp_room(p, rooms, 'white')


# ================================================================================================================
# 29  Science Lab I
# ================================================================================================================
def floor_lab1(p):
    p.style.update(wall=P.WHITE_CONCRETE, wall2=P.PALE_GREY)
    halo(p, P.LIGHT_BLUE, P.DARK_BRICKS)
    rooms = ring_rooms(p, [-45, 45, 135, 225], inner='glass', radial='glass', block=P.WHITE_CONCRETE,
                       names=['analysis bay', 'bench lab', 'specimen lab', 'artifact workshop'],
                       glass='cyan_stained_glass_pane')
    floor_inlay(p, rooms, (P.PALE_GREY, P.WHITE, P.PALE_GREY, P.WHITE), 5)
    ab = rooms[0]                                           # analysis bay: hologram disc ringed by consoles
    x, z = at(ab, 0.5, 0, p); map_table(p, x, z, 2.2, glass=P.GLASS_CYAN)
    for fa in (-0.9, -0.45, 0.45, 0.9):
        x, z = at(ab, 0.95, fa, p); console(p, x, z, fin(x, z), 3)
    bl = rooms[1]                                           # bench lab
    for fa in (-0.65, 0.0, 0.65):
        for fr in (0.3, 0.7):
            x, z = at(bl, fr, fa, p); bench_lab(p, x, z, fin(x, z), 3, p.rng)
    for fa in (-0.8, 0.8):
        x, z = at(bl, 0.97, fa, p); shelf(p, x, z, 3)
        x, z = at(bl, 0.97, fa * 0.5, p); barrel(p, x, z)
    sp = rooms[2]                                           # specimen lab
    for k in range(6):
        x, z = at(sp, 0.95, -0.85 + 0.34 * k, p); case(p, x, z, 'verdant_froglight' if k % 2 else 'pearlescent_froglight')
    for fa in (-0.5, 0.5):
        x, z = at(sp, 0.5, fa, p); bench_lab(p, x, z, fin(x, z), 3, p.rng, ['cauldron', 'brewing_stand', 'lantern[hanging=false]'])
    x, z = at(sp, 0.25, 0, p); seat_desk(p, x, z, fout(x, z), 3)
    aw = rooms[3]                                           # artifact workshop
    x, z = at(aw, 0.55, 0, p); table_round(p, x, z, 1.6, top='smooth_stone_slab', lamp=True)
    for fa in (-0.8, 0.8):
        x, z = at(aw, 0.95, fa, p); column_data(p, x, z, 4, 'white')
        x, z = at(aw, 0.3, fa, p); bench_lab(p, x, z, fin(x, z), 2, p.rng)
    for r in rooms: room_edges(p, r, mk(p, f_bench, f_bench, f_case), mk(p, f_bench, f_shelf, f_bench), 1.0)
    ring_lights(p, 'cool', 8.0, 8)
    lamp_room(p, rooms, 'cool')


# ================================================================================================================
# 35  Science Lab II: energy research, one open hall with a glass crystal test bay
# ================================================================================================================
def floor_lab2(p):
    W = p.W
    p.style.update(wall=P.DARK_PRISMARINE)
    halo(p, P.CYAN, P.DARK)
    for k in range(8):                                      # conduits in the floor
        a = 22.5 + 45 * k
        for x, z, r, aa in p.cells:
            if r > 7.6 and abs(angd(aa, a)) * math.pi / 180 * r < 0.6:
                p.inlay(x, z, P.CYAN if int(r) % 3 else P.COPPER_CUT)
    ring_inlay(p, 12.0, 12.7, P.DARK)
    ring_inlay(p, 12.7, 13.4, P.COPPER_CUT)
    a0, a1 = -26, 26                                        # crystal test bay (east): a glass cell, doors at both ends
    g = 'cyan_stained_glass_pane'
    p.arc_wall(15.2, a0, a1, style='glass', block=P.DARK_PRISMARINE, glass=g, gaps=[(a1 - 4, 1.5), (a0 + 4, 1.5)])
    p.ray_wall(a0, 10.5, 15.2, style='glass', block=P.DARK_PRISMARINE, glass=g)
    p.ray_wall(a1, 10.5, 15.2, style='glass', block=P.DARK_PRISMARINE, glass=g)
    p.arc_wall(10.4, a0, a1, style='glass', block=P.DARK_PRISMARINE, glass=g, gaps=[(0, 1.5)])
    p.room('crystal test bay', 0, 10.4, 12.7)
    cx, cz = pol(13.2, 0)
    p.set(cx, W, cz, P.COPPER_CUT); p.set(cx, W + 1, cz, 'ochre_froglight'); p.set(cx, W + 2, cz, P.GLASS)
    p.set(cx, W + 3, cz, P.COPPER_CUT)
    for dx, dz in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
        p.set(cx + dx, W, cz + dz, P.DARK_PRISMARINE); p.set(cx + dx, W + 1, cz + dz, 'end_rod[facing=up]')
    p.rooms[-1]['centre'] = pol(11.8, 0)
    for a in (65, 115, 160, 200, 245, 295):                 # reactor stacks
        x, z = pol(13.6, a)
        cells = [(x + dx, z + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
        if not p.free(cells): continue
        for cx_, cz_ in cells:
            for y in range(W, W + 3): p.set(cx_, y, cz_, P.COPPER_CUT)
        for y in range(W, W + 4): p.set(x, y, z, LAMP if y == W + 2 else P.GLASS_CYAN)
        p.set(x, W + 4, z, P.COPPER_CUT, occ=False)
    for a in (80, 100, 135, 180, 225, 260, 280):            # test consoles at the wall
        x, z = pol(p.R - 1.2, a); console(p, x, z, fin(x, z), 3)
    for a in (40, 320):
        x, z = pol(12.0, a); bench_lab(p, x, z, fin(x, z), 3, p.rng, ['brewing_stand', 'lantern[hanging=false]', 'cauldron'])
    for k in range(8):
        x, z = pol(8.3, 22.5 + 45 * k); p.lamp(x, z, 'cool')
    for a in range(0, 360, 30):
        x, z = pol(12.0, a + 15); p.lamp(x, z, 'cool')
    for a in range(0, 360, 40):
        x, z = pol(p.R - 2.5, a + 10); p.lamp(x, z, 'cool')


# ================================================================================================================
# 41  Medical Annex
# ================================================================================================================
def floor_medical(p):
    W = p.W
    p.style.update(wall=P.WHITE_CONCRETE, wall2=P.WHITE)
    halo(p, P.WHITE, P.CYAN)
    rooms = ring_rooms(p, [-60, 30, 100, 160, 215], inner='solid', radial='glass', block=P.WHITE_CONCRETE,
                       names=['ward', 'scanner room', 'treatment room', 'supply room', 'nurses station'],
                       glass='white_stained_glass_pane')
    floor_inlay(p, rooms, (P.WHITE, P.LIGHT_BLUE, P.WHITE, P.PALE_GREY, P.WHITE))
    wd = rooms[0]                                           # ward: four beds with bedside lanterns
    for fa in (-0.8, -0.27, 0.27, 0.8):
        x, z = at(wd, 0.8, fa, p); face = fout(x, z)
        if bed(p, x, z, face, 'white'):
            px, pz = perp(face)
            bx, bz = x + px * 2, z + pz * 2
            if p.free([(bx, bz)]):
                p.set(bx, W, bz, P.WHITE_BLOCK); p.set(bx, W + 1, bz, 'lantern[hanging=false]')
    x, z = at(wd, 0.3, 0.0, p); table_round(p, x, z, 1.0, top='smooth_quartz_slab')
    sc = rooms[1]                                           # scanner room: bed in a glass arch, consoles
    x, z = at(sc, 0.65, 0, p)
    if bed(p, x, z, fout(x, z), 'light_blue'):
        face = fout(x, z); fx, fz = B.DIRS[face]; px, pz = perp(face)
        for k in (-1, 0):
            for s in (-2, 2):
                ax, az = x + px * s - fx * k, z + pz * s - fz * k
                if p.free([(ax, az)]):
                    for y in range(W, W + 3): p.set(ax, y, az, P.GLASS_CYAN if y > W else P.DARK_PRISMARINE)
        for s in (-1, 0, 1):
            ax, az = x + px * s - fx * 2, z + pz * s - fz * 2
            p.set(ax, W + 3, az, P.GLASS_CYAN, occ=False)
    for fa in (-0.8, 0.8):
        x, z = at(sc, 0.93, fa, p); console(p, x, z, fin(x, z), 3)
    tr = rooms[2]                                           # treatment room
    for fa in (-0.5, 0.5):
        x, z = at(tr, 0.6, fa, p); bed(p, x, z, fout(x, z), 'white')
    x, z = at(tr, 0.95, 0.0, p); console(p, x, z, fin(x, z), 3)
    x, z = at(tr, 0.3, 0.7, p); bench_lab(p, x, z, fin(x, z), 2, p.rng, ['brewing_stand', 'lantern[hanging=false]'])
    su = rooms[3]                                           # supply room
    for k in range(5):
        x, z = at(su, 0.96, -0.8 + 0.4 * k, p); shelf(p, x, z, 3, 'barrel[facing=up]' if k % 2 else 'bookshelf')
    for fa in (-0.6, 0.0, 0.6):
        x, z = at(su, 0.5, fa, p); chest(p, x, z, fin(x, z))
        x, z = at(su, 0.7, fa, p); barrel(p, x, z)
    ns = rooms[4]                                           # nurses station
    x, z = at(ns, 0.45, 0, p); seat_desk(p, x, z, fout(x, z), 5)
    for f in (-0.85, 0.0, 0.85):
        x, z = at(ns, 0.97, f, p); shelf(p, x, z, 3)
    for r in rooms: room_edges(p, r, mk(p, f_barrel, f_shelf, f_plant), None, 1.8)
    room_edges(p, rooms[3], mk(p, f_shelf, f_barrel), mk(p, f_shelf, f_barrel, f_chest), 1.0)
    ring_lights(p, 'white', 8.0, 8)
    lamp_room(p, rooms, 'white')


# ================================================================================================================
# 47  Archive & Database
# ================================================================================================================
def floor_archive(p):
    W = p.W
    p.style.update(wall=P.DARK_PRISMARINE, desk=P.WHITE_SLAB)
    halo(p, P.CYAN, P.DARK)
    ring_inlay(p, 10.5, 11.3, P.DARK)
    for r0 in (12.0, 14.2): ring_inlay(p, r0 + 0.6, r0 + 1.0, P.CYAN)
    aisles = list(range(0, 360, 45))
    for ri_, rr in enumerate((12.4, 14.6)):                 # two arcs of crystal data columns with eight aisles
        n = int(2 * math.pi * rr / 2.2)
        for k in range(n):
            a = 360.0 * k / n + ri_ * 4.0
            if any(abs(angd(a, s)) < 8.5 for s in aisles) or abs(angd(a, 270)) < 26: continue
            x, z = pol(rr, a); column_data(p, x, z, 4, 'white' if k % 3 == 0 else 'cool')
    for k in range(16):                                     # reading desks on the inner side
        a = 11.25 + 22.5 * k
        if any(abs(angd(a, s)) < 17 for s in aisles): continue
        x, z = pol(10.5, a)
        if desk(p, x, z, fout(x, z), 3):
            dx, dz = B.DIRS[fin(x, z)]; chair(p, x + dx, z + dz, fout(x, z))
    v0, v1 = -112, -68                                      # restricted vault (north): purple glass, amethyst crystal
    g = 'purple_stained_glass_pane'
    p.arc_wall(11.6, v0, v1, style='glass', block=P.DARK, glass=g, gaps=[(-90, 1.5)])
    p.ray_wall(v0, 11.6, p.R + 0.5, style='glass', block=P.DARK, glass=g)
    p.ray_wall(v1, 11.6, p.R + 0.5, style='glass', block=P.DARK, glass=g)
    x, z = pol(14.4, -90)
    p.set(x, W, z, P.DARK); p.set(x, W + 1, z, 'amethyst_block'); p.set(x, W + 2, z, 'amethyst_cluster[facing=up]')
    p.room('vault', 270, 11.6, 13.0)
    for s in aisles:                                        # terminals at the wall at the diagonal aisles
        if s % 90 == 45:
            x, z = pol(p.R - 1.4, s); console(p, x, z, fin(x, z), 3)
    ring_lights(p, 'cool', 8.3, 8)
    p.rooms.append({'name': 'reading hall', 'door': pol(9.5, 0), 'centre': pol(13.5, 0), 'door_ang': 0, 'door_r': 9.5})


# ================================================================================================================
# 53  Quarters
# ================================================================================================================
def floor_quarters(p):
    W = p.W
    p.style.update(wall=P.WHITE, wall2=P.PALE, desk=P.WHITE_SLAB)
    halo(p, P.FROG_WARM, P.PALE_GREY)
    rooms = ring_rooms(p, [-36, 36, 93.6, 151.2, 208.8, 266.4], inner='solid', radial='solid', block=P.PALE,
                       names=['lounge'] + [f'quarters {i}' for i in range(1, 6)])
    ring_inlay(p, 9.0, 9.9, P.PALE_GREY)
    lg = rooms[0]                                           # lounge: tables with seats, shelves, sofa
    for fa in (-0.55, 0.55):
        x, z = at(lg, 0.3, fa, p); table_round(p, x, z, 1.0, seats=4, seat_mat='quartz', lamp=True)
    for k in range(4):
        x, z = at(lg, 0.96, -0.75 + 0.5 * k, p); shelf(p, x, z, 3)
    c = at(lg, 0.3, 0, p)
    for k in (-1, 0, 1):
        x, z = at(lg, 0.72, k * 0.25, p); chair(p, x, z, toward(x, z, *c), 'quartz')
    for fa in (-0.95, 0.95):
        x, z = at(lg, 0.1, fa, p); planter(p, x, z)
    cols = ['light_blue', 'white', 'cyan', 'gray', 'blue']
    for i, r in enumerate(rooms[1:]):                       # bedrooms
        flip = -1 if i % 2 else 1
        x, z = at(r, 0.85, 0.35 * flip, p); face = fout(x, z)
        if bed(p, x, z, face, cols[i]):
            px, pz = perp(face)
            bx, bz = x + px * 2, z + pz * 2
            if p.free([(bx, bz)]):
                p.set(bx, W, bz, P.WHITE_BLOCK); p.set(bx, W + 1, bz, 'lantern[hanging=false]')
        x, z = at(r, 0.5, -0.7 * flip, p); seat_desk(p, x, z, fout(x, z), 2, lamp=False)
        x, z = at(r, 0.97, -0.6 * flip, p); chest(p, x, z, fin(x, z))
        x, z = at(r, 0.97, -0.9 * flip, p); shelf(p, x, z, 2, 'barrel[facing=up]')
        x, z = at(r, 0.2, 0.85 * flip, p); planter(p, x, z, 'potted_fern')
    for r in rooms[1:]: room_edges(p, r, mk(p, f_chest, f_shelf, f_plant), None, 2.2)
    room_edges(p, rooms[0], mk(p, f_shelf, f_plant), mk(p, f_shelf, f_plant), 1.5)
    lamp_room(p, rooms, 'warm', ((0.5, 0.0), (0.85, 0.0), (0.3, 0.0)))
    ring_lights(p, 'warm', 8.0, 8, hang=True)


# ================================================================================================================
# 59  Mess & Lounge
# ================================================================================================================
def floor_mess(p):
    W = p.W
    p.style.update(wall=P.PALE, wall2=P.WHITE, desk=P.WHITE_SLAB)
    halo(p, P.FROG_WARM, P.PALE_GREY)
    g0, g1 = 150.0, 232.0                                   # galley sector
    gc = (g0 + g1) / 2
    gaps = [(a, 3.0) for a in (20, 80, 290, 340)] + [(gc, 1.6)]
    p.arc_wall(10.0, 0, 360, style='panel', block=P.PALE, gaps=gaps)
    for a in (g0, g1): p.ray_wall(a, 9.6, p.R + 0.5, style='solid', block=P.PALE)
    p.room('dining hall', 20, 10.0, 13.0); p.room('galley', gc, 10.0, 13.0); p.room('lounge', 290, 10.0, 12.5)
    ring_inlay(p, 10.9, p.R - 0.5, P.PALE_GREY, g1 + 3, g0 + 360 - 3)
    ring_inlay(p, 10.9, p.R - 0.5, P.WHITE_TERRACOTTA, g0 + 3, g1 - 3)
    for rr, n in ((12.7, 6), (14.0, 7)):                    # dining: round tables with four seats
        for k in range(n):
            a = g1 + 14 + (360 - (g1 - g0) - 28) * (k + 0.5) / n + (3 if rr > 13 and k % 2 else 0)
            x, z = pol(rr, a); table_round(p, x, z, 1.1, seats=4, seat_mat='quartz', lamp=True)
    for x, z in p.sel(11.3, 12.4, g0 + 14, g1 - 14):        # galley: serving counter (open at the door), cooking line, stores
        if abs(angd(ang(x, z), gc)) < 9: continue
        p.set(x, W, z, B.slab('smooth_stone', 'top'))
    for k in range(8):
        x, z = pol(p.R - 1.0, g0 + 9 + 8 * k)
        blk = ('smoker[facing=%s,lit=false]', 'blast_furnace[facing=%s,lit=false]', 'cauldron',
               'furnace[facing=%s,lit=false]')[k % 4]
        if p.free([(x, z)]): p.set(x, W, z, blk % fin(x, z) if '%s' in blk else blk)
    for k in range(3):
        x, z = pol(p.R - 2.6, g0 + 16 + 22 * k); barrel(p, x, z)
        x, z = pol(13.8, g0 + 17 + 24 * k); bench_lab(p, x, z, fin(x, z), 3, p.rng, ['cauldron', 'lantern[hanging=false]', None, 'barrel[facing=up]'])
    for a in (280, 300):                                    # lounge corner
        x, z = pol(12.5, a); table_round(p, x, z, 1.0, seats=3, seat_mat='quartz', lamp=True)
    for k in range(5):
        x, z = pol(p.R - 1.0, 270 + 9 * k + 4)
        if k % 2 == 0: shelf(p, x, z, 2)
        else: planter(p, x, z, 'azalea')
    for a in range(0, 360, 30):
        for rr in (12.8, 14.2):
            if (a // 30) % 2 == (rr > 13): continue
            x, z = pol(rr, a + 7); p.lamp(x, z, 'warm')
    ring_lights(p, 'warm', 8.0, 8, hang=True)


# ================================================================================================================
# 65  Technical Floor
# ================================================================================================================
def floor_tech(p):
    W = p.W
    p.style.update(wall=P.DARK_PRISMARINE, wall2=P.METAL)
    halo(p, P.COPPER_CUT, P.DARK)
    ring_inlay(p, 9.0, 9.6, P.DARK_TILES)
    rooms = ring_rooms(p, [-30, 30, 150, 210], inner='solid', radial='glass', block=P.DARK_PRISMARINE,
                       names=['reactor bay', 'switchgear bay', 'plant room', 'maintenance workshop'],
                       glass='gray_stained_glass_pane')
    rb = rooms[0]                                           # reactor bay: two glowing core stacks
    for k in (0, 1):
        cx, cz = at(rb, 0.35 + 0.4 * k, 0, p)
        cells = [(cx + dx, cz + dz) for dx in (-1, 0, 1) for dz in (-1, 0, 1)]
        if not p.free(cells): continue
        for (x, z) in cells:
            for y in range(W, W + 4): p.set(x, y, z, P.COPPER_CUT)
            p.set(x, W + 4, z, P.METAL, occ=False)
        for y in range(W, W + 4): p.set(cx, y, cz, LAMP if y in (W + 1, W + 2) else P.GLASS_CYAN)
    for fa in (-0.8, 0.8):
        x, z = at(rb, 0.95, fa, p); console(p, x, z, fin(x, z), 3)
    sg = rooms[1]                                           # switchgear: iron cabinets with indicator lamps
    for fa in (-0.75, -0.25, 0.25, 0.75):
        for fr in (0.4, 0.9):
            x, z = at(sg, fr, fa, p)
            if p.free([(x, z)]):
                for y in range(W, W + 3): p.set(x, y, z, P.METAL if y != W + 1 else 'redstone_lamp[lit=true]')
    pr = rooms[2]                                           # plant room: pump blocks, conduits
    for fa in (-0.6, 0.0, 0.6):
        x, z = at(pr, 0.6, fa, p)
        cells = [(x + dx, z + dz) for dx in (0, 1) for dz in (0, 1)]
        if p.free(cells):
            for cx, cz in cells:
                p.set(cx, W, cz, P.COPPER_CUT); p.set(cx, W + 1, cz, P.COPPER_CUT)
            p.set(x, W + 2, z, 'lightning_rod[facing=up]')
        x, z = at(pr, 0.95, fa, p); shelf(p, x, z, 4, P.COPPER_GRATE)
    mw = rooms[3]                                           # workshop
    for fa in (-0.6, 0.0, 0.6):
        x, z = at(mw, 0.4, fa, p)
        bench_lab(p, x, z, fin(x, z), 3, p.rng, ['grindstone[face=floor,facing=north]', 'lantern[hanging=false]', None, 'cauldron'])
        x, z = at(mw, 0.95, fa, p); barrel(p, x, z)
        x, z = at(mw, 0.95, fa + 0.25, p); chest(p, x, z, fin(x, z))
    for r in rooms: room_edges(p, r, mk(p, f_cab, f_cab, f_barrel), mk(p, f_cab, f_cab, f_cab, f_barrel), 1.0)
    ring_lights(p, 'cool', 8.0, 8)
    lamp_room(p, rooms, 'cool')


# ================================================================================================================
# 71  Observation Ring
# ================================================================================================================
def floor_observation(p):
    W = p.W
    p.style.update(wall=P.WHITE, desk=P.WHITE_SLAB)
    halo(p, P.CYAN, P.WHITE_BRICKS)
    ring_inlay(p, 11.5, 12.5, P.CYAN)
    ring_inlay(p, 12.5, p.R - 0.4, P.WHITE_BRICKS)
    for ri_, rr in enumerate((18.6, 16.4)):                 # two rows of seats facing out to the glass
        n = int(2 * math.pi * rr / 1.6)
        for k in range(n):
            a = 360.0 * k / n
            if any(abs(angd(a, s)) < 10 + 2 * ri_ for s in range(22, 360, 45)): continue
            x, z = pol(rr, a); chair(p, x, z, fout(x, z), 'quartz' if ri_ == 0 else QS)
    for k in range(8):                                      # low tables on the promenade, one per aisle gap
        x, z = pol(13.6, 45 * k); table_round(p, x, z, 0.6, top='smooth_quartz_slab', lamp=True)
    for s in range(22, 360, 45):
        x, z = pol(20.2, s)
        if p.free([(x, z)]):
            for y in range(W, W + 2): p.set(x, y, z, P.WHITE_PILLAR)
            p.set(x, W + 2, z, 'lantern[hanging=false]')
    for s in range(0, 360, 90):                             # telescopes at the glass
        x, z = pol(p.R - 1.0, s + 67)
        if p.free([(x, z)]):
            p.set(x, W, z, P.WHITE_BLOCK); p.set(x, W + 1, z, P.WHITE_PILLAR); p.set(x, W + 2, z, 'end_rod[facing=up]')
    for s in range(11, 360, 45):
        x, z = pol(p.R - 1.0, s); planter(p, x, z, 'azalea')
    for a in (30, 120, 210, 300):                           # lounge ring round the core
        x, z = pol(9.2, a); table_round(p, x, z, 1.2, seats=4, lamp=True)
    for k in range(8):
        x, z = pol(8.2, 22.5 + 45 * k); p.hang(x, z)
    for k in range(12):
        x, z = pol(14.5, 15 + 30 * k); p.lamp(x, z, 'warm')
        x, z = pol(19.0, 30 * k); p.lamp(x, z, 'warm')
    p.rooms.append({'name': 'observation promenade', 'door': pol(11.0, 0), 'centre': pol(17.0, 67), 'door_ang': 0,
                    'door_r': 11.0})


# ================================================================================================================
# 77  Command Deck
# ================================================================================================================
def floor_deck(p):
    W = p.W
    p.style.update(wall=P.DARK_PRISMARINE)
    halo(p, P.CYAN, P.DARK)
    ring_inlay(p, 9.0, 9.8, P.DARK)
    ring_inlay(p, 18.8, 19.6, P.CYAN)
    tx, tz = pol(14.6, 270)                                 # the holographic map table (north side)
    for x, z, r, a in p.cells:
        d = math.hypot(x - tx, z - tz)
        if 4.2 < d <= 5.0: p.inlay(x, z, P.CYAN)
        elif 3.4 < d <= 4.2: p.inlay(x, z, P.DARK)
    map_table(p, tx, tz, 3.4)
    for k in range(8):                                      # stations round the table
        a = 22.5 + 45 * k
        x, z = tx + int(round(6.2 * math.cos(math.radians(a)))), tz + int(round(6.2 * math.sin(math.radians(a))))
        if math.hypot(x, z) < 10.5: continue
        if k % 2 or not console(p, x, z, toward(x, z, tx, tz), 3): column_data(p, x, z, 4, 'white' if k % 2 else 'cool')
    for x, z in p.sel(p.R - 1.0, p.R + 0.1):                # strategic screens on the south-east wall
        a = ang(x, z)
        if not 0 <= (a - 30) % 360 <= 100: continue
        p.set(x, W + 1, z, 'black_stained_glass_pane'); p.set(x, W + 2, z, 'blue_stained_glass_pane', occ=False)
        p.set(x, W, z, LAMP)
    for x, z in p.sel(14.5, 17.0, 70, 110):                 # commander's podium
        p.set(x, W, z, P.WHITE_BLOCK)
    for k in range(-1, 2):
        x, z = pol(15.8, 90 + 9 * k); p.set(x, W + 1, z, B.stairs(QS, fout(x, z)))
    for a in (170, 10):
        x, z = pol(17.5, a); console(p, x, z, fin(x, z), 3)
    for a in (200, 340):                                    # briefing tables
        x, z = pol(15.0, a); table_round(p, x, z, 1.6, seats=6, seat_mat='dark_prismarine', lamp=True)
    for k in range(8):
        x, z = pol(8.0, 22.5 + 45 * k); p.lamp(x, z, 'cool')
    for k in range(10):
        x, z = pol(15.0, 36 * k); p.lamp(x, z, 'cool')
        x, z = pol(19.8, 36 * k + 18); p.lamp(x, z, 'cool')
    p.rooms.append({'name': 'command deck', 'door': pol(8.5, 0), 'centre': pol(9.4, 200), 'door_ang': 0, 'door_r': 8.5})


# ================================================================================================================
# 83  Navigation Room
# ================================================================================================================
def floor_navigation(p):
    W = p.W
    p.style.update(wall=P.DARK_PRISMARINE)
    halo(p, P.DARK, P.DARK_PRISMARINE)
    chx, chz = pol(8.0, 0)
    for x, z, r, a in p.cells:                              # raised dais with the glowing ring (glass over lanterns)
        if abs(angd(a, 0)) <= 62 and r >= 6.9:
            ring = math.hypot(x - chx, z - chz)
            p.inlay(x, z, LAMP if 2.6 <= ring < 3.6 else P.DARK if ring < 2.6 else P.DARK_PRISMARINE)
            if ring < 3.6: p.set(x, W, z, P.GLASS_BLUE if ring >= 2.6 else P.WHITE_BLOCK, occ=False)
    p.set(chx, W + 1, chz, 'chiseled_quartz_block'); p.occ.add((chx, chz))      # orrery: a lit sun on a column
    p.set(chx, W + 2, chz, P.WHITE_PILLAR + '[axis=y]')
    p.set(chx, W + 3, chz, 'sea_lantern')
    p.set(chx, W + 4, chz, 'end_rod[facing=up]')
    for dx, dz in ((2, 0), (-2, 0), (0, 2), (0, -2)):                                  # planets on glass plinths
        p.set(chx + dx, W + 1, chz + dz, P.GLASS_BLUE); p.occ.add((chx + dx, chz + dz))
    for dz in (-1, 0, 1):
        for y in range(W + 1, W + 4): p.set(chx - 2, y, chz + dz, P.DARK if dz == 0 else P.DARK_PRISMARINE)
    for a in (-70, 70, 110, 250, -110):
        x, z = pol(p.R - 1.0, a); console(p, x, z, fin(x, z), 3)
    for a in (95, 140, 185, 230, 275, 62, -62):
        x, z = pol(8.0, a); p.hang(x, z, 'soul')
    p.rooms.append({'name': 'orrery dais', 'door': pol(7.5, 140), 'centre': pol(8.0, 160), 'door_ang': 140, 'door_r': 7.5})


def _stairs(mat, facing):
    return B.stairs(mat, facing)


# ================================================================================================================
# 89  Sensor & Comms Lab (a 2.7 block wide ring: only walkable and overhead fittings)
# ================================================================================================================
def floor_sensor(p):
    W = p.W
    p.style.update(wall=P.DARK_PRISMARINE)
    halo(p, P.CYAN, P.DARK)
    ring_inlay(p, 7.4, 8.0, P.DARK_PRISMARINE)
    ta = 90.0
    for x, z, r, a in p.cells:                              # transporter alcove at the south: lit pad, overhead frame
        s = abs(angd(a, ta)) * math.pi / 180 * r
        if s < 3.2: p.inlay(x, z, LAMP if r < 7.7 else P.CYAN)
        if 2.6 <= s < 3.4:
            p.set(x, W + 3, z, P.WHITE_BLOCK, occ=False); p.set(x, W + 4, z, P.WHITE, occ=False)
    for a in range(0, 360, 6):                              # wall terminals: walkable stairs and high screens
        x, z = pol(p.R - 0.3, a)
        if abs(angd(a, ta)) < 22 or p.get(x, W, z): continue
        p.set(x, W, z, _stairs('dark_prismarine', fin(x, z)), occ=False)
        p.set(x, W + 3, z, 'black_stained_glass_pane', occ=False); p.set(x, W + 4, z, 'blue_stained_glass_pane', occ=False)
    for k in range(12):                                     # hanging sensor pendants
        x, z = pol(7.4, 15 + 30 * k)
        p.set(x, p.top, z, LAMP, occ=False); p.set(x, p.top - 1, z, 'end_rod[facing=down]', occ=False)
        x, z = pol(8.4, 30 * k)
        p.set(x, p.top, z, 'chain[axis=y]', occ=False); p.set(x, p.top - 1, z, 'soul_lantern[hanging=true]', occ=False)
    p.rooms.append({'name': 'sensor ring', 'door': pol(7.5, 0), 'centre': pol(7.5, 180), 'door_ang': 0, 'door_r': 7.5})


FLOORS = {
    23: floor_offices, 29: floor_lab1, 35: floor_lab2, 41: floor_medical, 47: floor_archive, 53: floor_quarters,
    59: floor_mess, 65: floor_tech, 71: floor_observation, 77: floor_deck, 83: floor_navigation, 89: floor_sensor,
}
