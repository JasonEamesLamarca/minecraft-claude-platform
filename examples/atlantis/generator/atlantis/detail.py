"""Layers 70 (detail) and 80 (lighting): the public realm of the city: avenues, plazas, gateways, street furniture.

Everything here decorates the streets and public spaces between the buildings (r < 535, inside the dome) and never
touches a layer-40/50 bbox, a road centre line or the clear width of a street.

LIGHTING HIERARCHY (class of a place = class of its district, see CLASS)
  gate   district B (Fountain & Arrival complex): distinctive CYAN glass channels over sea lanterns, flush in the paving, along both
         avenue edges plus a bright centre stripe; cyan-banded bollards.
  core   tower district: white glass over sea lanterns.
  civic  H, L: warm white (white glass over pearlescent froglight), froglight bollards.
  warm   residential piers C1..C3: soft warm light (orange glass over shroomlight, standing lanterns, lights hidden
         under bench seats).
  cool   science / technical districts D, F, W, K, G, E: light-blue glass over sea lanterns, end rods, crystal pylons,
         lit copper-grate conduit crossings.
  Major plazas get radial / ring inlays of the plaza class colour and a rim of bollards and lamp posts.

FEATURES (all small, tight bboxes; the names sort the layer-80 lighting after the layer-70 detail)
  detail_avenue_<plan>_<k>   furniture along the kerbs of one avenue segment (benches, planters, trees, bollards,
                             crystal pylons, low lantern posts)                                    layer 70
  light_avenue_<plan>_<k>    flush illuminated channels / centre stripe / conduit crossings        layer 80
  detail_plaza_<slug>        bollards and lamp posts on the plaza rim                               layer 70
  light_plaza_floor_<slug>   floor inlays                                                           layer 80
  light_plaza_<slug>         the lights of the rim posts                                            layer 80
  detail_gate_<id>, light_gate_<id>   district gateway pylons (coloured concrete + banner flag) and their lights

Rules kept (tests/test_detail.py): no cell inside a layer-40/50 bbox or within 2 blocks of an entrance, nothing on a
street column above the paving (except plaza rim bollards that sit off the avenue / ring / radial lanes), paving
changes are flush (y 3 and 4 only), deterministic.  Layer 70/80 bboxes are not subject to the `overlaps` check.
"""
import importlib
import math

from . import config, planner as PL, roads, blocks as B
from .feature import Feature, register, registry
from .util import hash_str

LAYER_DETAIL = 70
LAYER_LIGHT = 80
DECK = PL.DECK_TOP          # 4
WALK = PL.WALK_Y            # 5
MAX_R = 535                 # nothing at or beyond this radius (the perimeter promenade belongs to another worker)

# make sure every other generator has registered before the bbox index is built
from . import generators as _gens
for _m in _gens.GENERATORS:
    if _m != __name__ and _m != 'atlantis.detail':
        importlib.import_module(_m)

_CTX = config.load()
_hyp = math.hypot

# ---------------------------------------------------------------- classes and colours
CLASS = {'A': 'core', 'B': 'gate', 'K': 'cool', 'F': 'cool', 'W': 'cool', 'D': 'cool', 'G': 'cool', 'E': 'cool',
         'C1': 'warm', 'C2': 'warm', 'C3': 'warm', 'H': 'civic', 'L': 'civic'}
COLOR = {'A': 'white', 'B': 'cyan', 'K': 'blue', 'F': 'lime', 'H': 'yellow', 'L': 'green', 'W': 'light_blue',
         'G': 'light_gray', 'D': 'purple', 'C1': 'orange', 'C2': 'pink', 'C3': 'magenta', 'E': 'red'}

