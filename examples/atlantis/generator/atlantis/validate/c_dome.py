"""dome_geometry (shell fit + continuity) and clearance (nothing above the dome-limited building height)."""
import math

from . import classify as K
from .core import Issue, Partial, OPTIONS, register_check, tinfo, CHECKS
from .trace import get_trace

_WIN_BELOW, _WIN_ABOVE = 3.0, 3.0      # shell-material sample window around the ideal band, in blocks


def _solve(M, r):
    """Gaussian elimination with partial pivoting; returns solution list or None when (near) singular."""
    n = len(r)
    a = [list(M[i]) + [r[i]] for i in range(n)]
    scale = max(abs(v) for row in M for v in row) or 1.0
    for c in range(n):
        p = max(range(c, n), key=lambda i: abs(a[i][c]))
        if abs(a[p][c]) < 1e-12 * scale: return None
        a[c], a[p] = a[p], a[c]
        for i in range(c + 1, n):
            f = a[i][c] / a[c][c]
            for j in range(c, n + 1): a[i][j] -= f * a[c][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (a[i][n] - sum(a[i][j] * x[j] for j in range(i + 1, n))) / a[i][i]
    return x


class _Sums:
    """Running sums for the least-squares sphere fit x²+y²+z² = 2ax+2by+2cz+d (coordinates relative to ctx centre)."""
    __slots__ = ('n', 'M', 'r', 'sd2')

    def __init__(self):
        self.n = 0; self.sd2 = 0.0
        self.M = [0.0] * 10          # upper triangle of sum f f^T, f = (x, y, z, 1)
        self.r = [0.0] * 4

    def add(self, x, y, z):
        s = x * x + y * y + z * z
        M, r = self.M, self.r
        M[0] += x * x; M[1] += x * y; M[2] += x * z; M[3] += x; M[4] += y * y
        M[5] += y * z; M[6] += y; M[7] += z * z; M[8] += z; M[9] += 1.0
        r[0] += s * x; r[1] += s * y; r[2] += s * z; r[3] += s
        self.n += 1; self.sd2 += s

    def merge(self, o):
        self.n += o.n; self.sd2 += o.sd2
        self.M = [a + b for a, b in zip(self.M, o.M)]
        self.r = [a + b for a, b in zip(self.r, o.r)]

    def fit(self):
        """(cx, cy, cz, radius) relative to the ctx centre, or None if degenerate."""
        if self.n < 8: return None
        M = [[0.0] * 4 for _ in range(4)]
        k = 0
        for i in range(4):
            for j in range(i, 4):
                M[i][j] = M[j][i] = self.M[k]; k += 1
        th = _solve(M, self.r)
        if th is None: return None
        c = (th[0] / 2, th[1] / 2, th[2] / 2)
        r2 = th[3] + c[0] ** 2 + c[1] ** 2 + c[2] ** 2
        return (*c, math.sqrt(r2) if r2 > 0 else 0.0)

    def rms(self):
        return math.sqrt(self.sd2 / self.n) if self.n else 0.0


@register_check('dome_geometry')
def dome_geometry(ctx, tile_key, vol, features):
    """Shell cells = glass anywhere within 3 blocks of the ideal band, plus any non-air/non-water block in the band
    (R-t <= dist < R, y >= 0, dist from the sphere centre (0, dome_cy, 0)). Per tile: sums for a least-squares sphere
    fit and for the ideal band cells (all in-area band cells); reduce fits both and compares (centre offset and
    radius difference must be < 0.5, with a radial-RMS fallback if the fit is degenerate). Continuity: every in-area
    band cell must be a non-air, non-water block; holes are reported per tile with positions. Severity: error."""
    R, cy, th = ctx.dome_R, ctx.dome_cy, ctx.dome_thickness
    w_lo, w_hi = R - th - _WIN_BELOW, R + _WIN_ABOVE
    b_lo2, b_hi2, wl2, wh2 = (R - th) ** 2, R * R, w_lo * w_lo, w_hi * w_hi
    # tile can only hold shell cells if its nearest column is within the base circle of the window
    nx = min(max(0, vol.x1), vol.x2); nz = min(max(0, vol.z1), vol.z2)
    if nx * nx + nz * nz >= wh2 - cy * cy: return []
    t = tinfo(ctx, vol)
    pal = vol.palette
    infos = [K.info(s) for s in pal]
    hole = [b.cls in (K.AIR, K.WATER, K.WPLANT) for b in infos]
    solid = [b.cls != K.VOID and b.cls not in (K.AIR, K.WATER, K.WPLANT) for b in infos]
    glass = [b.glass for b in infos]
    cells, sx, sz, stride = vol.cells, vol.sx, vol.sz, t.stride
    ideal, samp = _Sums(), _Sums()
    holes = []
    # intentional openings (airlocks): band cells inside the bbox of a feature with meta shell_opening_ok
    openings = [f.bbox for f in (features or []) if f.meta.get('shell_opening_ok') and vol.intersects(*f.bbox)]
    ylo_t, yhi_t = max(0, vol.y1), vol.y2
    for z in range(vol.z1, vol.z2 + 1):
        for x in range(vol.x1, vol.x2 + 1):
            q = x * x + z * z
            if q >= wh2 or not ctx.in_area(x, z): continue
            if cy < 0:
                ya = 0 if q >= wl2 else int(math.ceil(cy + math.sqrt(wl2 - q))) - 1
                yb = int(math.floor(cy + math.sqrt(wh2 - q))) + 1
            else:
                ya, yb = ylo_t, yhi_t
            ya, yb = max(ya, ylo_t), min(yb, yhi_t)
            base = (z - vol.z1) * sx + (x - vol.x1)
            sub = not (x & 1 or z & 1)          # the fit uses every 2nd column in x and z (4x cheaper)
            for y in range(ya, yb + 1):
                d2 = q + (y - cy) ** 2
                if d2 < wl2 or d2 >= wh2: continue
                pid = cells[base + (y - vol.y1) * stride]
                if b_lo2 <= d2 < b_hi2:
                    if sub: ideal.add(x, y - cy, z)
                    if hole[pid]:
                        if not any(b[0] <= x <= b[3] and b[1] <= y <= b[4] and b[2] <= z <= b[5] for b in openings):
                            holes.append((x, y, z))
                        continue
                    if sub and solid[pid]: samp.add(x, y - cy, z)
                elif sub and glass[pid]:
                    samp.add(x, y - cy, z)
    out = []
    if holes:
        out.append(Issue('dome_geometry', 'error', 'hole in the dome shell band (air/water in a shell cell)',
                         holes[0], len(holes), holes[:20]))
    if ideal.n: out.append(Partial('dome_geometry', tile_key, (ideal, samp)))
    return out


def dome_reduce(partials, ctx, features=None):
    ideal, samp = _Sums(), _Sums()
    for p in partials:
        i, s = p.data
        ideal.merge(i); samp.merge(s)
    if not ideal.n: return []
    tol = OPTIONS['dome_tolerance']
    fi, fs = ideal.fit(), samp.fit()
    out = []
    if fi and fs:
        dc = math.sqrt(sum((a - b) ** 2 for a, b in zip(fs[:3], fi[:3])))
        dr = abs(fs[3] - fi[3])
        centre = (fs[0], ctx.dome_cy + fs[1], fs[2])
        msg = (f'dome fit: centre ({centre[0]:.2f}, {centre[1]:.2f}, {centre[2]:.2f}) vs ctx (0, {ctx.dome_cy:.2f}, 0), '
               f'radius {fs[3]:.2f} vs ideal-band {fi[3]:.2f} (ctx R={ctx.dome_R:.2f}); '
               f'deviation centre {dc:.2f}, radius {dr:.2f}, tolerance {tol}')
        sev = 'error' if (dc > tol or dr > tol) else 'info'
        out.append(Issue('dome_geometry', sev, msg if sev == 'info' else 'DEVIATION ' + msg, None, 1))
    else:
        dr = abs(samp.rms() - ideal.rms()) if samp.n else float('inf')
        msg = (f'dome radial fit (sphere fit degenerate for this tile set): rms radius {samp.rms():.2f} vs ideal '
               f'{ideal.rms():.2f}, deviation {dr:.2f}, tolerance {tol}; samples {samp.n}/{ideal.n}')
        out.append(Issue('dome_geometry', 'error' if dr > tol else 'info',
                         ('DEVIATION ' if dr > tol else '') + msg, None, 1))
    return out


CHECKS['dome_geometry'].reduce = dome_reduce


# ---------------------------------------------------------------- clearance
@register_check('clearance', trace=True)
def clearance(ctx, tile_key, vol, features):
    """No non-air block last written by a feature of layer >= 30 above ctx.max_building_top(x, z) + margin (margin =
    OPTIONS['clearance_margin'], default 0), measured inside the dome footprint only. Features with
    meta['clearance_ok'] are exempt. Uses the last-writer trace. Severity: error. The reduce step additionally warns
    for features whose bbox top exceeds max_building_top at their footprint (conservative, see reduce)."""
    tr = get_trace(ctx, vol, features)
    thr = tr.first_of_layer(30)
    if thr > len(tr.order): return []
    exempt = {k for k, f in enumerate(tr.order, 1) if f.meta.get('clearance_ok')}
    margin = OPTIONS['clearance_margin']
    t = tinfo(ctx, vol)
    infos = [K.info(s) for s in vol.palette]
    isair = [b.cls == K.AIR for b in infos]
    cells, writer = vol.cells, tr.writer
    sx, step, y1 = vol.sx, t.stride, vol.y1
    cnt, ps, names = 0, [], set()
    for z in range(vol.z1, vol.z2 + 1):
        for x in range(vol.x1, vol.x2 + 1):
            if ctx.dome_inner_y(x, z) is None: continue
            top = int(math.floor(ctx.max_building_top(x, z) + margin))
            ys = max(top + 1, y1)
            if ys > vol.y2: continue
            base = (z - vol.z1) * sx + (x - vol.x1)
            w = writer[base + (ys - y1) * step::step]
            if max(w) < thr: continue
            for k, wv in enumerate(w):
                if wv >= thr and wv not in exempt and not isair[cells[base + (ys - y1 + k) * step]]:
                    cnt += 1; names.add(tr.order[wv - 1].name)
                    if len(ps) < 20: ps.append((x, ys + k, z))
    if not cnt: return []
    nm = ', '.join(sorted(names)[:3]) + (' ...' if len(names) > 3 else '')
    return [Issue('clearance', 'error', f'blocks above max_building_top (features: {nm})', ps[0], cnt, ps)]


def clearance_reduce(partials, ctx, features=None):
    """Meta-based: for features of layer >= 30 (not meta clearance_ok) compare bbox top with max_building_top at the
    bbox footprint, evaluated at its corners and edge midpoints that lie under the dome (minimum). Warn only: a
    round tower's square bbox corners are conservative."""
    out = []
    margin = OPTIONS['clearance_margin']
    for f in features or ():
        if f.layer < 30 or f.meta.get('clearance_ok'): continue
        x1, y1, z1, x2, y2, z2 = f.bbox
        xm, zm = (x1 + x2) // 2, (z1 + z2) // 2
        pts = [(x, z) for x in (x1, xm, x2) for z in (z1, zm, z2)]
        lim = [ctx.max_building_top(x, z) + margin for x, z in pts if ctx.dome_inner_y(x, z) is not None]
        if not lim: continue
        m = min(lim)
        if y2 > m:
            out.append(Issue('clearance', 'warn',
                             f'feature bbox top above max_building_top (feature {f.name}: top {y2}, limit {m:.0f})',
                             (xm, y2, zm), 1))
    return out


CHECKS['clearance'].reduce = clearance_reduce
