"""Parametric, clip-aware drawing primitives.

Every drawing function takes `vol` (a Volume, normally one tile) first and loops only over the part of the shape
that intersects it, so a 600-block-wide shape drawn into a 32-wide tile costs about as much as its 32-wide part.
Coordinates are city-local blocks; cell (x, y, z) is the block whose centre is at integer coordinates, and shape
membership is always decided by the exact predicate on cell centres (distance^2 <= r^2 etc.), so the same cell is
in or out regardless of the volume it is drawn into.

`block` may be a state string or a callable ``f(x, y, z) -> state | None`` (None = leave the cell alone).
`replace` (optional) is a set of states (may include None): only cells currently holding one of them are written.
Angles are degrees: 0 = east (+x), 90 = south (+z).

Pure variants (``*_cells`` / ``*_spans``) iterate cells without touching a Volume, so a generator can test a cell
before placing it.  2D clip boxes are ``(x1, z1, x2, z2)``, 3D boxes ``(x1, y1, z1, x2, y2, z2)``; None = unbounded.
"""
import math

from . import blocks as B

_INF = 10 ** 9
_EPS = 1e-9


def _floor(v): return int(math.floor(v))
def _ceil(v): return int(math.ceil(v))
def rnd(v):
    """Round half up (deterministic, unlike banker's rounding)."""
    return int(math.floor(v + 0.5))


def srnd(v):
    """Round half away from zero: symmetric under negation (use for offsets from a centre)."""
    return int(math.floor(v + 0.5)) if v >= 0 else -int(math.floor(-v + 0.5))


# ---------------------------------------------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------------------------------------------
def _zspan(q, c, r2):
    """Integers z with q + (z - c)^2 <= r2, as (z0, z1) or None.  Exact on cell centres."""
    rem = r2 - q
    if rem < 0:
        return None
    w = math.sqrt(rem)
    z0 = _ceil(c - w); z1 = _floor(c + w)
    while q + (z0 - 1 - c) ** 2 <= r2: z0 -= 1
    while z0 <= z1 and q + (z0 - c) ** 2 > r2: z0 += 1
    while q + (z1 + 1 - c) ** 2 <= r2: z1 += 1
    while z1 >= z0 and q + (z1 - c) ** 2 > r2: z1 -= 1
    return (z0, z1) if z0 <= z1 else None


def _clip2(clip):
    return clip if clip is not None else (-_INF, -_INF, _INF, _INF)


def _vclip2(vol):
    return (vol.x1, vol.z1, vol.x2, vol.z2)


def _put_fn(vol, block, replace=None):
    """put(x, y, z) for a constant or callable block, honouring `replace`."""
    vs, vg = vol.set, vol.get
    if callable(block):
        def put(x, y, z):
            b = block(x, y, z)
            if b is not None and (replace is None or vg(x, y, z) in replace):
                vs(x, y, z, b)
    elif replace is None:
        def put(x, y, z): vs(x, y, z, block)
    else:
        def put(x, y, z):
            if vg(x, y, z) in replace: vs(x, y, z, block)
    return put


def _fill_xz_span(vol, x, y1, y2, z0, z1, block, replace):
    """Fill the column-run x, y1..y2, z0..z1 (a z-span over a y-range)."""
    if callable(block):
        put = _put_fn(vol, block, replace)
        for y in range(y1, y2 + 1):
            for z in range(z0, z1 + 1): put(x, y, z)
    else:
        vol.fill(x, y1, z0, x, y2, z1, block, replace)


def _yrange(vol, y1, y2):
    a, b = max(min(y1, y2), vol.y1), min(max(y1, y2), vol.y2)
    return (a, b) if a <= b else None


# ---------------------------------------------------------------------------------------------------------------
# 2D cell sets: discs, rings, arcs, outlines
# ---------------------------------------------------------------------------------------------------------------
def disc_spans(cx, cz, r, clip=None):
    """Yield (x, z0, z1): maximal z-runs of the filled disc (d <= r), clipped to `clip` (x1, z1, x2, z2)."""
    if r < 0: return
    c = _clip2(clip)
    r2 = r * r
    for x in range(max(_ceil(cx - r - _EPS), c[0]), min(_floor(cx + r + _EPS), c[2]) + 1):
        s = _zspan((x - cx) ** 2, cz, r2)
        if s:
            a, b = max(s[0], c[1]), min(s[1], c[3])
            if a <= b: yield x, a, b


def ring_spans(cx, cz, r_in, r_out, clip=None):
    """Yield (x, z0, z1) runs of the annulus r_in < d <= r_out.  r_in < 0 means a full disc."""
    if r_out < 0: return
    c = _clip2(clip)
    ro2 = r_out * r_out
    ri2 = r_in * r_in if r_in >= 0 else None
    for x in range(max(_ceil(cx - r_out - _EPS), c[0]), min(_floor(cx + r_out + _EPS), c[2]) + 1):
        q = (x - cx) ** 2
        s = _zspan(q, cz, ro2)
        if not s: continue
        i = _zspan(q, cz, ri2) if ri2 is not None else None
        runs = [s] if i is None else [(s[0], i[0] - 1), (i[1] + 1, s[1])]
        for a, b in runs:
            a, b = max(a, c[1]), min(b, c[3])
            if a <= b: yield x, a, b


def disc_cells(cx, cz, r, clip=None):
    """Yield (x, z) of the filled disc (cell centre within r of (cx, cz))."""
    for x, a, b in disc_spans(cx, cz, r, clip):
        for z in range(a, b + 1): yield x, z


def ring_cells(cx, cz, r_in, r_out, clip=None):
    """Yield (x, z) with r_in < dist <= r_out."""
    for x, a, b in ring_spans(cx, cz, r_in, r_out, clip):
        for z in range(a, b + 1): yield x, z


def _in_arc(dx, dz, a0, a1):
    span = (a1 - a0)
    if span >= 360: return True
    ang = math.degrees(math.atan2(dz, dx))
    return (ang - a0) % 360 <= span % 360 + 1e-9


