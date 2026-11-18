"""Ocean environment (layer 90 vegetation, layer 50 small structures): the seabed OUTSIDE the dome.

A restrained, planned underwater landscape between the dome plinth (r ~ 641) and the work-area edge (r 800):
  * kelp forests          kelp_plant column + kelp top, a few blocks under the surface at most
  * seagrass meadows      seagrass / tall_seagrass, a few sea pickles
  * coral reefs           mounds of coral blocks (every live block touches water), waterlogged fans and plants
  * research markers      prismarine pillars with one sea lantern each (the only lights out here)
  * beacons               taller markers on the far wall
  * sensor stations       small solid prismarine pods (sealed, no interior)
Rules kept by construction (and by tests/test_ocean_env.py):
  * every vegetation / coral column is >= CLEAR (30) blocks from the dome's outer surface (3D distance to the cap
    including its rim), so the glass keeps its view;
  * nothing in the airlock station approaches (dome.py STATION_PLANS) and nothing in plan angle 80..100 (exterior
    research facility) or 200..220 (underwater ruins);
  * deterministic and tile-independent: clusters are planned once from the seed, every column decision is a hash of
    (seed, x, z); features never write over cells another feature already set;
  * no gravity blocks anywhere (no sand / gravel / concrete powder in this module).
"""
import math
import random

from . import background, config, feature, terrain
from . import planner as PL
from .feature import Feature
from .util import hash_str

CLEAR = 30                    # minimum 3D distance from the dome's outer surface (the brief asks for 25)
R_MIN, R_MAX = 658, 797       # vegetation / structures live in this radius band
SECTORS = ((80.0, 100.0), (200.0, 220.0))     # plan angles owned by other builders (r >= 600)
SECTOR_MARGIN = 3.0           # extra clearance kept around those sectors
STATION_HALF = 36.0           # lateral half width of the airlock station approaches
KELP_TOP_GAP = 4              # kelp stops this many blocks under the highest water block
CORALS = ('tube', 'brain', 'bubble', 'fire', 'horn')
_FAN_DIRS = (('north', 0, -1), ('south', 0, 1), ('west', -1, 0), ('east', 1, 0))
GOOD_FLOOR = ('sand', 'gravel', 'clay', 'sandstone', 'tuff')      # blocks seagrass / pickles / kelp sit on happily


# ---------------------------------------------------------------- geometry helpers
def dome_distance(ctx, x, y, z):
    """Distance from (x, y, z) to the dome's outer glass surface (the spherical cap above y = 0 with its rim)."""
    r = math.hypot(x, z)
    dy = y - ctx.dome_cy
    rho = math.sqrt(r * r + dy * dy)
    if rho > 0:
        yh = ctx.dome_cy + ctx.dome_R * dy / rho            # height where the ray centre->point meets the sphere
        if yh >= 0:
            return abs(rho - ctx.dome_R)
    base = ctx.dome_R * math.sqrt(max(0.0, 1 - ((0 - ctx.dome_cy) / ctx.dome_R) ** 2))   # rim radius at y = 0
    return math.hypot(r - base, y)


def in_reserved_sector(x, z, margin=0.0):
    a = PL.plan_angle(x, z)
    return any(lo - margin <= a <= hi + margin for lo, hi in SECTORS)


def in_station_approach(x, z):
    r = math.hypot(x, z)
    a = PL.plan_angle(x, z)
    for w in PL.WEDGE_ANGLES:
        d = math.radians(PL.ang_diff(a, w))
        if abs(d) < math.pi / 2 and abs(r * math.sin(d)) < STATION_HALF: return True
    return False


_FORMS = {}


def _formation_boxes(ctx):
    k = ctx.seed
    if k not in _FORMS:
        _FORMS[k] = [f.bbox for f in terrain._make_formations(ctx)]
    return _FORMS[k]


def column_ok(ctx, x, z):
    """Vegetation / structure columns must satisfy this (cheap, per column)."""
    r2 = x * x + z * z
    if r2 < R_MIN * R_MIN or r2 > R_MAX * R_MAX: return False
    if in_reserved_sector(x, z, SECTOR_MARGIN) or in_station_approach(x, z): return False
    for b in _formation_boxes(ctx):
        if b[0] - 4 <= x <= b[3] + 4 and b[2] - 4 <= z <= b[5] + 4: return False
    return ctx.in_area(x, z)


def _slope(ctx, x, z):
    h = terrain.seabed_height
    s = h(ctx, x, z)
    return max(abs(h(ctx, x + 1, z) - s), abs(h(ctx, x - 1, z) - s), abs(h(ctx, x, z + 1) - s), abs(h(ctx, x, z - 1) - s))


