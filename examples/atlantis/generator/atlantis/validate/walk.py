"""Walk graph: standable positions of a built tile, their edges, and the whole-city stitching.

MOVEMENT MODEL (an approximation of vanilla Minecraft Java walking; simplifications are listed at the end)

Node = a place a player can stand: (x, z, h) where h is the standing surface height in HALF blocks
(h = 2 * surface y).  A node is one of these kinds:
  * support node   a block with a top collision surface whose body cells above are passable:
        full block, glass, leaves, top/double slab, top stairs, pane, iron bars, chest, ... (anything not special)
                                                            surface = y + 1        h = 2*y + 2
        bottom slab, bed                                    surface = y + 0.5      h = 2*y + 1
        bottom stairs (flag STAIR; the low front half)      surface = y + 0.5      h = 2*y + 1
        fence, wall (1.5 blocks tall)                       surface = y + 1.5      h = 2*y + 3
    Body cells: y+1 and y+2 (y+1..y+3 for fence/wall) must all be passable.
  * climb node     a passable climbable cell (ladder, any vine, scaffolding) whose cell above is passable.
    The player hangs in the cell: h = 2*y_cell.  Flag CLIMB (also set on a support node whose feet cell is climbable).
  * swim node      the top water cell of a water column when the two cells above it are passable (not water).
    The player floats at the surface: h = 2*y_cell + 1.  Flag WET.  Covered water makes no nodes.
Passable (body cells): air, cave_air, light, all no-collision blocks (torches, signs, buttons, rails, plants, ...),
doors (every *_door is treated as open), trapdoors, fence gates, carpets (a carpet is passable and NOT a surface: the
floor under it is the support), lanterns, candles, chains, end rods, lily pads, flower pots, heads, repeaters.
Not passable and no surface: lava, structure_void.  Water is the swim kind only.  Unknown blocks are full cubes.
Nodes exist only for support y >= 0 and, for dry nodes, when the first body cell is inside the dome
(ctx.inside_dome) or both body cells are plain air / cave_air (explicit dry air outside the dome).  Swim nodes need the
cell above the water inside the dome.  The exterior seabed and sea never make nodes.

Edges join 4-neighbour columns (no diagonals).  With nodes A (here) and B (next column), dh = hB - hA and
H(h) = (h + 1) // 2 + 1 the highest cell a body at height h touches; top(N) is the highest cell of the passable run
above N's support (scanned up to support + 8).  A -> B is possible iff
    top(B) >= H(hA)      B's column is clear up to A's head (no wall / ceiling), and one of
    dh <= 1              level, step down or step up <= 0.5 block; dh >= -6 (a drop of at most 3 blocks);
    dh == 2              one full block up: a jump, needing top(A) >= H(hA) + 1 (3 free cells at the takeoff); no jump
                         needed if A or B is a STAIR, A is CLIMB (climbing out of a ladder) or A is WET (swimming out
                         onto a ledge at most one block above the surface).
    anything else        impossible (climb > 1 block, drop > 3 blocks).
Climb nodes in the same column whose h differ by 2 are joined both ways (the climbable column).
An edge is MUTUAL when A -> B and B -> A are both possible, otherwise ONE-WAY (a 3 block drop is one-way: you can
drop but not climb back).  An edge touching a WET node is a SWIM edge; all others are DRY.

GRAPH.  Per tile: nodes come from whole-tile bigint masks (one byte per cell), edges inside the tile are labelled with
a union-find over MUTUAL edges: DRY labels (dry mutual edges only) and WET labels (dry + swim mutual edges).  Only
compact data leaves the tile: per dry label the size and a representative cell, the one-way edges between labels, and
the border stubs of each face (coordinate, h, top, flags, labels).  `Solution` stitches neighbouring tiles by applying
the same edge rule to matching stubs, unions the labels globally and computes directed REACHABILITY from seed
components: dry reach follows dry edges only, wet reach adds swim edges.  A ledge you can only drop FROM is therefore
not reachable, but a pit you can drop INTO is.

SIMPLIFICATIONS.  Stairs ignore their facing (a bottom stair is a +0.5 surface that also allows a +1 step in any
direction).  Diagonal moves, sneaking, sprint jumps, boats, elytra, doors that are locked, and blocks that hurt
(cactus, magma, fire) are ignored.  Fences/walls are climbable-onto only by drops.  Carpets add no height.
"""
import bisect, math
from collections import defaultdict

