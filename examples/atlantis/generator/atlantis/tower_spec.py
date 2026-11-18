"""Shared specification of the Central Tower (district A), used by tower.py (structure/exterior) and
tower_interior.py (rooms, furniture). Pure numbers and helpers, no blocks. Local coordinates, tower axis at (0, 0).

Vertical budget (overworld profile): stands on the deck (walk y 5); ctx.max_building_top(0, 0) is ~107, so the
spire tip is at TIP_Y <= 104. Everything reads ctx so a taller profile could rescale it later (SCALE).

Profile (outer radius by y): see TIERS. Floors every FLOOR_H blocks: FLOORS lists the floor-block y of each level
(walk at y + 1) with its name, programme and usable radius. The central CORE (r <= CORE_R) holds the spiral
staircase and a lift shaft (bubble column + soul sand up / magma block down) and is the same on every floor.
"""
import math

BASE_Y = 5                 # deck walk level = tower ground floor block y (the floor block replaces deck top + 1)
FLOOR_H = 6                # floor-to-floor height
CORE_R = 5                 # central core: spiral stair r 2..5 around a lift at the centre
WALL = 1                   # exterior wall thickness (glass/quartz), structure adds fins outside
TIP_Y = 104                # spire tip

# (y_from, y_to, r_bottom, r_top, kind): outer radius profile, linear within each tier
TIERS = [
    (5, 9, 34, 34, 'podium1'),        # podium terrace 1 (Grand Atrium level)
    (9, 13, 30, 30, 'podium2'),
    (13, 17, 26, 26, 'podium3'),
    (17, 71, 20, 16, 'shaft'),        # main shaft, slight taper
    (71, 83, 23, 23, 'crown'),        # control crown: observation ring + command deck (wide flare)
    (83, 95, 12, 10, 'upper'),        # upper shaft: navigation room, sensor lab
    (95, TIP_Y, 10, 0, 'spire'),      # spire
]

ENTRANCE_PLAN_ANGLES = (0, 60, 120, 180, 240, 300)   # ground entrances face the six grand avenues

# floor-block y -> (name, programme). Walk at y + 1. Interior agents give each a distinct design.
FLOORS = [
    (5, 'Grand Atrium', 'grand ground-floor atrium, 3 floors high (to y 17), reception, grand stair, '
                             'six entrance halls, Atlantean wall panels, central light well'),
    (17, 'Operations Hall', 'control room overlooking the atrium (mezzanine), consoles in arcs, big screen walls'),
    (23, 'Command Offices', "commander's office, briefing room with round table, staff offices"),
    (29, 'Science Lab I', 'Atlantean technology lab, workbenches, analysis consoles, specimen cases'),
    (35, 'Science Lab II', 'energy research lab, crystal test bay, conduits'),
    (41, 'Medical Annex', 'small infirmary, scanner bed, supply room'),
    (47, 'Archive & Database', 'Atlantean database: rows of crystal (glass/light) data columns, reading desks'),
    (53, 'Quarters', 'senior staff quarters, beds, lounge'),
    (59, 'Mess & Lounge', 'dining room with ocean view, galley'),
    (65, 'Technical Floor', 'machinery, power distribution, maintenance crawlways'),
    (71, 'Observation Ring', 'crown level: wide glass observation ring, balconies all round'),
    (77, 'Command Deck', 'crown upper level: strategic command, holographic map table'),
    (83, 'Navigation Room', 'a star-map orrery on a raised dais, glowing floor ring'),
    (89, 'Sensor & Comms Lab', 'long-range sensors, comms consoles, the transporter alcove'),
    (95, 'Summit Platform', 'open summit platform at the spire base, railings, beacon lights; spire above'),
]


def outer_r(y):
    """Outer radius of the tower body at local y (0 above TIP_Y or below BASE_Y)."""
    for y0, y1, r0, r1, _ in TIERS:
        if y0 <= y < y1:
            return r0 + (r1 - r0) * (y - y0) / max(1, y1 - y0)
    return 0.0


def tier(y):
    for t in TIERS:
        if t[0] <= y < t[1]: return t[4]
    return None


def usable_r(y):
    """Interior radius available for rooms on a floor whose walk level is y (inside the wall)."""
    top = y + FLOOR_H - 2                      # highest air cell of the floor (below the next floor block)
    return max(0.0, min(outer_r(y), outer_r(top) if top < TIP_Y else outer_r(y)) - WALL - 0.5)


def floor_list():
    """[(floor_y, walk_y, ceiling_y, name, programme, usable_radius)]. ceiling_y = next floor block y - 1."""
    out = []
    ys = [f[0] for f in FLOORS] + [TIP_Y]
    for i, (y, name, prog) in enumerate(FLOORS):
        nxt = ys[i + 1]
        out.append((y, y + 1, nxt - 1, name, prog, usable_r(y + 1)))
    return out


def plan_xz(r, plan):
    a = math.radians(plan)
    return r * math.sin(a), -r * math.cos(a)


def entrances():
    """Feet-cell positions just outside each ground entrance (walk y BASE_Y + 1), for validator meta."""
    return [tuple(int(round(v)) for v in (plan_xz(36, a)[0], BASE_Y + 1, plan_xz(36, a)[1]))
            for a in ENTRANCE_PLAN_ANGLES]


if __name__ == '__main__':
    for f in floor_list(): print(f[:4], round(f[5], 1))
    print('entrances', entrances())
