"""connectivity and accessibility checks on the walk graph (model and rules: see walk.py).

connectivity  Seeds (Tower Plaza first, then the Fountain Plaza and the six airlock halls; planner.plazas()/airlocks())
              are the starting areas.  The MAIN component is everything reachable (directed, dry edges) from the Tower
              Plaza seed.  Errors: a seed with no standable node or outside the main component; a district with deck
              cells and no node of the main component; every unreachable component of more than 50 nodes that lies on a
              deck or inside a layer >= 40 feature ("island").  A component that is reachable only by swimming is a
              warning instead.  Everything else unreachable (small components, other places) is info.
accessibility Features of layer >= 40 that declare meta['entrances'] / meta['interior'] (see validate/__init__.py).

Both are per-tile (graph extraction) + whole-city reduce (stitching + union-find + reachability).
Seeds can be replaced for tests with validate(..., options={'walk_seeds': [{name, x, z, r, hmin, hmax}, ...]});
the first seed is the primary one (the per-district check is skipped with custom seeds).  Only seeds whose centre tile was validated are evaluated.
"""
import math

from . import walk as W
from .core import Issue, Partial, register_check, CHECKS, OPTIONS, parse_key
from .. import planner

ISLAND_MIN = 50            # components of up to this many nodes are 'info'
MAX_ISLANDS = 100          # reported individually (the rest is summarised)
_AN = {}


def get_seeds():
    return OPTIONS.get('walk_seeds') or W.default_seeds()


# ---------------------------------------------------------------- per-tile checks
@register_check('connectivity')
def connectivity(ctx, tile_key, vol, features):
    """Walk graph of the tile (nodes, labelled mutual-edge components, border stubs); stitched in reduce."""
    data, _ = W.tile_graph(ctx, vol, features, get_seeds())
    return [Partial('connectivity', tile_key, data)]


@register_check('accessibility')
def accessibility(ctx, tile_key, vol, features):
    """Entrance and interior nodes of the features that declare meta['entrances'] / meta['interior']."""
    data, raw = W.tile_graph(ctx, vol, features, get_seeds())
    recs = []
    for f in features:
        m = f.meta
        ent, inter = m.get('entrances'), m.get('interior')
        if f.layer < 40 or not (ent or inter): continue
        rec = {'name': f.name, 'ent': [], 'int': {}, 'int_total': 0}
        skip = set()
        for i, (x, y, z) in enumerate(ent or ()):
            if not (vol.x1 <= x <= vol.x2 and vol.z1 <= z <= vol.z2): continue
            a = W.find_node(raw, x, y, z)
            if a is not None: skip.add(a)
            rec['ent'].append((i, (x, y, z), None if a is None else raw.ld[a]))
        bx1, by1, bz1, bx2, by2, bz2 = m.get('interior_box') or f.bbox
        roof = m.get('roof_y', f.bbox[4])
        for zz in range(max(bz1, vol.z1), min(bz2, vol.z2) + 1):
            for xx in range(max(bx1, vol.x1), min(bx2, vol.x2) + 1):
                for a in raw.colnodes.get((zz - raw.z1) * raw.sx + (xx - raw.x1), ()):
                    h = raw.H[a]
                    if raw.ld[a] < 0 or a in skip or not by1 <= h // 2 <= by2 or (h + 1) // 2 + 1 >= roof: continue
                    rec['int'][raw.ld[a]] = rec['int'].get(raw.ld[a], 0) + 1
                    rec['int_total'] += 1
        recs.append(rec)
    return [Partial('accessibility', tile_key, {'graph': data, 'feats': recs})]


# ---------------------------------------------------------------- whole-city analysis (shared)
class Analysis:
    pass


