"""Atlantean-style architectural components.

Every component is ``fn(vol, x, y, z, facing='north', **params) -> bbox`` and draws a recognisable element around
an anchor point, returning its world bbox ``(x1, y1, z1, x2, y2, z2)`` (computed even when the volume is clipped,
so callers can chain placements).  A component that cannot touch `vol` returns immediately.

Conventions (see geometry.Pen):
  * `facing` is the outward direction of the element.  The anchor sits on the wall's OUTER FACE cell for
    wall-mounted elements (lz = 0 is that cell, lz > 0 in front, lz < 0 inside the wall).  For free-standing
    elements (column, tower cap, floor pattern, console) the anchor is the centre of the base.
  * Visual rules: white / pale-grey primary (palette.WHITE...), light-blue / cyan glass and energy accents,
    dark prismarine / deepslate for contrast, glow always behind glass or inside channels, never bare grids.
Colours and block names come from `palette`.
"""
import math

from . import palette as P
from . import blocks as B
from . import geometry as G
from .geometry import Pen, rnd

PILLAR_Y = 'quartz_pillar[axis=y]'
AIR = 'air'


def _slab(name, t='bottom'):
    return B.slab(name, t)


def _stair(name, facing, half='bottom'):
    return B.stairs(name, facing, half)


# ---------------------------------------------------------------------------------------------------------------
def atlantean_column(vol, x, y, z, facing='north', height=14, radius=1, base=True, capital=True, glow=True,
                   block=P.WHITE_PILLAR, trim=P.DARK_PRISMARINE):
    """Slender fluted column (plus-shaped for radius 1, round for larger), dark plinth, flared capital and a
    recessed light-blue glow strip on the `facing` side (glass over a hidden sea lantern)."""
    r = radius
    bb = (x - r - 2, y, z - r - 2, x + r + 2, y + height - 1, z + r + 2)
    if not vol.intersects(*bb): return bb
    top = y + height - 1
    shaft_lo, shaft_hi = y + (2 if base else 0), top - (3 if capital else 0)
    if base:
        G.disc(vol, x, z, r + 2.0, y, trim)
        G.disc(vol, x, z, r + 1.2, y + 1, P.WHITE_BLOCK)
    G.cylinder(vol, x, z, r + 0.25, shaft_lo, shaft_hi, PILLAR_Y)
    # fluting: four vertical ribs of pale grey on the diagonals for the larger columns
    if r >= 2:
        for dx, dz in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            for yy in range(shaft_lo + 1, shaft_hi, 1):
                vol.set(x + dx * r, yy, z + dz * r, P.PALE_GREY)
    if capital:
        G.disc(vol, x, z, r + 0.9, top - 2, P.WHITE)
        G.disc(vol, x, z, r + 1.6, top - 1, P.WHITE_BLOCK)
        G.disc(vol, x, z, r + 2.0, top, P.DARK)
    if glow:
        p = Pen(vol, x, y, z, facing, reach=r + 2, up=height)
        g0, g1 = shaft_lo + 2, shaft_hi - 2
        for ly in range(g0 - y, g1 - y + 1):
            p.set(0, ly, r - 1, P.LAMP)
            p.set(0, ly, r, P.GLASS_BLUE)
    return bb


def curved_support(vol, x, y, z, facing='north', height=16, depth=8, width=3, power=2.0, block=P.WHITE,
                   foot=P.DARK_PRISMARINE, trim=True):
    """Buttress fin projecting from a wall: deep at the foot, sweeping in to the wall at the top along
    depth * (1 - h/H)**power, with stair-softened top edge and a dark foot."""
    hw = width // 2
    p = Pen(vol, x, y, z, facing, reach=max(hw, depth) + 3, up=height + 1, down=1)
    prof = [max(1, rnd(depth * (1 - ly / float(height)) ** power)) for ly in range(height)]
    if p.live:
        for ly in range(height):
            d = prof[ly]
            if d >= 1:
                p.fill(-hw, ly, 1, hw, ly, d, block)
            if ly > 0 and prof[ly - 1] > d:      # exposed top of the layer below: a stair leaning into the wall
                zz = d + 1
                if zz <= prof[ly - 1]:
                    p.fill(-hw, ly, zz, hw, ly, zz, _stair(block, 'south'))
        p.fill(-hw - 1, 0, 1, hw + 1, 0, max(1, prof[0]) + 1, foot)
        if trim and height > 6:
            for ly in range(2, height - 2, 5):    # thin pale-grey bands across the fin
                d = prof[ly]
                if d >= 1: p.fill(-hw, ly, d, hw, ly, d, P.PALE_GREY)
    p.set(-hw - 1, 0, 0, None); p.set(hw + 1, height - 1, depth + 1, None)
    return p.finish()


