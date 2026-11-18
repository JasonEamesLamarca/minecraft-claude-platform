"""Waves 7-9: District I (Ocean Interface & Research Perimeter) and District J (Old Ruins).

  (a) INSIDE the dome, layer 40, perimeter promenade r 540..549 (the strip between the pier tips and the promenade path;
      the dome cap there is 12..9 so everything is low and ribbed):
        perim_bay_0..5       six glass-fronted observation bays (benches, consoles, telescopes) at plan 15/75/135/195/255/315
        perim_dock_090/270   two sub-dock control offices beside the airlock halls at plan 90 and 270
        perim_shrine_210     a quiet alcove with a replica obelisk and lit inscriptions near plan 210
      Roofs: every building has a flat roof at (cap - 2) cut into strips by 2-high fins, so no roof strip has more than 50
      standable nodes (the walk validator counts any walkable surface above deck level as an island).
  (b) OUTSIDE, layer 50, plan 80..100 / r 615..720: `perim_ocean_lab`, a sealed prismarine/glass research station on a
      terraced pit in the basin wall, joined to the east airlock station (plan 90) by a sealed, lit, 3 x 3 walking tunnel.
      The station chamber's front window (x 621, z -1..1, y 1..3) is opened into the tunnel (the chamber is already sealed
      glass/quartz and outside the dome), so no extra hatch is needed.  Everything dry is explicit `air`, closed on all six
      faces by non-water blocks; the pit around the hull is explicit water (no water_ok needed: no water inside the dome).
  (c) OUTSIDE, layer 50, plan 200..220 / r 630..730: `ruins_circle` (District J), solid ruins in water on a levelled terrace.

All three outside/inside groups are Plans (dict of cells) built once per process and copied into tiles column by column.
"""
import math

from . import config, planner as PL, roads, blocks as B, palette as P
from . import background as BG
from .feature import Feature, register
from .util import hash_str

_CTX = config.load()
LIGHT = P.HIDDEN_LIGHT
WALK = PL.WALK_Y            # 5
DECK = PL.DECK_TOP          # 4

SEALANT = 'sea_lantern'
QB, QS, QP, QCH = 'quartz_bricks', 'smooth_quartz', 'quartz_pillar[axis=y]', 'chiseled_quartz_block'
GLB, GLC = 'light_blue_stained_glass', 'cyan_stained_glass'


def _rnd(*k):
    return (hash_str(*[str(x) for x in k]) & 0xFFFFFF) / float(0x1000000)


# ================================================================================================================
# the cell store
# ================================================================================================================
class Plan:
    """Dict of cells with hard limits.  mode 'dome': only district I cells off roads, y <= ctx.max_building_top.
    mode 'free': only inside the bbox and ctx.in_area."""

    def __init__(self, name, bbox, ctx, mode='free', district=None):
        self.name, self.bbox, self.ctx, self.mode, self.district = name, bbox, ctx, mode, district
        self.c = {}
        self.cols = {}
        self.rejected = 0
        self.first_rejects = []
        self._ci = {}

    def cap(self, x, z):
        v = self._ci.get((x, z))
        if v is None:
            if self.mode == 'dome':
                ok = PL.district_at(x, z) == self.district and roads.surface(x, z) is None and self.ctx.in_area(x, z)
                v = int(self.ctx.max_building_top(x, z)) if ok else -999
            else:
                v = 9999 if self.ctx.in_area(x, z) else -999
            self._ci[(x, z)] = v
        return v

    def set(self, x, y, z, b):
        if b is None: return
        bb = self.bbox
        if not (bb[0] <= x <= bb[3] and bb[1] <= y <= bb[4] and bb[2] <= z <= bb[5]) or y > self.cap(x, z):
            self.rejected += 1
            if len(self.first_rejects) < 8: self.first_rejects.append((x, y, z, b))
            return
        self.c[(x, y, z)] = b

    def get(self, x, y, z):
        return self.c.get((x, y, z))

    def finish(self):
        cols = {}
        for (x, y, z), b in sorted(self.c.items()):
            cols.setdefault((x, z), []).append((y, b))
        self.cols = cols
        return self

    def write(self, vol):
        c = vol.clip(*self.bbox)
        if not c: return
        cols = self.cols
        for x in range(c[0], c[3] + 1):
            for z in range(c[2], c[5] + 1):
                for y, b in cols.get((x, z), ()):
                    if c[1] <= y <= c[4]: vol.set(x, y, z, b)

    def bbox_of_cells(self):
        xs = [k[0] for k in self.c]; ys = [k[1] for k in self.c]; zs = [k[2] for k in self.c]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def stair(mat, facing, half='bottom', wl=False):
    return B.stairs(mat, facing, half, waterlogged=wl)


def slab(mat, typ='bottom', wl=False):
    return B.slab(mat, typ, waterlogged=wl)


# ================================================================================================================
# (a) buildings inside the dome
# ================================================================================================================
U0, U1 = 540.6, 548.0                      # radial range of the footprints (plan r)

# kind, plan angle, lateral centre, half width, variant
SPECS = [
    ('bay', 15, 0.0, 10.0, 0), ('bay', 75, 0.0, 11.0, 1), ('bay', 135, 0.0, 10.0, 2),
    ('bay', 195, 0.0, 11.0, 0), ('bay', 255, 0.0, 10.0, 1), ('bay', 315, 0.0, 11.0, 2),
    ('dock', 90, 11.0, 7.0, 0), ('dock', 270, 11.0, 7.0, 1),
    ('shrine', 210, -9.5, 5.0, 0),
]