from . import classify as K
from .core import tinfo, toint, parse_key
from .. import planner
from ..util import parse_state

WET, CLIMB, STAIR = 1, 2, 4
RUN_CAP = 8                      # cells above the support scanned for the clear run
DECK_BAND = (9, 11)              # half-block heights counted as 'on the deck' (deck top y 4 -> h 10)

_CLIMB_NAMES = {'ladder', 'vine', 'scaffolding', 'twisting_vines', 'twisting_vines_plant', 'weeping_vines',
                'weeping_vines_plant', 'cave_vines', 'cave_vines_plant'}
_PASS_PART_SUFFIX = ('_door', '_trapdoor', '_fence_gate', '_carpet', '_hanging_sign', '_head', '_skull', '_candle')
_PASS_PART_NAMES = {'lantern', 'soul_lantern', 'candle', 'chain', 'end_rod', 'lily_pad', 'repeater', 'comparator',
                    'flower_pot', 'small_dripleaf', 'pointed_dripstone', 'sculk_sensor', 'calibrated_sculk_sensor',
                    'bamboo', 'sugar_cane', 'cocoa', 'chorus_flower', 'chorus_plant', 'azalea', 'flowering_azalea',
                    'big_dripleaf_stem', 'cake', 'decorated_pot', 'turtle_egg', 'bell'}
_NO_SURFACE = {'lava', 'structure_void'}

# (passable, support kind, climb, water, plain_air); support kind 0 none, 1 half, 2 full, 3 fence, 4 stair
_PROPS = {}


def block_props(b):
    r = _PROPS.get(b.state)
    if r is None:
        r = _PROPS[b.state] = _compute_props(b)
    return r


def _compute_props(b):
    name, cls = b.name, b.cls
    if name in _CLIMB_NAMES: return (1, 0, 1, 0, 0)
    if cls == K.AIR: return (1, 0, 0, 0, 1 if b.plain_air else 0)
    if cls in (K.WATER, K.WPLANT): return (0, 0, 0, 1, 0)
    if cls == K.VOID or name in _NO_SURFACE: return (0, 0, 0, 0, 0)
    if cls == K.NOCOLL: return (1, 0, 0, 0, 0)
    if cls == K.PART:
        if name in _PASS_PART_NAMES or name.endswith(_PASS_PART_SUFFIX) or name.startswith('potted_'):
            return (1, 0, 0, 0, 0)
        if name.endswith('_slab'):
            t = parse_state(b.state)[1].get('type', 'bottom')
            return (0, 1 if t == 'bottom' else 2, 0, 0, 0)
        if name.endswith('_stairs'):
            h = parse_state(b.state)[1].get('half', 'bottom')
            return (0, 4 if h == 'bottom' else 2, 0, 0, 0)
        if name.endswith('_bed'): return (0, 1, 0, 0, 0)
        if name.endswith(('_fence', '_wall')): return (0, 3, 0, 0, 0)
    return (0, 2, 0, 0, 0)       # full cubes, panes, bars, chests, anvils, ...