def analyze(partials, ctx):
    graphs = W.graphs_from_partials(partials)
    seeds = get_seeds()
    key = (tuple(sorted((k, id(g)) for k, g in graphs.items())), W._seed_sig(seeds), ctx.tile)
    if _AN.get('key') == key: return _AN['an']
    an = Analysis()
    an.graphs, an.seeds = graphs, seeds
    an.sol = sol = W.Solution(graphs)
    an.state = {}
    for s in seeds:
        c = (math.floor(s['x'] / ctx.tile), math.floor(s['z'] / ctx.tile))
        labels = [(k, ld) for k, g in graphs.items() for ld, _ in g['seeds'].get(s['name'], ())]
        an.state[s['name']] = {'evaluated': c in graphs, 'labels': labels, 'seed': s}
    an.primary = seeds[0]['name'] if seeds else None
    an.mode = 'primary'
    starts = set()
    if an.primary and an.state[an.primary]['evaluated']:
        starts = {sol.root_d(k, ld) for k, ld in an.state[an.primary]['labels']}
    elif any(v['evaluated'] for v in an.state.values()):
        an.mode = 'other seeds'
        starts = {sol.root_d(k, ld) for v in an.state.values() if v['evaluated'] for k, ld in v['labels']}
    elif sol.comp:
        an.mode = 'largest component'
        starts = {max(sol.comp, key=lambda r: sol.comp[r][0])}
    an.starts = starts
    sol.compute_reach(starts)
    an.main_size = max((sol.comp[r][0] for r in starts), default=0)
    an.reach_nodes = sum(sol.comp[r][0] for r in sol.reach_d)
    _AN.clear()
    _AN.update(key=key, an=an)
    return an


def _where(c):
    if c[4]:
        return f' in feature {max(c[4], key=c[4].get)}'
    rep = c[3] or c[2]
    d = planner.district_at(rep[0], rep[2])
    return f' in district {d} ({planner.DISTRICTS[d][0]})' if d else ''