#: per class: floor channel (top glass, light under it) + lamp materials
STYLE = {
    'gate':  dict(glass='cyan_stained_glass', under='sea_lantern', band='cyan_concrete'),
    'core':  dict(glass='white_stained_glass', under='sea_lantern', band='light_blue_concrete'),
    'civic': dict(glass='white_stained_glass', under='pearlescent_froglight', band='white_concrete'),
    'warm':  dict(glass='orange_stained_glass', under='shroomlight', band='orange_concrete'),
    'cool':  dict(glass='light_blue_stained_glass', under='sea_lantern', band='light_blue_concrete'),
}
PILLAR = 'quartz_pillar[axis=y]'
PAVE = 'smooth_quartz'
SLAB = B.slab('smooth_quartz', 'bottom')
SLAB_TOP = B.slab('smooth_quartz', 'top')
FLOWERS = ['allium', 'azure_bluet', 'cornflower', 'blue_orchid', 'oxeye_daisy', 'lily_of_the_valley', 'poppy',
           'flowering_azalea', 'azalea', 'flowering_azalea', 'fern']
LEAF_A = 'azalea_leaves[persistent=true]'
LEAF_B = 'flowering_azalea_leaves[persistent=true]'


def cls_at(x, z):
    d = PL.district_at(x, z)
    return CLASS.get(d), d


# ---------------------------------------------------------------- exclusion index (buildings, props, entrances)
class _Index:
    def __init__(self):
        self.boxes = {}                 # bucket -> [(x1,z1,x2,z2)]
        self.ent = set()
        for f in registry():
            b = f.bbox
            if f.layer in (40, 50):
                self._add(b[0], b[2], b[3], b[5])
                for e in f.meta.get('entrances', ()) or ():
                    self.ent.add((e[0], e[2]))
            elif f.name.startswith(('roads_b_', 'roads_c_')):
                self._add(b[0] - 1, b[2] - 1, b[3] + 1, b[5] + 1)
        for R in roads.RESERVED:
            r = int(R['r']) + 1
            self._add(R['x'] - r, R['z'] - r, R['x'] + r, R['z'] + r)

    def _add(self, x1, z1, x2, z2):
        for bx in range(x1 >> 4, (x2 >> 4) + 1):
            for bz in range(z1 >> 4, (z2 >> 4) + 1):
                self.boxes.setdefault((bx, bz), []).append((x1, z1, x2, z2))

    def hit(self, x, z, m=0):
        for (x1, z1, x2, z2) in self.boxes.get((x >> 4, z >> 4), ()):
            if x1 - m <= x <= x2 + m and z1 - m <= z <= z2 + m: return True
        return False

    def near_entrance(self, x, z, m=2):
        for dx in range(-m, m + 1):
            for dz in range(-m, m + 1):
                if (x + dx, z + dz) in self.ent: return True
        return False


_IDX = None


def index():
    global _IDX
    if _IDX is None: _IDX = _Index()
    return _IDX


def blocked(x, z, m=0):
    """True if column (x, z) lies in a building / landmark bbox, a prop of the roads module, a reserved core."""
    return index().hit(x, z, m)


def _lagoon_near(x, z, m=2):
    r2 = x * x + z * z
    if r2 < 292 * 292 or r2 > 548 * 548: return False
    for dx in range(-m, m + 1):
        for dz in range(-m, m + 1):
            if PL.zone(x + dx, z + dz) == 'lagoon': return True
    return False


def free_col(x, z):
    """A public-realm column furniture may stand on: dry deck, not street, not near a building / entrance / lagoon."""
    if x * x + z * z >= MAX_R * MAX_R or not PL.has_deck(x, z): return False
    if PL.zone(x, z) not in ('inner', 'pier', 'core'): return False
    if roads.surface(x, z) is not None: return False
    ix = index()
    if ix.hit(x, z) or ix.near_entrance(x, z): return False
    if _lagoon_near(x, z): return False
    return True


def street_col(x, z, kinds=None):
    """Street column that patterns may change (flush, y 3/4): not in a building bbox, prop, fountain or reserved core."""
    if x * x + z * z >= MAX_R * MAX_R: return None
    s = roads.surface(x, z)
    if s is None or s[3] != DECK: return None
    if kinds and s[0] not in kinds: return None
    if index().hit(x, z): return None
    return s