def can(ha, ta, fa, hb, tb, fb):
    """Is the move A -> B possible (see module docstring)?"""
    if tb < (ha + 1) // 2 + 1: return False
    dh = hb - ha
    if dh <= 1: return dh >= -6
    if dh == 2:
        return bool((fa & (CLIMB | WET | STAIR)) or (fb & STAIR) or ta >= (ha + 1) // 2 + 2)
    return False


# ---------------------------------------------------------------- seeds
def default_seeds():
    """Seed areas: dicts {name, x, z, r, hmin, hmax}: a disc around (x, z), node heights in half blocks."""
    out = []
    lo, hi = 2 * planner.WALK_Y - 2, 2 * planner.WALK_Y + 2
    for p in planner.plazas():
        if p['name'] in ('Tower Plaza', 'Fountain Plaza'):
            out.append({'name': p['name'], 'x': p['x'], 'z': p['z'], 'r': p['r'], 'hmin': lo, 'hmax': hi})
    for a in planner.airlocks():
        out.append({'name': f'Airlock hall {int(a["plan"]):03d}', 'x': a['x'], 'z': a['z'], 'r': 8,
                    'hmin': 2, 'hmax': hi})
    return out


def _seed_sig(seeds):
    return tuple((s['name'], round(s['x'], 2), round(s['z'], 2), s['r'], s['hmin'], s['hmax']) for s in seeds)


# ---------------------------------------------------------------- per-tile graph
class Raw:
    """Node arrays of one tile, kept in-process (never pickled) for the accessibility check."""
    __slots__ = ('x1', 'z1', 'sx', 'y1', 'C', 'H', 'T', 'F', 'colnodes', 'ld', 'lw')


def _deck_rule(vol):
    """True: every column of the tile has a deck; False: none; None: ask planner.has_deck per column."""
    x1, x2, z1, z2 = vol.x1, vol.x2, vol.z1, vol.z2
    rmin = math.hypot(min(max(0, x1), x2), min(max(0, z1), z2))
    rmax = math.hypot(max(abs(x1), abs(x2)), max(abs(z1), abs(z2)))
    if rmax < planner.INNER_R: return True
    if rmin >= planner.PERIM_R0 and rmax < planner.PERIM_R1: return True
    if rmin >= planner.PERIM_R1: return False
    return None


def tile_graph(ctx, vol, features, seeds=None):
    """Walk graph of a built tile: (data, raw).  `data` is the picklable per-tile payload; cached per volume."""
    seeds = default_seeds() if seeds is None else seeds
    t = tinfo(ctx, vol)
    sig = _seed_sig(seeds)
    c = getattr(t, '_walk', None)
    if c is not None and c[0] == sig: return c[1], c[2]
    r = _tile_graph(ctx, vol, t, features, seeds)
    t._walk = (sig, r[0], r[1])
    return r


_KIND_H = ((2, 2, 0), (1, 1, 0), (4, 1, STAIR), (3, 3, 0))      # (support kind, h = 2y + add, flag)


def _tile_graph(ctx, vol, t, features, seeds):
    sx, sz, st, n, y1 = vol.sx, vol.sz, t.stride, t.n, vol.y1
    full = (1 << (8 * n)) - 1
    prop = lambda i: (lambda b: block_props(b)[i])
    PASS = t.cellmap('w_pass', prop(0))
    Pi = toint(PASS)
    s8 = 8 * st
    p1, p2, p3 = Pi >> s8, Pi >> (2 * s8), Pi >> (3 * s8)
    INS = toint(t.inside)
    PA = toint(t.cellmap('w_plain', prop(4)))
    ok = (INS >> s8) | ((PA >> s8) & (PA >> (2 * s8)))
    keep = full if y1 >= 0 else full ^ ((1 << (8 * st * (-y1))) - 1)
    kind = t.cellmap('w_kind', prop(1))
    base = ok & keep & p1 & p2
    CLm = toint(t.cellmap('w_climb', prop(2))) & p1 & INS & keep
    Wm = toint(t.cellmap('w_water', prop(3))) & p1 & p2 & (INS >> s8) & keep

    C, H, T, F = [], [], [], []
    byk, colnodes = {}, defaultdict(list)

    def add(i_sup, h2, fl):
        col = i_sup % st
        key = (col, h2)
        a = byk.get(key)
        if a is not None:
            F[a] |= fl
            return
        seg = PASS[i_sup + st: i_sup + st * (RUN_CAP + 1): st]
        run = seg.find(0)
        if run < 0: run = len(seg)
        a = len(C)
        byk[key] = a
        C.append(col); H.append(h2); T.append(y1 + i_sup // st + run); F.append(fl)
        colnodes[col].append(a)

    for kd, add_h, fl in _KIND_H:
        m = toint(kind.translate(bytes(1 if i == kd else 0 for i in range(256)))) & base
        if kd == 3: m &= p3
        b = m.to_bytes(n, 'little')
        k = b.find(1)
        while k >= 0:
            add(k, 2 * (y1 + k // st) + add_h, fl)
            k = b.find(1, k + 1)
    b = CLm.to_bytes(n, 'little')
    k = b.find(1)
    while k >= 0:
        add(k - st, 2 * (y1 + k // st), CLIMB)       # virtual support one cell below the climbable feet cell
        k = b.find(1, k + 1)
    b = Wm.to_bytes(n, 'little')
    k = b.find(1)
    while k >= 0:
        add(k, 2 * (y1 + k // st) + 1, WET)
        k = b.find(1, k + 1)
    N = len(C)

    # ---- union-find over mutual edges
    pd, pw = list(range(N)), list(range(N))

    def find(p, a):
        while p[a] != a:
            p[a] = p[p[a]]; a = p[a]
        return a

    def union(p, a, b):
        a, b = find(p, a), find(p, b)
        if a != b: p[a] = b

    one_d, one_w = [], []                            # (a, b): one-way a -> b
    for a in range(N):
        c0, ha, ta, fa = C[a], H[a], T[a], F[a]
        if fa & CLIMB:
            b = byk.get((c0, ha + 2))
            if b is not None and F[b] & CLIMB: union(pd, a, b); union(pw, a, b)
        for c2 in (c0 + 1 if c0 % sx < sx - 1 else -1, c0 + sx if c0 < st - sx else -1):
            lst = colnodes.get(c2) if c2 >= 0 else None
            if not lst: continue
            for b in lst:
                hb, fb = H[b], F[b]
                wet = (fa | fb) & WET
                if hb == ha:
                    if wet: union(pw, a, b)
                    else: union(pd, a, b); union(pw, a, b)
                    continue
                tb = T[b]
                ab = can(ha, ta, fa, hb, tb, fb)
                ba = can(hb, tb, fb, ha, ta, fa)
                if ab and ba:
                    if wet: union(pw, a, b)
                    else: union(pd, a, b); union(pw, a, b)
                elif ab or ba:
                    s, d = (a, b) if ab else (b, a)
                    one_w.append((s, d))
                    if not wet: one_d.append((s, d))

    # ---- labels
    ld, lw = [-1] * N, [0] * N
    rd, rw = {}, {}
    lw_of_ld = []
    for a in range(N):
        r = find(pw, a)
        w = rw.get(r)
        if w is None: w = rw[r] = len(rw)
        lw[a] = w
        if not F[a] & WET:
            r = find(pd, a)
            d = rd.get(r)
            if d is None:
                d = rd[r] = len(rd); lw_of_ld.append(w)
            ld[a] = d
    nd = len(rd)

    x1, z1 = vol.x1, vol.z1
    size, rep = [0] * nd, [None] * nd
    rel, relrep, feats = [0] * nd, [None] * nd, [None] * nd
    for a in range(N):
        d = ld[a]
        if d < 0: continue
        size[d] += 1
        if rep[d] is None:
            c0 = C[a]
            rep[d] = (x1 + c0 % sx, H[a] // 2, z1 + c0 // sx)

    # relevance: a node above deck level in a column with a deck, or inside a layer >= 40 feature bbox
    rule = _deck_rule(vol)
    deckcache = {}

    def hasdeck(c0):
        if rule is not None: return rule
        v = deckcache.get(c0)
        if v is None: v = deckcache[c0] = planner.has_deck(x1 + c0 % sx, z1 + c0 // sx)
        return v
    relnode = [None] * N                             # True or the feature name
    if rule is not False:
        dh0 = 2 * planner.DECK_TOP + 1
        for a in range(N):
            if ld[a] >= 0 and H[a] >= dh0 and hasdeck(C[a]): relnode[a] = True
    for f in features:
        if f.layer < 40 or f.meta.get('islands_ok'): continue
        bx1, by1, bz1, bx2, by2, bz2 = f.bbox
        for zz in range(max(bz1, z1), min(bz2, vol.z2) + 1):
            for xx in range(max(bx1, x1), min(bx2, vol.x2) + 1):
                for a in colnodes.get((zz - z1) * sx + (xx - x1), ()):
                    if ld[a] >= 0 and by1 <= H[a] // 2 <= by2: relnode[a] = f.name
    for a in range(N):
        v, d = relnode[a], ld[a]
        if v is None or d < 0: continue
        rel[d] += 1
        if relrep[d] is None:
            c0 = C[a]
            relrep[d] = (x1 + c0 % sx, H[a] // 2, z1 + c0 // sx)
        if v is not True:
            if feats[d] is None: feats[d] = {}
            feats[d][v] = feats[d].get(v, 0) + 1

    # ---- one-way edges in label space
    od = sorted({(ld[a], ld[b]) for a, b in one_d if ld[a] != ld[b]})
    ow = sorted({(lw[a], lw[b]) for a, b in one_w if lw[a] != lw[b]})

    # ---- border stubs
    stubs = {'W': [], 'E': [], 'N': [], 'S': []}
    for a in range(N):
        c0 = C[a]
        xl, zl = c0 % sx, c0 // sx
        if xl == 0 or xl == sx - 1 or zl == 0 or zl == sz - 1:
            s = (H[a], T[a], F[a], ld[a], lw[a])
            if xl == 0: stubs['W'].append((z1 + zl,) + s)
            if xl == sx - 1: stubs['E'].append((z1 + zl,) + s)
            if zl == 0: stubs['N'].append((x1 + xl,) + s)
            if zl == sz - 1: stubs['S'].append((x1 + xl,) + s)

    # ---- seeds and districts
    seed_hits = {}
    for s in seeds:
        r = s['r']
        if s['x'] + r < x1 or s['x'] - r > vol.x2 or s['z'] + r < z1 or s['z'] - r > vol.z2: continue
        hits, r2 = set(), r * r
        for a in range(N):
            d = ld[a]
            if d < 0 or not s['hmin'] <= H[a] <= s['hmax']: continue
            c0 = C[a]
            dx, dz = x1 + c0 % sx - s['x'], z1 + c0 // sx - s['z']
            if dx * dx + dz * dz <= r2: hits.add((d, lw[a]))
        seed_hits[s['name']] = sorted(hits)
    deck_d, dist_labels = set(), defaultdict(set)
    if rule is not False:
        for zz in range((z1 + 3) // 4 * 4, vol.z2 + 1, 4):
            for xx in range((x1 + 3) // 4 * 4, vol.x2 + 1, 4):
                if rule is None and not planner.has_deck(xx, zz): continue
                dname = planner.district_at(xx, zz)
                if dname is None: continue
                deck_d.add(dname)
                for a in colnodes.get((zz - z1) * sx + (xx - x1), ()):
                    if ld[a] >= 0 and DECK_BAND[0] <= H[a] <= DECK_BAND[1]: dist_labels[dname].add(ld[a])

    data = {'nodes': N, 'nd': nd, 'nw': len(rw), 'lw_of_ld': lw_of_ld, 'size': size, 'rep': rep, 'rel': rel,
            'relrep': relrep, 'feats': feats, 'od': od, 'ow': ow, 'stubs': stubs, 'seeds': seed_hits,
            'deck_d': sorted(deck_d), 'dist': {k: sorted(v) for k, v in dist_labels.items()}}
    raw = Raw()
    raw.x1, raw.z1, raw.sx, raw.y1 = x1, z1, sx, y1
    raw.C, raw.H, raw.T, raw.F, raw.colnodes, raw.ld, raw.lw = C, H, T, F, colnodes, ld, lw
    return data, raw


def find_node(raw, x, y_feet, z, tol=1):
    """Dry node of column (x, z) whose standing height best matches feet cell y_feet (h in 2y-tol..2y+tol)."""
    c0 = (z - raw.z1) * raw.sx + (x - raw.x1)
    best, bd = None, 99
    for a in raw.colnodes.get(c0, ()):
        if raw.F[a] & WET: continue
        d = abs(raw.H[a] - 2 * y_feet)
        if d <= tol and d < bd: best, bd = a, d
    return best


# ---------------------------------------------------------------- whole-city solve
class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]; a = p[a]
        return a

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b: self.p[a] = b


class Solution:
    """All tile graphs stitched.  Labels are addressed as (tile (tx, tz), label)."""

    def __init__(self, graphs):
        self.graphs = graphs
        self.off_d, self.off_w = {}, {}
        nd = nw = 0
        wmap = []
        for k in sorted(graphs):
            g = graphs[k]
            self.off_d[k], self.off_w[k] = nd, nw
            wmap.extend(nw + w for w in g['lw_of_ld'])
            nd += g['nd']; nw += g['nw']
        self.wmap = wmap                      # global dry label -> global wet label
        self.ufd, self.ufw = UF(nd), UF(nw)
        self.adj_d, self.adj_w = defaultdict(set), defaultdict(set)
        self._stitch()
        self._stats()
        self.reach_d, self.reach_w = set(), set()

    def _pair(self, ka, A, kb, B):
        if not A or not B: return
        uw, ud = self.ufw, self.ufd
        oda, owa, odb, owb = self.off_d[ka], self.off_w[ka], self.off_d[kb], self.off_w[kb]
        byc = defaultdict(list)
        for s in B: byc[s[0]].append(s)
        for a in A:
            for b in byc.get(a[0], ()):
                _, ha, ta, fa, lda, lwa = a
                _, hb, tb, fb, ldb, lwb = b
                ab = can(ha, ta, fa, hb, tb, fb)
                ba = can(hb, tb, fb, ha, ta, fa)
                if not (ab or ba): continue
                wet = (fa | fb) & WET
                if ab and ba:
                    uw.union(owa + lwa, owb + lwb)
                    if not wet: ud.union(oda + lda, odb + ldb)
                else:
                    if ab: sw, dw, sd, dd = owa + lwa, owb + lwb, oda + lda, odb + ldb
                    else: sw, dw, sd, dd = owb + lwb, owa + lwa, odb + ldb, oda + lda
                    self.adj_w[sw].add(dw)
                    if not wet: self.adj_d[sd].add(dd)

    def _stitch(self):
        g = self.graphs
        for k, d in g.items():
            tx, tz = k
            od, ow_ = self.off_d[k], self.off_w[k]
            for a, b in d['od']: self.adj_d[od + a].add(od + b)
            for a, b in d['ow']: self.adj_w[ow_ + a].add(ow_ + b)
            r = g.get((tx + 1, tz))
            if r is not None: self._pair(k, d['stubs']['E'], (tx + 1, tz), r['stubs']['W'])
            r = g.get((tx, tz + 1))
            if r is not None: self._pair(k, d['stubs']['S'], (tx, tz + 1), r['stubs']['N'])

    def _stats(self):
        """comp[dry root] = [nodes, relevant nodes, rep, relevant rep, {feature: nodes}]."""
        comp = {}
        for k, g in self.graphs.items():
            o = self.off_d[k]
            for d in range(g['nd']):
                r = self.ufd.find(o + d)
                c = comp.get(r)
                if c is None: c = comp[r] = [0, 0, g['rep'][d], None, {}]
                c[0] += g['size'][d]; c[1] += g['rel'][d]
                if c[3] is None and g['relrep'][d] is not None: c[3] = g['relrep'][d]
                f = g['feats'][d]
                if f:
                    for nme, v in f.items(): c[4][nme] = c[4].get(nme, 0) + v
        self.comp = comp

    # ---- queries
    def root_d(self, k, ld):
        return self.ufd.find(self.off_d[k] + ld)

    def wet_root_of_dry(self, k, ld):
        return self.ufw.find(self.wmap[self.off_d[k] + ld])

    def _bfs(self, starts, adj, find):
        seen = set(starts)
        norm = defaultdict(set)
        for a, bs in adj.items():
            ra = find(a)
            for b in bs:
                rb = find(b)
                if rb != ra: norm[ra].add(rb)
        q = list(seen)
        while q:
            a = q.pop()
            for b in norm.get(a, ()):
                if b not in seen: seen.add(b); q.append(b)
        return seen

    def compute_reach(self, start_dry_roots):
        """Directed reach from the given dry component roots (dry edges), then with swim edges added."""
        self.reach_d = self._bfs(start_dry_roots, self.adj_d, self.ufd.find)
        sw = {self.ufw.find(self.wmap[r]) for r in self.reach_d}
        self.reach_w = self._bfs(sw, self.adj_w, self.ufw.find)

    def dry_reached(self, k, ld):
        return self.root_d(k, ld) in self.reach_d

    def wet_reached(self, k, ld):
        return self.wet_root_of_dry(k, ld) in self.reach_w


def graphs_from_partials(partials):
    """Partials of the connectivity / accessibility checks -> {(tx, tz): graph data}."""
    out = {}
    for p in partials:
        out[parse_key(p.tile)] = p.data['graph'] if 'graph' in p.data else p.data
    return out
