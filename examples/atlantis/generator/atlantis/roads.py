"""Layer 30: the street network, plazas and bridges, drawn ON the raised deck (top y = planner.DECK_TOP) and above it.

Model
  * `surface(x, z)` is the single source of truth: the paving of one column, or None when the column is not street.
    Priority: plaza core > grand avenue (+ tail + rim stairs) > plaza outer band > ring road > minor radial.
  * Paving features are 32x32 chunks aligned to the tiles (`roads_a_<cx>_<cz>`): every tile builds only its own chunk.
    Where the street leaves the deck (minor radials over the lagoons, the r 300 ring where it overhangs a lagoon) the
    chunk also builds the bridge: a 2-thick deck at y 3..4, glass rails and arched supports standing in the water down
    to the lagoon bed (y -4). Such chunks carry meta water_ok. Nothing but solid blocks is ever placed in the lagoon.
  * Props are small features: fountains (`roads_b_*`, water_ok, closed basins), pylons / benches / planters
    (`roads_c_*`). They sort after the paving (the orchestrator orders a layer by name).

Public API: RESERVED, FOUNTAINS, PROMENADE_R, surface(), street_kind(), is_street(), make_features(), features().

Deviation from the planner: the planner lists a ring at r 520, but pier tips end at r 540 so that circle is lagoon
for ~85 % of its length. The perimeter promenade path is drawn at PROMENADE_R (552), the middle of the perimeter deck.
The grand avenues are extended past the pier tip by a narrower 'tail' (width 7) to r 575; no rim stairs (the deck
ramps down by itself under the dome edge, fabric.deck_top_at).
"""
import math

from . import planner as PL, palette as P, blocks as B
from .feature import Feature, register
from . import fabric as _fabric

# ---------------------------------------------------------------- constants
LAYER = 30
PROMENADE_R = 552
TAIL_W = 3.5              # half widths
END_R = 575               # avenues / radials end here (the deck ramps down under the dome edge beyond r ~577)
RIM_R = END_R
ACC_TOP = 'cyan_stained_glass'
ACC_UNDER = 'sea_lantern'
CHUNK = 32

QUARTZ = 'smooth_quartz'
BRICKS = 'quartz_bricks'
DIORITE = 'polished_diorite'
ANDESITE = 'polished_andesite'
CYAN = 'cyan_concrete'
DARK = 'polished_deepslate'
PRIS = 'prismarine_bricks'
MOSS = 'moss_block'
PILLAR = 'quartz_pillar[axis=y]'

_gx, _gz = PL.from_polar(205, 0)
PLAZAS = [dict(p, x=int(round(p['x'])), z=int(round(p['z']))) for p in PL.plazas()]
_GATE = [p for p in PLAZAS if p['name'] == 'Fountain Plaza'][0]
_TOWER = PLAZAS[0]

#: reserved cores: later waves put the tower / the rings dais here; roads draw only flat paving inside them.
RESERVED = [{'name': 'Tower footprint', 'x': 0, 'z': 0, 'r': 34},
            {'name': 'Rings dais', 'x': _GATE['x'], 'z': _GATE['z'], 'r': 16}]

FOUNTAINS = []            # filled by _build_props: dicts {name, x, z, rw, bbox}

_hyp, _atan2, _floor, _sqrt = math.hypot, math.atan2, math.floor, math.sqrt
_ang_diff, _nearest, _plan_angle, _axis = PL.ang_diff, PL.nearest, PL.plan_angle, PL.axis_coords


# ---------------------------------------------------------------- pattern helpers
def _acc(dx, dz, pa, r, rad, n, base=0.0):
    """True if column (dx, dz) (offsets from the plaza centre; pa = plan angle, r = radius) is the cell nearest to one of
    n evenly spaced accent points on the circle of radius `rad`; the first point sits at plan angle `base`."""
    if abs(r - rad) >= 1.3: return False
    step = 360.0 / n
    k = round((pa - base) / step)
    ix, iz = PL.from_polar(rad, base + k * step)
    return dx == int(round(ix)) and dz == int(round(iz))


