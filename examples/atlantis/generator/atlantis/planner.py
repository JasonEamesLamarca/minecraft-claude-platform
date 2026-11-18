"""The city master plan as code: zones, districts, decks, avenues, rings, plazas.

Everything here is pure geometry (no blocks). Generators ask the planner where things are, so the layout lives in
one place. Plan angles: degrees clockwise from north (-z); 0 = north, 90 = east (see ARCHITECTURE.md).

Vertical convention for the city fabric (local y):
  DECK_TOP = 4      top block of the raised city deck (core, inner ring, piers, perimeter promenade);
                    players walk at y = DECK_TOP + 1. Buildings stand on the deck.
  LAGOON_SURFACE=0  water surface of the lagoons between the piers (water cells LAGOON_FLOOR+1 .. 0)
  LAGOON_FLOOR=-4   lagoon bed (sand / prismarine), on the foundation
  The original foundation floor (y 0) stays only under the decks.
"""
import math

DECK_TOP = 4
WALK_Y = DECK_TOP + 1
LAGOON_SURFACE = 0
LAGOON_FLOOR = -4

CORE_R = 130            # core (district A) radius
INNER_R = 300           # inner ring outer radius (pier roots)
PIER_TIP_R = 540        # pier tips
PERIM_R0 = 540          # perimeter promenade inner radius
PERIM_R1 = 588          # perimeter promenade outer radius (deck); beyond it, a 1-block kerb, then the plinth/shell
PIER_ANGLES = (0, 60, 120, 180, 240, 300)
WEDGE_ANGLES = (30, 90, 150, 210, 270, 330)      # lagoons, minor radials, airlock stations

ROAD_MAJOR = 11         # grand avenues along pier axes
ROAD_RING = 9           # ring roads r 130 and r 300
ROAD_MINOR = 5          # minor radials along wedge axes, perimeter promenade path
RINGS = ((CORE_R, ROAD_RING), (INNER_R, ROAD_RING), (520, ROAD_MINOR))

DISTRICTS = {
    # id: (name, zone, plan angle centre)
    'A': ('Central Tower Complex', 'core', None),
    'B': ('Fountain & Arrival Complex', 'inner', 0),
    'K': ('Command & Strategic Planning', 'inner', 60),
    'F': ('Medical & Life Sciences', 'inner', 120),
    'H': ('Commercial & Civic', 'inner', 180),
    'L': ('Education, Culture & Botanical', 'inner', 240),
    'W': ('Water Management & Hydroponics', 'inner', 300),
    'G': ('Transportation & Landing', 'pier', 0),
    'D': ('Science & Research', 'pier', 60),
    'C1': ('Residential: East Terraces', 'pier', 120),
    'C2': ('Residential: South Promenade', 'pier', 180),
    'E': ('Power & Engineering', 'pier', 240),
    'C3': ('Residential: West Gardens', 'pier', 300),
    'I': ('Ocean Interface & Research Perimeter', 'perimeter', None),
    'J': ('Old Ruins', 'exterior', 210),
}
_INNER_BY_ANGLE = {0: 'B', 60: 'K', 120: 'F', 180: 'H', 240: 'L', 300: 'W'}
_PIER_BY_ANGLE = {0: 'G', 60: 'D', 120: 'C1', 180: 'C2', 240: 'E', 300: 'C3'}


# ---------------------------------------------------------------- angles
def plan_angle(x, z):
    """Plan angle of (x, z) in degrees, 0 = north (-z), clockwise, in [0, 360)."""
    return math.degrees(math.atan2(x, -z)) % 360.0


def geom_angle(plan):
    """Plan angle -> geometry.py angle (0 = east, towards south)."""
    return (plan - 90.0) % 360.0


def from_polar(r, plan):
    """(x, z) of radius r at plan angle `plan`."""
    a = math.radians(plan)
    return r * math.sin(a), -r * math.cos(a)