def arc_cells(cx, cz, r_in, r_out, a0, a1, clip=None):
    """Yield (x, z) of the annular sector r_in < d <= r_out, angle a0..a1 (degrees, wraps; centre cell excluded
    unless the sector is a full circle)."""
    full = (a1 - a0) >= 360
    for x, z in ring_cells(cx, cz, r_in, r_out, clip):
        dx, dz = x - cx, z - cz
        if full or (dx or dz) and _in_arc(dx, dz, a0, a1):
            yield x, z


def circle_outline_cells(cx, cz, r, clip=None, connected=True):
    """Yield (x, z) of a one-block circle outline.  connected=True (default): the outline is 4-connected (no
    diagonal-only steps, so it is a watertight loop: a cell is on the outline if it is inside the disc and any of
    its 8 neighbours is outside).  connected=False: the thinner 8-connected outline (4-neighbour test)."""
    if r < 0: return
    r2 = r * r
    c = _clip2(clip)
    nb = ((1, 0), (-1, 0), (0, 1), (0, -1)) + (((1, 1), (1, -1), (-1, 1), (-1, -1)) if connected else ())
    def inside(x, z): return (x - cx) ** 2 + (z - cz) ** 2 <= r2
    for x in range(max(_ceil(cx - r - _EPS), c[0]), min(_floor(cx + r + _EPS), c[2]) + 1):
        s = _zspan((x - cx) ** 2, cz, r2)
        if not s: continue
        for z in range(max(s[0], c[1]), min(s[1], c[3]) + 1):
            for dx, dz in nb:
                if not inside(x + dx, z + dz):
                    yield x, z
                    break


def _paint_cells(vol, cells, y1, y2, block, replace):
    yr = _yrange(vol, y1, y2)
    if not yr: return
    cells = list(cells)
    put = _put_fn(vol, block, replace)
    for y in range(yr[0], yr[1] + 1):
        for x, z in cells: put(x, y, z)


def disc(vol, cx, cz, r, y, block, y2=None, replace=None):
    """Filled disc at height y (or the y..y2 range): a solid cylinder."""
    yr = _yrange(vol, y, y if y2 is None else y2)
    if not yr: return
    for x, a, b in disc_spans(cx, cz, r, _vclip2(vol)):
        _fill_xz_span(vol, x, yr[0], yr[1], a, b, block, replace)


def ring(vol, cx, cz, r_in, r_out, y, block, y2=None, replace=None):
    """Annulus r_in < d <= r_out at y (or y..y2)."""
    yr = _yrange(vol, y, y if y2 is None else y2)
    if not yr: return
    for x, a, b in ring_spans(cx, cz, r_in, r_out, _vclip2(vol)):
        _fill_xz_span(vol, x, yr[0], yr[1], a, b, block, replace)


def arc(vol, cx, cz, r_in, r_out, a0, a1, y, block, y2=None, replace=None):
    """Annular sector (a thick arc) at y or y..y2."""
    _paint_cells(vol, arc_cells(cx, cz, r_in, r_out, a0, a1, _vclip2(vol)), y, y if y2 is None else y2, block,
                 replace)


def circle_outline(vol, cx, cz, r, y, block, y2=None, connected=True, replace=None):
    """One-block-thick circle outline (4-connected by default) at y or y..y2."""
    _paint_cells(vol, circle_outline_cells(cx, cz, r, _vclip2(vol), connected), y, y if y2 is None else y2, block,
                 replace)


def curved_wall(vol, cx, cz, r, a0, a1, y1, y2, block, thickness=1, replace=None):
    """A wall following an arc of the circle of outer radius r (angles a0..a1), from y1 to y2."""
    arc(vol, cx, cz, r - thickness, r, a0, a1, y1, block, y2, replace)


def radial(n, radius, angle0=0.0, cx=0.0, cz=0.0, rounded=True):
    """n points evenly spaced on a circle: [(x, z, angle_degrees)].  Angle 0 = east, 90 = south; points are
    rounded to cells unless rounded=False."""
    out = []
    for i in range(n):
        a = angle0 + 360.0 * i / n
        x = cx + radius * math.cos(math.radians(a))
        z = cz + radius * math.sin(math.radians(a))
        out.append((rnd(cx) + srnd(x - cx), rnd(cz) + srnd(z - cz), a) if rounded else (x, z, a))
    return out


# ---------------------------------------------------------------------------------------------------------------
# polygons
# ---------------------------------------------------------------------------------------------------------------
def polygon_cells(cx, cz, n, r, rot=0.0, clip=None, apothem=None):
    """Yield (x, z) inside a regular n-gon with circumradius r (vertices at rot + k*360/n degrees)."""
    ap = r * math.cos(math.pi / n) if apothem is None else apothem
    c = _clip2(clip)
    norms = [(math.cos(math.radians(rot + (k + 0.5) * 360.0 / n)), math.sin(math.radians(rot + (k + 0.5) * 360.0 / n)))
             for k in range(n)]
    for x in range(max(_ceil(cx - r), c[0]), min(_floor(cx + r), c[2]) + 1):
        for z in range(max(_ceil(cz - r), c[1]), min(_floor(cz + r), c[3]) + 1):
            dx, dz = x - cx, z - cz
            for nx, nz in norms:
                if dx * nx + dz * nz > ap + 1e-9: break
            else:
                yield x, z


def polygon_prism(vol, cx, cz, n, r, y1, y2, block, rot=0.0, hollow=False, thickness=1, replace=None):
    """Regular n-gon prism (rot in degrees).  hollow=True leaves the interior empty (wall `thickness`)."""
    clip = _vclip2(vol)
    cells = polygon_cells(cx, cz, n, r, rot, clip)
    if hollow:
        ap = r * math.cos(math.pi / n)
        inner = set(polygon_cells(cx, cz, n, r, rot, clip, apothem=ap - thickness))
        cells = (c for c in cells if c not in inner)
    _paint_cells(vol, cells, y1, y2, block, replace)


# ---------------------------------------------------------------------------------------------------------------
# cylinders, cones, towers, terraces
# ---------------------------------------------------------------------------------------------------------------
def cylinder(vol, cx, cz, r, y1, y2, block, hollow=False, thickness=1, closed_top=False, closed_bottom=False,
             replace=None):
    """Vertical cylinder of radius r.  hollow=True: a tube of wall `thickness` (open ends unless closed_*)."""
    yr = _yrange(vol, y1, y2)
    if not yr: return
    clip = _vclip2(vol)
    if not hollow:
        disc(vol, cx, cz, r, y1, block, y2, replace)
        return
    for x, a, b in ring_spans(cx, cz, r - thickness, r, clip):
        _fill_xz_span(vol, x, yr[0], yr[1], a, b, block, replace)
    for closed, yy in ((closed_top, y2), (closed_bottom, y1)):
        if closed and vol.y1 <= yy <= vol.y2:
            disc(vol, cx, cz, r, yy, block, None, replace)