# ---------------------------------------------------------------- stamps
class Stamp:
    """A small piece of furniture: cells (layer 70) + lights (layer 80) + footprint columns."""

    def __init__(self):
        self.d, self.l, self.fp = {}, {}, set()

    def put(self, x, y, z, b, fp=True):
        self.d[(x, y, z)] = b
        if fp: self.fp.add((x, z))

    def lit(self, x, y, z, b, fp=True):
        self.l[(x, y, z)] = b
        if fp: self.fp.add((x, z))

    def ok(self):
        for (x, y, z) in list(self.d) + list(self.l):
            if y > _CTX.max_building_top(x, z) - 1 or y > 14: return False
        return True


def _bench(x, z, toward, cls):
    """Seat of 3 along the kerb: stair - slab - stair, back rest of top slabs behind it; light hidden under the seat."""
    st = STYLE[cls]
    dx, dz = B.DIRS[toward]
    ex, ez = -dz, dx                      # along the bench
    s = Stamp()
    for k in (-1, 0, 1):
        cx, cz = x + k * ex, z + k * ez
        if k == 0:
            s.put(cx, WALK, cz, SLAB)
            s.lit(cx, DECK, cz, st['under'])
        else:
            s.put(cx, WALK, cz, B.stairs('smooth_quartz', B.facing_from_vector(k * ex, k * ez)))
        s.put(cx - dx, WALK, cz - dz, SLAB_TOP)
    return s


def _planter(x, z, toward, cls):
    """4 x 2 flush planter: moss on the deck level, low bushes and flowers."""
    dx, dz = B.DIRS[toward]
    ex, ez = -dz, dx
    s = Stamp()
    for k in (-1, 0, 1, 2):
        for row in (0, 1):
            cx, cz = x + k * ex - row * dx, z + k * ez - row * dz
            h = hash_str('plant', cx, cz)
            s.put(cx, DECK, cz, 'moss_block')
            s.put(cx, WALK, cz, FLOWERS[h % len(FLOWERS)] if row == 1 else ('flowering_azalea' if h % 3 == 0 else 'azalea'))
    return s


def _tree(x, z, toward, cls):
    """3 x 3 tree bed: quartz slab ring, moss centre, birch trunk, azalea canopy (hidden light inside)."""
    s = Stamp()
    for ddx in (-1, 0, 1):
        for ddz in (-1, 0, 1):
            cx, cz = x + ddx, z + ddz
            if ddx == 0 and ddz == 0:
                s.put(cx, DECK, cz, 'moss_block')
                for y in (5, 6, 7, 8): s.put(cx, y, cz, 'birch_log[axis=y]')
            else:
                s.put(cx, WALK, cz, SLAB)
                s.put(cx, 9, cz, LEAF_B if (ddx + ddz) % 2 else LEAF_A)
                if ddx == 0 or ddz == 0:
                    s.put(cx, 8, cz, LEAF_A)
                    s.put(cx, 10, cz, LEAF_B)
    s.put(x, 9, z, LEAF_A)
    s.put(x, 10, z, LEAF_B)
    s.lit(x, 9, z, 'light[level=13]')
    return s


def _bollards(x, z, toward, cls):
    """Two bollards 4 apart along the kerb."""
    dx, dz = B.DIRS[toward]
    ex, ez = -dz, dx
    s = Stamp()
    for k in (-2, 2):
        _bollard_cells(s, x + k * ex, z + k * ez, cls)
    return s


def _bollard_cells(s, cx, cz, cls, tall=False):
    n = 2 if tall else 0
    base = {'gate': STYLE['gate']['band'], 'warm': PAVE}.get(cls, PILLAR)
    cap = {'gate': 'sea_lantern', 'cool': 'end_rod[facing=up]', 'warm': 'lantern[hanging=false]',
           'civic': 'ochre_froglight'}.get(cls, 'sea_lantern')
    s.put(cx, 5, cz, base)
    for y in range(6, 6 + n): s.put(cx, y, cz, PILLAR if cls != 'warm' else PAVE)
    s.lit(cx, 6 + n, cz, cap)