def spec_name(s):
    kind, pa, vc, hw, var = s
    if kind == 'bay': return f'perim_bay_{pa:03d}'
    if kind == 'dock': return f'perim_dock_{pa:03d}'
    return f'perim_shrine_{pa:03d}'


class Frame:
    """Local frame of a footprint: u radial (plan r), v lateral (+ clockwise) about the lateral centre vc."""

    def __init__(self, pa, vc):
        self.pa, self.vc = pa, vc
        self.ux, self.uz = PL.from_polar(1.0, pa)          # outward unit vector
        self.vx, self.vz = -self.uz, self.ux               # clockwise lateral

    def uv(self, x, z):
        a, c = PL.axis_coords(x, z, self.pa)
        return a, c - self.vc

    def xz(self, u, v):
        w = v + self.vc
        return int(math.floor(u * self.ux + w * self.vx + 0.5)), int(math.floor(u * self.uz + w * self.vz + 0.5))


def _cap(ctx, x, z):
    return int(ctx.max_building_top(x, z))


class DomeBuilding:
    """Observation bay / dock office / shrine: classification of the footprint cells, then walls, roof, fins, furniture."""

    def __init__(self, spec, ctx):
        self.spec = spec
        kind, pa, vc, hw, var = spec
        self.kind, self.pa, self.hw, self.var = kind, pa, hw, var
        self.name = spec_name(spec)
        self.fr = Frame(pa, vc)
        self.ctx = ctx
        corners = [self.fr.xz(u, v) for u in (U0 - 2, U1 + 2) for v in (-hw - 2, hw + 2)]
        xs = [c[0] for c in corners]; zs = [c[1] for c in corners]
        self.box2 = (min(xs) - 1, min(zs) - 1, max(xs) + 1, max(zs) + 1)
        self.bbox = (self.box2[0], DECK, self.box2[1], self.box2[2], DECK + 9, self.box2[3])   # y2 fixed later
        self.plan = None
        self._build()

    # ---- classification
    def classify(self, x, z):
        u, v = self.fr.uv(x, z)
        du0, du1, dv = u - U0, U1 - u, self.hw - abs(v)
        e = min(du0, du1, dv)
        if e < -0.5: return None, u, v
        if e < 1.1:
            if e == dv and dv < min(du0, du1) - 0.2: return 'side', u, v
            if e == du1 or du1 <= du0 and du1 < dv + 0.2 and e < 0.6: return 'front', u, v
            if e == du0: return 'back', u, v
            return 'side', u, v
        return 'in', u, v

    def _build(self):
        ctx = self.ctx
        x1, z1, x2, z2 = self.box2
        cls = {}
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                k, u, v = self.classify(x, z)
                if k is None: continue
                cls[(x, z)] = (k, u, v)
        self.cls = cls
        self.cells = {}
        p = Plan(self.name, self.bbox, ctx, 'dome', 'I')
        self.p = p
        door = self._door(cls)
        self.door = door
        fr = self.fr
        hw = self.hw
        out = (self.fr.ux, self.fr.uz)
        face_in = B.facing_from_vector(-out[0], -out[1])       # stair 'facing' with the tall back toward the city
        face_out = B.facing_from_vector(out[0], out[1])
        roofy = {}
        for (x, z), (k, u, v) in cls.items():
            roofy[(x, z)] = _cap(ctx, x, z) - 2
        self.roofy = roofy
        occupied = set()
        vi = lambda v: int(math.floor(v + hw + 0.5))
        # ---- floor, walls, roof, fins
        for (x, z), (k, u, v) in sorted(cls.items()):
            R = roofy[(x, z)]
            if R < WALK + 2: continue
            # floor paving (replaces the deck top)
            if abs(v) < 0.6: fl = 'cyan_concrete'
            elif k == 'in' and (vi(v) % 4 == 2 and int(u + 0.5) % 4 == 0): fl = SEALANT
            else: fl = QS if (vi(v) + int(u)) % 2 == 0 else 'polished_diorite'
            p.set(x, DECK, z, fl)
            if k == 'in':
                p.set(x, R, z, SEALANT if (vi(v) % 3 == 1 and int(u + 0.5) % 3 == 0) else GLB if (vi(v) % 2 == 0) else QS)
                for y in range(WALK, R): p.set(x, y, z, 'air')
                continue
            # wall cells
            isdoor = door is not None and (x, z) in door
            if k == 'front':
                pil = vi(v) % 4 == 0 or abs(abs(v) - hw) < 1.2
                for y in range(WALK, R):
                    if isdoor: p.set(x, y, z, 'air'); continue
                    if pil: b = QP
                    elif y == WALK: b = QS
                    else: b = GLB if self.kind != 'shrine' else QB
                    p.set(x, y, z, b)
                p.set(x, R, z, QCH if isdoor or pil else QS)
            else:
                for y in range(WALK, R):
                    if k == 'back' and y >= R - 2 and vi(v) % 3 != 0 and self.kind != 'shrine': b = GLB
                    elif k == 'side' and y == R - 1 and vi(v) % 3 == 1: b = GLB
                    elif abs(abs(v) - hw) < 1.2 and k != 'back' and False: b = QP
                    else: b = QB
                    if self.kind == 'shrine' and y == WALK + 2 and (vi(v) + int(u)) % 3 == 0: b = self._inscr(x, z, y)
                    p.set(x, y, z, b)
                p.set(x, R, z, QS)
        # ---- fins on the roof (2 high, cut the roof into strips of <= 5 x 8 cells)
        for (x, z), (k, u, v) in cls.items():
            R = roofy[(x, z)]
            if R < WALK + 2: continue
            if vi(v) % 6 == 0 and R + 2 <= _cap(ctx, x, z):
                # a fin is a pillar topped by a wall post (surface +1.5): nothing can climb onto it from a roof strip
                p.set(x, R + 1, z, SEALANT if int(u) % 3 == 0 else QP)
                p.set(x, R + 2, z, B.wall('diorite_wall'))
        # ---- furniture
        self.furnish(cls, roofy, p, face_in, face_out, occupied)
        # ---- exterior lights beside the entrance and the corners (hidden light blocks, not on a road)
        for (u, v) in ((U1 + 1.4, -hw - 1.2), (U1 + 1.4, hw + 1.2), (U1 + 1.4, 0.0), (U0 - 1.4, -hw), (U0 - 1.4, hw), (U0 - 1.4, 0.0)):
            x, z = fr.xz(u, v)
            if (x, z) not in cls and p.cap(x, z) >= WALK: p.set(x, WALK, z, LIGHT)
        # tighten the bbox to the produced cells (y2 = highest used)
        c = p.c
        top = max(y for (_, y, _) in c)
        self.bbox = (self.bbox[0], DECK, self.bbox[2], self.bbox[3], top, self.bbox[5])
        p.bbox = self.bbox
        p.finish()
        interior = [(x, z) for (x, z), (k, u, v) in cls.items() if k == 'in' and roofy[(x, z)] >= WALK + 2]
        ix = [a for a, _ in interior]; iz = [b for _, b in interior]
        rtop = max(roofy[k] for k in cls)
        self.meta = {'kind': 'perimeter_' + self.kind, 'district': 'I', 'interior': True,
                     'entrances': [(self.entrance[0], WALK, self.entrance[1])],
                     'interior_box': (min(ix), WALK, min(iz), max(ix), rtop - 1, max(iz)), 'roof_y': rtop}

    def _door(self, cls):
        """Door cells: front-wall cells within 1.6 of the lateral centre; the entrance is the one nearest v = 0."""
        cand = [((x, z), abs(v), u) for (x, z), (k, u, v) in cls.items() if k == 'front' and abs(v) < 1.7
                and self.ctx.max_building_top(x, z) - 2 >= WALK + 2]
        if not cand: return None
        cand.sort(key=lambda t: (t[1], -t[2]))
        self.entrance = cand[0][0]
        return {c for c, _, _ in cand}

    # ---- decoration helpers
    def _inscr(self, x, z, y):
        k = (x * 7 + z * 13 + y) % 5
        return ('cyan_concrete', 'light_blue_concrete', 'yellow_concrete', 'cyan_concrete', 'white_concrete')[k]

    def spot(self, u, v):
        return self.fr.xz(u, v)

    def furnish(self, cls, roofy, p, face_in, face_out, occ):
        fr, hw, kind, var = self.fr, self.hw, self.kind, self.var
        W = WALK

        def free(x, z, h=2):
            k = cls.get((x, z))
            if not k or k[0] != 'in' or (x, z) in occ: return False
            if roofy[(x, z)] - W < h + 1: return False
            return True

        def put(x, z, blocks):
            if not free(x, z, len(blocks)): return False
            occ.add((x, z))
            for i, b in enumerate(blocks): p.set(x, W + i, z, b)
            return True

        def pt(u, v): return fr.xz(u, v)

        uf = U1 - 2.2              # row beside the front glass
        um = (U0 + U1) / 2.0
        ub = U0 + 2.0              # row beside the back wall
        # keep the door lane (v within 2.4 of the entrance) and the centre line clear
        def lane(v): return abs(v) < 2.4 and kind != 'shrine'
        if kind == 'bay':
            v = -hw + 2.2
            while v < hw - 1.5:
                if not lane(v):
                    if var == 0 or (int(v) % 2 == 0):
                        put(*pt(uf, v), [stair('quartz', face_in)])
                    else:
                        put(*pt(uf, v), [stair('smooth_quartz', face_in)])
                v += 1.0
            # consoles on the back row
            for v in (-hw + 3.0, -hw + 6.0, hw - 3.0, hw - 6.0):
                x, z = pt(ub, v)
                put(x, z, ['dark_prismarine', stair('dark_prismarine', face_out, 'bottom')])
                x2, z2 = pt(ub - 0.0, v + 1.0)
                put(x2, z2, ['dark_prismarine', slab('dark_prismarine', 'bottom')])
            # telescopes (iron + end rod) / glass pedestals
            for v in ((-4.0, 4.0) if var != 1 else (-5.0, 0.0, 5.0)):
                if lane(v): continue
                x, z = pt(um + 0.5, v)
                put(x, z, ['polished_deepslate', 'iron_bars' if var == 2 else 'end_rod'])
            # planters
            for v in (-hw + 1.4, hw - 1.4):
                put(*pt(um, v), ['moss_block', 'azalea'])
        elif kind == 'dock':
            # desk line along the back, chairs, a plot table with a glowing top, lockers
            for v in range(-5, 6):
                if abs(v) < 2 and False: continue
                put(*pt(ub, v), ['dark_prismarine', slab('quartz_slab', 'bottom')] if v % 3 == 0 else ['smooth_quartz', slab('quartz_slab', 'bottom')])
            for v in (-4.0, -1.0, 2.0, 5.0):
                put(*pt(um, v + 0.0), [stair('quartz', face_in)])
            for v in (-3.0, 0.0, 3.0):
                put(*pt(um + 1.2, v), ['light_blue_concrete', 'light_blue_stained_glass'] if v == 0.0 else ['cyan_concrete', 'sea_lantern'] if False else ['cyan_concrete'])
            for v in (-hw + 1.2, hw - 1.2):
                put(*pt(ub + 1.0, v), ['barrel[facing=up]', 'barrel[facing=up]'])
            put(*pt(uf, -hw + 1.8), [stair('prismarine_brick', face_in)])
            put(*pt(uf, hw - 1.8), [stair('prismarine_brick', face_in)])
        else:   # shrine: replica obelisk on a step, flanked by lit inscription bars
            cx_, cz_ = pt(um, 0.0)
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if free(cx_ + dx, cz_ + dz, 1):
                        p.set(cx_ + dx, W, cz_ + dz, 'chiseled_quartz_block' if (dx or dz) else 'sea_lantern'); occ.add((cx_ + dx, cz_ + dz))
            for dx in (0, 1):
                for dz in (0, 1):
                    pass
            for y in range(W + 1, W + 5):
                p.set(cx_, y, cz_, QP if y < W + 4 else QCH)
            p.set(cx_, W + 5, cz_, 'smooth_quartz_slab[type=bottom]') if roofy[(cx_, cz_)] > W + 5 else None
            for dv in (-1, 1):
                x, z = pt(um + 0.0, dv * 2.0)
                put(x, z, ['cyan_concrete', 'yellow_concrete', 'cyan_concrete'])
            for i, v in enumerate((-3.0, 3.0)):
                put(*pt(ub, v), ['light_blue_concrete', 'sea_lantern'])
        # hidden lights keep the interior lit at floor level (ceiling lanterns cover the rest)
        for u in (ub, uf):
            for v in range(int(-hw + 2), int(hw - 1), 4):
                x, z = pt(u, v)
                if free(x, z, 1) and roofy[(x, z)] - W >= 3: p.set(x, W + 2, z, LIGHT)