def tower(vol, cx, cz, y1, y2, rfunc, block, hollow=False, thickness=1, smooth=False, closed_top=False,
          closed_bottom=False, replace=None):
    """Solid or hollow body of revolution: layer y has radius rfunc(y) (float; <0 or None = empty layer).
    Hollow shells are widened on slopes so the wall is watertight (no air/water leaks face to face).
    smooth=True softens each outer step with stairs (step >= 0.75 block) or slabs, using the block's family;
    it also adds a topper layer at y2+1 if rfunc(y2+1) is not None."""
    ylo, yhi = max(y1 - 1, vol.y1 - 1), min(y2 + 2, vol.y2 + 1)
    radii = {}
    def R(y):
        v = radii.get(y)
        if v is None and y not in radii:
            v = radii[y] = rfunc(y)
        return v
    rmax = max([R(y) for y in range(max(y1, ylo), min(y2, yhi) + 1) if R(y) is not None] + [-1])
    if rmax < 0 and not smooth: return
    if not vol.intersects(_floor(cx - rmax - 1), y1 - 1, _floor(cz - rmax - 1), _ceil(cx + rmax + 1), y2 + 1,
                          _ceil(cz + rmax + 1)):
        return
    clip = _vclip2(vol)
    fam = B.family(block) if (smooth and not callable(block)) else None
    for y in range(max(y1, vol.y1), min(y2, vol.y2) + 1):
        r = R(y)
        if r is None or r < 0: continue
        if hollow:
            ns = [R(y - 1) if y > y1 else r, r, R(y + 1) if y < y2 else r]
            m = min(v for v in ns if v is not None) if all(v is not None for v in ns) else r
            ri = min(m, r) - thickness
            if (closed_top and y == y2) or (closed_bottom and y == y1): ri = -1
            spans = ring_spans(cx, cz, ri, r, clip)
        else:
            spans = disc_spans(cx, cz, r, clip)
        for x, a, b in spans:
            _fill_xz_span(vol, x, y, y, a, b, block, replace)
    if fam:
        _tower_toppers(vol, cx, cz, y1, y2, R, fam, clip, replace)


def _tower_toppers(vol, cx, cz, y1, y2, R, fam, clip, replace):
    stair_n, slab_n = fam
    for y in range(max(y1 + 1, vol.y1), min(y2 + 1, vol.y2) + 1):
        rp = R(y - 1)
        r = R(y)
        if rp is None or rp < 0: continue
        if r is None: continue
        rin = r if r >= 0 else -1
        if rp <= r: continue
        gap = rp - max(r, 0)
        rout = min(rp, max(r, 0) + 1.0)
        for x, z in ring_cells(cx, cz, rin, rout, clip):
            dx, dz = x - cx, z - cz
            if gap < 0.75 or (dx == 0 and dz == 0):
                st = B.fmt(slab_n, {'type': 'bottom'})
            else:
                st = B.stairs(stair_n, B.facing_from_vector(-dx, -dz))
            if replace is None or vol.get(x, y, z) in replace:
                vol.set(x, y, z, st)


def frustum(vol, cx, cz, y1, y2, r1, r2, block, hollow=False, thickness=1, smooth=False, **kw):
    """Truncated cone: radius r1 at y1 linearly to r2 at y2 (r2 may exceed r1: a flaring bowl)."""
    h = max(1, y2 - y1)
    def rf(y):
        return None if (y < y1 or y > y2) else r1 + (r2 - r1) * (y - y1) / h
    tower(vol, cx, cz, y1, y2, rf, block, hollow, thickness, smooth, **kw)


def cone(vol, cx, cz, y1, y2, r_base, block, r_top=0.0, hollow=False, thickness=1, smooth=False, **kw):
    """Cone from r_base at y1 to r_top at y2 (default: to a point)."""
    frustum(vol, cx, cz, y1, y2, r_base, r_top, block, hollow, thickness, smooth, **kw)


def terraces(vol, cx, cz, y, tiers, block, smooth=False, hollow=False, thickness=1, **kw):
    """Stepped tiers [(radius, height), ...] from the bottom up, starting at layer y.  smooth=True turns each
    step edge into stairs/slabs."""
    layers = []
    for r, h in tiers: layers += [r] * h
    def rf(yy):
        i = yy - y
        return layers[i] if 0 <= i < len(layers) else None
    tower(vol, cx, cz, y, y + len(layers) - 1, rf, block, hollow, thickness, False, **kw)
    if smooth and not callable(block):
        fam = B.family(block)
        if fam:
            clip = _vclip2(vol)
            # toppers go on each tier's step: radius of the lower tier beyond the upper tier's radius
            yy = y
            for (r, h), nxt in zip(tiers, tiers[1:]):
                yy += h
                if nxt[0] < r and vol.y1 <= yy <= vol.y2:
                    def rr(v, a=r, b=nxt[0], top=yy):
                        return a if v == top - 1 else b
                    _tower_toppers(vol, cx, cz, yy - 1, yy, rr, fam, clip, kw.get('replace'))


