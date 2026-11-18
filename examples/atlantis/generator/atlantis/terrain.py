"""Layer 0: the abyssal basin and the rock formations on its wall.

Installs the seabed relief into background.set_seabed() at import. Profile (r = distance from the city centre):
  r <= BASIN_R (606)        height 0 (level base under the dome)
  606 .. ~640               gentle floor-ring relief (0..~4)
  ~618 .. 772               terraced basin wall rising to the edge height (local 101): stepped cliffs, ledges
  772 .. 800                smooth blend to exactly the edge height so the wall meets the natural world, no lip
Everything is deterministic (stable integer hash value noise, seeded from ctx.seed).
"""
import math

from . import background
from .feature import Feature, register
from .util import hash_str

BASIN_R = 606.0
WALL_R0 = 618.0           # wall starts rising
WALL_R1 = 772.0           # wall reaches the rim height
BLEND_R0 = 772.0          # final blend to exactly the edge height (to the area radius)
N_STEPS = 17              # terraces on the wall
WARP_AMP = 24.0           # angular warp of the wall radius (blocks)
DEEPSLATE_Y = 56          # natural world deepslate line (local y)
TOP_LIMIT = 111           # rock formations never go above this local y

_SEED = {}


def _seed_for(ctx):
    s = _SEED.get(ctx.seed)
    if s is None:
        s = _SEED[ctx.seed] = hash_str('terrain', ctx.seed) & 0x7FFFFFFF
    return s