def _lamp_low(x, z, toward, cls):
    s = Stamp()
    _bollard_cells(s, x, z, cls, tall=True)
    return s


def _crystal(x, z, toward, cls):
    """Small crystal pylon: quartz base, amethyst block, cluster, small buds; a hidden light in the crown."""
    s = Stamp()
    s.put(x, 5, z, 'chiseled_quartz_block')
    s.put(x, 6, z, 'amethyst_block')
    s.put(x, 7, z, 'amethyst_cluster[facing=up,waterlogged=false]')
    s.lit(x, 8, z, 'light[level=12]')
    return s


ITEMS = {'bench': _bench, 'planter': _planter, 'tree': _tree, 'bollards': _bollards, 'lamp': _lamp_low,
         'crystal': _crystal}
MIX = {   # cumulative weights per class: (limit, item)
    'gate':  [(30, 'bollards'), (55, 'bench'), (75, 'planter'), (85, 'tree'), (100, None)],
    'cool':  [(22, 'bench'), (44, 'crystal'), (58, 'planter'), (68, 'tree'), (80, 'bollards'), (100, None)],
    'warm':  [(22, 'bench'), (44, 'planter'), (66, 'tree'), (88, 'lamp'), (100, None)],
    'civic': [(25, 'bench'), (50, 'planter'), (70, 'tree'), (85, 'bollards'), (100, None)],
    'core':  [(30, 'bench'), (60, 'planter'), (80, 'tree'), (100, None)],
}
SIZE = {'bench': 6, 'planter': 5, 'tree': 4, 'bollards': 6, 'lamp': 2, 'crystal': 3}


def _pick(cls, h):
    for lim, item in MIX[cls]:
        if h % 100 < lim: return item
    return None


def _try(stamp, taken):
    if stamp is None or not stamp.ok(): return False
    for (x, z) in stamp.fp:
        if (x, z) in taken or not free_col(x, z): return False
    for (x, z) in stamp.fp:
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1): taken.add((x + dx, z + dz))
    return True


# ---------------------------------------------------------------- avenue furniture plan
SEG = 84.0
_AVE_SEGS = []          # (plan, k, s0, s1)
for _a in PL.PIER_ANGLES:
    _s, _k = 34.0, 0
    while _s < 540:
        _AVE_SEGS.append((_a, _k, _s, min(_s + SEG, 541.0)))
        _s += SEG
        _k += 1


def _axis_pt(a, s, c):
    """City-local (x, z) at `s` along the plan-angle axis and `c` across it (+ = clockwise side)."""
    ux, uz = PL.from_polar(1.0, a)
    return s * ux - c * uz, s * uz + c * ux