# ---------------------------------------------------------------------------------------------------------------
# spheres, shells, caps (exact, watertight)
# ---------------------------------------------------------------------------------------------------------------
def shell_runs(cx, cy, cz, r_in, r_out, box=None, y_min=None, y_max=None):
    """Yield (x, z, y0, y1): vertical runs of the spherical shell r_in < d <= r_out about (cx, cy, cz), restricted to
    y_min..y_max and the 3D `box`.  r_in < 0 (or None) gives a solid sphere.  The shell is watertight for
    r_out - r_in >= 1: no cell with d <= r_in is face-adjacent to a cell with d > r_out."""
    ro2 = r_out * r_out
    ri2 = r_in * r_in if (r_in is not None and r_in >= 0) else None
    b = box or (-_INF, -_INF, -_INF, _INF, _INF, _INF)
    lo_y = max(b[1], -_INF if y_min is None else y_min)
    hi_y = min(b[4], _INF if y_max is None else y_max)
    for x in range(max(_ceil(cx - r_out - _EPS), b[0]), min(_floor(cx + r_out + _EPS), b[3]) + 1):
        dx2 = (x - cx) ** 2
        if dx2 > ro2: continue
        w = math.sqrt(ro2 - dx2)
        for z in range(max(_ceil(cz - w - _EPS), b[2]), min(_floor(cz + w + _EPS), b[5]) + 1):
            q = dx2 + (z - cz) ** 2
            s = _zspan(q, cy, ro2)
            if not s: continue
            i = _zspan(q, cy, ri2) if ri2 is not None else None
            runs = [s] if i is None else [(s[0], i[0] - 1), (i[1] + 1, s[1])]
            for a, c in runs:
                a, c = max(a, lo_y), min(c, hi_y)
                if a <= c: yield x, z, a, c


def shell_cells(cx, cy, cz, r_in, r_out, box=None, y_min=None, y_max=None):
    for x, z, a, c in shell_runs(cx, cy, cz, r_in, r_out, box, y_min, y_max):
        for y in range(a, c + 1): yield x, y, z


def _vbox(vol): return (vol.x1, vol.y1, vol.z1, vol.x2, vol.y2, vol.z2)


def sphere(vol, cx, cy, cz, r, block, replace=None):
    """Solid sphere (cells with centre distance <= r)."""
    cap_shell(vol, cx, cy, cz, r, None, block, replace=replace)


def shell(vol, cx, cy, cz, r, thickness, block, replace=None):
    """Spherical shell, outer radius r, wall `thickness` (>= 1 keeps it watertight)."""
    cap_shell(vol, cx, cy, cz, r, thickness, block, replace=replace)


def cap_shell(vol, cx, cy, cz, R, thickness, block, y_min=None, y_max=None, smooth=False, replace=None):
    """Spherical shell of outer radius R and wall `thickness` (None = solid) about (cx, cy, cz), keeping only
    y_min <= y <= y_max (a spherical cap when y_min is given).  Cells: R - thickness < d <= R, decided exactly, so
    the shell is watertight for thickness >= 1.  smooth=True adds stairs/slabs on top of the outer surface where
    the true surface is within the upper half of a block (use thickness >= 2 for dry interiors)."""
    if thickness is not None: thickness = max(1, thickness)
    r_in = None if thickness is None else R - thickness
    c = vol.clip(_floor(cx - R) - 1, max(_floor(cy - R) - 1, _floor(y_min) if y_min is not None else -_INF),
                 _floor(cz - R) - 1, _ceil(cx + R) + 1, _ceil(cy + R) + 1, _ceil(cz + R) + 1)
    if not c: return
    fam = B.family(block) if (smooth and not callable(block)) else None
    for x, z, a, b in shell_runs(cx, cy, cz, r_in, R, c, y_min, y_max):
        _fill_xz_span_y(vol, x, z, a, b, block, replace)
    if fam:
        _cap_toppers(vol, cx, cy, cz, R, c, y_min, y_max, fam, replace)


def _fill_xz_span_y(vol, x, z, y0, y1, block, replace):
    if callable(block):
        put = _put_fn(vol, block, replace)
        for y in range(y0, y1 + 1): put(x, y, z)
    else:
        vol.fill(x, y0, z, x, y1, z, block, replace)


def _cap_toppers(vol, cx, cy, cz, R, c, y_min, y_max, fam, replace):
    stair_n, slab_n = fam
    R2 = R * R

    def top(x, z):
        s = _zspan((x - cx) ** 2 + (z - cz) ** 2, cy, R2)
        return None if not s else s[1]

    def height(x, z):
        q = (x - cx) ** 2 + (z - cz) ** 2
        return cy + math.sqrt(R2 - q) if q <= R2 else None

    for x in range(c[0], c[3] + 1):
        for z in range(c[2], c[5] + 1):
            yt = top(x, z)
            if yt is None or (y_max is not None and yt >= y_max): continue
            if y_min is not None and yt < y_min: continue
            h = height(x, z)
            if h - yt < 0.5: continue
            # steepest uphill neighbour
            best, bd = None, 0.0
            for dx, dz in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                hn = height(x + dx, z + dz)
                if hn is not None and hn - h > bd + 1e-9:
                    best, bd = (dx, dz), hn - h
            if best is not None and bd >= 0.5 and (top(x + best[0], z + best[1]) or -_INF) >= yt + 1:
                st = B.stairs(stair_n, B.facing_from_vector(*best))
            else:
                st = B.fmt(slab_n, {'type': 'bottom'})
            if replace is None or vol.get(x, yt + 1, z) in replace:
                vol.set(x, yt + 1, z, st)


def dome_params(base_radius, height):
    """(R, centre_offset): sphere radius and the sphere centre's y relative to the cap base, for a spherical cap of
    base radius `base_radius` and height `height` (centre_offset = height - R, usually negative)."""
    R = (base_radius ** 2 + height ** 2) / (2.0 * height)
    return R, height - R


def dome_cap(vol, cx, cz, y_base, base_radius, height, thickness, block, smooth=False, replace=None):
    """A spherical cap (dome) standing on y_base: base radius, apex height above y_base, wall thickness."""
    R, off = dome_params(base_radius, height)
    cap_shell(vol, cx, y_base + off, cz, R, thickness, block, y_min=y_base, smooth=smooth, replace=replace)


# ---------------------------------------------------------------------------------------------------------------
# boxes and lines
# ---------------------------------------------------------------------------------------------------------------
def box(vol, x1, y1, z1, x2, y2, z2, block, replace=None):
    """Solid axis-aligned box (inclusive corners, any order)."""
    x1, x2 = min(x1, x2), max(x1, x2); y1, y2 = min(y1, y2), max(y1, y2); z1, z2 = min(z1, z2), max(z1, z2)
    c = vol.clip(x1, y1, z1, x2, y2, z2)
    if not c: return
    if callable(block):
        put = _put_fn(vol, block, replace)
        for y in range(c[1], c[4] + 1):
            for z in range(c[2], c[5] + 1):
                for x in range(c[0], c[3] + 1): put(x, y, z)
    else:
        vol.fill(c[0], c[1], c[2], c[3], c[4], c[5], block, replace)