# ================================================================================================================
# (b) the ocean research facility
# ================================================================================================================
LAB_F = 8                    # floor block y of the facility (feet y = 9)
LAB_CX = 667                 # hub centre x (z = 0)
TUN_X0, TUN_X1 = 621, 646    # tunnel x range (x = 621 is the opened station wall)
TUN_RISE0 = 628              # the floor starts rising after this x (half-block steps: stair, full, stair ...)
CRATER_X0 = 643
CRATER_D0 = 5
LAB_NAME = 'perim_ocean_lab'


def tunnel_floor(x):
    """(floor y, is_stair) of the tunnel column x."""
    s = max(0, min(2 * LAB_F, x - TUN_RISE0))
    return (s // 2, False) if s % 2 == 0 else ((s + 1) // 2, True)


def _rooms():
    """List of (tag, test(x, z) -> bool, y_top_fn(x, z) -> top air y offset above F), all interior boxes in x/z."""
    cx = LAB_CX
    rooms = []

    def rect(tag, x1, x2, z1, z2, h=4):
        rooms.append((tag, (lambda x, z, a=x1, b=x2, c=z1, d=z2: a <= x <= b and c <= z <= d), (lambda x, z, h=h: h)))

    def hub_h(x, z):
        r = math.hypot(x - cx, z)
        return 4 + int(round(4 * max(0.0, 1.0 - (r / 8.2) ** 2)))
    rooms.append(('hub', lambda x, z: math.hypot(x - cx, z) <= 8.2, hub_h))
    rect('lobby', cx - 21, cx - 11, -4, 4)
    rect('lab', cx - 7, cx + 7, -20, -11)
    rect('dorm', cx - 7, cx + 7, 11, 20)
    rect('spec', cx + 11, cx + 22, -5, 5)
    # door passages (3 wide, 3 high) between rooms
    def door(tag, x1, x2, z1, z2): rect(tag, x1, x2, z1, z2, h=3)
    door('d_lobby', cx - 12, cx - 9, -1, 1)
    door('d_lab', cx - 1, cx + 1, -11, -8)
    door('d_dorm', cx - 1, cx + 1, 8, 11)
    door('d_spec', cx + 9, cx + 12, -1, 1)
    return rooms


def build_lab(ctx):
    F = LAB_F
    cx = LAB_CX
    bbox = (TUN_X0, 0, -60, 718, 80, 60)
    p = Plan(LAB_NAME, bbox, ctx, 'free')
    sea = lambda x, z: BG.seabed_height(ctx, x, z)
    air = {}                       # (x, y, z) -> tag
    # ---- interior air
    for (tag, test, hfn) in _rooms():
        for x in range(cx - 23, cx + 25):
            for z in range(-22, 23):
                if test(x, z):
                    for y in range(F + 1, F + 1 + hfn(x, z)):
                        air.setdefault((x, y, z), tag)
    for x in range(TUN_X0, TUN_X1 + 1):
        yf, st = tunnel_floor(x)
        for z in (-1, 0, 1):
            for y in range(yf + 1, yf + 4):
                air[(x, y, z)] = 'tunnel'
    # ---- hull: layer 1 (faces of air), layer 2 (faces of facility layer 1)
    D6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    hull1 = {}
    for (x, y, z), tag in air.items():
        for d in D6:
            q = (x + d[0], y + d[1], z + d[2])
            if q not in air:
                hull1.setdefault(q, []).append((d, tag))
    for q in [q for q in hull1 if q[0] <= TUN_X0]: del hull1[q]      # the station supplies its own walls at x <= 621
    hull2 = {}
    for q, lst in hull1.items():
        if all(t == 'tunnel' for _, t in lst): continue
        for d in D6:
            r = (q[0] + d[0], q[1] + d[1], q[2] + d[2])
            if r not in air and r not in hull1: hull2[r] = q

    def exposed(q, d):
        """True when the cell beyond q (away from the air) is water, i.e. above the seabed."""
        b = (q[0] + d[0], q[1] + d[1], q[2] + d[2])
        return b not in air and b not in hull1 and b[1] > sea(b[0], b[2])

    glass_pairs = []
    mat = {}
    for q, lst in hull1.items():
        x, y, z = q
        dirs = {d: t for d, t in lst}
        tags = {t for _, t in lst}
        tunnel = tags == {'tunnel'}
        if (0, -1, 0) in dirs:                     # air below: ceiling
            b = 'prismarine_bricks'
            if x % 4 == 0 and z % 4 == 2: b = SEALANT
            elif tunnel and x % 4 == 1 and z == 0: b = SEALANT
            elif tunnel and exposed(q, (0, 1, 0)) and x % 3 != 0: b = GLB
            mat[q] = b
        elif (0, 1, 0) in dirs:                    # air above: floor
            if tunnel:
                if z == 0 and x % 4 == 2 and not tunnel_floor(x)[1]: b = SEALANT
                else: b = QS if abs(z) < 1 else 'prismarine_bricks'
                st = tunnel_floor(x)
                if st[1] and y == st[0]: b = stair('smooth_quartz', 'east')
            else:
                b = QS if ((x // 2) + (z // 2)) % 2 == 0 else 'polished_diorite'
                if z == 0 and abs(x - cx) > 9 and x % 4 == 0: b = SEALANT
            mat[q] = b
        else:                                      # side wall
            rel = y - F
            win = False
            d = next(iter(dirs))
            if not tunnel:
                tag = dirs[d]
                win = tag in ('lab', 'spec', 'hub', 'dorm') and 2 <= rel <= 3 and exposed(q, (-d[0], 0, -d[2])) and \
                    ((x + z) % 4 in (1, 2))
                if tag == 'hub': win = win and rel <= 4
            else:
                win = 2 <= rel <= 3 and (x % 3 == 0) and exposed(q, (-d[0], 0, -d[2]))
            if win:
                mat[q] = GLB if not tunnel else GLC
                glass_pairs.append((q, (q[0] - d[0], q[1], q[2] - d[2])))
            else:
                mat[q] = 'prismarine_bricks' if (y + x + z) % 5 else 'prismarine'
    for q, src in hull2.items():
        mat[q] = 'dark_prismarine' if (q[1] - F) % 4 else 'prismarine_bricks'
    for q1, q2 in glass_pairs:
        if q2 in hull2: mat[q2] = 'glass'
    # the tunnel stair steps (floor cell of odd columns) are stairs already; make sure the cell below is solid
    # ---- moon pool look in the hub floor (blue glass over lanterns)
    for x in range(cx - 4, cx + 5):
        for z in range(-4, 5):
            r = math.hypot(x - cx, z)
            if r <= 2.7:
                mat[(x, F, z)] = 'blue_stained_glass' if r > 1.2 else GLB
                mat[(x, F - 1, z)] = SEALANT if r <= 1.7 else 'light_blue_stained_glass'
    # ---- the station window becomes the tunnel mouth (x 621, y 1..3) -> air cells already; walls of the tunnel sides
    # ---- foundation: pylons from hull bottoms down to the seabed
    lows = {}
    for (x, y, z) in mat:
        if (x, z) not in lows or y < lows[(x, z)]: lows[(x, z)] = y
    for (x, z), yb in lows.items():
        s = sea(x, z)
        for y in range(s + 1, yb):
            mat[(x, y, z)] = 'dark_prismarine'
    # ---- pit (crater) around the hull: explicit water above the bench
    hx1, hx2, hz1, hz2 = cx - 23, cx + 24, -22, 22
    cells = {}
    SL = 1.0
    rmax2 = 716.0 ** 2
    for x in range(CRATER_X0, 719):
        for z in range(-58, 59):
            if x * x + z * z > rmax2: continue
            dx = max(hx1 - x, 0, x - hx2); dz = max(hz1 - z, 0, z - hz2)
            d = math.hypot(dx, dz)
            s = sea(x, z)
            y0 = F + 1 + int(max(0.0, d - CRATER_D0) * SL)
            for y in range(y0, s + 1):
                cells[(x, y, z)] = 'water'
            if y0 <= s and d <= CRATER_D0 + 0.5:
                cells[(x, y0 - 1, z)] = 'prismarine_bricks' if (x + z) % 3 else 'dark_prismarine'
    for k, b in cells.items(): p.set(k[0], k[1], k[2], b)
    for k, b in mat.items(): p.set(k[0], k[1], k[2], b)
    # ---- air (explicit)
    furn = {}
    _furnish_lab(F, cx, air, furn)
    for k in air:
        p.set(k[0], k[1], k[2], furn.get(k, 'air'))
    for k, b in furn.items():
        if k not in air: p.set(k[0], k[1], k[2], b)
    p.air = set(air)
    p.hullcells = set(mat)
    p.finish()
    return p


def _furnish_lab(F, cx, air, f):
    def put(x, y, z, b):
        if (x, y, z) in air and (x, y, z) not in f: f[(x, y, z)] = b

    def console(x, z, face):
        put(x, F + 1, z, 'dark_prismarine')
        put(x, F + 2, z, stair('dark_prismarine', face))

    def tank(x0, z0):          # 3 x 3 x 4 glass tank, blue glass core, no water
        for dx in range(3):
            for dz in range(3):
                x, z = x0 + dx, z0 + dz
                put(x, F + 1, z, SEALANT if (dx == 1 and dz == 1) else 'prismarine_bricks')
                for y in (F + 2, F + 3):
                    put(x, y, z, 'blue_stained_glass' if (dx == 1 and dz == 1) else 'glass')
                put(x, F + 4, z, slab('prismarine_brick', 'bottom'))
    # hub: ring of consoles, pool rim
    for i in range(8):
        a = math.radians(45 * i + 22.5)
        x = cx + int(round(6 * math.cos(a))); z = int(round(6 * math.sin(a)))
        if abs(z) <= 1 and x < cx - 3: continue            # keep the lobby door free
        if abs(x - cx) <= 1 and abs(z) >= 5: continue      # lab / dorm doors
        if abs(z) <= 1 and x > cx + 3: continue
        face = B.facing_from_vector(-(x - cx), -z)
        console(x, z, face)
    for x in range(cx - 4, cx + 5):
        for z in range(-4, 5):
            r = math.hypot(x - cx, z)
            if 2.8 < r <= 3.6: put(x, F + 1, z, slab('prismarine_brick', 'bottom'))
    # lobby: lockers, benches, a console desk
    for z in (-3, 3):
        for x in range(cx - 20, cx - 12, 2):
            put(x, F + 1, z, 'barrel[facing=up]'); put(x, F + 2, z, 'light_blue_carpet')
    for x in range(cx - 19, cx - 13, 2):
        put(x, F + 1, 0, stair('smooth_quartz', 'west')) if False else None
    put(cx - 21, F + 1, -2, 'dark_prismarine'); put(cx - 21, F + 2, -2, stair('dark_prismarine', 'west'))
    put(cx - 21, F + 1, 2, 'dark_prismarine'); put(cx - 21, F + 2, 2, stair('dark_prismarine', 'west'))
    # lab: tanks along the north wall, counters, consoles
    for i in range(4):
        tank(cx - 7 + 4 * i, -20)
    for x in range(cx - 6, cx - 1):
        put(x, F + 1, -15, 'smooth_quartz'); put(x, F + 2, -15, slab('quartz', 'bottom'))
    for x in range(cx + 2, cx + 7):
        put(x, F + 1, -15, 'smooth_quartz'); put(x, F + 2, -15, slab('quartz', 'bottom'))
    put(cx - 4, F + 3, -15, 'brewing_stand'); put(cx + 4, F + 3, -15, 'brewing_stand')
    for x in (cx - 6, cx + 6):
        console(x, -12, 'south')
        console(x, -18 + 0, 'south') if False else None
    # specimen hall: tanks along both long walls, a holo table
    for i in range(3):
        tank(cx + 12 + 4 * i, -5) if i < 3 else None
    for i in range(3):
        tank(cx + 12 + 4 * i, 3)
    put(cx + 16, F + 1, 0, 'polished_blackstone'); put(cx + 17, F + 1, 0, 'polished_blackstone')
    put(cx + 16, F + 2, 0, 'light_blue_stained_glass'); put(cx + 17, F + 2, 0, 'cyan_stained_glass')
    # dorm: four bunks on the south wall, rug, lockers, desk
    for i, x in enumerate((cx - 6, cx - 3, cx + 1, cx + 4)):
        put(x, F + 1, 20, f'{("cyan", "light_blue", "white", "blue")[i]}_bed[facing=north,part=head]')
        put(x, F + 1, 19, f'{("cyan", "light_blue", "white", "blue")[i]}_bed[facing=north,part=foot]')
        put(x + 1, F + 1, 20, 'barrel[facing=up]')
    for x in range(cx - 4, cx + 5):
        for z in (14, 15):
            put(x, F + 1, z, 'light_blue_carpet')
    for x in (cx - 6, cx + 6):
        put(x, F + 1, 12, 'smooth_quartz'); put(x, F + 2, 12, stair('quartz', 'north'))
    # hidden lights in the corners keep every cell lit even where the lantern lattice is shadowed
    for (x, y, z), t in list(air.items()):
        if t in ('hub',) and (x - cx) % 6 == 3 and z % 6 == 3 and y == F + 3 and (x, y, z) not in f:
            f[(x, y, z)] = LIGHT


# ================================================================================================================
# (c) the ruins
# ================================================================================================================
RUIN_PLAN = 210
RUIN_R = 668
RUIN_NAME = 'ruins_circle'


def build_ruins(ctx):
    cx, cz = [int(round(v)) for v in PL.from_polar(RUIN_R, RUIN_PLAN)]
    PADR, SKIRT = 21, 9
    Rm = PADR + SKIRT + 2
    bbox = (cx - Rm, ctx.y_min + 1, cz - Rm, cx + Rm, 90, cz + Rm)
    p = Plan(RUIN_NAME, bbox, ctx, 'free')
    sea = lambda x, z: BG.seabed_height(ctx, x, z)
    Yp = sea(cx, cz)
    r = lambda *k: _rnd(RUIN_NAME, ctx.seed, *k)
    W = True
    # ---- terrace: flat pad, blended skirt
    for x in range(cx - Rm, cx + Rm + 1):
        for z in range(cz - Rm, cz + Rm + 1):
            d = math.hypot(x - cx, z - cz)
            if d > PADR + SKIRT: continue
            s = sea(x, z)
            t = Yp if d <= PADR else int(round(Yp + (s - Yp) * (d - PADR) / float(SKIRT)))
            if s > t:
                for y in range(t + 1, s + 1): p.set(x, y, z, 'water')
            elif s < t:
                for y in range(s + 1, t): p.set(x, y, z, 'cobblestone' if r(x, z) < 0.5 else 'mossy_cobblestone')
            top = t
            # surface
            if d <= PADR:
                b = _plaza(x - cx, z - cz, r)
                p.set(x, top, z, b)
            else:
                p.set(x, top, z, 'moss_block' if r(x, z, 1) < 0.35 else 'mossy_cobblestone')
    top = Yp
    # ---- central dais and the broken standing obelisk
    for dx in range(-3, 4):
        for dz in range(-3, 4):
            if max(abs(dx), abs(dz)) <= 3:
                p.set(cx + dx, top + 1, cz + dz, 'stone_bricks' if (dx + dz) % 2 else 'cracked_stone_bricks')
    for dx in range(-2, 3):
        for dz in range(-2, 3):
            p.set(cx + dx, top + 2, cz + dz, 'mossy_stone_bricks' if r(dx, dz, 7) < 0.4 else 'chiseled_stone_bricks')
    p.set(cx, top + 2, cz, SEALANT)
    for y in range(top + 3, top + 9):
        for dx in (0, 1):
            for dz in (0, 1):
                if y >= top + 7 and (dx + dz) == 2 and True: continue
                if y == top + 8 and (dx, dz) != (0, 0): continue
                p.set(cx + dx, y, cz + dz, 'mossy_stone_bricks' if y % 3 == 0 else 'stone_bricks' if y % 3 == 1 else 'cracked_stone_bricks')
    p.set(cx + 1, top + 7, cz, stair('stone_brick', 'west', wl=W))
    # ---- ring of broken columns
    heights = [8, 3, 6, 0, 9, 2, 5, 7, 1, 4, 8, 3]
    for k in range(12):
        a = math.radians(30 * k + 15 + (r(k, 3) - 0.5) * 8)
        rr = 15 + (r(k, 4) - 0.5) * 2
        bx, bz = cx + int(round(rr * math.cos(a))), cz + int(round(rr * math.sin(a)))
        # entrance gap toward the city (north of the pad)
        if abs(bx - cx) <= 6 and bz < cz - 10: continue
        _column(p, bx, bz, top, heights[k], r, k)
    # ---- fallen drums and two fallen obelisks
    for k, (dx, dz, ax) in enumerate(((-9, 5, 'x'), (10, 8, 'z'), (-3, -12, 'x'), (6, -4, 'z'))):
        n = 4
        for i in range(n):
            for w in (0, 1):
                x, z = (cx + dx + i, cz + dz + w) if ax == 'x' else (cx + dx + w, cz + dz + i)
                p.set(x, top + 1, z, 'mossy_stone_bricks' if (i + w) % 2 else 'cracked_stone_bricks')
    _fallen_obelisk(p, cx - 12, cz - 5, top, 'x', 11, r)
    _fallen_obelisk(p, cx + 4, cz + 9, top, 'z', 9, r)
    # ---- the overgrown archway to the north (opening along z, spanning x)
    ax_, az_ = cx, cz - 19
    for sx in (-4, 3):
        for dx in (0, 1):
            for dz in (0, 1):
                for y in range(top + 1, top + 8):
                    p.set(ax_ + sx + dx, y, az_ + dz, 'mossy_stone_bricks' if y % 3 else 'cracked_stone_bricks')
        p.set(ax_ + sx, top + 8, az_, 'chiseled_stone_bricks')
    for dx in range(-3, 3):
        y = top + 8 + (1 if -2 <= dx <= 1 else 0)
        if dx == 1:
            continue                                        # a missing keystone block
        for dz in (0, 1):
            p.set(ax_ + dx, y, az_ + dz, 'stone_bricks' if dx % 2 else 'mossy_stone_bricks')
    p.set(ax_ - 3, top + 8, az_ + 1, stair('stone_brick', 'east', wl=W))
    p.set(ax_ + 2, top + 8, az_ + 1, stair('stone_brick', 'west', wl=W))
    for dx, dz in ((-4, 0), (-3, 1), (3, 0), (2, 0), (-2, 1), (-1, 0)):
        p.set(ax_ + dx, top + 9 + (1 if -2 <= dx <= 0 else 0) - (1 if dx in (-3, -4, 2, 3) else 0) + 0, az_ + dz,
              'moss_block' if (dx + dz) % 2 else 'moss_carpet')
    for dx in (-4, 4, -3, 3):
        p.set(ax_ + dx - (0 if dx < 0 else 0), top + 1, az_ + 2, 'moss_carpet')
    p.finish()
    p.center, p.top = (cx, cz), top
    return p


def _plaza(dx, dz, r):
    d = math.hypot(dx, dz)
    u = r(dx, dz, 11)
    if abs(d - 9.0) < 0.9 or abs(d - 4.4) < 0.7:
        return 'chiseled_stone_bricks' if u < 0.5 else 'prismarine_bricks' if u < 0.8 else 'mossy_stone_bricks'
    if u < 0.04: return 'cracked_deepslate_bricks'
    if u < 0.07: return 'moss_block'
    if u < 0.32: return 'cracked_stone_bricks'
    if u < 0.55: return 'mossy_stone_bricks'
    if u < 0.62: return 'dark_prismarine'
    return 'stone_bricks'


def _column(p, bx, bz, top, h, r, k):
    for dx in (-1, 0, 1, 2):
        for dz in (-1, 0, 1, 2):
            if max(abs(dx - 0.5), abs(dz - 0.5)) <= 1.5:
                p.set(bx + dx, top + 1, bz + dz, 'stone_bricks' if (dx + dz) % 2 else 'mossy_stone_bricks')
    if h == 0:
        p.set(bx, top + 2, bz, 'cracked_stone_bricks')
        return
    for y in range(top + 2, top + 2 + h):
        for dx in (0, 1):
            for dz in (0, 1):
                if (y == top + 1 + h) and r(k, dx, dz, 5) < 0.5: continue      # jagged break
                if (y == top + h) and (dx, dz) == (1, 1) and r(k, 6) < 0.5: continue
                i = y - top
                b = 'chiseled_stone_bricks' if i % 4 == 0 else ('mossy_stone_bricks' if r(k, y, dx, dz) < 0.4 else
                                                                  'cracked_stone_bricks' if r(k, y, dz, dx, 2) < 0.25 else 'stone_bricks')
                p.set(bx + dx, y, bz + dz, b)
    if h >= 8 and r(k, 8) < 0.5:
        p.set(bx, top + 2 + h, bz, stair('stone_brick', 'east', wl=True))


def _fallen_obelisk(p, x0, z0, top, ax, n, r):
    for i in range(n):
        w = 3 if i < n // 2 else 2 if i < n - 2 else 1
        for j in range(w):
            for y in (top + 1, top + 2):
                if y == top + 2 and j == w - 1 and w > 1 and i % 3 == 0: continue
                x, z = (x0 + i, z0 + j) if ax == 'x' else (x0 + j, z0 + i)
                b = 'cracked_stone_bricks' if r(x, z, y) < 0.3 else 'stone_bricks' if r(x, z, y, 1) < 0.6 else 'mossy_stone_bricks'
                if i >= n - 2: b = 'prismarine_bricks' if i == n - 1 else 'dark_prismarine'
                p.set(x, y, z, b)


# ================================================================================================================
# features
# ================================================================================================================
class PlanFeature(Feature):
    def __init__(self, name, bbox, layer, meta, maker):
        super().__init__(name, bbox, layer, meta)
        self.maker = maker
        self._plan = None

    def plan(self, ctx=None):
        if self._plan is None: self._plan = self.maker(ctx or _CTX)
        return self._plan

    def build(self, vol, ctx):
        if not vol.intersects(*self.bbox): return
        self.plan(ctx).write(vol)


def _lab_meta():
    cx, F = LAB_CX, LAB_F
    return {'kind': 'ocean_research_station', 'district': 'I', 'sealed': True, 'interior': True,
            'entrances': [(TUN_X0, 1, 0)],
            'interior_box': (cx - 21, F + 1, -20, cx + 22, F + 8, 20), 'roof_y': F + 9}


def _ruins_bbox():
    cx, cz = [int(round(v)) for v in PL.from_polar(RUIN_R, RUIN_PLAN)]
    Rm = 21 + 9 + 2
    return (cx - Rm, _CTX.y_min + 1, cz - Rm, cx + Rm, 90, cz + Rm)


def make_features(ctx=None):
    ctx = ctx or _CTX
    feats = []
    for s in SPECS:
        b = DomeBuilding(s, ctx)
        meta = dict(b.meta)
        # the axis-aligned bbox of an arc-shaped footprint reaches radii where the cap is lower than the tallest cells;
        # every CELL is clamped to ctx.max_building_top by Plan (tests/test_district_perimeter.py proves it)
        meta['clearance_ok'] = True
        f = PlanFeature(b.name, b.bbox, 40, meta, (lambda ctx, b=b: b.p))
        f._plan = b.p
        f.building = b
        feats.append(f)
    feats.append(PlanFeature(LAB_NAME, (TUN_X0, 0, -60, 718, 80, 60), 50, _lab_meta(), build_lab))
    feats.append(PlanFeature(RUIN_NAME, _ruins_bbox(), 50,
                             {'kind': 'old_ruins', 'district': 'J'}, build_ruins))
    return feats


FEATURES = make_features()
register(*FEATURES)


def features():
    return list(FEATURES)