def _hh(seed, x, z, k=0):
    return terrain._h(seed + 7919 * k, x, z)


# ---------------------------------------------------------------- planning
def plan(ctx):
    """All sites, planned once: dict of lists of (name, x, z, radius or size, extra)."""
    rng = random.Random(hash_str(ctx.seed, 'ocean_env_plan'))
    taken = []                                   # (x, z, r) of everything already planned
    out = {'sensor': [], 'marker': [], 'beacon': [], 'reef': [], 'kelp': [], 'meadow': []}

    def free(x, z, r, gap):
        return all(math.hypot(x - tx, z - tz) >= r + tr + gap for tx, tz, tr in taken)

    def sample_ok(x, z, r):
        if not column_ok(ctx, x, z): return False
        for k in range(8):
            a = k * math.pi / 4
            if not column_ok(ctx, int(x + r * math.cos(a)), int(z + r * math.sin(a))): return False
        return True

    def pick(kind, n, rlo, rhi, size_lo, size_hi, gap, sl_max, h_lo, h_hi, tries=4000):
        got = 0
        for _ in range(tries):
            if got >= n: break
            rr = rng.uniform(rlo, rhi)
            a = rng.uniform(0, 2 * math.pi)
            x, z = int(round(rr * math.cos(a))), int(round(rr * math.sin(a)))
            size = rng.uniform(size_lo, size_hi)
            h = terrain.seabed_height(ctx, x, z)
            if not (h_lo <= h <= h_hi): continue
            if not sample_ok(x, z, size + 2) or not free(x, z, size, gap): continue
            if _slope(ctx, x, z) > sl_max: continue
            taken.append((x, z, size))
            out[kind].append((x, z, int(size)))
            got += 1

    pick('sensor', 4, 668, 735, 6, 6, 70, 2, 8, 70)
    pick('beacon', 4, 735, 780, 4, 4, 90, 3, 40, 100)
    pick('marker', 14, 662, 720, 3, 3, 55, 2, 8, 70)
    pick('reef', 5, 676, 740, 12, 15, 60, 2, 12, 70)
    pick('kelp', 9, 670, 770, 24, 36, 18, 3, 10, 95)
    pick('meadow', 10, 664, 775, 16, 26, 12, 3, 8, 95)
    return out