def box_cells(x1, y1, z1, x2, y2, z2, clip=None):
    c = clip or (-_INF, -_INF, -_INF, _INF, _INF, _INF)
    for y in range(max(y1, c[1]), min(y2, c[4]) + 1):
        for z in range(max(z1, c[2]), min(z2, c[5]) + 1):
            for x in range(max(x1, c[0]), min(x2, c[3]) + 1): yield x, y, z


def hollow_box(vol, x1, y1, z1, x2, y2, z2, walls, floor=None, ceiling=None, thickness=1, clear=False, replace=None):
    """A room shell: four walls of `walls` (full height), optional `floor` / `ceiling` blocks (each `thickness`
    thick), interior cleared to air if clear=True."""
    x1, x2 = min(x1, x2), max(x1, x2); y1, y2 = min(y1, y2), max(y1, y2); z1, z2 = min(z1, z2), max(z1, z2)
    if not vol.intersects(x1, y1, z1, x2, y2, z2): return
    t = thickness
    if clear:
        box(vol, x1 + t, y1 + (t if floor else 0), z1 + t, x2 - t, y2 - (t if ceiling else 0), z2 - t, 'air')
    if walls is not None:
        box(vol, x1, y1, z1, x2, y2, z1 + t - 1, walls, replace)
        box(vol, x1, y1, z2 - t + 1, x2, y2, z2, walls, replace)
        box(vol, x1, y1, z1, x1 + t - 1, y2, z2, walls, replace)
        box(vol, x2 - t + 1, y1, z1, x2, y2, z2, walls, replace)
    if floor is not None: box(vol, x1, y1, z1, x2, y1 + t - 1, z2, floor, replace)
    if ceiling is not None: box(vol, x1, y2 - t + 1, z1, x2, y2, z2, ceiling, replace)


def line_cells(p0, p1, thickness=1, clip=None):
    """Yield unique (x, y, z) along the 3D segment p0 -> p1 (sampled once per block of the longest axis); each
    sample is a `thickness`-sized cube.  Only the part inside the 3D `clip` box is visited."""
    (x0, y0, z0), (x1, y1, z1) = p0, p1
    d = (x1 - x0, y1 - y0, z1 - z0)
    n = max(abs(d[0]), abs(d[1]), abs(d[2]), 1)
    lo, hi = (thickness - 1) // 2, thickness // 2
    i0, i1 = 0, n
    if clip is not None:
        t0, t1 = 0.0, 1.0
        o = (x0, y0, z0)
        for a in range(3):
            lo_b, hi_b = clip[a] - hi - 1, clip[a + 3] + lo + 1
            if d[a] == 0:
                if not (lo_b <= o[a] <= hi_b): return
            else:
                ta, tb = (lo_b - o[a]) / d[a], (hi_b - o[a]) / d[a]
                if ta > tb: ta, tb = tb, ta
                t0, t1 = max(t0, ta), min(t1, tb)
        if t0 > t1: return
        i0, i1 = max(0, _floor(t0 * n) - 1), min(n, _ceil(t1 * n) + 1)
    seen = set()
    for i in range(i0, i1 + 1):
        t = i / n
        px, py, pz = rnd(x0 + d[0] * t), rnd(y0 + d[1] * t), rnd(z0 + d[2] * t)
        for ox in range(px - lo, px + hi + 1):
            for oy in range(py - lo, py + hi + 1):
                for oz in range(pz - lo, pz + hi + 1):
                    c = (ox, oy, oz)
                    if c not in seen:
                        seen.add(c)
                        if clip is None or (clip[0] <= ox <= clip[3] and clip[1] <= oy <= clip[4] and
                                            clip[2] <= oz <= clip[5]):
                            yield c


def line3d(vol, p0, p1, block, thickness=1, replace=None):
    """Beam between two 3D points (x, y, z); `thickness` is the side of the square cross-section."""
    put = _put_fn(vol, block, replace)
    for x, y, z in line_cells(p0, p1, thickness, _vbox(vol)): put(x, y, z)


beam = line3d