def avenue_stamps():
    """{(plan, seg_index): [Stamp]}: furniture along the avenue kerbs."""
    out = {}
    taken = set()
    for (a, k, s0, s1) in _AVE_SEGS:
        lst = out.setdefault((a, k), [])
        vx, vz = _axis_pt(a, 0, 1)                               # across unit vector (clockwise side)
        for sgn in (-1, 1):
            s = s0 + 5 + (3 if sgn > 0 else 0)
            while s < s1:
                x0, z0 = _axis_pt(a, s, 0)
                cls, dist = cls_at(int(round(x0)), int(round(z0)))
                step = 8
                if cls is not None and s > 70:
                    item = _pick(cls, hash_str('item', a, sgn, int(s // 9)))
                    if item:
                        hw = roads._avenue_hw(s)
                        toward = B.facing_from_vector(-sgn * vx, -sgn * vz)
                        for off in (hw + 1.5, hw + 2.5, hw + 3.5):
                            px, pz = _axis_pt(a, s, sgn * off)
                            st = ITEMS[item](int(round(px)), int(round(pz)), toward, cls)
                            if _try(st, taken):
                                lst.append(st)
                                step = SIZE[item] + 6
                                break
                s += step
    return out


# ---------------------------------------------------------------- gateway pylons
def _pylon(x, z, toward, color, h):
    """Coloured concrete pylon: 3x3 slab plinth, shaft, lit crown, a banner flag over the kerb."""
    s = Stamp()
    dx, dz = B.DIRS[toward]
    conc = f'{color}_concrete'
    glass = f'{color}_stained_glass'
    for ax in (-1, 0, 1):
        for az in (-1, 0, 1):
            s.put(x + ax, 5, z + az, 'quartz_block' if ax == 0 and az == 0 else SLAB)
    for y in range(6, h - 4): s.put(x, y, z, conc)
    s.put(x, h - 4, z, PILLAR)
    s.put(x, h - 3, z, glass)
    s.lit(x, h - 2, z, 'sea_lantern')
    s.put(x, h - 1, z, glass)
    s.lit(x, h, z, 'end_rod[facing=up]')
    for k in (1, 2):                                  # banner flag toward the street, bottom over head height
        for y in range(h - 8, h - 4):
            s.put(x + k * dx, y, z + k * dz, conc if k == 1 else glass, fp=False)
    return s


def _gate_ok(st):
    if not st.ok(): return False
    for (x, z) in st.fp:
        if not free_col(x, z): return False
    for (x, y, z) in list(st.d) + list(st.l):         # the overhanging flag too
        if blocked(x, z) or index().near_entrance(x, z, 1): return False
        if y < 8 and roads.surface(x, z) is not None: return False
    return True


def gate_stamps():
    """[(name, [Stamp])]: pylon pairs at both ends of every pier-root plaza and at the district borders."""
    out = []
    for a in PL.PIER_ANGLES:
        vx, vz = _axis_pt(a, 0, 1)
        for tag, s, dist in (('in', 272.0, PL._INNER_BY_ANGLE[a]), ('out', 328.0, PL._PIER_BY_ANGLE[a])):
            lst = []
            for sgn in (-1, 1):
                px, pz = _axis_pt(a, s, sgn * 9.0)
                toward = B.facing_from_vector(-sgn * vx, -sgn * vz)
                st = _pylon(int(round(px)), int(round(pz)), toward, COLOR[dist], 14)
                if _gate_ok(st): lst.append(st)
            if lst: out.append((f'{a:03d}_{tag}', lst))
    for w in PL.WEDGE_ANGLES:
        left = PL._INNER_BY_ANGLE[(w - 30) % 360]
        right = PL._INNER_BY_ANGLE[(w + 30) % 360]
        vx, vz = _axis_pt(w, 0, 1)
        for tag, s in (('a', 148.0), ('b', 288.0)):
            lst = []
            for sgn, dist in ((-1, left), (1, right)):
                px, pz = _axis_pt(w, s, sgn * 5.5)
                toward = B.facing_from_vector(-sgn * vx, -sgn * vz)
                st = _pylon(int(round(px)), int(round(pz)), toward, COLOR[dist], 12)
                if _gate_ok(st): lst.append(st)
            if lst: out.append((f'w{w:03d}_{tag}', lst))
    return out


# ---------------------------------------------------------------- plazas
def _plaza_slug(p):
    return p['name'].lower().replace(' ', '_').replace('-', '_')


def _plaza_lane(x, z):
    """True if (x, z) lies on an avenue / radial / ring lane (rim posts keep off them)."""
    pa = PL.plan_angle(x, z)
    a = PL.nearest(PL.PIER_ANGLES, pa)
    s, c = PL.axis_coords(x, z, a)
    if s > 20 and abs(c) < roads._avenue_hw(s) + 2.5: return True
    w = PL.nearest(PL.WEDGE_ANGLES, pa)
    s, c = PL.axis_coords(x, z, w)
    if s > 100 and abs(c) < 5: return True
    r = _hyp(x, z)
    for R, wd in roads._RINGS:
        if abs(r - R) < wd / 2.0 + 2: return True
    return False


def _plaza_cls(p):
    if p['name'] == 'Tower Plaza': return 'core'
    return cls_at(p['x'], p['z'])[0] or 'core'


def plaza_stamps(p):
    """Rim bollards (every ~11 blocks), every third one a lamp post."""
    cls = _plaza_cls(p)
    rr = p['r'] - 2.0
    n = max(6, int(2 * math.pi * rr / 11.0))
    out, taken = [], set()
    for k in range(n):
        x0, z0 = PL.from_polar(rr, 360.0 * k / n + 3.0)
        x, z = int(round(p['x'] + x0)), int(round(p['z'] + z0))
        s = roads.surface(x, z)
        if s is None or s[0] != 'plaza' or _plaza_lane(x, z): continue
        if blocked(x, z, 1) or index().near_entrance(x, z) or (x, z) in taken: continue
        st = Stamp()
        _bollard_cells(st, x, z, cls, tall=(k % 3 == 0))
        if not st.ok(): continue
        out.append(st)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1): taken.add((x + dx, z + dz))
    return out


#: floor inlay specs per plaza: ('ring', radius) | ('spoke', n, base_plan, r0, r1, dash) -> glass over a light
PLAZA_INLAY = {
    'Tower Plaza': [('spoke', 6, 0, 37, 57, 4), ('spoke', 12, 15, 8, 20, 3)],
    'Fountain Plaza': [('ring', 27.2), ('spoke', 9, 20, 17, 26, 3), ('spoke', 9, 0, 28, 40, 3)],
    'Civic Square': [('ring', 19.0), ('spoke', 8, 22.5, 9, 17, 3)],
    'Botanical Court': [('ring', 17.4), ('spoke', 8, 0, 8, 12, 3)],
}


def inlay_fn(p):
    st = STYLE[_plaza_cls(p)]
    spec = PLAZA_INLAY.get(p['name'])
    if spec is None:
        a = int(p['name'][-3:])
        spec = [('spoke', 6, a + 30, 6.5, 13, 3), ('ring', 13.0)]
    px, pz, R = p['x'], p['z'], p['r']

    def fn(x, z):
        dx, dz = x - px, z - pz
        d = _hyp(dx, dz)
        if d >= R - 5.5 or d < 4: return None
        pa = PL.plan_angle(dx, dz)
        hit = False
        for sp in spec:
            if sp[0] == 'ring':
                if abs(d - sp[1]) < 0.5: hit = True; break
            else:
                _, n, base, r0, r1, dash = sp
                if not (r0 <= d <= r1): continue
                step = 360.0 / n
                ang = base + round((pa - base) / step) * step
                ax, az = PL.from_polar(1.0, ang)
                if abs(dx * az - dz * ax) < 0.5 and dx * ax + dz * az > 0 and int(d) % dash != dash - 1:
                    hit = True; break
        if not hit or street_col(x, z, ('plaza',)) is None: return None
        return [(DECK - 1, st['under']), (DECK, st['glass'])]
    return fn


# ---------------------------------------------------------------- avenue floor channels
def avenue_fn(a, s0, s1):
    def fn(x, z):
        if street_col(x, z, ('avenue',)) is None: return None
        if PL.nearest(PL.PIER_ANGLES, PL.plan_angle(x, z)) != a: return None
        s, c = PL.axis_coords(x, z, a)
        if not (s0 <= s < s1): return None
        cls = cls_at(x, z)[0]
        if cls is None: return None
        st = STYLE[cls]
        hw = roads._avenue_hw(s)
        ac = abs(c)
        if hw - 2 <= ac < hw - 1:
            return [(DECK - 1, st['under']), (DECK, st['glass'])]
        if cls == 'gate' and ac < 0.5 and s > 60:
            return [(DECK - 1, st['under']), (DECK, st['glass'])]
        if cls == 'cool' and ac < hw - 2 and int(s) % 56 == 28:
            return [(DECK - 1, 'sea_lantern'), (DECK, 'waxed_oxidized_copper_grate')]
        return None
    return fn


# ---------------------------------------------------------------- Feature
class Deco(Feature):
    def __init__(self, name, layer, bbox, cells=None, colfn=None):
        super().__init__(name, bbox, layer, {'kind': 'detail' if layer == LAYER_DETAIL else 'lighting'})
        self.cells = cells or {}
        self.colfn = colfn

    def _own(self, x, y, z):
        return (x, y, z) in self.cells

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        x1, y1, z1, x2, y2, z2 = c
        lost = []
        for (x, y, z), b in self.cells.items():
            if x1 <= x <= x2 and z1 <= z <= z2 and y1 <= y <= y2:
                if y == WALK and not b.startswith('light'):
                    o = vol.get(x, y, z)
                    if o is not None and o.startswith('light'): lost.append((x, y, z))
                vol.set(x, y, z, b)
        for (x, y, z) in lost:                  # a deck light lattice point was built over: keep its light above
            spots = [(x + dx, y, z + dz) for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))] + \
                    [(x, yy, z) for yy in range(y + 1, y + 3)]
            for (sx, sy, sz) in spots:
                if (x1 <= sx <= x2 and z1 <= sz <= z2 and vol.get(sx, sy, sz) in (None, 'air', 'cave_air')
                        and not self._own(sx, sy, sz) and roads.surface(sx, sz) is None):
                    vol.set(sx, sy, sz, 'light[level=15]')
                    break
        fn = self.colfn
        if fn:
            for z in range(z1, z2 + 1):
                for x in range(x1, x2 + 1):
                    r = fn(x, z)
                    if r:
                        for (y, b) in r: vol.set(x, y, z, b)