def _star(pa, tip, valley):
    """Radius of the six-pointed Atlantean star at plan angle pa (tips on the pier axes)."""
    t = abs(_ang_diff(pa, _nearest(PL.PIER_ANGLES, pa))) / 30.0
    return tip + (valley - tip) * t


# ---------------------------------------------------------------- plaza floors: (top, under) per column
def _floor_tower(dx, dz, r, pa):
    if r >= 68: return ANDESITE, None
    if r >= 66.5: return CYAN, None
    if (_acc(dx, dz, pa, r, 61.5, 24, 15) or _acc(dx, dz, pa, r, 44, 18, 0) or _acc(dx, dz, pa, r, 24, 12, 0) or _acc(dx, dz, pa, r, 29.5, 18, 10)
            or _acc(dx, dz, pa, r, 53, 18, 0) or _acc(dx, dz, pa, r, 6.5, 5, 0) or _acc(dx, dz, pa, r, 15, 8, 20) or _acc(dx, dz, pa, r, 66, 24, 7.5)
            or _acc(dx, dz, pa, r, 57, 6, 0) or _acc(dx, dz, pa, r, 47, 12, 15)):
        return ACC_TOP, ACC_UNDER
    if r < 34:
        if r >= 32.5: return CYAN, None
        if r < 5: return DARK, None
        return (QUARTZ if int(r // 3) % 2 == 0 else DIORITE), None
    rs = _star(pa, 58, 40)
    if abs(r - rs) < 0.8: return CYAN, None
    if r < rs:
        return (BRICKS if r > 36 and r < 38 else QUARTZ), None
    if abs(r - 63.5) < 0.5: return ANDESITE, None
    k = int(pa // 15)
    return (DIORITE if (k + int((r - 40) // 8)) % 2 == 0 else QUARTZ), None


def _floor_gate(dx, dz, r, pa):
    if (_acc(dx, dz, pa, r, 12, 8, 0) or _acc(dx, dz, pa, r, 20, 9, 20) or _acc(dx, dz, pa, r, 29.5, 18, 0) or _acc(dx, dz, pa, r, 38, 18, 10)
            or _acc(dx, dz, pa, r, 34, 9, 0) or _acc(dx, dz, pa, r, 42.2, 18, 5)):
        return ACC_TOP, ACC_UNDER
    if r >= 43: return ANDESITE, None
    if r >= 41.5: return CYAN, None
    if r < 16:
        if r >= 14: return CYAN, None
        return DARK, None
    k = int(pa // 40)
    edge = min(pa % 40, 40 - pa % 40) * 0.0174533 * r
    if edge < 0.6 and 18 <= r <= 38: return CYAN, None
    if abs(r - 24.5) < 0.5: return ANDESITE, None
    if abs(r - 33.5) < 0.5: return BRICKS, None
    return (QUARTZ if (k + int((r - 16) // 8)) % 2 == 0 else DIORITE), None


def _floor_pier(p):
    a = int(p['name'][-3:])

    def fn(dx, dz, r, pa):
        if r >= 20.5: return ANDESITE, None
        if r >= 19.5: return CYAN, None
        if _acc(dx, dz, pa, r, 15, 6, a) or _acc(dx, dz, pa, r, 10.5, 6, a + 30) or _acc(dx, dz, pa, r, 17.8, 12, a + 15):
            return ACC_TOP, ACC_UNDER
        if r < 5.5: return PRIS, None
        rs = _star(pa, 18, 10)
        if abs(r - rs) < 0.8: return CYAN, None
        if r < rs: return QUARTZ, None
        return DIORITE, None
    return fn


def _floor_civic(dx, dz, r, pa):
    if r >= 30.5: return ANDESITE, None
    if r >= 29: return CYAN, None
    if r < 7.5: return PRIS, None
    gx, gz = dx % 8, dz % 8
    ti = (dx // 8 + dz // 8) % 2
    if gx == 0 and gz == 0 and (dx // 8 + dz // 8) % 2 == 0: return ACC_TOP, ACC_UNDER
    if _acc(dx, dz, pa, r, 11.5, 6, 0) or _acc(dx, dz, pa, r, 25.5, 12, 8): return ACC_TOP, ACC_UNDER
    if (gx == 0 or gz == 0) and r > 9: return CYAN, None
    return (QUARTZ if ti == 0 else DIORITE), None


def _floor_botanical(dx, dz, r, pa):
    if _acc(dx, dz, pa, r, 14.5, 8, 22.5) or _acc(dx, dz, pa, r, 24.5, 8, 0) or _acc(dx, dz, pa, r, 9.5, 4, 45) or _acc(dx, dz, pa, r, 20, 8, 22.5) or _acc(dx, dz, pa, r, 27.8, 12, 15):
        return ACC_TOP, ACC_UNDER
    if r >= 28.5: return ANDESITE, None
    if r >= 27.5: return CYAN, None
    if r < 7: return PRIS, None
    if 13 <= r < 16:
        return (CYAN if abs(r - 14.5) < 0.5 else DIORITE), None
    off = (pa % 45.0)
    off = min(off, 45.0 - off) * 0.0174533 * r
    if r > 7 and off < 1.6: return QUARTZ, None
    return MOSS, None


_FLOORS = {'Tower Plaza': _floor_tower, 'Fountain Plaza': _floor_gate, 'Civic Square': _floor_civic,
           'Botanical Court': _floor_botanical}
for _p in PLAZAS:
    _p['fn'] = _FLOORS.get(_p['name']) or _floor_pier(_p)
    _p['r2'] = _p['r'] ** 2
    _p['core'] = (_p['r'] - 5) ** 2


# ---------------------------------------------------------------- road paving
def _avenue_hw(s):
    """Half width of a grand avenue at `along` = s: 5.5 (11 wide), flaring subtly towards the tower."""
    if s >= 140: return 5.5
    return 5.5 + 2.0 * (140 - s) / 106.0 if s > 34 else 7.5


def _paving_avenue(s, c, hw):
    ac = abs(c)
    edge = hw - ac
    fl = hw - 5.5
    if edge < 1: return ANDESITE, None
    ph = s % 12
    if ac < 0.5 and 6 <= ph < 7: return ACC_TOP, ACC_UNDER
    if 2.5 <= ac < 3.5 and ph < 1: return ACC_TOP, ACC_UNDER
    if ac <= 1.5: return QUARTZ, None
    if ac <= 2.5: return CYAN, None
    if fl > 0.5 and abs(ac - (hw - 1.5)) < 0.5: return CYAN, None
    if edge < 2: return BRICKS, None
    if s % 24 < 2: return QUARTZ, None
    return DIORITE, None


def _paving_tail(s, c, hw):
    ac = abs(c)
    if hw - ac < 1: return ANDESITE, None
    ph = s % 12
    if ac < 0.5 and 4 <= ph < 5: return ACC_TOP, ACC_UNDER
    if ac <= 0.5: return CYAN, None
    return (QUARTZ if ac <= 1.5 else DIORITE), None


def _paving_ring(R, w, r, th):
    d = abs(r - R)
    hw = w / 2.0
    s = R * (th % 6.283185307)
    si = int(s)
    if d > hw - 1: return ANDESITE, None
    if w <= 5:
        if d <= 0.5:
            if si % 8 < 1: return ACC_TOP, ACC_UNDER
            return CYAN, None
        return QUARTZ, None
    if 1.5 <= d < 2.5 and si % 12 < 1: return ACC_TOP, ACC_UNDER
    if d <= 0.5: return CYAN, None
    if si % 24 < 2: return QUARTZ, None
    return DIORITE, None


def _paving_radial(s, c, hw, water):
    ac = abs(c)
    if hw - ac < 1: return (QUARTZ if water else ANDESITE), None
    if ac <= 0.5:
        if int(s) % 8 < 1: return ACC_TOP, ACC_UNDER
        return CYAN, None
    if int(s) % 8 == 4 and ac < 1.6 and water: return ACC_TOP, ACC_UNDER
    return (QUARTZ if water else (BRICKS if int(s) % 8 < 1 else DIORITE)), None


def surface(x, z):
    """(kind, top, under, y) of the street paving in column (x, z), or None. kind: 'plaza' | 'avenue' | 'tail' |
    'stairs' | 'ring' | 'radial'. `top` goes at y (4, lowered by fabric.deck_top_at past r 566), `under` (accent lamp) at y - 1."""
    r = _hyp(x, z)
    if r > END_R + 1.5: return None
    band = None
    for p in PLAZAS:
        dx, dz = x - p['x'], z - p['z']
        d2 = dx * dx + dz * dz
        if d2 <= p['r2']:
            if d2 < p['core']:
                t, u = p['fn'](dx, dz, _sqrt(d2), _plan_angle(dx, dz))
                return 'plaza', t, u, 4
            band = (p, dx, dz, _sqrt(d2))
            break
    pa = _plan_angle(x, z)
    # ---- grand avenue (+ tail + rim stairs)
    a = _nearest(PL.PIER_ANGLES, pa)
    s, c = _axis(x, z, a)
    if s >= 34:
        if s <= PL.PIER_TIP_R:
            hw = _avenue_hw(s)
            if abs(c) <= hw:
                t, u = _paving_avenue(s, c, hw)
                return 'avenue', t, u, 4
        elif s <= END_R:
            if abs(c) <= TAIL_W:
                t, u = _paving_tail(s, c, TAIL_W)
                return 'tail', t, u, 4
    # ---- plaza outer band (below roads of any kind except where handled)
    ring = None
    for R, w in _RINGS:
        if abs(r - R) <= w / 2.0:
            ring = (R, w); break
    if ring is None and band is None:
        # ---- minor radial
        w = _nearest(PL.WEDGE_ANGLES, pa)
        s, c = _axis(x, z, w)
        if PL.CORE_R <= s <= END_R:
            water = not PL.has_deck(x, z)
            hw = 3.5 if water else 2.5
            if abs(c) <= hw:
                t, u = _paving_radial(s, c, hw, water)
                return 'radial', t, u, 4
        return None
    if ring is not None:
        t, u = _paving_ring(ring[0], ring[1], r, _atan2(z, x))
        return 'ring', t, u, 4
    p, dx, dz, d = band
    # radial streets also cross plaza bands (only the deck part matters here: plazas sit on the deck)
    t, u = p['fn'](dx, dz, d, _plan_angle(dx, dz))
    return 'plaza', t, u, 4


_RINGS = [(PL.CORE_R, PL.ROAD_RING), (PL.INNER_R, PL.ROAD_RING), (PROMENADE_R, PL.ROAD_MINOR)]


def street_kind(x, z):
    s = surface(x, z)
    return s[0] if s else None


def is_street(x, z, margin=0):
    """True if column (x, z) is taken by a road, plaza or bridge (or lies within `margin` blocks of one). Later
    generators must keep buildings off these columns."""
    if margin <= 0: return surface(x, z) is not None
    for dx in range(-margin, margin + 1):
        for dz in range(-margin, margin + 1):
            if surface(x + dx, z + dz) is not None: return True
    return False


# ---------------------------------------------------------------- bridges: rails and supports
def _bridge_cell(x, z):
    s = surface(x, z)
    return s is not None and s[0] in ('radial', 'ring') and not PL.has_deck(x, z)


def _rail_cell(x, z):
    """Edge of a bridge over the lagoon: a bridge column with a 4-neighbour that is neither street nor deck."""
    if not _bridge_cell(x, z): return False
    for nx, nz in ((x + 1, z), (x - 1, z), (x, z + 1), (x, z - 1)):
        if surface(nx, nz) is None and not PL.has_deck(nx, nz): return True
    return False


def _phase(x, z, kind):
    if kind == 'ring':
        R = 300
        return int(R * (_atan2(z, x) % 6.283185307))
    w = _nearest(PL.WEDGE_ANGLES, _plan_angle(x, z))
    return int(_axis(x, z, w)[0])


def _build_bridge_column(vol, ctx, x, z, kind):
    """Rails, posts and supports for one bridge column (the deck blocks are already placed)."""
    ph = _phase(x, z, kind)
    rail = _rail_cell(x, z)
    band = ph % 24 in (0, 1)
    if band:
        if rail:
            vol.fill(x, -4, z, x, 2, z, PILLAR)
        elif kind == 'radial':
            vol.set(x, 2, z, QUARTZ)
            # haunches of the arch under the deck edge
            if surface(x, z) and _rail_adjacent(x, z): vol.set(x, 1, z, QUARTZ)
    if not rail: return
    cap = ctx.max_building_top(x, z)
    if cap < 7: return
    if ph % 4 == 0:
        vol.set(x, 5, z, PILLAR); vol.set(x, 6, z, PILLAR)
        vol.set(x, 7, z, 'end_rod[facing=up]')
    else:
        n = _rail_cell(x, z - 1); e = _rail_cell(x + 1, z); s_ = _rail_cell(x, z + 1); w_ = _rail_cell(x - 1, z)
        vol.set(x, 5, z, B.pane('glass_pane', n, e, s_, w_))
        vol.set(x, 6, z, B.pane('cyan_stained_glass_pane', n, e, s_, w_))


def _rail_adjacent(x, z):
    return any(_rail_cell(x + dx, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))


_CLEAR = ('air', 'cave_air')


def _build_paving(vol, ctx, bbox):
    c = vol.clip(*bbox)
    if not c: return
    for z in range(c[2], c[5] + 1):
        for x in range(c[0], c[3] + 1):
            s = surface(x, z)
            if s is None: continue
            kind, top, under, y = s
            deck = PL.has_deck(x, z) if kind in ('radial', 'ring') else True
            if deck:
                y = 4
                if x * x + z * z > 566 * 566:          # perimeter ramp: the real deck top is lowered under the dome
                    y = _fabric.deck_top_at(ctx, x, z)
                    if y is None or y < 1: continue
                if under and y >= 2: vol.set(x, y - 1, z, under)
                elif under: top, under = (ANDESITE, None)[0], None
                vol.set(x, y, z, top)
                for yy in (5, 6, 7):                   # remove deck-edge railings (fabric) over the street
                    b = vol.get(x, yy, z)
                    if b is not None and not b.startswith('light') and b not in _CLEAR:
                        vol.set(x, yy, z, 'air')
            else:                                      # bridge over the lagoon
                vol.set(x, 3, z, under or BRICKS)
                vol.set(x, 4, z, top)
                _build_bridge_column(vol, ctx, x, z, kind)


class Paving(Feature):
    def __init__(self, cx, cz, water, clearance_ok):
        meta = {'kind': 'roads'}
        if water: meta['water_ok'] = True
        if clearance_ok: meta['clearance_ok'] = True
        super().__init__(f'roads_a_{cx // CHUNK}_{cz // CHUNK}', (cx, -4, cz, cx + CHUNK - 1, 7, cz + CHUNK - 1),
                         LAYER, meta)

    def build(self, vol, ctx):
        _build_paving(vol, ctx, self.bbox)


# ---------------------------------------------------------------- props
def _cardinal(ox, oz):
    if abs(ox) >= abs(oz): return 'east' if ox > 0 else 'west'
    return 'south' if oz > 0 else 'north'


def _plaza_cell_ok(x, z, kinds=('plaza',)):
    s = surface(x, z)
    if s is None or s[0] not in kinds: return False
    for R in RESERVED:
        if _hyp(x - R['x'], z - R['z']) < R['r'] + 1: return False
    return True


def _pylon_build(x, z, h, lantern_every=True):
    def fn(vol, ctx):
        top = min(4 + h, ctx.max_building_top(x, z))
        if top < 8: return
        vol.set(x, 5, z, 'quartz_block')
        for y in range(6, top - 1): vol.set(x, y, z, PILLAR)
        vol.set(x, top - 2, z, 'chiseled_quartz_block')
        vol.set(x, top - 1, z, ACC_UNDER)
        vol.set(x, top, z, 'end_rod[facing=up]')
    return fn


def _bench_build(x, z, facing):
    horiz = facing in ('north', 'south')
    cells = [(x + i, z) if horiz else (x, z + i) for i in (-1, 0, 1)]

    def fn(vol, ctx):
        for (cx, cz) in cells:
            vol.set(cx, 5, cz, B.stairs('quartz_stairs', facing))
    return fn, cells


def _planter_build(x, z, rad):
    cells = [(x + i, z + j) for i in range(-rad, rad + 1) for j in range(-rad, rad + 1)
             if i * i + j * j <= rad * rad + 1]

    def fn(vol, ctx):
        for (cx, cz) in cells:
            inner = (cx - x) ** 2 + (cz - z) ** 2 <= (rad - 1) ** 2 + 1 and abs(cx - x) < rad and abs(cz - z) < rad
            if inner:
                vol.set(cx, 4, cz, MOSS)
                vol.set(cx, 5, cz, MOSS)
                if (cx * 7 + cz * 13) % 3 != 0:
                    vol.set(cx, 6, cz, 'flowering_azalea_leaves[persistent=true]')
                else:
                    vol.set(cx, 6, cz, 'azalea_leaves[persistent=true]')
            else:
                vol.set(cx, 5, cz, 'smooth_quartz')
    return fn, cells


def _fountain_cells(rw):
    n = int(rw) + 1
    water = {(i, j) for i in range(-n, n + 1) for j in range(-n, n + 1) if i * i + j * j <= rw * rw}
    rim = set()
    for (i, j) in water:
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            q = (i + d[0], j + d[1])
            if q not in water: rim.add(q)
    return water, rim


def fountain_cells(x, z, rw):
    """(water_cells, rim_cells) of a fountain basin in absolute columns; the rim touches every 4-neighbour of the
    water, so the basin is closed on its sides (floor: y 3..4)."""
    w, r = _fountain_cells(rw)
    return {(x + i, z + j) for i, j in w}, {(x + i, z + j) for i, j in r}


class Fountain(Feature):
    def __init__(self, name, x, z, rw):
        n = int(rw) + 2
        super().__init__(f'roads_b_{name}', (x - n, 3, z - n, x + n, 10, z + n), LAYER,
                         {'kind': 'fountain', 'water_ok': True, 'fountain': (x, z, rw)})
        self.x, self.z, self.rw = x, z, rw

    def build(self, vol, ctx):
        water, rim = fountain_cells(self.x, self.z, self.rw)
        for (cx, cz) in rim:
            vol.set(cx, 3, cz, QUARTZ); vol.set(cx, 4, cz, QUARTZ)
            vol.set(cx, 5, cz, 'smooth_quartz')
        for (cx, cz) in water:
            vol.set(cx, 3, cz, QUARTZ); vol.set(cx, 4, cz, PRIS)
            vol.set(cx, 5, cz, 'water')
        if self.rw >= 2.5:
            for y in range(5, 9): vol.set(self.x, y, self.z, PILLAR)
            vol.set(self.x, 9, self.z, ACC_UNDER)
            vol.set(self.x, 10, self.z, 'end_rod[facing=up]')
        else:
            vol.set(self.x, 5, self.z, 'sea_lantern')


class Prop(Feature):
    def __init__(self, name, cells, fn, y2=14):
        xs = [c[0] for c in cells]; zs = [c[1] for c in cells]
        super().__init__(f'roads_c_{name}', (min(xs), 4, min(zs), max(xs), y2, max(zs)), LAYER,
                         {'kind': 'prop', 'clearance_ok': True})   # pylons clamp themselves to max_building_top
        self.fn = fn

    def build(self, vol, ctx):
        self.fn(vol, ctx)


def _at(p, rad, plan):
    x, z = PL.from_polar(rad, plan)
    return int(round(p['x'] + x)), int(round(p['z'] + z))


def _prop_features():
    out, fountains = [], []
    ids = set()

    def uid(base):
        n, k = base, 0
        while n in ids: k += 1; n = f'{base}_{k}'
        ids.add(n); return n

    def fountain(p, x, z, rw, tag):
        wc, rc = fountain_cells(x, z, rw)
        if not all(_plaza_cell_ok(cx, cz) for cx, cz in wc | rc): return
        f = Fountain(uid(f'fountain_{tag}_{x}_{z}'), x, z, rw)
        out.append(f); fountains.append({'name': f.name, 'x': x, 'z': z, 'rw': rw, 'bbox': f.bbox})

    def pylon(p, x, z, h, tag):
        if _plaza_cell_ok(x, z) and surface(x, z)[2] is None:
            out.append(Prop(uid(f'pylon_{tag}_{x}_{z}'), [(x, z)], _pylon_build(x, z, h)))

    def bench(p, rad, plan, tag):
        x, z = _at(p, rad, plan)
        ox, oz = PL.from_polar(1, plan)
        fn, cells = _bench_build(x, z, _cardinal(ox, oz))
        if all(_plaza_cell_ok(cx, cz) and surface(cx, cz)[2] is None for cx, cz in cells):
            out.append(Prop(uid(f'bench_{tag}_{x}_{z}'), cells, fn, 5))

    def planter(p, rad, plan, size, tag):
        x, z = _at(p, rad, plan)
        fn, cells = _planter_build(x, z, size)
        if all(_plaza_cell_ok(cx, cz) and surface(cx, cz)[2] is None for cx, cz in cells):
            out.append(Prop(uid(f'planter_{tag}_{x}_{z}'), cells, fn, 6))

    for p in PLAZAS:
        nm = p['name']
        if nm == 'Tower Plaza':
            for k in range(6):
                fx, fz = _at(p, 51, 60 * k + 30); fountain(p, fx, fz, 3.0, 'tw')
                for off in (-14, 14): bench(p, 60.5, 60 * k + 30 + off, 'tw')
                planter(p, 62, 60 * k + 30, 2, 'tw')
            for k in range(12):
                x, z = _at(p, 64.5, 15 + 30 * k); pylon(p, x, z, 9, 'tw')
        elif nm == 'Fountain Plaza':
            for plan in (90, 270):
                fx, fz = _at(p, 30, plan); fountain(p, fx, fz, 2.4, 'gt')
            for k in range(8):
                x, z = _at(p, 39.5, 22.5 + 45 * k); pylon(p, x, z, 8, 'gt')
            for k in range(4):
                bench(p, 36.5, 45 + 90 * k, 'gt'); planter(p, 26, 45 + 90 * k, 2, 'gt')
        elif nm.startswith('Pier-Root'):
            a = int(nm[-3:])
            fountain(p, p['x'], p['z'], 2.9, 'pr')
            for k in range(6):
                plan = a + 30 + 60 * k
                bench(p, 12.5, plan, 'pr'); planter(p, 16, plan, 2, 'pr')
                x, z = _at(p, 19.5, plan); pylon(p, x, z, 7, 'pr')
        elif nm == 'Civic Square':
            fountain(p, p['x'], p['z'], 5.4, 'cv')
            for k in range(8):
                bench(p, 22, 22.5 + 45 * k, 'cv')
            for k in range(4):
                planter(p, 17, 45 + 90 * k, 2, 'cv')
                x, z = _at(p, 27, 45 + 90 * k); pylon(p, x, z, 8, 'cv')
        elif nm == 'Botanical Court':
            fountain(p, p['x'], p['z'], 4.9, 'bt')
            for k in range(8):
                bench(p, 18, 22.5 + 45 * k, 'bt'); planter(p, 22.5, 22.5 + 45 * k, 2, 'bt')
                x, z = _at(p, 25.5, 45 * k); pylon(p, x, z, 8, 'bt')
    # ---- avenue lamp posts: ~24 apart, both sides, kept off the paving; grand pylons flank the pier tips
    for a in PL.PIER_ANGLES:
        s = 84.0
        while s <= 530:
            hw = _avenue_hw(s)
            for sgn in (-1, 1):
                ux, uz = PL.from_polar(1.0, a); vx, vz = -uz, ux
                px = ux * s + vx * sgn * (hw + 1.8); pz = uz * s + vz * sgn * (hw + 1.8)
                x, z = int(round(px)), int(round(pz))
                if (surface(x, z) is None and PL.has_deck(x, z) and surface(x + 1, z) is None
                        and surface(x - 1, z) is None and surface(x, z + 1) is None and surface(x, z - 1) is None):
                    out.append(Prop(uid(f'lamp_{a}_{int(s)}_{x}_{z}'), [(x, z)], _pylon_build(x, z, 7)))
            s += 24.0
        for sgn in (-1, 1):
            ux, uz = PL.from_polar(1.0, a); vx, vz = -uz, ux
            x = int(round(ux * 538 + vx * sgn * 7.5)); z = int(round(uz * 538 + vz * sgn * 7.5))
            if surface(x, z) is None and PL.has_deck(x, z):
                out.append(Prop(uid(f'tip_{a}_{x}_{z}'), [(x, z)], _pylon_build(x, z, 8)))
    FOUNTAINS[:] = fountains
    return out


# ---------------------------------------------------------------- feature list
def _chunk_relevant(cx, cz):
    """Conservative analytic test: may chunk (cx, cz) contain street columns? -> (relevant, water, clearance_ok)."""
    mx, mz = cx + CHUNK / 2.0, cz + CHUNK / 2.0
    d = 23.0
    r = _hyp(mx, mz)
    pa = _plan_angle(mx, mz)
    rel = water = False
    for p in PLAZAS:
        if _hyp(mx - p['x'], mz - p['z']) <= p['r'] + d: rel = True; break
    if not rel:
        for R, w in _RINGS:
            if abs(r - R) <= w / 2.0 + d: rel = True; break
    if not rel:
        a = _nearest(PL.PIER_ANGLES, pa)
        s, c = _axis(mx, mz, a)
        if 34 - d <= s <= END_R + d and abs(c) <= 8 + d: rel = True
    wd = _nearest(PL.WEDGE_ANGLES, pa)
    s, c = _axis(mx, mz, wd)
    if PL.CORE_R - d <= s <= END_R + d and abs(c) <= 3.5 + d:
        rel = True
        if PL.INNER_R - 6 - d <= s <= PL.PERIM_R0 + 6 + d: water = True
    if abs(r - PL.INNER_R) <= 6 + d: water = water or rel
    return rel, water, (r + d > 548)


def make_features(ctx=None):
    fs = []
    lim = 640
    for cx in range(-lim, lim, CHUNK):
        for cz in range(-lim, lim, CHUNK):
            rel, water, cok = _chunk_relevant(cx, cz)
            if rel: fs.append(Paving(cx, cz, water, cok))
    fs.extend(_prop_features())
    return fs


FEATURES = make_features()
register(*FEATURES)


def features():
    return list(FEATURES)