# ---------------------------------------------------------------- connectivity reduce
def connectivity_reduce(partials, ctx, features=None):
    if not partials:
        return [Issue('connectivity', 'info', 'no tiles: connectivity not evaluated', None, 0)]
    an = analyze(partials, ctx)
    sol, out, pr = an.sol, [], an.primary
    total = sum(c[0] for c in sol.comp.values())
    note = '' if an.mode == 'primary' else f' [no {pr} seed in the validated tiles: started from {an.mode}]'
    out.append(Issue('connectivity', 'info',
                     f'main walk component: {an.main_size} standable nodes in the largest component containing the '
                     f'{pr}; {an.reach_nodes} reachable counting one-way drops; {total} nodes in {len(sol.comp)} '
                     f'components, {len(an.graphs)} tiles{note}', None, an.main_size))
    skipped = [n for n, v in an.state.items() if not v['evaluated']]
    if skipped:
        out.append(Issue('connectivity', 'info', f'{len(skipped)} seed(s) outside the validated tiles, not checked: '
                         + ', '.join(skipped), None, len(skipped)))
    # ---- seeds
    for name, v in an.state.items():
        if not v['evaluated']: continue
        s = v['seed']
        pos = (int(s['x']), planner.WALK_Y, int(s['z']))
        if not v['labels']:
            out.append(Issue('connectivity', 'error', f'seed {name}: no standable node in the seed area', pos, 1))
        elif not any(sol.dry_reached(k, ld) for k, ld in v['labels']):
            if any(sol.wet_reached(k, ld) for k, ld in v['labels']):
                out.append(Issue('connectivity', 'warn', f'seed {name}: reachable from the {pr} only by swimming',
                                 pos, 1))
            else:
                out.append(Issue('connectivity', 'error', f'seed {name}: not connected to the {pr} (main component)',
                                 pos, 1))
    # ---- districts
    if an.starts and not OPTIONS.get('walk_seeds'):        # custom seeds (tests) skip the district check
        decks, labels = {}, {}
        for k, g in an.graphs.items():
            for d in g['deck_d']:
                decks.setdefault(d, k)
                for ld in g['dist'].get(d, ()): labels.setdefault(d, []).append((k, ld))
        for d in sorted(decks):
            if d not in planner.DISTRICTS: continue
            labs = labels.get(d, [])
            t = ctx.tile
            pos = (decks[d][0] * t + t // 2, planner.WALK_Y, decks[d][1] * t + t // 2)
            nm = f'district {d} ({planner.DISTRICTS[d][0]})'
            if any(sol.dry_reached(k, ld) for k, ld in labs): continue
            if any(sol.wet_reached(k, ld) for k, ld in labs):
                out.append(Issue('connectivity', 'warn', f'{nm}: reachable only by swimming', pos, 1))
            elif not labs:
                out.append(Issue('connectivity', 'error', f'{nm} has deck cells but no standable deck node', pos, 1))
            else:
                out.append(Issue('connectivity', 'error', f'{nm} has deck cells but is unreachable from the {pr}',
                                 pos, len(labs)))
    # ---- islands and the rest
    big, small_n, small_nodes, small_pos = [], 0, 0, []
    for r, c in sol.comp.items():
        if r in sol.reach_d: continue
        if c[0] > ISLAND_MIN and c[1] > 0: big.append((c[0], r))
        else:
            small_n += 1; small_nodes += c[0]
            if len(small_pos) < 20: small_pos.append(c[3] or c[2])
    big.sort(key=lambda t: (-t[0], t[1]))
    for n, (size, r) in enumerate(big):
        c = sol.comp[r]
        if n >= MAX_ISLANDS:
            out.append(Issue('connectivity', 'error', f'... and {len(big) - n} more unreachable islands',
                             None, len(big) - n))
            break
        pos = c[3] or c[2]
        swim = sol.ufw.find(sol.wmap[r]) in sol.reach_w
        out.append(Issue('connectivity', 'warn' if swim else 'error',
                         f'walkable island of {size} nodes{_where(c)} is '
                         + (f'reachable from the {pr} only by swimming' if swim else f'unreachable from the {pr}'),
                         pos, size, [pos]))
    if small_n:
        out.append(Issue('connectivity', 'info', f'{small_n} small or off-deck unreachable walkable components '
                         f'({small_nodes} nodes in total, each <= {ISLAND_MIN} nodes or not on a deck/in a building)',
                         small_pos[0], small_n, small_pos))
    return out


CHECKS['connectivity'].reduce = connectivity_reduce


# ---------------------------------------------------------------- accessibility reduce
def accessibility_reduce(partials, ctx, features=None):
    out = []
    an = analyze(partials, ctx)
    sol, pr = an.sol, an.primary
    merged = {}                                  # feature name -> [(tile, rec)]
    for p in partials:
        k = parse_key(p.tile)
        for rec in p.data['feats']: merged.setdefault(rec['name'], []).append((k, rec))
    checked = 0
    for f in features or ():
        m = f.meta
        ent, inter = m.get('entrances'), m.get('interior')
        if f.layer < 40 or not (ent or inter): continue
        checked += 1
        recs = merged.get(f.name, [])
        by_i = {i: (k, pos, ld) for k, rec in recs for i, pos, ld in rec['ent']}
        reach_ent = 0
        for i, pos in enumerate(ent or ()):
            if i not in by_i: continue            # entrance tile not validated
            k, pos, ld = by_i[i]
            if ld is None:
                out.append(Issue('accessibility', 'error', f'{f.name}: entrance {i} has no standable node', pos, 1))
            elif sol.dry_reached(k, ld):
                reach_ent += 1
            elif sol.wet_reached(k, ld):
                out.append(Issue('accessibility', 'warn',
                                 f'{f.name}: entrance {i} is reachable only by swimming', pos, 1))
            else:
                out.append(Issue('accessibility', 'error',
                                 f'{f.name}: entrance {i} is not in the main component (from the {pr})', pos, 1))
        tot = reach = 0
        for k, rec in recs:
            tot += rec['int_total']
            reach += sum(n for ld, n in rec['int'].items() if sol.dry_reached(k, ld))
        centre = ((f.bbox[0] + f.bbox[3]) // 2, f.bbox[1], (f.bbox[2] + f.bbox[5]) // 2)
        if ent and reach_ent and not reach:
            out.append(Issue('accessibility', 'error',
                             f'{f.name}: no interior node reachable via an entrance'
                             + ('' if tot else ' (the feature has no interior standable nodes)'), centre, 1))
        if inter and tot == 0:
            out.append(Issue('accessibility', 'warn', f'{f.name}: declares an interior but has no standable interior '
                             f'nodes', centre, 1))
        elif inter and reach < 0.8 * tot:
            out.append(Issue('accessibility', 'warn',
                             f'{f.name}: only {100 * reach // tot}% of {tot} interior nodes are reachable (need 80%)',
                             centre, tot - reach))
    out.append(Issue('accessibility', 'info', f'{checked} feature(s) with declared entrances/interior checked',
                     None, checked))
    return out


CHECKS['accessibility'].reduce = accessibility_reduce