def _h(seed, ix, iz):
    """Stable 32-bit integer hash -> float 0..1."""
    n = (ix * 374761393 + iz * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    n ^= n >> 16
    return n / 4294967295.0


def vnoise(seed, x, z):
    """Smooth value noise in 0..1, lattice spacing 1."""
    ix, iz = math.floor(x), math.floor(z)
    fx, fz = x - ix, z - iz
    fx = fx * fx * (3 - 2 * fx)
    fz = fz * fz * (3 - 2 * fz)
    a = _h(seed, ix, iz); b = _h(seed, ix + 1, iz)
    c = _h(seed, ix, iz + 1); d = _h(seed, ix + 1, iz + 1)
    top = a + (b - a) * fx
    return top + ((c + (d - c) * fx) - top) * fz


def fbm(seed, x, z, octaves=3):
    v, amp, tot, f = 0.0, 1.0, 0.0, 1.0
    for o in range(octaves):
        v += amp * vnoise(seed + o * 101, x * f, z * f)
        tot += amp; amp *= 0.5; f *= 2.0
    return v / tot


def _smooth(t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return t * t * (3 - 2 * t)


# plan-angle sectors owned by other builders (exterior research facility, ruins): the extra relief of this wave
# fades to nothing inside them so their ground stays exactly as it was
KEEP_SECTORS = (90.0, 210.0)
KEEP_HALF = 10.0
KEEP_FADE = 8.0


def sector_gain(x, z):
    """0 inside the reserved plan-angle sectors, 1 clear of them (smooth)."""
    a = math.degrees(math.atan2(x, -z)) % 360.0
    d = min(abs((a - c + 180.0) % 360.0 - 180.0) for c in KEEP_SECTORS)
    return _smooth((d - KEEP_HALF) / KEEP_FADE)


def edge_height(ctx):
    p = ctx.profile
    return p['edge_seabed_y'] - p['floor_y']


def seabed_height(ctx, x, z):
    r = math.hypot(x, z)
    if r <= BASIN_R: return 0
    e = edge_height(ctx)
    if r >= ctx.area_radius: return e
    sd = _seed_for(ctx)
    ang = math.atan2(z, x)
    ca, sa = math.cos(ang), math.sin(ang)
    # angle-dependent noise sampled on a circle in noise space so there is no seam at +-pi
    w1 = fbm(sd + 7, 1.7 * ca + 3, 1.7 * sa + 3, 2)                 # large lobes
    w2 = vnoise(sd + 13, 9.0 * ca + 5, 9.0 * sa + 5)                # finer wobble
    warp = WARP_AMP * ((w1 - 0.5) * 2 * 0.8 + (w2 - 0.5) * 2 * 0.2)
    reff = r + warp * _smooth((r - BASIN_R) / 20.0)
    u = (reff - WALL_R0) / (WALL_R1 - WALL_R0)
    u = 0.0 if u < 0 else 1.0 if u > 1 else u
    # terraces: the phase carries a little noise so ledges are not perfectly concentric
    ph = u * N_STEPS + 2.2 * (fbm(sd + 29, x / 80.0, z / 80.0, 2) - 0.5)
    k = math.floor(ph)
    riser = _smooth((ph - k - 0.42) / 0.42)
    lvl = (k + riser) / N_STEPS
    lvl = 0.0 if lvl < 0 else 1.0 if lvl > 1 else lvl
    h = e * lvl
    # roughness on the ledges, and a floor-ring relief that dies out at r<=606 and into the wall
    rough = ((fbm(sd + 41, x / 9.0, z / 9.0, 2) - 0.5) * 3.0 * _smooth(u * N_STEPS / 1.5)
             * (1 - _smooth((r - 760) / 40.0)))
    ring = (fbm(sd + 53, x / 28.0, z / 28.0, 3) * 4.0 * _smooth((r - BASIN_R) / 12.0)
            * (1 - _smooth((r - 645) / 30.0)))
    h += rough + ring
    # wave 9: a softer shoulder where the plinth meets the seabed, then gentle low hills on the wall
    gain = sector_gain(x, z) if r < 790 else 0.0
    if gain > 0.0:
        h -= gain * 0.30 * h * _smooth((r - 615) / 12.0) * (1 - _smooth((r - 640) / 55.0))
        hill = (fbm(sd + 71, x / 46.0, z / 46.0, 3) - 0.5) * 18.0 + (fbm(sd + 77, x / 17.0, z / 17.0, 2) - 0.5) * 3.0
        h += gain * hill * _smooth((r - 640) / 22.0) * (1 - _smooth((r - 760) / 30.0))
    # blend to exactly the edge height over the last stretch
    h += (e - h) * _smooth((r - BLEND_R0) / (ctx.area_radius - BLEND_R0))
    return max(0, min(e + 6, int(round(h))))


# ---- materials (per cell; kept cheap: column facts are cached for the current column)
_col = [None, None]


def _column_info(ctx, x, z, s):
    key = (x, z)
    if _col[0] == key: return _col[1]
    sl = max(abs(seabed_height(ctx, x + 1, z) - s), abs(seabed_height(ctx, x - 1, z) - s),
             abs(seabed_height(ctx, x, z + 1) - s), abs(seabed_height(ctx, x, z - 1) - s))
    sd = _seed_for(ctx)
    info = (sl, fbm(sd + 61, x / 14.0, z / 14.0, 2), vnoise(sd + 67, x / 5.0, z / 5.0),
            fbm(sd + 83, x / 21.0, z / 21.0, 2))
    _col[0], _col[1] = key, info
    return info


def seabed_block(ctx, x, y, z, s):
    sl, n1, n2, n3 = _column_info(ctx, x, z, s)
    depth = s - y
    deep = DEEPSLATE_Y + (n2 - 0.5) * 8
    if sl >= 3 or (sl >= 2 and n2 > 0.45):            # steep: bare rock
        if depth <= 2:
            if y < deep: return 'deepslate' if n1 < 0.7 else 'tuff'
            if n1 > 0.72: return 'tuff'
            if n1 < 0.3: return 'andesite'
            return 'stone'
        return 'deepslate' if y < deep else 'stone'
    if depth == 0:                                    # flat: sediment patches
        if sl >= 2: return 'gravel' if n2 > 0.5 else 'stone'
        if n3 > 0.62 and sl <= 1: return 'tuff' if n2 > 0.35 else 'clay'      # tuff / clay flats
        if n1 < 0.33: return 'gravel'
        if n1 > 0.7 and n2 > 0.4: return 'clay'
        if n2 > 0.8: return 'gravel'
        return 'sand'
    if depth <= 3:
        if n1 < 0.33: return 'gravel'
        if n1 > 0.7: return 'clay' if depth == 1 else 'stone'
        return 'sandstone' if depth <= 2 and sl == 0 else 'stone'
    return 'deepslate' if y < deep else 'stone'


background.set_seabed(seabed_height, seabed_block)


# ---------------------------------------------------------------- rock formations
class Formation(Feature):
    layer = 0

    def __init__(self, name, kind, cx, cz, radius, height, rot, mats, seed, ground_y, cave=False):
        self.kind, self.cx, self.cz, self.radius, self.height = kind, cx, cz, radius, height
        self.rot, self.mats, self.seed, self.ground_y = rot, mats, seed, ground_y
        self.cave = cave                  # spire with a small through-tunnel (cave / natural arch) near its base
        top = min(TOP_LIMIT, ground_y + height)
        rr = int(radius) + 9
        super().__init__(name, (cx - rr, max(0, ground_y - 14), cz - rr, cx + rr, top, cz + rr),
                         0, {'kind': kind, 'formation': True})

    def build(self, vol, ctx):
        c = vol.clip(*self.bbox)
        if not c: return
        if self.kind == 'arch': self._arch(vol, ctx, c)
        else: self._spire(vol, ctx, c)

    def _mat(self, x, y, z, tip=False):
        n = _h(self.seed, x // 3, (y // 4) * 977 + z // 3)
        if tip and n > 0.62 and 'dripstone_block' in self.mats: return 'dripstone_block'
        return self.mats[int(n * len(self.mats)) % len(self.mats)]

    def _spire(self, vol, ctx, c):
        sd = self.seed
        top_y = min(TOP_LIMIT, self.ground_y + self.height)
        H = top_y - self.ground_y
        if H < 2: return
        for z in range(c[2], c[5] + 1):
            for x in range(c[0], c[3] + 1):
                dx, dz = x - self.cx, z - self.cz
                d = math.hypot(dx, dz)
                if d > self.radius * 1.4 + 2: continue
                ang = math.atan2(dz, dx)
                wob = 1.0 + 0.3 * (vnoise(sd, 2.2 * math.cos(ang) + 4, 2.2 * math.sin(ang) + 4) - 0.5) * 2
                g = seabed_height(ctx, x, z)
                if self.cave:
                    cu = dx * math.cos(self.rot) + dz * math.sin(self.rot)       # along the tunnel
                    cv = -dx * math.sin(self.rot) + dz * math.cos(self.rot)      # across it
                    cw, ch = 2.6 + 0.12 * self.radius, 3.5 + 0.11 * H            # half width / height of the opening
                    if abs(cu) > self.radius * 1.5 + 2: cw = 0.0
                else:
                    cw = 0.0
                for y in range(max(c[1], g - 3), min(c[4], top_y) + 1):
                    if cw and y >= g:
                        e = (cv / cw) ** 2 + ((y - g) / ch) ** 2
                        if e < 1.0: continue                                    # open water inside the tunnel
                    t = max(0.0, (y - self.ground_y) / H)
                    notch = 0.12 * (vnoise(sd + 5, x * 0.35 + y * 0.22, z * 0.35) - 0.5)
                    rad = self.radius * (wob * (1 - t) ** 1.25 + notch) + (0.0 if t < 0.97 else -1.0)
                    if t < 0.9: rad = max(rad, 1.2 * (1 - t))
                    if d <= rad:
                        vol.set(x, y, z, self._mat(x, y, z, tip=t > 0.65))

    def _arch(self, vol, ctx, c):
        sd = self.seed
        ca, sa = math.cos(self.rot), math.sin(self.rot)
        A = self.radius                       # half span: centre to leg centre
        thick = 4.0 + (sd % 3)
        depth = thick * 1.1
        y0 = self.ground_y
        hh = min(self.height, TOP_LIMIT - y0)
        if hh < 6: return
        for z in range(c[2], c[5] + 1):
            for x in range(c[0], c[3] + 1):
                dx, dz = x - self.cx, z - self.cz
                u = dx * ca + dz * sa
                v = -dx * sa + dz * ca
                if abs(u) > A + thick or abs(v) > depth: continue
                lim = depth * (1.0 - (v / depth) ** 2 * 0.3)
                if abs(v) > lim: continue
                g = seabed_height(ctx, x, z)
                for y in range(max(c[1], g - 3), min(c[4], TOP_LIMIT) + 1):
                    if y < y0:                                   # leg footings down to the slope
                        if A - thick * 0.5 <= abs(u) <= A + thick * 0.5:
                            vol.set(x, y, z, self._mat(x, y, z))
                        continue
                    ey = (y - y0 + 0.5) / hh
                    if ey > 1.0: break
                    e = math.hypot(u / (A + thick * 0.5), ey)
                    ei = math.hypot(u / max(1.0, A - thick * 0.5), ey / 0.72)
                    if e <= 1.0 and ei >= 1.0:
                        vol.set(x, y, z, self._mat(x, y, z, tip=ey > 0.8))


def _make_formations(ctx):
    # (kind, polar angle in degrees from +x, radius, base radius (arch: half span), height, materials)
    spec = [
        ('spire', 12, 668, 9, 46, ['stone', 'tuff', 'andesite']),
        ('arch', 38, 700, 14, 30, ['deepslate', 'stone', 'tuff']),
        ('spire', 66, 640, 7, 34, ['deepslate', 'deepslate', 'tuff']),
        ('spire', 95, 725, 10, 40, ['stone', 'andesite', 'dripstone_block']),
        ('arch', 128, 660, 12, 26, ['stone', 'andesite', 'tuff']),
        ('spire', 152, 700, 8, 40, ['deepslate', 'tuff', 'stone']),
        ('spire', 181, 635, 8, 38, ['tuff', 'stone', 'andesite']),
        ('arch', 214, 722, 15, 28, ['deepslate', 'tuff', 'dripstone_block']),
        ('spire', 238, 680, 11, 52, ['stone', 'deepslate', 'tuff']),
        ('spire', 265, 740, 9, 30, ['dripstone_block', 'stone', 'tuff']),
        ('arch', 295, 650, 12, 28, ['stone', 'tuff', 'andesite']),
        ('spire', 322, 712, 10, 44, ['deepslate', 'stone', 'tuff']),
        ('spire', 348, 760, 8, 12, ['stone', 'andesite', 'dripstone_block']),
        # wave 9: tall distant silhouettes on the far wall (heights are capped by TOP_LIMIT)
        ('spire', 52, 722, 9, 60, ['deepslate', 'stone', 'tuff']),
        ('spire', 140, 736, 8, 60, ['stone', 'deepslate', 'andesite']),
        ('spire', 200, 728, 9, 60, ['deepslate', 'tuff', 'stone']),
        ('spire', 250, 746, 8, 60, ['stone', 'deepslate', 'dripstone_block']),
        ('spire', 290, 738, 9, 60, ['deepslate', 'stone', 'tuff']),
        ('spire', 332, 748, 7, 60, ['stone', 'andesite', 'deepslate']),
    ]
    caves = {3, 5, 8, 11, 14, 16}
    out = []
    for i, (kind, adeg, r, rad, hgt, mats) in enumerate(spec):
        sd = hash_str('formation', ctx.seed, i) & 0x7FFFFFFF
        a = math.radians(adeg + (_h(sd, 1, 1) - 0.5) * 14)
        rr = r + (_h(sd, 2, 2) - 0.5) * 16
        cx, cz = int(round(rr * math.cos(a))), int(round(rr * math.sin(a)))
        if kind == 'arch':          # span along the contour; base on the higher leg so both legs reach the ground
            rot = a + math.pi / 2
            ca, sa = math.cos(rot), math.sin(rot)
            g = max(seabed_height(ctx, int(cx + ca * rad), int(cz + sa * rad)),
                    seabed_height(ctx, int(cx - ca * rad), int(cz - sa * rad)))
            out.append(Formation(f'rock_arch_{i}', 'arch', cx, cz, rad, hgt, rot, mats, sd, g))
        else:
            g = seabed_height(ctx, cx, cz)
            cave = i in caves
            rot = a + math.pi / 2 if cave else 0.0                    # the tunnel runs along the contour
            out.append(Formation(f'rock_spire_{i}', 'spire', cx, cz, rad, hgt, rot, mats, sd, g, cave=cave))
    return out


def _register():
    from . import config
    register(*_make_formations(config.load()))


_register()
