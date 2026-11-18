"""The dome (layer 20): glass shield shell, ribs/bands/apex ring, perimeter plinth with buttresses, six stations.

Everything is decided from the exact sphere predicates that `ctx.inside_dome` uses, so the shell is watertight:
a cell is shell iff y >= 0 and  (R-t)^2 <= d^2 < R^2  (d = distance to the sphere centre (0, cy, 0)).
Per column (x, z) the shell is one vertical run  start..yo  with  yo = highest y with d^2 < R^2  and
start = max(0, yi + 1), yi = highest y with d^2 < (R-t)^2  (the dry air volume starts right above yi).

Angles: ribs/stations use geometry convention (degrees from east towards south); station plan angles
(clockwise from north) are converted with  geom = plan - 90.

Features (all layer 20, stations 21):  dome_shell, dome_plinth, dome_station_030 .. dome_station_330.
`dome_info(ctx)` describes the dome for the deploy sweep.
"""
import math

from . import config, feature
from .util import hash_str

H3 = math.sqrt(3.0) / 2.0
STATION_PLANS = (30, 90, 150, 210, 270, 330)

# ---- plan constants (blocks)
APEX_R = 25.0            # apex ring radius (horizontal)
CROWN_R = 7.5            # inner crown ring
BASE_R = 600             # nominal r where the outer surface meets the floor
PLINTH_IN = 590          # plinth solid below/at the floor from this radius
COLLAR_R0 = 580          # the collar around the shell's outer face starts here
PLINTH_OUT = 641         # outermost plinth radius
HEX_A = 11.0             # hexagon apothem of the shield pattern


def _lantern_block(): return 'sea_lantern'