def ang_diff(a, b):
    """Signed smallest difference a - b in degrees, in (-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


def nearest(angles, a):
    return min(angles, key=lambda c: abs(ang_diff(a, c)))


def axis_coords(x, z, plan):
    """(along, across): coordinates of (x, z) in the frame of the radial axis at plan angle `plan`.
    along = distance from the centre along the axis, across = signed perpendicular offset (+ = clockwise side)."""
    ux, uz = from_polar(1.0, plan)          # unit vector along the axis
    vx, vz = -uz, ux                        # clockwise-perpendicular
    return x * ux + z * uz, x * vx + z * vz


# ---------------------------------------------------------------- piers and zones
def pier_half_width(r):
    """Half width of a pier at radius r: 80 at the root (r 300), 45 at the tip (r 540)."""
    t = min(1.0, max(0.0, (r - INNER_R) / (PIER_TIP_R - INNER_R)))
    return 80.0 - 35.0 * t


def in_pier(x, z, plan=None):
    """Plan angle of the pier containing (x, z) (radially between INNER_R and PIER_TIP_R), else None."""
    r = math.hypot(x, z)
    if not (INNER_R <= r <= PIER_TIP_R): return None
    a = plan if plan is not None else nearest(PIER_ANGLES, plan_angle(x, z))
    along, across = axis_coords(x, z, a)
    return a if along > 0 and abs(across) <= pier_half_width(along) else None


def zone(x, z):
    """'core' | 'inner' | 'pier' | 'lagoon' | 'perimeter' | 'rim' (r 588..600, kerb/plinth) | 'exterior'."""
    r = math.hypot(x, z)
    if r < CORE_R: return 'core'
    if r < INNER_R: return 'inner'
    if r < PERIM_R0:
        return 'pier' if in_pier(x, z) is not None else 'lagoon'
    if r < PERIM_R1: return 'perimeter'
    if r < 600: return 'rim'
    return 'exterior'


def district_at(x, z):
    """District id at (x, z) or None (lagoon, rim, exterior)."""
    zn = zone(x, z)
    if zn == 'core': return 'A'
    if zn == 'inner': return _INNER_BY_ANGLE[nearest(PIER_ANGLES, plan_angle(x, z))]
    if zn == 'pier': return _PIER_BY_ANGLE[in_pier(x, z)]
    if zn == 'perimeter': return 'I'
    return None


def has_deck(x, z):
    """True where the raised city deck (top DECK_TOP) exists: core, inner ring, piers, perimeter promenade."""
    return zone(x, z) in ('core', 'inner', 'pier', 'perimeter')


def deck_top(x, z):
    return DECK_TOP if has_deck(x, z) else None


# ---------------------------------------------------------------- network
def avenues():
    """Grand avenues: list of dicts {plan, r0, r1, width}. Along each pier axis from the core edge to the pier tip."""
    return [{'plan': a, 'r0': 34, 'r1': PIER_TIP_R, 'width': ROAD_MAJOR} for a in PIER_ANGLES]


def minor_radials():
    """Minor radials along the wedge axes from the inner ring edge over the lagoon (bridge) to the perimeter airlocks."""
    return [{'plan': a, 'r0': CORE_R, 'r1': PERIM_R1, 'width': ROAD_MINOR,
             'bridge': (INNER_R, PERIM_R0)} for a in WEDGE_ANGLES]


def ring_roads():
    """[(radius, width)]: tower ring, pier-root ring, perimeter promenade path."""
    return list(RINGS)


def plazas():
    """Major plazas: list of dicts {name, x, z, r, district}. At least 8."""
    out = [{'name': 'Tower Plaza', 'x': 0, 'z': 0, 'r': 70, 'district': 'A'}]
    gx, gz = from_polar(205, 0)
    out.append({'name': 'Fountain Plaza', 'x': gx, 'z': gz, 'r': 45, 'district': 'B'})
    for a in PIER_ANGLES:
        x, z = from_polar(INNER_R, a)
        out.append({'name': f'Pier-Root Plaza {a:03d}', 'x': x, 'z': z, 'r': 22,
                    'district': _PIER_BY_ANGLE[a]})
    cx, cz = from_polar(215, 180)
    out.append({'name': 'Civic Square', 'x': cx, 'z': cz, 'r': 32, 'district': 'H'})
    bx, bz = from_polar(215, 240)
    out.append({'name': 'Botanical Court', 'x': bx, 'z': bz, 'r': 30, 'district': 'L'})
    return out


def airlocks():
    """Interior ends of the six station airlock tunnels (dome.py): a 3-wide, 2-high passage at y 1..2 that
    reaches the dome at r ~594 along each wedge axis. The perimeter deck must keep it reachable: an airlock hall
    (floor y 0, walk y 1) with stairs up to the deck. List of dicts {plan, x, z}."""
    return [dict(zip(('plan', 'x', 'z'), (a,) + from_polar(594, a))) for a in WEDGE_ANGLES]


def height_cap(ctx, x, z):
    """Max local y a building may reach at (x, z) (dome clearance minus margin)."""
    return ctx.max_building_top(x, z)


if __name__ == '__main__':
    for p in [(0, 0), (0, -200), (0, -400), (150, -400), (300, -300), (0, -560), (0, -595)]:
        print(p, zone(*p), district_at(*p), round(plan_angle(*p)))
    print(len(plazas()), 'plazas')