def decorative_rib(vol, x, y, z, facing='north', radius=8, a0=180, a1=90, thickness=1, width=3, block=P.WHITE,
                   inlay=P.LIGHT_BLUE, glow=False):
    """Curved rib: an arc in the vertical plane through the anchor, centred `radius` in front of the wall at
    anchor height (a0=180 starts at the anchor going straight up; a1=90 ends at the top of the arc), `width`
    wide with a light-blue inlay line along the middle.  glow=True embeds a sea lantern every 5th inlay cell
    behind glass."""
    hw = width // 2
    p = Pen(vol, x, y, z, facing, reach=radius + hw + 3, up=radius + 2, down=radius + 1)
    n = max(8, int(abs(a1 - a0) * math.pi / 180 * radius * 1.6))
    seen = set()
    for i in range(n + 1):
        a = math.radians(a0 + (a1 - a0) * i / n)
        lz, ly = rnd(radius + radius * math.cos(a)), rnd(radius * math.sin(a))
        if (lz, ly) in seen: continue
        seen.add((lz, ly))
        for t in range(thickness):
            for lx in range(-hw, hw + 1):
                inner = lx == 0 and t == thickness - 1
                if inner and glow and len(seen) % 5 == 0:
                    p.set(lx, ly, lz, P.LAMP)
                else:
                    p.set(lx, ly, lz, inlay if inner and width > 1 else block)
    p.set(-hw, 0, 0, None); p.set(hw, radius, radius * 2, None)
    return p.finish()