# ---------------------------------------------------------------- vegetation features
class _Cluster(Feature):
    layer = 90

    def __init__(self, kind, idx, cx, cz, rad, ctx):
        self.kind, self.cx, self.cz, self.rad = kind, cx, cz, rad
        self.seed = hash_str(ctx.seed, 'ocean', kind, idx) & 0x7FFFFFFF
        hs = [terrain.seabed_height(ctx, int(cx + rad * math.cos(a * math.pi / 8)), int(cz + rad * math.sin(a * math.pi / 8)))
              for a in range(16)] + [terrain.seabed_height(ctx, cx, cz)]
        top = {'kelp': 40, 'meadow': 3, 'reef': 10}[kind]
        y2 = min(ctx.y_max, max(hs) + top)
        super().__init__(f'ocean_{kind}_{idx:02d}', (cx - rad, max(ctx.y_min, min(hs) - 1), cz - rad, cx + rad, y2, cz + rad),
                         90, {'kind': f'ocean_{kind}', 'ocean_env': True})

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        getattr(self, '_' + self.kind)(vol, ctx, c)

    def _falloff(self, x, z):
        d = math.hypot(x - self.cx, z - self.cz) / self.rad
        return d, (1 - d * d) if d < 1 else 0.0

    # kelp: tall stems, taller in the middle of the forest
    def _kelp(self, vol, ctx, c):
        sd = self.seed
        for z in range(c[2], c[5] + 1):
            for x in range(c[0], c[3] + 1):
                d, f = self._falloff(x, z)
                if f <= 0 or _hh(sd, x, z) > 0.05 + 0.17 * f: continue
                if not column_ok(ctx, x, z): continue
                s = background.seabed_height(ctx, x, z)
                if _slope(ctx, x, z) > 2: continue
                if vol.get(x, s, z) is not None or background.background(ctx, x, s, z) == 'deepslate': continue
                h = 6 + int((0.25 + 0.75 * f) * _hh(sd, x, z, 1) * 34)
                h = min(h, ctx.y_max - KELP_TOP_GAP - s)
                if h < 3: continue
                if any(vol.get(x, s + k, z) is not None for k in range(1, h + 1)): continue
                for k in range(1, h):
                    vol.set(x, s + k, z, 'kelp_plant')
                vol.set(x, s + h, z, 'kelp[age=25]')

    # seagrass meadows with a few sea pickles
    def _meadow(self, vol, ctx, c):
        sd = self.seed
        for z in range(c[2], c[5] + 1):
            for x in range(c[0], c[3] + 1):
                d, f = self._falloff(x, z)
                if f <= 0 or _hh(sd, x, z) > 0.12 + 0.50 * f: continue
                if not column_ok(ctx, x, z): continue
                s = background.seabed_height(ctx, x, z)
                if vol.get(x, s, z) is not None or vol.get(x, s + 1, z) is not None: continue
                if background.background(ctx, x, s, z) not in GOOD_FLOOR: continue
                u = _hh(sd, x, z, 2)
                if u < 0.035:
                    vol.set(x, s + 1, z, f'sea_pickle[pickles={1 + int(_hh(sd, x, z, 3) * 3.99)},waterlogged=true]')
                elif u < 0.30 and vol.get(x, s + 2, z) is None:
                    vol.set(x, s + 1, z, 'tall_seagrass[half=lower]')
                    vol.set(x, s + 2, z, 'tall_seagrass[half=upper]')
                else:
                    vol.set(x, s + 1, z, 'seagrass')

    # coral reef: blobs of coral blocks (shell of live coral, dead coral core), plants / fans / pickles on top
    def _reef_model(self, ctx):
        """(cells {(x,y,z): block}) for the whole reef; independent of any tile."""
        sd, cx, cz, R = self.seed, self.cx, self.cz, self.rad
        rng = random.Random(sd)
        blobs = []
        for i in range(6 + rng.randrange(3)):
            a = rng.uniform(0, 2 * math.pi)
            d = rng.uniform(0, 0.55) * R
            blobs.append((cx + d * math.cos(a), cz + d * math.sin(a), rng.uniform(3.0, 5.5), rng.randint(2, 6), i % 5))
        sc = {}

        def seabed(x, z):
            v = sc.get((x, z))
            if v is None: v = sc[(x, z)] = background.seabed_height(ctx, x, z)
            return v

        hc = {}

        def mound(x, z):
            v = hc.get((x, z))
            if v is None:
                best, sp = 0, 0
                if column_ok(ctx, x, z):
                    for bx, bz, rb, hb, k in blobs:
                        dd = ((x - bx) ** 2 + (z - bz) ** 2) / (rb * rb)
                        if dd < 1:
                            h = int(math.ceil(hb * (1 - dd) ** 0.6))
                            if h > best: best, sp = h, k
                v = hc[(x, z)] = (best, sp)
            return v

        cells = {}
        rr = int(R)
        for z in range(cz - rr, cz + rr + 1):
            for x in range(cx - rr, cx + rr + 1):
                h, sp = mound(x, z)
                if h <= 0: continue
                s = seabed(x, z)
                for y in range(s + 1, s + h + 1):
                    wet = y == s + h
                    for _, dx, dz in _FAN_DIRS:
                        nh = mound(x + dx, z + dz)[0]
                        if y > seabed(x + dx, z + dz) + nh: wet = True
                    if wet:
                        k = sp if _hh(sd, x, z, 5 + y) > 0.2 else int(_hh(sd, x, z, 9 + y) * 5) % 5
                        cells[(x, y, z)] = f'{CORALS[k]}_coral_block'
                    else:
                        cells[(x, y, z)] = f'dead_{CORALS[int(_hh(sd, x, z, 11 + y) * 5) % 5]}_coral_block'
        # decoration in the water cells touching the mound
        deco = {}
        for (x, y, z), b in list(cells.items()):
            if b.startswith('dead_'): continue
            top = (x, y + 1, z) not in cells
            if top:
                u = _hh(sd, x, z, 21)
                k = int(_hh(sd, x, z, 22) * 5) % 5
                if u < 0.30: deco[(x, y + 1, z)] = f'{CORALS[k]}_coral[waterlogged=true]'
                elif u < 0.52: deco[(x, y + 1, z)] = f'{CORALS[k]}_coral_fan[waterlogged=true]'
                elif u < 0.58: deco[(x, y + 1, z)] = f'sea_pickle[pickles={1 + int(_hh(sd, x, z, 23) * 3.99)},waterlogged=true]'
            for fac, dx, dz in _FAN_DIRS:
                p = (x + dx, y, z + dz)
                if p in cells or p in deco: continue
                if y <= seabed(p[0], p[2]) + mound(p[0], p[2])[0]: continue
                if _hh(sd, x + 3 * dx, z + 3 * dz, 31 + y) < 0.22:
                    k = int(_hh(sd, p[0], p[2], 32 + y) * 5) % 5
                    deco[p] = f'{CORALS[k]}_coral_wall_fan[facing={fac},waterlogged=true]'
        cells.update(deco)
        return cells

    def _reef(self, vol, ctx, c):
        for (x, y, z), b in self._reef_model(ctx).items():
            if c[0] <= x <= c[3] and c[2] <= z <= c[5] and vol.get(x, y, z) is None:
                vol.set(x, y, z, b)