# ====================================================================================================== shell
class Geo:
    """Sphere constants + the per-column shell/pattern functions. One per ctx (cached on the ctx)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.R = ctx.dome_R
        self.cy = ctx.dome_cy
        self.rin = ctx.dome_R - ctx.dome_thickness          # same expression as ctx.inside_dome
        self.RR = self.R * self.R
        self.rr = self.rin * self.rin
        cfg = ctx.cfg['dome']
        self.n_ribs = int(cfg.get('rib_count', 24))
        self.n_bands = int(cfg.get('band_count', 4))
        self.rib_step = 2 * math.pi / self.n_ribs
        self.rib_phase = self.rib_step / 2                    # ribs sit between the 15-degree multiples
        h = ctx.dome_height
        self.band_r = []
        for k in range(1, self.n_bands + 1):
            y = h * k / (self.n_bands + 1.0)
            self.band_r.append(math.sqrt(max(0.0, self.RR - (y - self.cy) ** 2)))
        self.base_r = math.sqrt(max(0.0, self.RR - self.cy ** 2))   # ~600

    # ---- exact vertical extent
    def top(self, x, z, rad2):
        """Highest integer y with x^2 + (y-cy)^2 + z^2 < rad2 (same arithmetic as ctx.inside_dome), or None."""
        t = rad2 - (x * x + z * z)
        if t <= 0: return None
        cy = self.cy
        y = int(math.floor(cy + math.sqrt(t)))
        while True:
            d = y + 1 - cy
            if x * x + d * d + z * z < rad2: y += 1
            else: break
        n = 0
        while True:
            d = y - cy
            if x * x + d * d + z * z < rad2: return y
            y -= 1; n += 1
            if n > 8: return None

    def run(self, x, z):
        """(start, yo): inclusive y run of shell cells in the column, or None."""
        yo = self.top(x, z, self.RR)
        if yo is None or yo < 0: return None
        yi = self.top(x, z, self.rr)
        start = 0 if (yi is None or yi < 0) else yi + 1
        return (start, yo) if start <= yo else None

    # ---- ribs
    def rib_of(self, x, z, r):
        """(k, lateral distance, phi) of the nearest rib."""
        phi = math.atan2(z, x)
        k = round((phi - self.rib_phase) / self.rib_step)
        dphi = phi - (self.rib_phase + k * self.rib_step)
        return k % self.n_ribs, r * math.sin(dphi), r * math.cos(dphi), phi

    # ---- hex shield pattern
    def hexinfo(self, x, z, r):
        a = HEX_A
        k = 1.0
        if r > 1e-6:
            k = self.R * math.asin(min(1.0, r / self.R)) / r
        u, v = x * k + 3.1, z * k - 1.7
        cw = 2 * a * H3
        i0 = int(math.floor(u / cw))
        best = None
        for i in (i0, i0 + 1):
            j0 = int(math.floor((v - i * a) / (2 * a)))
            for j in (j0, j0 + 1):
                px, py = u - i * cw, v - (i * a + j * 2 * a)
                d = px * px + py * py
                if best is None or d < best[0]: best = (d, i, j, px, py)
        d, i, j, px, py = best
        ms = sorted((abs(py), abs(px * H3 + py * 0.5), abs(px * H3 - py * 0.5)))
        edge = a - ms[2] < 0.5
        vertex = edge and (a - ms[1] < 1.3)
        return edge, vertex, (i + 2 * j) % 3, d < 2.6

    # ---- one column
    def column(self, x, z):
        """[(y, block)] for the shell column at (x, z) (glass pattern, ribs, bands, apex ring)."""
        run = self.run(x, z)
        if run is None: return []
        start, yo = run
        n = yo - start + 1
        r = math.sqrt(x * x + z * z)
        spec = self.special(x, z, r)
        out = []
        if spec is not None:
            if isinstance(spec, tuple):                        # (inner, outer)
                for k in range(n): out.append((start + k, spec[0] if k == 0 and n > 1 else spec[1]))
            else:
                for k in range(n): out.append((start + k, spec))
            return out
        edge, vertex, cls, dot = self.hexinfo(x, z, r)
        if vertex: o, i = 'white_stained_glass', 'white_stained_glass'
        elif edge: o, i = 'cyan_stained_glass', 'light_blue_stained_glass'
        elif dot and cls == 0: o, i = 'white_stained_glass', 'glass'
        elif cls == 2: o, i = 'glass', 'glass'
        else: o, i = 'light_blue_stained_glass', 'glass'
        for k in range(n): out.append((start + k, i if (k == 0 and n > 1) else o))
        return out

    def special(self, x, z, r):
        """Opaque/luminous reinforcement for this column, or None. Returns a block or an (inner, outer) pair."""
        # crown at the apex
        if r < 1.7: return 'pearlescent_froglight'
        if r < 3.3: return 'smooth_quartz'
        ang = None
        if CROWN_R - 0.55 <= r < CROWN_R + 0.55:
            phi = math.atan2(z, x)
            a8 = (phi / (2 * math.pi) * 8) % 1.0
            return 'sea_lantern' if min(a8, 1 - a8) * 2 * math.pi * r / 8 < 0.8 else ('quartz_block', 'smooth_quartz')
        if APEX_R - 0.55 <= r < APEX_R + 0.55:
            phi = math.atan2(z, x)
            a = (phi / (2 * math.pi) * 32) % 1.0
            return 'sea_lantern' if min(a, 1 - a) * 2 * math.pi * r / 32 < 0.8 else ('quartz_block', 'smooth_quartz')
        # bands
        for rb in self.band_r:
            if rb - 0.55 <= r < rb + 0.55:
                phi = math.atan2(z, x)
                cnt = max(8, int(round(2 * math.pi * rb / 13.0)))
                a = (phi / (2 * math.pi) * cnt) % 1.0
                return 'sea_lantern' if min(a, 1 - a) * 2 * math.pi * rb / cnt < 0.8 else ('quartz_block', 'smooth_quartz')
        # ribs
        if r > 3.3:
            k, lat, u, phi = self.rib_of(x, z, r)
            if abs(lat) < 0.55 and u > 0:
                if r >= APEX_R + 0.55 or (k % 3 == 0 and r >= CROWN_R - 0.55):
                    s = self.R * math.asin(min(1.0, r / self.R))
                    if (int(s) + (k % 2) * 4) % 9 == 0: return 'sea_lantern'
                    return ('quartz_block', 'smooth_quartz')
        return None


def geo(ctx):
    g = getattr(ctx, '_dome_geo', None)
    if g is None:
        g = Geo(ctx)
        try: ctx._dome_geo = g
        except Exception: pass
    return g


def _tile_radii(vol):
    """(min, max) horizontal distance from the origin over the volume's x/z rectangle."""
    nx = min(max(0, vol.x1), vol.x2); nz = min(max(0, vol.z1), vol.z2)
    fx = max(abs(vol.x1), abs(vol.x2)); fz = max(abs(vol.z1), abs(vol.z2))
    return math.hypot(nx, nz), math.hypot(fx, fz)


