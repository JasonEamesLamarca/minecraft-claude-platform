"""Layer 50: District B, the Fountain & Arrival Complex (inner ring, plan angle 0 = north, r 140..270).

Pieces (each a Feature with a tight bbox; every piece is first drawn once into a private Volume over its bbox, cached
per process, and the tile only copies its part, so panes connect identically in every tile):
  rings_dais            dais with Plato's rings (glowing glass bands) + ramps + the Trident Arch over the avenue + two
                        pylons, on the Fountain Plaza
  arrival_approach_s/n  ceremonial arches over the grand avenue, south (city side) and north of the plaza centre
  arrival_hall          Arrival & reception hall east of the plaza (3 doors, porch, desk, benches, back offices)
  checkpoint_w/e        two guard pavilions flanking the avenue where it meets the core ring road, door on the avenue
The grand avenue (x -5.5..5.5 along the north axis) and the ring roads stay free: every piece keeps a margin to the
street network (see tests/test_landmark_fountain.py).
"""
import math

from . import planner as PL, palette as P, blocks as B, architecture as A
from .feature import Feature, register
from .geometry import connect_state
from .volume import Volume

LAYER = 50
DECK = PL.DECK_TOP            # 4
WALK = PL.WALK_Y              # 5
_gate = [p for p in PL.plazas() if p['name'] == 'Fountain Plaza'][0]
GX, GZ = int(round(_gate['x'])), int(round(_gate['z']))      # (0, -205)

QB, QS, QP, QBR = P.WHITE_BLOCK, P.WHITE, P.WHITE_PILLAR + '[axis=y]', P.WHITE_BRICKS
PANE = P.GLASS_BLUE_PANE
LAMP = P.LAMP


# ------------------------------------------------------------------------------------------------ helpers
def _erode(mask, k=1):
    m = set(mask)
    for _ in range(k):
        m = {(x, z) for (x, z) in m if (x + 1, z) in m and (x - 1, z) in m and (x, z + 1) in m and (x, z - 1) in m}
    return m


def _rrect(x1, z1, x2, z2, c):
    """Rectangle with chamfered corners (cells with dx + dz < c from the nearest sides are cut)."""
    out = set()
    for x in range(x1, x2 + 1):
        for z in range(z1, z2 + 1):
            if min(x - x1, x2 - x) + min(z - z1, z2 - z) >= c: out.add((x, z))
    return out


def _bounds(cells):
    xs = [c[0] for c in cells]; zs = [c[1] for c in cells]
    return min(xs), min(zs), max(xs), max(zs)


def _flip(d, s):
    """Mirror a facing across the x axis when s < 0 (east <-> west)."""
    if s < 0 and d in ('east', 'west'): return B.OPPOSITE[d]
    return d


def _connect_panes(v):
    """Give every pane / wall / fence in the private volume explicit connection states."""
    todo = []
    for x, y, z, b in v.items():
        n = b.split('[', 1)[0]
        if n.endswith(('_pane', 'iron_bars', '_wall', '_fence')) and not n.endswith('_wall_banner'):
            todo.append((x, y, z, b))
    offs = {'north': (0, -1), 'east': (1, 0), 'south': (0, 1), 'west': (-1, 0)}
    for x, y, z, b in todo:
        def nb(d, x=x, y=y, z=z):
            if d == 'up': return v.get(x, y + 1, z)
            dx, dz = offs[d]
            return v.get(x + dx, y, z + dz)
        v.set(x, y, z, connect_state(b, nb))


class Piece(Feature):
    """A layer-50 piece drawn once into a private Volume and copied (clipped) into each tile."""
    layer = LAYER
    _cache = {}

    def __init__(self, name, bbox, draw, meta):
        super().__init__(name, bbox, LAYER, dict(meta, district='B'))
        self.draw = draw

    def _private(self, ctx):
        key = (ctx.seed, self.name)
        v = Piece._cache.get(key)
        if v is None:
            v = Volume(*self.bbox)
            self.draw(v, ctx)
            _connect_panes(v)
            Piece._cache[key] = v
        return v

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        pv = self._private(ctx)
        get, setb = pv.get, vol.set
        for y in range(c[1], c[4] + 1):
            for z in range(c[2], c[5] + 1):
                for x in range(c[0], c[3] + 1):
                    b = get(x, y, z)
                    if b is not None: setb(x, y, z, b)