# ---------------------------------------------------------------- structures (layer 50)
class _Struct(Feature):
    layer = 50

    def __init__(self, kind, idx, cx, cz, size, ctx):
        self.kind, self.cx, self.cz, self.size = kind, cx, cz, size
        h = size // 2 + 1                                   # half footprint
        hs = [terrain.seabed_height(ctx, cx + dx, cz + dz) for dx in (-h, 0, h) for dz in (-h, 0, h)]
        self.base = max(hs)
        height = {'marker': 9, 'beacon': 17, 'sensor': 10}[kind]
        super().__init__(f'ocean_{kind}_{idx:02d}', (cx - h, min(hs) - 1, cz - h, cx + h, self.base + height + 1, cz + h),
                         50, {'kind': f'ocean_{kind}', 'ocean_env': True, 'solid': True})

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        getattr(self, '_' + self.kind)(vol, ctx)

    def _footing(self, vol, ctx, x, z, top, block):
        s = background.seabed_height(ctx, x, z)
        for y in range(s + 1, top + 1):
            vol.set(x, y, z, block)

    def _marker(self, vol, ctx, tall=False):
        cx, cz, b = self.cx, self.cz, self.base
        for dx in (-1, 0, 1):                               # 3x3 footing, stepped
            for dz in (-1, 0, 1):
                x, z = cx + dx, cz + dz
                self._footing(vol, ctx, x, z, b + 1, 'dark_prismarine')
        sh = 15 if tall else 7
        for y in range(b + 2, b + sh):
            vol.set(cx, y, cz, 'prismarine_bricks' if (y - b) % 4 else 'dark_prismarine')
        if tall:
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                for y in range(b + 2, b + 5):
                    vol.set(cx + dx, y, cz + dz, 'prismarine_bricks')
        vol.set(cx, b + sh, cz, 'sea_lantern')
        vol.set(cx, b + sh + 1, cz, 'prismarine_brick_slab[type=bottom,waterlogged=true]')
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            vol.set(cx + dx, b + sh, cz + dz, 'prismarine_brick_slab[type=top,waterlogged=true]')

    def _beacon(self, vol, ctx):
        self._marker(vol, ctx, tall=True)

    def _sensor(self, vol, ctx):
        """Solid pod: stepped octagon, a tinted-glass band and a mast. Nothing inside."""
        cx, cz, b = self.cx, self.cz, self.base
        layers = ((3.6, 'dark_prismarine'), (3.6, 'prismarine_bricks'), (3.0, 'prismarine'), (2.2, 'prismarine_bricks'),
                  (1.4, 'dark_prismarine'))
        for i, (rad, blk) in enumerate(layers):
            y = b + 1 + i
            for dx in range(-4, 5):
                for dz in range(-4, 5):
                    if math.hypot(dx, dz) > rad: continue
                    mat = blk
                    if i == 2 and (abs(dx) + abs(dz)) in (3, 4) and (dx == 0 or dz == 0 or abs(dx) == abs(dz)):
                        mat = 'tinted_glass'
                    if i == 0: self._footing(vol, ctx, cx + dx, cz + dz, y, mat)
                    else: vol.set(cx + dx, y, cz + dz, mat)
        for k in range(1, 4):
            vol.set(cx, b + 5 + k, cz, 'prismarine_bricks')
        vol.set(cx, b + 9, cz, 'dark_prismarine')


# ---------------------------------------------------------------- registration
def make_features(ctx):
    p = plan(ctx)
    fs = []
    for kind in ('sensor', 'marker', 'beacon'):
        for i, (x, z, sz) in enumerate(p[kind]):
            fs.append(_Struct(kind, i, x, z, {'sensor': 7, 'marker': 3, 'beacon': 3}[kind], ctx))
    for kind in ('reef', 'kelp', 'meadow'):
        for i, (x, z, rad) in enumerate(p[kind]):
            fs.append(_Cluster(kind, i, x, z, rad, ctx))
    return fs


def features():
    return list(_FEATURES)


def _register():
    global _FEATURES
    _FEATURES = make_features(config.load())
    feature.register(*_FEATURES)


_FEATURES = []
_register()