class DomeShell(feature.Feature):
    layer = 20

    def __init__(self, ctx):
        r = int(math.ceil(geo(ctx).base_r)) + 1
        super().__init__('dome_shell', (-r, 0, -r, r, int(ctx.dome_height) + 2, r), 20,
                         {'kind': 'dome_shell', 'ribs': geo(ctx).n_ribs})

    def build(self, vol, ctx):
        g = geo(ctx)
        lo, hi = _tile_radii(vol)
        if lo > g.base_r + 1.5: return
        col = g.column
        for z in range(vol.z1, vol.z2 + 1):
            for x in range(vol.x1, vol.x2 + 1):
                for y, b in col(x, z):
                    vol.set(x, y, z, b)


# ====================================================================================================== plinth
def _collar_t(r):
    if r < COLLAR_R0: return 0
    if r < COLLAR_R0 + 2: return 1
    if r < COLLAR_R0 + 4: return 2
    return 3


def _apron_top(r):
    """Top y of the plinth for r >= 600 (stepped down to the seabed)."""
    if r < 604: return 3
    if r < 608: return 2
    if r < 612: return 1
    if r < 617: return 0
    return -1 - int((r - 617) // 4)


def _fin_top(u):
    """Buttress profile along the rib (u = distance along the rib's radial line)."""
    if u < 597 or u > 620: return None
    return 3 + int(round(9 * (1 - (u - 597) / 23.0) ** 2))


class DomePlinth(feature.Feature):
    layer = 20

    def __init__(self, ctx):
        r = PLINTH_OUT + 1
        super().__init__('dome_plinth', (-r, ctx.y_min + 1, -r, r, 14, r), 20, {'kind': 'dome_plinth'})

    def build(self, vol, ctx):
        g = geo(ctx)
        lo, hi = _tile_radii(vol)
        if hi < COLLAR_R0 - 1 or lo > PLINTH_OUT: return
        ymin = ctx.y_min + 1
        sqrt, hypot = math.sqrt, math.hypot
        for z in range(vol.z1, vol.z2 + 1):
            for x in range(vol.x1, vol.x2 + 1):
                r = hypot(x, z)
                if r < COLLAR_R0 or r >= PLINTH_OUT: continue
                run = g.run(x, z)
                cells = {}
                top_y = None
                # fin (buttress at a rib foot)
                ftop = None
                if 594 <= r <= 621:
                    k, lat, u, phi = g.rib_of(x, z, r)
                    if abs(lat) <= 1.25:
                        ftop = _fin_top(u)
                        fu = u
                if r >= PLINTH_IN:
                    tp = _apron_top(r) if r >= BASE_R else 0
                    hb = min(tp, 0)
                    for y in range(ymin, hb + 1):
                        if y == 0 and run and run[0] == 0: continue
                        cells[y] = None
                # collar / apron above the floor
                if run:
                    ys = range(run[1] + 1, run[1] + _collar_t(r) + 1)
                    ybase = run[1] + 1
                elif r >= BASE_R and _apron_top(r) > 0:
                    ys = range(1, _apron_top(r) + 1)
                    ybase = 1
                else:
                    ys = range(0)
                    ybase = 1
                for y in ys: cells[y] = None
                if ftop is not None:
                    for y in range(ybase, ftop + 1): cells[y] = None
                if not cells: continue
                top_y = max(cells)
                for y in cells:
                    cells[y] = self.mat(x, y, z, r, top_y, ftop, fu if ftop is not None else 0, y == top_y)
                for y, b in cells.items(): vol.set(x, y, z, b)

    @staticmethod
    def mat(x, y, z, r, top_y, ftop, fu, is_top):
        if ftop is not None and y > 0 and (y > _apron_top(r) if r >= BASE_R else True):
            if is_top:
                if 598.5 <= fu < 600.5: return 'pearlescent_froglight'
                if 605.5 <= fu < 606.8 or 611.5 <= fu < 612.8: return 'sea_lantern'
                return 'smooth_quartz'
            return 'quartz_bricks'
        if is_top and y >= 0:
            phi = math.atan2(z, x)
            a = (math.degrees(phi) + 1.5) % 3.0 - 1.5
            if 608.5 <= r < 609.8 and abs(math.radians(a)) * r < 0.7: return 'sea_lantern'
            if 583 <= r < 584.2 or 601 <= r < 602.1 or 613 <= r < 614.1: return 'smooth_quartz'
            return 'polished_deepslate'
        if y % 5 == 0: return 'deepslate_bricks'
        return 'dark_prismarine'


# ====================================================================================================== stations
CH_U0, CH_U1, CH_HW = 606.5, 620.5, 7.2          # observation chamber (radial range, half width)
TUN_U0, TUN_U1, TUN_HW = 588.0, 607.5, 1.2       # airlock passage
CH_TOP, TUN_TOP = 6, 2                           # highest air y
PAD_U1, PAD_HW = 623.5, 10.2
DECK_U1, DECK_HW = 640.0, 6.2


class Station(feature.Feature):
    layer = 21

    def __init__(self, ctx, plan):
        self.plan = plan
        self.geom = plan - 90
        a = math.radians(self.geom)
        self.cg, self.sg = math.cos(a), math.sin(a)
        xs, zs = [], []
        for u in (TUN_U0 - 2, DECK_U1 + 2):
            for v in (-DECK_HW - 5, DECK_HW + 5):
                xs.append(u * self.cg - v * self.sg); zs.append(u * self.sg + v * self.cg)
        self.box2 = (int(math.floor(min(xs))), int(math.floor(min(zs))), int(math.ceil(max(xs))), int(math.ceil(max(zs))))
        super().__init__(f'dome_station_{plan:03d}',
                         (self.box2[0], ctx.y_min + 1, self.box2[1], self.box2[2], 13, self.box2[3]), 21,
                         {'kind': 'dome_station', 'plan_angle': plan, 'geom_angle': self.geom, 'shell_opening_ok': True})
        self._cache = None

    def uv(self, x, z):
        return x * self.cg + z * self.sg, -x * self.sg + z * self.cg

    def xz(self, u, v):
        return int(round(u * self.cg - v * self.sg)), int(round(u * self.sg + v * self.cg))

    # ---- model
    def model(self, ctx):
        if self._cache is not None: return self._cache
        x1, z1, x2, z2 = self.box2
        uv = self.uv
        AIR = {}                                 # (x, z) -> top air y
        CHS = set(); TUN = set()
        for z in range(z1 - 2, z2 + 3):
            for x in range(x1 - 2, x2 + 3):
                u, v = uv(x, z)
                if CH_U0 <= u <= CH_U1 and abs(v) <= CH_HW:
                    AIR[(x, z)] = CH_TOP; CHS.add((x, z))
                if TUN_U0 <= u <= TUN_U1 and abs(v) <= TUN_HW:
                    TUN.add((x, z))
                    if (x, z) not in AIR: AIR[(x, z)] = TUN_TOP
        inside = ctx.inside_dome
        C = {}
        ymin = ctx.y_min + 1

        def air(x, y, z):
            a = AIR.get((x, z))
            return a is not None and 1 <= y <= a

        # pad under the chamber, deck and piles
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                u, v = uv(x, z)
                if CH_U0 - 3 <= u <= PAD_U1 and abs(v) <= PAD_HW:
                    for y in range(ymin, 0): C[(x, y, z)] = 'deepslate_bricks' if y % 3 == 0 else 'dark_prismarine'
                    edge = u > PAD_U1 - 1 or abs(v) > PAD_HW - 1
                    C[(x, 0, z)] = 'smooth_quartz' if edge else 'polished_deepslate'
                elif PAD_U1 < u <= DECK_U1 and abs(v) <= DECK_HW:
                    edge = abs(v) > DECK_HW - 1 or u > DECK_U1 - 1
                    C[(x, 0, z)] = 'smooth_quartz' if edge else 'polished_deepslate'
                    C[(x, -1, z)] = 'dark_prismarine'
        for u0 in (627.0, 634.0, 639.0):
            for v0 in (-5.5, 5.5, 0.0):
                px, pz = self.xz(u0, v0)
                for y in range(ymin, -1): C[(px, y, pz)] = 'deepslate_bricks'
        # deck guide lights (flush)
        for u0 in (626.0, 630.0, 634.0, 638.0):
            for v0 in (-5.6, 5.6):
                C[self.xz(u0, v0)[0:1] + (0,) + self.xz(u0, v0)[1:2]] = 'sea_lantern'
        # posts: entrance (flank the front portal) and dock corners; lamp on top
        for u0, v0, hgt in ((622.2, -3.8, 3), (622.2, 3.8, 3), (625.0, -5.4, 3), (625.0, 5.4, 3),
                            (638.5, -5.4, 3), (638.5, 5.4, 3)):
            px, pz = self.xz(u0, v0)
            for y in range(1, hgt + 1): C[(px, y, pz)] = 'quartz_pillar[axis=y]'
            C[(px, hgt + 1, pz)] = 'sea_lantern'
        # roof tier, spire
        for z in range(z1, z2 + 1):
            for x in range(x1, x2 + 1):
                u, v = uv(x, z)
                if 609.0 <= u <= 618.0 and abs(v) <= 5.2:
                    C[(x, 8, z)] = 'smooth_quartz'
        for u0, v0 in ((609.5, -4.6), (609.5, 4.6), (617.5, -4.6), (617.5, 4.6)):
            px, pz = self.xz(u0, v0); C[(px, 8, pz)] = 'sea_lantern'
        px, pz = self.xz(613.5, 0.0)
        for y in range(8, 12): C[(px, y, pz)] = 'quartz_pillar[axis=y]'
        C[(px, 12, pz)] = 'sea_lantern'
        # eaves: roof also over the 8-neighbourhood of the chamber
        roof8 = set()
        for (x, z) in CHS:
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1): roof8.add((x + dx, z + dz))
        for (x, z) in roof8: C[(x, 7, z)] = 'quartz_block'
        # walls = face-neighbours of air (not air, not in the dry dome)
        cand = set()
        for (x, z) in AIR:
            for dx, dz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)): cand.add((x + dx, z + dz))
        walls = {}
        for (x, z) in cand:
            for y in range(0, 8):
                if air(x, y, z) or inside(x, y, z): continue
                if (air(x + 1, y, z) or air(x - 1, y, z) or air(x, y, z + 1) or air(x, y, z - 1)
                        or air(x, y - 1, z) or air(x, y + 1, z)):
                    walls[(x, y, z)] = self.wall_mat(x, y, z, CHS)
        C.update(walls)
        # roof / ceiling lights (after the walls, which would otherwise overwrite them)
        for u0, v0 in ((610.5, -3.5), (610.5, 3.5), (614.5, 0.0), (618.5, -3.5), (618.5, 3.5)):
            px, pz = self.xz(u0, v0); C[(px, 7, pz)] = 'sea_lantern'
        # tunnel ceiling lights
        for u0 in (596.0, 600.0, 604.0):
            px, pz = self.xz(u0, 0.0)
            if (px, 3, pz) in walls: C[(px, 3, pz)] = 'sea_lantern'
        # explicit air last
        airc = []
        for (x, z), top in AIR.items():
            for y in range(1, top + 1):
                if not inside(x, y, z):
                    C[(x, y, z)] = 'air'; airc.append((x, y, z))
        self._cache = C
        self._air = airc
        self._air2 = AIR
        return C

    def wall_mat(self, x, y, z, CHS):
        u, v = self.uv(x, z)
        if y <= 0:
            return 'smooth_quartz'
        if y == 7: return 'quartz_block'
        near_ch = ((x + 1, z) in CHS or (x - 1, z) in CHS or (x, z + 1) in CHS or (x, z - 1) in CHS)
        if y == 3 and not near_ch and not (x, z) in CHS:
            return 'smooth_quartz'
        if not near_ch:
            return 'quartz_bricks'
        # chamber wall
        if y <= 2:
            if u > CH_U1 and abs(v) <= 1.6: return 'white_stained_glass'
            if u > CH_U1 and abs(v) <= 2.8: return 'quartz_pillar[axis=y]'
            return 'quartz_bricks'
        if y == 6: return 'smooth_quartz'
        if u < CH_U0: return 'quartz_bricks'
        side = abs(v) > CH_HW - 0.3 or abs(v) > CH_HW - 1.4 and u <= CH_U1
        if u > CH_U1 and abs(v) <= CH_HW:
            pil = (v + CH_HW) % 4.0 < 1.5
        else:
            pil = (u - CH_U0) % 4.0 < 1.5
        if pil or (abs(v) > CH_HW - 1.0 and u > CH_U1 - 1.0): return 'quartz_pillar[axis=y]'
        return 'light_blue_stained_glass'

    def build(self, vol, ctx):
        C = self.model(ctx)
        x1, y1, z1, x2, y2, z2 = vol.x1, vol.y1, vol.z1, vol.x2, vol.y2, vol.z2
        for (x, y, z), b in C.items():
            if x1 <= x <= x2 and z1 <= z <= z2 and y1 <= y <= y2:
                vol.set(x, y, z, b)

    def air_boxes(self, ctx):
        """Exact set of explicit-air cells (not in the dry dome) merged into boxes."""
        self.model(ctx)
        by = {}
        for (x, y, z) in self._air: by.setdefault((y, z), []).append(x)
        # runs along x for each (y, z)
        runs = {}
        for (y, z), xs in by.items():
            xs.sort(); a = b = xs[0]
            for x in xs[1:]:
                if x == b + 1: b = x
                else: runs.setdefault((a, b, y), []).append(z); a = b = x
            runs.setdefault((a, b, y), []).append(z)
        # merge consecutive z for equal (x-run, y), then equal (x-run, z-run) over consecutive y
        zb = {}
        for (a, b, y), zs in runs.items():
            zs.sort(); s = e = zs[0]
            for z in zs[1:]:
                if z == e + 1: e = z
                else: zb.setdefault((a, b, s, e), []).append(y); s = e = z
            zb.setdefault((a, b, s, e), []).append(y)
        boxes = []
        for (a, b, s, e), ys in zb.items():
            ys.sort(); y0 = y1 = ys[0]
            for y in ys[1:]:
                if y == y1 + 1: y1 = y
                else: boxes.append((a, y0, s, b, y1, e)); y0 = y1 = y
            boxes.append((a, y0, s, b, y1, e))
        return sorted(boxes)


# ====================================================================================================== API
_CTX = None
_FEATURES = None


def make_features(ctx):
    return [DomeShell(ctx), DomePlinth(ctx)] + [Station(ctx, p) for p in STATION_PLANS]


def dome_info(ctx):
    """Dict for the deploy sweep tool (see the report for the field list)."""
    g = geo(ctx)
    stations = []
    allboxes = []
    for f in make_features(ctx)[2:]:
        boxes = f.air_boxes(ctx)
        allboxes += boxes
        stations.append({'name': f.name, 'plan_angle': f.plan, 'geom_angle': f.geom, 'bbox': f.bbox,
                         'air_boxes': boxes})
    return {'center': (0, 0), 'floor_y': 0, 'R': g.R, 'inner_R': g.rin, 'sphere_cy': g.cy,
            'thickness': ctx.dome_thickness, 'base_radius': g.base_r, 'footprint_radius': PLINTH_OUT + 1,
            'apex_y': ctx.dome_height, 'stations': stations, 'station_air_boxes': allboxes}


def _register():
    global _CTX, _FEATURES
    _CTX = config.load()
    _FEATURES = make_features(_CTX)
    feature.register(*_FEATURES)


_register()