# ---------------------------------------------------------------------------------------------------------------
# stairs, spirals, ramps, bridges
# ---------------------------------------------------------------------------------------------------------------
def stairs_run(vol, x, y, z, direction, rise, width=3, block='quartz_block', fill_to=None, run=1, half='bottom'):
    """A straight staircase of `rise` steps starting at (x, y, z) (the lowest tread) and climbing toward
    `direction` ('north'|'east'|'south'|'west').  Tread i is at (start + i*run along direction, y + i).  The
    stairs are centred on the line, `width` wide.  fill_to: fill solid `block` below each tread down to that y.
    Returns the bbox."""
    dx, dz = B.DIRS[direction]
    px, pz = -dz, dx
    lo, hi = -((width - 1) // 2), width // 2
    ex, ez = x + dx * run * (rise - 1), z + dz * run * (rise - 1)
    bb = (min(x, ex) + min(px * lo, px * hi, 0), y, min(z, ez) + min(pz * lo, pz * hi, 0),
          max(x, ex) + max(px * lo, px * hi, 0), y + rise - 1, max(z, ez) + max(pz * lo, pz * hi, 0))
    if not vol.intersects(*bb): return bb
    fam = B.family(block)
    st = B.stairs(block, direction, half) if fam else block
    for i in range(rise):
        for r in range(run):
            for w in range(lo, hi + 1):
                sx, sz = x + dx * (i * run + r) + px * w, z + dz * (i * run + r) + pz * w
                # only the run's first cell is a stair, the rest of the run is a flat tread at the same height
                vol.set(sx, y + i, sz, st if r == 0 else block)
                if fill_to is not None:
                    for yy in range(fill_to, y + i):
                        vol.set(sx, yy, sz, block)
    return bb


def spiral_stairs(vol, cx, cz, y1, y2, r_out, r_in=1.0, block='smooth_quartz', turns=None, steps_per_turn=None,
                  clockwise=True, a0=0.0, column=None):
    """Spiral staircase climbing from y1 to y2 around (cx, cz).  Half-block rise per step (slab, full block,
    slab...) so it can be walked.  The annulus r_in < d <= r_out is split into angular wedges; each wedge is one
    tread, repeating every turn.  `column` (a block) fills the central pillar (radius r_in).  Returns the bbox."""
    bb = (_floor(cx - r_out), y1, _floor(cz - r_out), _ceil(cx + r_out), y2, _ceil(cz + r_out))
    if not vol.intersects(*bb): return bb
    steps = 2 * (y2 - y1)
    rm = (r_in + r_out) / 2.0
    spt = steps_per_turn or max(10, _ceil(2 * math.pi * rm / 1.2))
    fam = B.family(block)
    slab_s = B.fmt(fam[1], {'type': 'bottom'}) if fam else block
    sgn = 1 if clockwise else -1      # on screen (x east, z south) increasing angle is clockwise
    for x, z in ring_cells(cx, cz, r_in, r_out, _vclip2(vol)):
        ang = math.degrees(math.atan2(z - cz, x - cx))
        idx = int(((ang - a0) * sgn) % 360.0 / 360.0 * spt)
        j = idx
        while j < steps:
            yy = y1 + j // 2
            vol.set(x, yy, z, slab_s if j % 2 == 0 else block)
            j += spt
    if column:
        disc(vol, cx, cz, r_in, y1, column, y2 + 1)
    return bb


def _ramp_cells(x0, z0, x1, z1, y0, y1, width, clip):
    dx, dz = x1 - x0, z1 - z0
    L = math.hypot(dx, dz) or 1.0
    ux, uz = dx / L, dz / L
    nx, nz = -uz, ux
    hw = width / 2.0
    ex, ez = abs(ux) * 0.5 + abs(nx) * hw, abs(uz) * 0.5 + abs(nz) * hw
    xa, xb = _floor(min(x0, x1) - hw - 1), _ceil(max(x0, x1) + hw + 1)
    za, zb = _floor(min(z0, z1) - hw - 1), _ceil(max(z0, z1) + hw + 1)
    c = _clip2(clip)
    for x in range(max(xa, c[0]), min(xb, c[2]) + 1):
        for z in range(max(za, c[1]), min(zb, c[3]) + 1):
            px, pz = x - x0, z - z0
            a = px * ux + pz * uz
            s = px * nx + pz * nz
            if -0.5 - 1e-9 <= a < L + 0.5 - 1e-9 and -hw - 1e-9 <= s < hw - 1e-9:
                t = min(1.0, max(0.0, a / L))
                yield x, z, a, s, y0 + (y1 - y0) * t, L


def _deck_put(vol, x, z, h, block, slab_s, thickness, fill_to, force_full=False):
    """Place a deck column whose walking surface is at block-top height h + 0.5 (h in cell units), rounded to the
    nearest half block.  Returns the cell y of the top block."""
    t2 = rnd(2 * (h + 0.5))
    if t2 % 2 == 0 and not force_full and slab_s:
        top = t2 // 2
        vol.set(x, top, z, slab_s)
        below = top - 1
    else:
        top = (t2 - 1) // 2 if t2 % 2 else t2 // 2
        vol.set(x, top, z, block)
        below = top - 1
    for k in range(thickness - 1): vol.set(x, below - k, z, block)
    if fill_to is not None:
        for yy in range(fill_to, below - (thickness - 1) + 1): vol.set(x, yy, z, block)
    return top


def ramp(vol, x0, z0, x1, z1, y0, y1, width, block, thickness=1, fill_to=None, slab=True):
    """A sloped (or flat) deck from (x0, z0) at cell height y0 to (x1, z1) at y1, `width` wide, surfaces rounded to
    half blocks using slabs (walk-up slope <= 0.5 per block).  thickness: blocks under the surface; fill_to: solid
    fill down to that y.  Returns the bbox."""
    hw = width / 2.0
    bb = (_floor(min(x0, x1) - hw), min(y0, y1) - thickness, _floor(min(z0, z1) - hw),
          _ceil(max(x0, x1) + hw), max(y0, y1), _ceil(max(z0, z1) + hw))
    if not vol.intersects(*bb): return bb
    fam = B.family(block)
    slab_s = B.fmt(fam[1], {'type': 'bottom'}) if (fam and slab) else None
    for x, z, a, s, h, L in _ramp_cells(x0, z0, x1, z1, y0, y1, width, _vclip2(vol)):
        _deck_put(vol, x, z, h, block, slab_s, thickness, fill_to)
    return bb


def bridge_deck(vol, x0, z0, x1, z1, y0, width, block, y1=None, thickness=2, rail=True, post_block='quartz_pillar[axis=y]',
                infill='light_blue_stained_glass_pane', post_every=4, cap='smooth_quartz_slab[type=bottom]',
                pier_block=None, pier_every=0, pier_to=None, edge_block=None):
    """A bridge between two points: sloped/flat deck (see ramp), optional railing with posts and glass infill on
    both sides, optional piers every `pier_every` blocks down to `pier_to`, optional `edge_block` along the deck
    edge.  Panes are connected automatically.  Returns the bbox."""
    y1 = y0 if y1 is None else y1
    hw = width / 2.0
    bb = (_floor(min(x0, x1) - hw), min(y0, y1) - thickness - 1, _floor(min(z0, z1) - hw),
          _ceil(max(x0, x1) + hw), max(y0, y1) + 3, _ceil(max(z0, z1) + hw))
    if pier_to is not None: bb = (bb[0], min(bb[1], pier_to), bb[2], bb[3], bb[4], bb[5])
    if not vol.intersects(*bb): return bb
    fam = B.family(block)
    slab_s = B.fmt(fam[1], {'type': 'bottom'}) if fam else None
    rails = {}                      # (x, y, z) -> block, for connection lookups across the volume edge
    c = _vclip2(vol)
    ext = (c[0] - 1, c[1] - 1, c[2] + 1, c[3] + 1)
    for x, z, a, s, h, L in _ramp_cells(x0, z0, x1, z1, y0, y1, width, ext):
        inside = c[0] <= x <= c[2] and c[1] <= z <= c[3]
        edge = s < -hw + 1 - 1e-9 or s >= hw - 1 - 1e-9
        blk = edge_block if (edge and edge_block) else block
        t2 = rnd(2 * (h + 0.5))
        top = (t2 - 1) // 2 if (t2 % 2 or (edge and rail) or not slab_s) else t2 // 2
        if inside:
            _deck_put(vol, x, z, h, blk, slab_s if not (edge and rail) else None, thickness, None,
                      force_full=edge)
        if rail and edge:
            k = int(round(a))
            post = k % post_every == 0 or a < 0.5 or a > L - 0.5
            rails[(x, top + 1, z)] = post_block if post else infill
            if post and cap: rails[(x, top + 2, z)] = cap
        if inside and pier_every and pier_to is not None and abs(s) < 1.0:
            k = int(round(a))
            if k % pier_every == 0 and 0 < a < L - 0.5:
                for yy in range(pier_to, top - thickness + 1): vol.set(x, yy, z, pier_block or block)
    for (x, y, z), blk in rails.items():
        if not vol.contains(x, y, z): continue
        if blk == infill and _kind(blk.split('[', 1)[0]):
            blk = connect_state(blk, lambda d, x=x, y=y, z=z: (
                rails.get((x, y + 1, z)) if d == 'up' else
                rails.get((x + B.DIRS[d][0], y, z + B.DIRS[d][1]))))
        vol.set(x, y, z, blk)
    return bb


# ---------------------------------------------------------------------------------------------------------------
# facade patterns
# ---------------------------------------------------------------------------------------------------------------
def facade_rows(vol, x0, z0, x1, z1, y, facing, rows=1, row_h=6, spacing=4, margin=2, module=None, ext=8, **opts):
    """Place window modules along the straight wall segment (x0, z0) -> (x1, z1) (axis-aligned, or diagonal in
    which case modules stay axis-facing), every `spacing` blocks (centred, `margin` blocks left at the ends),
    `rows` rows `row_h` apart starting at height y.  `facing` is the outward direction of the wall; anchors sit on
    the wall's outer face cell (see architecture.py conventions).  module defaults to
    architecture.window_module; extra keyword options go to the module.  Only modules that can touch the volume
    are drawn.  Returns the number of module positions."""
    if module is None:
        from . import architecture
        module = architecture.window_module
    L = math.hypot(x1 - x0, z1 - z0)
    n = max(1, int((L - 2 * margin) // spacing) + 1) if L > 2 * margin else 1
    used = (n - 1) * spacing
    start = (L - used) / 2.0
    ux, uz = ((x1 - x0) / L, (z1 - z0) / L) if L else (0, 0)
    count = 0
    for i in range(n):
        d = start + i * spacing
        px, pz = rnd(x0 + ux * d), rnd(z0 + uz * d)
        for r in range(rows):
            py = y + r * row_h
            count += 1
            if vol.intersects(px - ext, py - ext, pz - ext, px + ext, py + ext + row_h, pz + ext):
                module(vol, px, py, pz, facing, **opts)
    return count


def facade_rows_arc(vol, cx, cz, r, a0, a1, y, rows=1, row_h=6, spacing=4.0, module=None, ext=8, **opts):
    """Window modules along the outside of a circular wall of outer radius r between angles a0..a1; each module
    faces outward (nearest of the four directions) and sits on the wall's outer face cell."""
    if module is None:
        from . import architecture
        module = architecture.window_module
    span = (a1 - a0)
    arc_len = math.radians(span) * r
    n = max(1, int(arc_len // spacing))
    count = 0
    for i in range(n):
        a = a0 + span * (i + 0.5) / n
        px = rnd(cx + r * math.cos(math.radians(a)))
        pz = rnd(cz + r * math.sin(math.radians(a)))
        facing = B.facing_from_vector(math.cos(math.radians(a)), math.sin(math.radians(a)))
        for rr in range(rows):
            py = y + rr * row_h
            count += 1
            if vol.intersects(px - ext, py - ext, pz - ext, px + ext, py + ext + row_h, pz + ext):
                module(vol, px, py, pz, facing, **opts)
    return count


# ---------------------------------------------------------------------------------------------------------------
# connection fix-up for panes / walls / fences / iron bars
# ---------------------------------------------------------------------------------------------------------------
_NONSOLID_SUF = ('_stairs', '_slab', '_pane', '_wall', '_fence', '_door', '_trapdoor', '_button', '_rod', '_torch',
                 '_carpet', '_sign', '_plate', '_cluster', '_fence_gate', '_bud', '_candle')
_NONSOLID = {'air', 'cave_air', 'void_air', 'water', 'lava', 'lantern', 'soul_lantern', 'iron_bars', 'light', 'ladder',
             'chain', 'lever', 'seagrass', 'tall_seagrass', 'kelp', 'kelp_plant', 'glow_lichen', 'candle', 'vine'}


def _is_solid(state):
    if state is None: return False
    n = state.split('[', 1)[0]
    return not (n in _NONSOLID or n.endswith(_NONSOLID_SUF))


def _kind(n):
    if n.endswith('_pane') or n == 'iron_bars': return 'pane'
    if n.endswith('_wall'): return 'wall'
    if n.endswith('_fence'): return 'fence'
    return None


def autoconnect(vol, box=None):
    """Set north/east/south/west connections of glass panes, iron bars, walls and fences inside `box`
    (default the whole volume) from their neighbours IN THIS VOLUME.  The structure placer does not recompute
    them.  Note it cannot see across a tile edge, so a feature that must be identical in every tile should write
    explicit connections (blocks.pane / blocks.wall) or use Pen, which connects from the component's own cells."""
    ids = [i for i, s in enumerate(vol.palette) if s and _kind(s.split('[', 1)[0])]
    if not ids: return
    idset = set(ids)
    c = vol.clip(*box) if box else (vol.x1, vol.y1, vol.z1, vol.x2, vol.y2, vol.z2)
    if not c: return
    cells = vol.cells
    for y in range(c[1], c[4] + 1):
        for z in range(c[2], c[5] + 1):
            i0 = vol._i(c[0], y, z)
            for k in range(c[3] - c[0] + 1):
                if cells[i0 + k] in idset:
                    _connect_one(vol, c[0] + k, y, z)


def connect_state(state, nb):
    """Return `state` (a pane / bars / wall / fence) with its connection properties set from its neighbours.
    nb(d) gives the neighbouring state (or None) for d in 'north', 'east', 'south', 'west', 'up'."""
    name, props = B.parse(state)
    kind = _kind(name)
    if kind is None: return state
    con = {}
    for d in B.ORDER:
        n = nb(d)
        nk = _kind(n.split('[', 1)[0]) if n else None
        con[d] = bool(n) and (nk == kind or _is_solid(n)) and not (kind == 'wall' and nk in ('pane', 'fence'))
    if kind == 'wall':
        straight = (con['north'] and con['south'] and not con['east'] and not con['west']) or \
                   (con['east'] and con['west'] and not con['north'] and not con['south'])
        above = nb('up')
        up = not straight or (above is not None and above != 'air' and not _kind(above.split('[', 1)[0]))
        props.update({d: ('low' if con[d] else 'none') for d in con})
        props['up'] = 'true' if up else 'false'
    else:
        wl = props.get('waterlogged')
        props.update({d: ('true' if con[d] else 'false') for d in con})
        if wl: props['waterlogged'] = wl
    return B.fmt(name, props)


def _connect_one(vol, x, y, z):
    s = vol.get(x, y, z)
    def nb(d):
        if d == 'up': return vol.get(x, y + 1, z)
        dx, dz = B.DIRS[d]
        return vol.get(x + dx, y, z + dz)
    ns = connect_state(s, nb)
    if ns != s: vol.set(x, y, z, ns)


# ---------------------------------------------------------------------------------------------------------------
# local frame ("pen") for placing oriented components
# ---------------------------------------------------------------------------------------------------------------
class Pen:
    """Draw in a local frame around an anchor, rotated to a facing.

    Local axes (defined for facing='north' and rotated clockwise for the other facings):
      lx : sideways.  For facing north, +lx = east.  (So +lx is to the LEFT of someone standing outside looking
           at the facade; components are symmetric unless they say otherwise.)
      ly : up.
      lz : outward, toward `facing`.  lz = 0 is the wall's outer-face cell, lz > 0 is in front of the wall
           (protrusions), lz < 0 is inside the wall (recesses).
    Block states passed to set()/fill() are written for facing='north' and are rotated to match
    (stairs facing, axis, connection flags...).  `reach` / `up` / `down` bound the component so a Pen for a
    component that cannot touch the volume is inert (`live` False) and costs nothing.
    """
    def __init__(self, vol, x, y, z, facing='north', reach=0, up=0, down=0):
        self.vol, self.x, self.y, self.z = vol, x, y, z
        self.q = B.quarter_turns(facing)
        self.facing = facing
        ox, oz = B.DIRS[facing]
        self.o = (ox, oz)
        self.r = (-oz, ox)       # +lx direction in world
        self.live = vol.intersects(x - reach, y - down, z - reach, x + reach, y + up, z + reach)
        self.bb = None
        self._rot = {}
        self.cells = {}          # every local cell written: (lx, ly, lz) -> block (also when not live)
        self._pend = {}          # panes / walls / fences awaiting their connection state (written by finish())

    def w(self, lx, ly, lz):
        """World (x, y, z) of a local point."""
        return (self.x + lx * self.r[0] + lz * self.o[0], self.y + ly, self.z + lx * self.r[1] + lz * self.o[1])

    def _grow(self, p):
        b = self.bb
        self.bb = (p[0], p[1], p[2], p[0], p[1], p[2]) if b is None else (
            min(b[0], p[0]), min(b[1], p[1]), min(b[2], p[2]), max(b[3], p[0]), max(b[4], p[1]), max(b[5], p[2]))

    def _blk(self, block):
        if self.q == 0 or '[' not in block: return block
        v = self._rot.get(block)
        if v is None: v = self._rot[block] = B.rotate_state(block, self.q)
        return v

    def set(self, lx, ly, lz, block):
        p = self.w(lx, ly, lz)
        self._grow(p)
        if block is None or not self.live: return
        self.cells[(lx, ly, lz)] = block
        if 'north=' not in block and _kind(block.split('[', 1)[0]):
            self._pend[(lx, ly, lz)] = block
            return
        self._pend.pop((lx, ly, lz), None)
        self.vol.set(p[0], p[1], p[2], self._blk(block))

    def fill(self, lx1, ly1, lz1, lx2, ly2, lz2, block):
        a, b = self.w(lx1, ly1, lz1), self.w(lx2, ly2, lz2)
        self._grow(a); self._grow(b)
        if not self.live or block is None: return
        xs = range(min(lx1, lx2), max(lx1, lx2) + 1)
        ys = range(min(ly1, ly2), max(ly1, ly2) + 1)
        zs = range(min(lz1, lz2), max(lz1, lz2) + 1)
        if 'north=' not in block and _kind(block.split('[', 1)[0]):
            for lx in xs:
                for ly in ys:
                    for lz in zs: self.set(lx, ly, lz, block)
            return
        for lx in xs:
            for ly in ys:
                for lz in zs:
                    self.cells[(lx, ly, lz)] = block
                    self._pend.pop((lx, ly, lz), None)
        self.vol.fill(min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]), max(a[0], b[0]), max(a[1], b[1]),
                      max(a[2], b[2]), self._blk(block))

    def line(self, p0, p1, block, thickness=1):
        """Beam between two local points (lx, ly, lz)."""
        a, b = self.w(*p0), self.w(*p1)
        self._grow(a); self._grow(b)
        if self.live:
            line3d(self.vol, a, b, self._blk(block), thickness)

    def bbox(self):
        return self.bb if self.bb is not None else (self.x, self.y, self.z, self.x, self.y, self.z)

    def finish(self):
        """Write the pending panes / walls / fences with connections computed from the component's own cells
        (identical in every tile), then return the bbox.  Components call this last."""
        cells = self.cells
        for (lx, ly, lz), blk in self._pend.items():
            def nb(d, lx=lx, ly=ly, lz=lz):
                # local frame: north = +lz (outward), east = +lx, south = -lz, west = -lx
                if d == 'up': return cells.get((lx, ly + 1, lz))
                return cells.get({'north': (lx, ly, lz + 1), 'south': (lx, ly, lz - 1), 'east': (lx + 1, ly, lz),
                                  'west': (lx - 1, ly, lz)}[d])
            w = self.w(lx, ly, lz)
            self.vol.set(w[0], w[1], w[2], self._blk(connect_state(blk, nb)))
        self._pend = {}
        return self.bbox()