def _bbox_of(cells):
    xs = [k[0] for k in cells]; zs = [k[2] for k in cells]; ys = [k[1] for k in cells]
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _stamp_features(name, stamps):
    """One layer-70 and one layer-80 Feature for a group of stamps."""
    out = []
    d, l = {}, {}
    for st in stamps:
        d.update(st.d); l.update(st.l)
    if d: out.append(Deco('detail_' + name, LAYER_DETAIL, _bbox_of(d), d))
    if l: out.append(Deco('light_' + name, LAYER_LIGHT, _bbox_of(l), l))
    return out


def make_features():
    fs = []
    furn = avenue_stamps()
    for (a, k, s0, s1) in _AVE_SEGS:
        fs.extend(_stamp_features(f'avenue_{a:03d}_{k}', furn.get((a, k), [])))
        pts = [_axis_pt(a, s, c) for s in (s0, s1) for c in (-9, 9)]
        bb = (int(min(p[0] for p in pts)) - 2, 3, int(min(p[1] for p in pts)) - 2,
              int(max(p[0] for p in pts)) + 2, 4, int(max(p[1] for p in pts)) + 2)
        fs.append(Deco(f'light_avenue_{a:03d}_{k}_floor', LAYER_LIGHT, bb, colfn=avenue_fn(a, s0, s1)))
    for p in roads.PLAZAS:
        slug = _plaza_slug(p)
        fs.extend(_stamp_features('plaza_' + slug, plaza_stamps(p)))
        R = int(p['r'])
        fs.append(Deco('light_plaza_floor_' + slug, LAYER_LIGHT, (p['x'] - R, 3, p['z'] - R, p['x'] + R, 4, p['z'] + R),
                       colfn=inlay_fn(p)))
    for tag, lst in gate_stamps():
        fs.extend(_stamp_features('gate_' + tag, lst))
    return fs


FEATURES = make_features()
register(*FEATURES)


def features():
    return list(FEATURES)
