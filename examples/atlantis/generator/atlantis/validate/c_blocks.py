"""Checks on what is in the cells: block_states, water_inside, mask."""
from . import classify as K
from .core import Issue, register_check, tinfo, toint, tobytes, set_positions


def water_ok_boxes(features, vol=None):
    """Boxes where decorative water inside the dome is allowed: features with meta['water_ok'].
    True -> the feature bbox; a list of 6-tuples -> those boxes."""
    out = []
    for f in features:
        w = f.meta.get('water_ok')
        if not w: continue
        if w is True: out.append(tuple(f.bbox))
        else: out.extend(tuple(b) for b in w)
    return out


@register_check('block_states')
def block_states(ctx, tile_key, vol, features):
    """Every block state used in the tile is valid for 1.21.1: block exists, every property exists and has an allowed
    value, syntax is well formed. Omitted properties are fine (vanilla fills defaults). Severity: error."""
    out = []
    used = set(vol.cells)
    used.discard(0)
    for pid in sorted(used):
        state = vol.palette[pid]
        probs = K.state_problems(state)
        if not probs: continue
        i = vol.cells.index(pid)
        t = tinfo(ctx, vol)
        out.append(Issue('block_states', 'error', 'invalid block state: ' + '; '.join(probs), t.xyz(i),
                         vol.cells.count(pid), [t.xyz(i)]))
    return out


@register_check('water_inside')
def water_inside(ctx, tile_key, vol, features):
    """`water` cells where ctx.inside_dome is true (must be 0). Cells in a feature bbox with meta['water_ok'] are
    exempt (decorative lagoons). Waterlogged blocks inside the dome are reported by `leaks`. Severity: error."""
    t = tinfo(ctx, vol)
    wat = toint(t.cellmap('water', lambda b: 1 if b.cls == K.WATER else 0))
    if not wat: return []
    hit = wat & toint(t.inside)
    ok = water_ok_boxes(features)
    if ok and hit: hit &= ~toint(t.box_mask(ok))
    if not hit: return []
    pos, cnt = set_positions(tobytes(hit, t.n))
    ps = [t.xyz(i) for i in pos]
    return [Issue('water_inside', 'error', 'water inside the dome', ps[0], cnt, ps)]


@register_check('mask')
def mask(ctx, tile_key, vol, features):
    """Outside ctx.in_area every cell must be structure_void; inside it none may be (the exporter skips void cells).
    Severity: error."""
    void = vol.index.get('structure_void')
    cells, sx, sy, sz = vol.cells, vol.sx, vol.sy, vol.sz
    step = sx * sz
    outs, ins = [], []
    for z in range(vol.z1, vol.z2 + 1):
        for x in range(vol.x1, vol.x2 + 1):
            base = (z - vol.z1) * sx + (x - vol.x1)
            inside = ctx.in_area(x, z)
            if void is None:
                if not inside: outs.append((x, z, base, sy))
                continue
            col = cells[base::step]
            nv = col.count(void)
            if inside:
                if nv: ins.append((x, z, base, nv))
            elif nv != sy: outs.append((x, z, base, sy - nv))
    res = []
    for lst, msg, want_void in ((outs, 'non-void cell outside ctx.in_area', False),
                                (ins, 'structure_void cell inside ctx.in_area', True)):
        if not lst: continue
        pos = []
        for x, z, base, _ in lst[:20]:
            col = cells[base::step]
            y = next((k for k, c in enumerate(col) if (c == void) == want_void), 0)
            pos.append((x, vol.y1 + y, z))
        res.append(Issue('mask', 'error', msg, pos[0], sum(c for *_, c in lst), pos))
    return res