def window_module(vol, x, y, z, facing='north', width=1, height=5, depth=1, frame=P.WHITE_BLOCK,
                  glass=P.GLASS_BLUE_PANE, pointed=True, sill=True):
    """Narrow vertical Atlantean window cut into a wall: `width` (odd) x `height` opening through `depth` wall cells,
    light-blue glass pane, white pilaster frame projecting one block, slab sill and lintel, chamfered pointed top
    for width >= 3.  Anchor: bottom-centre of the opening on the wall's outer face cell."""
    hw = width // 2
    p = Pen(vol, x, y, z, facing, reach=hw + 3 + depth, up=height + 3, down=2)
    if p.live:
        pz = -(depth // 2)
        pane_state = B.pane(glass, east=True, west=True) if glass.endswith('_pane') else glass
        for lx in range(-hw, hw + 1):
            for ly in range(height):
                for lz in range(-depth + 1, 1): p.set(lx, ly, lz, AIR)
                p.set(lx, ly, pz, pane_state)
        for ly in range(-1, height + 1):
            p.set(-hw - 1, ly, 1, PILLAR_Y)
            p.set(hw + 1, ly, 1, PILLAR_Y)
        if sill:
            p.fill(-hw, -1, 1, hw, -1, 1, _slab(frame, 'bottom'))
        p.fill(-hw - 1, height, 1, hw + 1, height, 1, _slab(frame, 'bottom'))
        if pointed and width >= 3:
            for sgn, face in ((1, 'east'), (-1, 'west')):
                for lz in range(-depth + 1, 1): p.set(sgn * hw, height - 1, lz, _stair(frame, face, 'top'))
                p.set(sgn * hw, height - 1, pz, _stair(frame, face, 'top'))
    else:
        p.set(-hw - 1, -1, -depth, None); p.set(hw + 1, height, 1, None)
    return p.finish()


def _portal_profile(width, height, straight):
    hw = width // 2
    steps = max(1, height - straight)
    prof = []
    for ly in range(height):
        if ly < straight:
            prof.append(hw)
        else:
            k = ly - straight
            prof.append(max(0, rnd(hw * math.sqrt(max(0.0, 1 - ((k + 0.5) / steps) ** 2)))))
    return prof


def entrance_portal(vol, x, y, z, facing='north', width=7, height=14, straight=None, wall_depth=3, glow=True):
    """Tall stepped-arch doorway: opening (`width` wide, `height` tall with the arch starting at `straight`) cut
    through the wall, framed by three concentric stepped bands: an inner recessed band of cyan glass over sea
    lanterns (the glow strip), a projecting white band and a dark outer band.  Anchor: bottom-centre of the opening
    on the wall's outer face cell."""
    hw = width // 2
    straight = int(height * 0.55) if straight is None else straight
    p = Pen(vol, x, y, z, facing, reach=hw + 5, up=height + 4, down=1)
    if p.live:
        prof = _portal_profile(width, height, straight)
        op = {(lx, ly) for ly in range(height) for lx in range(-prof[ly], prof[ly] + 1)}
        dist = {}
        for (lx, ly) in op:
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    c = (lx + dx, ly + dy)
                    if c in op or c[1] < 0: continue
                    d = max(abs(dx), abs(dy))
                    if d < dist.get(c, 9): dist[c] = d
        for (lx, ly), d in dist.items():
            if d == 1:
                for lz in range(-wall_depth + 1, 0): p.set(lx, ly, lz, P.WHITE)
                if glow and (lx + ly) % 2 == 0:
                    p.set(lx, ly, 0, P.LAMP)
                else:
                    p.set(lx, ly, 0, P.GLASS_CYAN)
                p.set(lx, ly, 1, P.GLASS_CYAN)
            elif d == 2:
                p.fill(lx, ly, 0, lx, ly, 2, P.WHITE_BLOCK)
            else:
                p.fill(lx, ly, 0, lx, ly, 1, P.DARK)
        for (lx, ly) in op:
            p.fill(lx, ly, -wall_depth + 1, lx, ly, 3, AIR)
    else:
        p.set(-hw - 3, 0, -wall_depth, None); p.set(hw + 3, height + 3, 3, None)
    return p.finish()


def tower_cap(vol, x, y, z, facing='north', style='spire', radius=5, height=16, block=P.WHITE, accent=P.DARK):
    """Crown for a tower whose top centre is (x, y, z) (the cap's first layer is y).  Styles: 'spire' (tapering
    smooth spire with end-rod tip), 'crown' (ring of leaning prongs round a glass lantern), 'dish' (flaring dish with a
    glowing core), 'stepped' (terraced ziggurat with a short mast)."""
    r = radius
    bb = (x - r - 1, y, z - r - 1, x + r + 1, y + height + 3, z + r + 1)
    if not vol.intersects(*bb): return bb
    if style == 'spire':
        base_h = 2
        G.cylinder(vol, x, z, r + 1, y, y + base_h - 1, accent)
        G.cone(vol, x, z, y + base_h, y + height - 4, r, block, r_top=0.8, smooth=True)
        G.cylinder(vol, x, z, 0.5, y + height - 3, y + height - 1, P.ROD)
        for yy in range(y + height, y + height + 3): vol.set(x, yy, z, 'end_rod[facing=up]')
        G.ring(vol, x, z, r - 1, r, y + base_h, P.GLASS_CYAN)       # glass collar band
    elif style == 'crown':
        G.cylinder(vol, x, z, r, y, y + 1, accent)
        G.disc(vol, x, z, r - 1, y + 2, block)
        n = 8
        for (px, pz, a) in G.radial(n, r - 0.5, 0, x, z):
            tx, tz = rnd(x + (r * 0.45) * math.cos(math.radians(a))), rnd(z + (r * 0.45) * math.sin(math.radians(a)))
            G.line3d(vol, (px, y + 2, pz), (tx, y + height, tz), block, 1)
            vol.set(tx, y + height + 1, tz, P.ROD)
        G.sphere(vol, x, y + 5, z, 2.6, P.GLASS_BLUE)
        G.sphere(vol, x, y + 5, z, 1.0, P.LAMP)
    elif style == 'dish':
        G.cylinder(vol, x, z, 1, y, y + 3, block)
        G.frustum(vol, x, z, y + 4, y + 4 + max(3, height // 3), 2, r + 3, block, hollow=True, thickness=1, smooth=False)
        top = y + 4 + max(3, height // 3)
        G.disc(vol, x, z, r + 1.5, top - 1, accent)
        G.ring(vol, x, z, r + 2, r + 3, top, accent)
        G.disc(vol, x, z, r + 1, top, P.GLASS_CYAN)
        G.disc(vol, x, z, r, top - 1, P.LAMP)           # beneath the glass: hidden glow
        for yy in range(top + 1, top + 4): vol.set(x, yy, z, 'end_rod[facing=up]')
    else:   # stepped
        tiers = [(r, 2), (max(1, r * 0.78), 2), (max(1, r * 0.55), 2), (max(1, r * 0.3), max(2, height - 8))]
        G.terraces(vol, x, z, y, tiers, block, smooth=True)
        G.ring(vol, x, z, r - 1, r, y + 1, P.GLASS_CYAN)
        top = y + sum(h for _, h in tiers)
        for yy in range(top, top + 4): vol.set(x, yy, z, 'end_rod[facing=up]')
    return bb


def balcony(vol, x, y, z, facing='north', radius=4, floor=P.WHITE, rim=P.PALE_GREY, railing=True):
    """Semi-circular balcony projecting from a wall at floor level y (anchor on the wall's outer face cell, y =
    floor block height): paving with a pale rim, tapering dark-and-white corbel beneath, and a glass-and-post
    railing on the curved edge."""
    r = radius
    p = Pen(vol, x, y, z, facing, reach=r + 3, up=3, down=r)
    if p.live:
        ring_cells = []
        for lz in range(1, r + 2):
            for lx in range(-r, r + 1):
                d2 = lx * lx + (lz - 1) ** 2
                if d2 > r * r + r * 0.5: continue
                edge = d2 > (r - 1) ** 2 + 1
                p.set(lx, 0, lz, rim if edge else floor)
                if edge: ring_cells.append((lx, lz))
                dd = math.sqrt(d2)
                if dd <= r - 0.6: p.set(lx, -1, lz, P.WHITE_BLOCK)
                if dd <= r - 1.8: p.set(lx, -2, lz, P.DARK)
        if railing:
            ring_cells.sort(key=lambda c: math.atan2(c[1] - 1, c[0]))
            for i, (lx, lz) in enumerate(ring_cells):
                if lz < 1: continue
                if i % 3 == 0:
                    p.set(lx, 1, lz, PILLAR_Y)
                    p.set(lx, 2, lz, _slab(P.WHITE, 'bottom'))
                else:
                    p.set(lx, 1, lz, P.GLASS_BLUE_PANE)
    else:
        p.set(-r, -2, 0, None); p.set(r, 2, r + 2, None)
    return p.finish()


def energy_conduit(vol, x, y, z, facing='north', length=10, axis='x', spacing=1, casing=P.DARK_PRISMARINE,
                   cover=P.GLASS_CYAN, core=P.LAMP):
    """A line of lanterns hidden in a channel: dark casing, centre line of sea lanterns (every `spacing` cells), and
    cyan stained glass over the front (+lz) so only a thin cyan glow shows.  axis: 'x' (sideways), 'y' (up), 'z'
    (outward).  Starts at the anchor, runs `length` cells."""
    p = Pen(vol, x, y, z, facing, reach=length + 3, up=length + 3, down=3)
    def at(k, u, v):      # k along axis, u/v across (v = toward the front)
        if axis == 'x': return (k, u, v)
        if axis == 'y': return (u, k, v)
        return (u, v, k)
    if p.live:
        for k in range(length):
            for u in range(-1, 2):
                for v in range(-1, 2):
                    pos = at(k, u, v)
                    if u == 0 and v == 0:
                        p.set(*pos, core if k % spacing == 0 else P.GLASS_TINTED)
                    elif u == 0 and v == 1:
                        p.set(*pos, cover)
                    else:
                        p.set(*pos, casing)
    else:
        p.set(*at(0, -1, -1), None); p.set(*at(length - 1, 1, 1), None)
    return p.finish()


def light_fixture(vol, x, y, z, facing='north', kind='ceiling', size=1, lamp=P.LAMP, cover=P.GLASS_WHITE,
                  frame=P.WHITE_BLOCK):
    """Recessed light: the lamp sits inside the structure behind glass/slab, never exposed.
    kind='ceiling': anchor = the lowest ceiling cell (room below); glass flush with the ceiling, lamp above it.
    kind='wall': anchor = wall outer-face cell; glass at the face, lamp behind (lz -1), 1 x `size*2+1` strip.
    kind='floor': anchor = floor surface cell; glass flush, lamp below."""
    r = size
    p = Pen(vol, x, y, z, facing, reach=r + 3, up=r * 2 + 4, down=r + 3)
    if p.live:
        if kind == 'ceiling':
            for lx in range(-r, r + 1):
                for lz in range(-r, r + 1):
                    p.set(lx, 0, lz, cover)
                    p.set(lx, 1, lz, lamp)
                    p.set(lx, 2, lz, frame)
            for lx in range(-r - 1, r + 2):
                for lz in range(-r - 1, r + 2):
                    if max(abs(lx), abs(lz)) == r + 1:
                        p.set(lx, 0, lz, _slab(frame, 'top'))
                        p.set(lx, 1, lz, frame)
        elif kind == 'wall':
            for ly in range(-r, r + 1):
                p.set(0, ly, 0, cover if cover != P.GLASS_WHITE else P.GLASS_BLUE)
                p.set(0, ly, -1, lamp)
                p.set(-1, ly, 0, frame); p.set(1, ly, 0, frame)
                p.set(-1, ly, -1, frame); p.set(1, ly, -1, frame)
                p.set(0, ly, -2, frame)
            p.set(0, r + 1, 0, P.DARK); p.set(0, -r - 1, 0, P.DARK)
        else:   # floor
            for lx in range(-r, r + 1):
                for lz in range(-r, r + 1):
                    p.set(lx, 0, lz, P.GLASS_BLUE)
                    p.set(lx, -1, lz, lamp)
                    p.set(lx, -2, lz, frame)
            for lx in range(-r - 1, r + 2):
                for lz in range(-r - 1, r + 2):
                    if max(abs(lx), abs(lz)) == r + 1: p.set(lx, 0, lz, P.DARK)
    else:
        p.set(-r - 1, -2, -r - 1, None); p.set(r + 1, 2, r + 1, None)
    return p.finish()


def bridge_section(vol, x, y, z, facing='north', length=12, width=5, rail=True, underside=True, glow=True):
    """Section of an Atlantean walkway running from the anchor (centre of the start edge) toward `facing`, `length`
    long: white deck with dark kerbs and a pale centre strip, curved white belly beams underneath, glass railing with
    posts, hidden cyan glow strips beneath the deck edges."""
    hw = width // 2
    p = Pen(vol, x, y, z, facing, reach=length + hw + 3, up=4, down=hw + 4)
    if p.live:
        for lz in range(length):
            for lx in range(-hw, hw + 1):
                edge = abs(lx) == hw
                p.set(lx, 0, lz, P.DARK if edge else (P.PALE_GREY if lx == 0 else P.WHITE))
                p.set(lx, -1, lz, P.DARK_PRISMARINE if abs(lx) >= hw - 1 else P.WHITE_BLOCK)
            if underside:
                dip = rnd((hw + 1) * math.sin(math.pi * (lz + 0.5) / length))
                for k in range(dip):
                    for lx in range(-1, 2):
                        p.set(lx, -2 - k, lz, P.WHITE if k < dip - 1 else P.PALE_GREY)
            if glow and lz % 3 == 1:
                for sgn in (-1, 1):
                    p.set(sgn * (hw - 1), -1, lz, P.LAMP)
                    p.set(sgn * (hw - 1), -2, lz, P.GLASS_CYAN)
            if rail:
                for sgn in (-1, 1):
                    if lz % 4 == 0 or lz == length - 1:
                        p.set(sgn * hw, 1, lz, PILLAR_Y)
                        p.set(sgn * hw, 2, lz, _slab(P.WHITE, 'bottom'))
                    else:
                        p.set(sgn * hw, 1, lz, P.GLASS_BLUE_PANE)
    else:
        p.set(-hw, -2 - hw, 0, None); p.set(hw, 2, length - 1, None)
    return p.finish()


def railing(vol, x, y, z, facing='north', length=6, axis='x', style='glass', post_every=3):
    """Railing running `length` cells from the anchor along `axis` ('x' sideways, 'z' outward), sitting on the
    surface cell below y.  Styles: 'glass' (pale posts with light-blue panes, slab caps), 'bars' (dark iron bars),
    'solid' (quartz parapet with slab top)."""
    p = Pen(vol, x, y, z, facing, reach=length + 2, up=3, down=1)
    def at(k, ly): return (k, ly, 0) if axis == 'x' else (0, ly, k)
    if p.live:
        for k in range(length):
            post = k % post_every == 0 or k == length - 1
            if style == 'solid':
                p.set(*at(k, 0), P.WHITE_BLOCK)
                p.set(*at(k, 1), _slab(P.WHITE, 'bottom'))
            elif post:
                p.set(*at(k, 0), PILLAR_Y)
                p.set(*at(k, 1), _slab(P.WHITE, 'bottom'))
            else:
                p.set(*at(k, 0), P.GLASS_BLUE_PANE if style == 'glass' else 'iron_bars')
    else:
        p.set(*at(0, 0), None); p.set(*at(length - 1, 1), None)
    return p.finish()


def floor_pattern(vol, x, y, z, facing='north', radius=10, style='radial', sectors=12):
    """Paved floor centred on (x, z) at height y, in white / pale grey with cyan inlay lines and a dark rim.  Styles:
    'radial' (alternating wedges, cyan spokes and ring, light-blue medallion), 'rings' (concentric rings),
    'geometric' (square, 4x4 checker with cyan grid lines and a dark border)."""
    r = radius
    bb = (x - r, y, z - r, x + r, y, z + r)
    if not vol.intersects(*bb): return bb
    off = 90 * B.quarter_turns(facing)
    clip = (vol.x1, vol.z1, vol.x2, vol.z2)
    if style == 'geometric':
        for xx in range(max(x - r, vol.x1), min(x + r, vol.x2) + 1):
            for zz in range(max(z - r, vol.z1), min(z + r, vol.z2) + 1):
                dx, dz = xx - x, zz - z
                if max(abs(dx), abs(dz)) >= r:
                    b = P.DARK
                elif dx % 8 == 0 or dz % 8 == 0:
                    b = P.CYAN
                else:
                    b = P.WHITE if ((dx // 4) + (dz // 4)) % 2 == 0 else P.PALE_GREY
                vol.set(xx, y, zz, b)
        return bb
    for xx, zz in G.disc_cells(x, z, r, clip):
        dx, dz = xx - x, zz - z
        d = math.hypot(dx, dz)
        if d > r - 1:
            b = P.DARK
        elif style == 'rings':
            b = P.CYAN if int(d) % 6 == 5 else (P.WHITE, P.PALE_GREY, P.WHITE, P.GREY)[int(d / 2) % 4]
        else:
            ang = (math.degrees(math.atan2(dz, dx)) - off) % 360
            sw = 360.0 / sectors
            s = int(ang / sw)
            phi = min(ang % sw, sw - ang % sw)
            if d <= 1.5:
                b = P.LIGHT_BLUE
            elif d > 3 and d * math.sin(math.radians(phi)) < 0.6:
                b = P.CYAN
            elif abs(d - r * 0.5) < 0.5 and r >= 8:
                b = P.CYAN
            else:
                b = P.WHITE if s % 2 == 0 else P.PALE_GREY
        vol.set(xx, y, zz, b)
    return bb


def wall_panel(vol, x, y, z, facing='north', width=5, height=7, panel=P.PALE_GREY, frame=P.WHITE, line=True):
    """Atlantean wall panel: projecting white frame round a recessed pale-grey field with a thin glowing line (a row of
    light-blue glass over sea lanterns set in the wall) down the middle and across a third of the way up."""
    hw = width // 2
    p = Pen(vol, x, y, z, facing, reach=hw + 3, up=height + 2, down=1)
    if p.live:
        for lx in range(-hw, hw + 1):
            for ly in range(height):
                border = abs(lx) == hw or ly == 0 or ly == height - 1
                if border:
                    p.set(lx, ly, 1, frame)
                    p.set(lx, ly, 0, frame)
                else:
                    p.set(lx, ly, 0, panel)
                    p.set(lx, ly, -1, P.WHITE_BLOCK)
        if line:
            for ly in range(1, height - 1):
                p.set(0, ly, 0, P.GLASS_BLUE)
                p.set(0, ly, -1, P.LAMP)
            ty = max(2, height // 3)
            for lx in range(-hw + 1, hw):
                if lx == 0: continue
                p.set(lx, ty, 0, P.GLASS_BLUE)
                p.set(lx, ty, -1, P.LAMP)
    else:
        p.set(-hw, 0, -1, None); p.set(hw, height, 1, None)
    return p.finish()


def console(vol, x, y, z, facing='north', width=3, screens=True):
    """Atlantean control console (furniture): dark pedestal, white sloped desk (stairs rising to the back wall), a
    glass screen lit from beneath with a glowing crystal rod, and button rows.  Anchor: back-centre on the floor
    surface cell; the operator stands in front (lz > 0)."""
    hw = width // 2
    p = Pen(vol, x, y, z, facing, reach=hw + 3, up=4, down=1)
    if p.live:
        for lx in range(-hw, hw + 1):
            p.set(lx, 1, 0, P.DARK_PRISMARINE); p.set(lx, 1, 1, P.DARK_PRISMARINE)
            p.set(lx, 1, 2, P.DARK_PRISMARINE)                         # front casing hides the lamp below the screen
            p.set(lx, 2, 2, _slab(P.WHITE, 'bottom'))
            p.set(lx, 2, 0, _stair(P.WHITE, 'south'))                  # high back toward the wall (local south)
            if screens and abs(lx) == hw - 0 and width >= 3 or (screens and width == 1):
                p.set(lx, 2, 1, P.GLASS_BLUE)
                p.set(lx, 1, 1, P.LAMP)                                # hidden under the screen
            else:
                p.set(lx, 2, 1, _slab(P.WHITE, 'bottom'))
        p.set(0, 3, 0, 'end_rod[facing=up]')
        if width >= 3:
            p.set(0, 2, 1, 'light_blue_stained_glass')
            p.set(0, 1, 1, P.LAMP)
            for lx in (-hw, hw):
                p.set(lx, 3, 1, 'stone_button[face=floor,facing=north,powered=false]')
    else:
        p.set(-hw, 1, 0, None); p.set(hw, 3, 1, None)
    return p.finish()


# Registry: name -> (function, gallery kind).  kind 'wall' = needs a wall behind (drawn facing south in gallery).
COMPONENTS = {
    'atlantean_column': (atlantean_column, 'free'),
    'curved_support': (curved_support, 'wall'),
    'decorative_rib': (decorative_rib, 'wall'),
    'window_module': (window_module, 'wall'),
    'entrance_portal': (entrance_portal, 'wall'),
    'tower_cap': (tower_cap, 'free'),
    'balcony': (balcony, 'wall'),
    'energy_conduit': (energy_conduit, 'wall'),
    'light_fixture': (light_fixture, 'wall'),
    'bridge_section': (bridge_section, 'free'),
    'railing': (railing, 'free'),
    'floor_pattern': (floor_pattern, 'free'),
    'wall_panel': (wall_panel, 'wall'),
    'console': (console, 'free'),
}