# ------------------------------------------------------------------------------------------------ buildings
def _building(v, mask, H, doors, lamp_step, roof_ero, pillar_step=4):
    """Shell on the deck: floor at y4, walls y5..4+H, ceiling (lamps) at y5+H, stepped roof. `doors`: list of
    (x, z, outward) with outward a facing; the wall cell is carved 3 wide, 3 tall, with pillar jambs."""
    inner = _erode(mask, 1)
    top = WALK + H                              # ceiling / roof base y
    wy0, wy1 = WALK + 2, top - 2                # window rows
    for (x, z) in mask:
        v.set(x, DECK, z, QS if (x // 2 + z // 2) % 2 == 0 else P.PALE_GREY)
    for (x, z) in inner:
        v.fill(x, WALK, z, x, top - 1, z, 'air')
    for (x, z) in mask - inner:
        ox = (x + 1, z) not in mask or (x - 1, z) not in mask
        oz = (x, z + 1) not in mask or (x, z - 1) not in mask
        run = x if oz and not ox else z
        pillar = (ox and oz and (x + z) % 3 == 0) or (not (ox and oz) and run % pillar_step == 0)
        for y in range(WALK, top):
            if pillar and y in (WALK + 1, top - 1): b = LAMP                  # facade lanterns light the street outside
            elif pillar: b = QP
            elif y < wy0: b = QBR
            elif y <= wy1: b = PANE
            else: b = QS
            v.set(x, y, z, b)
    # doors
    for (dx, dz, out) in doors:
        ox, oz = B.DIRS[out]
        lx, lz = (0, 1) if ox else (1, 0)
        for k in (-1, 0, 1):
            cx, cz = dx + lx * k, dz + lz * k
            v.fill(cx, WALK, cz, cx, WALK + 2, cz, 'air')
            v.set(cx, WALK + 3, cz, PANE)
        for k in (-2, 2):
            v.fill(dx + lx * k, WALK, dz + lz * k, dx + lx * k, top - 1, dz + lz * k, QP)
    # ceiling and stepped roof
    for (x, z) in mask:
        lit = x % lamp_step == 0 and z % lamp_step == 0 and (x, z) in inner
        v.set(x, top, z, LAMP if lit else QS)
    prev = mask
    for i, e in enumerate(roof_ero):
        m = _erode(mask, e)
        for (x, z) in m:
            v.set(x, top + 1 + i, z, QB if (x, z) in _erode(m, 1) or i == 0 else P.QUARTZ_SLAB)
        prev = m
    # roof lamps on the exposed top of every level
    for i in range(len(roof_ero) + 1):
        lvl = mask if i == 0 else _erode(mask, roof_ero[i - 1])
        upper = _erode(mask, roof_ero[i]) if i < len(roof_ero) else set()
        for (x, z) in lvl:
            if (x, z) not in upper and x % 4 == 0 and z % 4 == 0:
                v.set(x, top + i, z, LAMP)
    return inner, top


def _roof_stairs(v, x0, z0, dx, dz, n, face):
    """Exterior stair run to a roof terrace: n steps from (x0, z0) along (dx, dz), step k at y = WALK + k on a solid
    wedge, facing `face` (the climbing direction); a lantern in the wedge lights every third step."""
    for k in range(n):
        x, z, y = x0 + dx * k, z0 + dz * k, WALK + k
        for yy in range(WALK, y):
            v.set(x, yy, z, LAMP if (k % 3 == 1 and yy == y - 1) else QB)
        v.set(x, y, z, B.stairs('smooth_quartz', face))


def _banner(v, inner, x, y, z):
    """Wall banner on the wall cell (x, z) facing into the single adjacent interior cell, if any."""
    opts = [(f, (x + dx, z + dz)) for f, (dx, dz) in B.DIRS.items() if (x + dx, z + dz) in inner]
    if len(opts) == 1:
        v.set(opts[0][1][0], y, opts[0][1][1], 'light_blue_wall_banner[facing=%s]' % opts[0][0])


def _bench(v, x, z, along, n, back):
    """Bench of n stairs starting at (x, z) running along 'x' or 'z'; `back` = side of the tall back."""
    for i in range(n):
        v.set(x + (i if along == 'x' else 0), WALK, z + (i if along == 'z' else 0), B.stairs('quartz', back))


# ------------------------------------------------------------------------------------------------ arrival hall
HALL = (56, -224, 92, -190)
HALL_H = 10
HALL_DOORS = [(56, -207, 'west'), (74, -224, 'north'), (74, -190, 'south')]


def _draw_hall(v, ctx):
    x1, z1, x2, z2 = HALL
    mask = _rrect(x1, z1, x2, z2, 6)
    inner, top = _building(v, mask, HALL_H, HALL_DOORS, 4, [1, 3, 6, 9])
    # floor inlay: cyan guide line from the west door to the desk, and a ring under the lobby
    for x in range(x1, 77):
        for z in (-208, -206): v.set(x, DECK, z, P.CYAN if x % 2 == 0 else P.LIGHT_BLUE)
    for z in range(z1 + 1, z2):
        if (74, z) in mask: v.set(74, DECK, z, P.CYAN) if z % 2 == 0 else None
    # porch: canopy, columns, lamps
    _roof_stairs(v, 62, -225, 1, 0, 11, 'east')
    for x in range(52, 56):
        for z in range(-211, -202):
            v.set(x, DECK, z, QS if (x + z) % 2 == 0 else P.PALE_GREY)
            v.set(x, 9, z, P.QUARTZ_SLAB if (x, z) != (53, -207) else LAMP)
    for (cx, cz) in ((52, -211), (52, -203), (55, -211), (55, -203)):
        v.fill(cx, WALK, cz, cx, 8, cz, QP)
    v.set(53, 9, -209, LAMP); v.set(53, 9, -205, LAMP)
    # central reception: counter, consoles, staff lane
    for z in range(-212, -201):
        if z in (-209, -205): continue
        v.set(76, WALK, z, QB)
        v.set(76, WALK + 1, z, B.slab(P.WHITE_SLAB, 'bottom'))
        v.set(75, WALK, z, B.stairs('smooth_quartz', 'east')) if z % 2 == 0 else None
    for cz in (-211, -207, -203):
        A.console(v, 80, DECK, cz, 'west', 3)
    for z in (-211, -203):
        v.set(77, WALK, z, B.stairs('quartz', 'east'))          # staff stools
    # benches facing the axis
    for bx in (60, 67):
        _bench(v, bx, -216, 'x', 5, 'north')
        _bench(v, bx, -198, 'x', 5, 'south')
    # columns at the corners of the lobby
    for (cx, cz) in ((60, -221), (60, -193), (70, -221), (70, -193)):
        A.atlantean_column(v, cx, DECK, cz, 'south', height=HALL_H + 1)
    # partition + back offices (x 85..91)
    for z in range(z1 + 1, z2):
        if (84, z) not in inner: continue
        if -212 <= z <= -210 or -204 <= z <= -202: continue          # door gaps
        v.fill(84, WALK, z, 84, top - 1, z, QP if z % 5 == 0 else PANE)
    for cz in (-217, -197):
        A.console(v, 91, DECK, cz, 'west', 3)
    for z in (-213, -207, -201):
        _bench(v, 86, z, 'z', 2, 'east')
    # banners on the lobby pillars
    for (x, z) in sorted(mask - inner):
        if v.get(x, WALK + 3, z) == QP and v.get(x, WALK + 6, z) == QP:
            _banner(v, inner, x, WALK + 4, z)
    return inner


# ------------------------------------------------------------------------------------------------ checkpoints
CP_H = 5


def _cp_geometry(s):
    """(x1, z1, x2, z2) for side s (+1 east, -1 west)."""
    xa, xb = 10, 21
    x1, x2 = (xa, xb) if s > 0 else (-xb, -xa)
    return x1, -157, x2, -146            # (the roof stair run adds z -158)


def _cp_doors(s):
    xin = 10 if s > 0 else -10
    return [(xin, -151, 'west' if s > 0 else 'east'), (15 * s, -146, 'south')]


def _draw_checkpoint(v, ctx, s):
    x1, z1, x2, z2 = _cp_geometry(s)
    mask = _rrect(x1, z1, x2, z2, 3)
    inner, top = _building(v, mask, CP_H, _cp_doors(s), 3, [1, 3])
    back = 20 * s
    A.console(v, back, DECK, -151, _flip('west', s), 3)
    # scanner arch inside the avenue door: two pillars and a glass lintel, 3 clear
    for z in (-154, -148):
        v.fill(14 * s, WALK, z, 14 * s, WALK + 3, z, QP)
    for z in range(-154, -147):
        v.set(14 * s, WALK + 3, z, P.GLASS_CYAN if z not in (-154, -148) else QP)
    for z in range(-153, -148):
        v.set(14 * s, DECK, z, P.CYAN)
    # benches along the north wall and a side console
    for i in range(4):
        xx = (12 + i) if s > 0 else -(12 + i)
        v.set(xx, WALK, -155, B.stairs('quartz', 'north'))
    for (x, z) in sorted(mask - inner):
        if v.get(x, WALK + 2, z) == QP: _banner(v, inner, x, WALK + 3, z)
    for (fx, fz) in ((24, -146), (22, -140), (24, -152)):     # flush deck lanterns where the pavilion shades the avenue lamps
        v.set(fx * s, DECK, fz, LAMP)
    _roof_stairs(v, 8 * s, -158, s, 0, 6, 'east' if s > 0 else 'west')
    # porch lamp on the avenue side
    px = 8 * s
    v.fill(px, WALK, -154, px, WALK + 3, -154, QP)
    v.set(px, WALK + 4, -154, LAMP)
    v.fill(px, WALK, -148, px, WALK + 3, -148, QP)
    v.set(px, WALK + 4, -148, LAMP)
    return inner


# ------------------------------------------------------------------------------------------------ rings dais and arch
#: Plato's Atlantis: an island ringed by alternating bands of water and land. Here they are glowing glass bands
#: (light-blue glass over sea lanterns) flush in the dais floor, so the avenue across the dais stays walkable.
WATER_RINGS = ((2.2, 3.4), (5.4, 6.6), (8.6, 9.8))


def _in_rings(r):
    return any(a < r <= b for a, b in WATER_RINGS)


def _draw_gate(v, ctx):
    """Rings dais on the Fountain Plaza with the Trident Arch over the grand avenue."""
    cx, cz = GX, GZ
    for x in range(cx - 17, cx + 18):
        for z in range(cz - 17, cz + 18):
            r = math.hypot(x - cx, z - cz)
            if r <= 15.4:
                v.set(x, 5, z, QBR if r > 13.6 else (P.DARK_PRISMARINE if 12.0 < r <= 13.0 else QS))
            if r <= 11.4:
                if _in_rings(r):
                    v.set(x, 5, z, LAMP)                       # the glass bands glow from below
                    b = P.GLASS_BLUE
                elif r <= 2.2:
                    b = 'chiseled_quartz_block'
                elif r > 10.2:
                    b = P.CYAN
                else:
                    b = QS if int(r) % 2 == 0 else P.PALE_GREY
                v.set(x, 6, z, b)
    # ramps (all four sides): 0.5-block steps from the plaza (surface 5) to the dais top (7)
    for fx, fz, hw in ((0, 1, 4), (0, -1, 4), (1, 0, 3), (-1, 0, 3)):
        face = B.facing_from_vector(-fx, -fz)             # ascending towards the centre
        for i in range(-hw, hw + 1):
            def cell(d):
                return (cx + fx * d - fz * i, cz + fz * d + fx * i)
            x, z = cell(15); v.set(x, 5, z, B.stairs('smooth_quartz', face))
            x, z = cell(14); v.set(x, 5, z, QS)
            x, z = cell(13); v.set(x, 5, z, QS); v.set(x, 6, z, B.stairs('smooth_quartz', face))
            for d in (12, 11):
                x, z = cell(d); v.set(x, 5, z, QS); v.set(x, 6, z, QS)
    # the arch's legs (the processional way passes between them)
    for sx in (-1, 1):
        lx = cx + sx * 8
        v.fill(lx - 1, 7, cz - 1, lx + 1, 7, cz + 1, P.DARK_PRISMARINE)
        v.fill(lx - 1, 8, cz - 1, lx + 1, 14, cz + 1, QP)
        for y in (9, 12):
            v.set(lx, y, cz - 2, LAMP); v.set(lx, y, cz + 2, LAMP)
    # lintel with lamps on its underside
    v.fill(cx - 9, 15, cz - 1, cx + 9, 15, cz + 1, QB)
    v.fill(cx - 9, 16, cz - 1, cx + 9, 16, cz + 1, P.QUARTZ_SLAB)
    for x in (cx - 4, cx, cx + 4):
        v.set(x, 15, cz, LAMP)
    # trident crest: shaft, cross-bar, three prongs tipped with end rods
    v.fill(cx, 16, cz, cx, 22, cz, QP)
    v.fill(cx - 3, 22, cz, cx + 3, 22, cz, QB)
    for px in (cx - 3, cx + 3):
        v.fill(px, 23, cz, px, 25, cz, QP)
        v.set(px, 26, cz, 'end_rod[facing=up]')
    v.fill(cx, 23, cz, cx, 27, cz, QP)
    v.set(cx, 28, cz, LAMP); v.set(cx, 29, cz, 'end_rod[facing=up]')
    # pylons
    for sx in (-1, 1):
        px = cx + sx * 20
        v.fill(px - 2, 5, cz - 2, px + 2, 6, cz + 2, P.DARK_BRICKS)
        v.fill(px - 1, 7, cz - 1, px + 1, 24, cz + 1, QP)
        for y in (10, 14, 18, 22):
            for ddx, ddz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                v.set(px + ddx, y, cz + ddz, LAMP)
        v.fill(px - 1, 25, cz - 1, px + 1, 25, cz + 1, P.DARK_PRISMARINE)
        v.set(px, 26, cz, P.GLASS_CYAN); v.set(px, 27, cz, P.GLASS_CYAN)
        v.set(px, 28, cz, 'end_rod[facing=up]')


# ------------------------------------------------------------------------------------------------ approach arches
def _arch_cells(half=8, base=5, rise=9, peak=2.0):
    """Cells (x, y) of a semi-elliptical rib between two columns at x = +-half."""
    out = {}
    for x in range(-half, half + 1):
        out[x] = base + rise + int(round(peak * math.sqrt(max(0.0, 1 - (x / half) ** 2))))
    return out


def _draw_approach(v, ctx, sgn):
    for dz in (28, 35, 42):
        z = GZ + sgn * dz
        rib = _arch_cells()
        for sx in (-1, 1):
            x = sx * 8
            v.fill(x, 5, z, x, 6, z, P.DARK_BRICKS)
            v.fill(x, 7, z, x, 13, z, QP)
        for x, y in rib.items():
            v.set(x, y, z, QS if abs(x) != 8 else QP)
            for yy in range(14, y):
                if abs(x) == 8: v.set(x, yy, z, QP)
        v.set(0, rib[0] - 1, z, LAMP)
        v.set(-8, 11, z + 1, LAMP); v.set(8, 11, z + 1, LAMP)
        v.set(-8, 11, z - 1, LAMP); v.set(8, 11, z - 1, LAMP)
        v.set(-4, rib[-4] - 1, z, 'end_rod[facing=down]'); v.set(4, rib[4] - 1, z, 'end_rod[facing=down]')


# ------------------------------------------------------------------------------------------------ features
def _entrances(doors):
    out = []
    for (x, z, f) in doors: out.append((x, WALK, z))
    return out


def _make():
    fs = []
    gate_bbox = (GX - 22, DECK, GZ - 17, GX + 22, 32, GZ + 17)
    fs.append(Piece('rings_dais', gate_bbox, _draw_gate, {'kind': 'landmark', 'islands_ok': True}))
    for name, sgn in (('arrival_approach_s', 1), ('arrival_approach_n', -1)):
        z0, z1 = sorted((GZ + sgn * 27, GZ + sgn * 43))
        fs.append(Piece(name, (-10, DECK, z0, 10, 17, z1), lambda v, c, s=sgn: _draw_approach(v, c, s),
                        {'kind': 'landmark', 'islands_ok': True}))
    hx1, hz1, hx2, hz2 = HALL
    inner = _erode(_rrect(*HALL, 6), 1)
    ix1, iz1, ix2, iz2 = _bounds(inner)
    fs.append(Piece('arrival_hall', (52, DECK, hz1 - 1, hx2, WALK + HALL_H + 5, hz2), _draw_hall,
                    {'kind': 'building', 'interior': True, 'entrances': _entrances(HALL_DOORS), 'islands_ok': True,
                     'interior_box': (ix1, WALK, iz1, ix2, WALK + HALL_H - 1, iz2), 'roof_y': WALK + HALL_H}))
    for name, s in (('checkpoint_w', -1), ('checkpoint_e', 1)):
        x1, z1, x2, z2 = _cp_geometry(s)
        inner = _erode(_rrect(x1, z1, x2, z2, 3), 1)
        ix1, iz1, ix2, iz2 = _bounds(inner)
        bx1 = min(x1, 8 * s, 25 * s); bx2 = max(x2, 8 * s, 25 * s)
        fs.append(Piece(name, (bx1, DECK, z1 - 1, bx2, WALK + CP_H + 3, z2 + 6), lambda v, c, s=s: _draw_checkpoint(v, c, s),
                        {'kind': 'building', 'interior': True, 'entrances': _entrances(_cp_doors(s)),
                         'islands_ok': True, 'interior_box': (ix1, WALK, iz1, ix2, WALK + CP_H - 1, iz2),
                         'roof_y': WALK + CP_H}))
    return fs


FEATURES = _make()
register(*FEATURES)
