"""overlaps (feature-only, reduce step).  connectivity / accessibility live in c_connectivity.py."""
from .core import Issue, register_check, CHECKS


# ---------------------------------------------------------------- overlaps
@register_check('overlaps')
def overlaps(ctx, tile_key, vol, features):
    """Feature-only check; everything happens in reduce (see overlaps_reduce)."""
    return []


def _allowed(a, b):
    for f, g in ((a, b), (b, a)):
        v = f.meta.get('allow_overlap')
        if v is True: return True
        if v and not isinstance(v, bool) and (g.name in v): return True
    return False


def overlaps_reduce(partials, ctx, features=None):
    """Features of layer 40 and 50 whose bboxes overlap in x/z are an error, unless meta['allow_overlap'] is True
    (overlap with anything allowed) or a collection of feature names (overlap with those allowed) on either feature.
    Only the features intersecting the validated tiles are considered. Reported once per pair (max 200)."""
    fs = sorted((f for f in (features or ()) if f.layer in (40, 50)), key=lambda f: (f.bbox[0], f.name))
    out, active, npairs = [], [], 0
    for f in fs:
        x1 = f.bbox[0]
        active = [g for g in active if g.bbox[3] >= x1]
        for g in active:
            if g.bbox[5] < f.bbox[2] or g.bbox[2] > f.bbox[5]: continue
            if _allowed(f, g): continue
            npairs += 1
            if npairs <= 200:
                ox1, ox2 = max(f.bbox[0], g.bbox[0]), min(f.bbox[3], g.bbox[3])
                oz1, oz2 = max(f.bbox[2], g.bbox[2]), min(f.bbox[5], g.bbox[5])
                area = (ox2 - ox1 + 1) * (oz2 - oz1 + 1)
                out.append(Issue('overlaps', 'error',
                                 f'building footprints overlap: {g.name} / {f.name} ({area} columns)',
                                 ((ox1 + ox2) // 2, max(f.bbox[1], g.bbox[1]), (oz1 + oz2) // 2), area))
        active.append(f)
    if npairs > 200:
        out.append(Issue('overlaps', 'error', f'... and {npairs - 200} more overlapping pairs', None, npairs - 200))
    return out


CHECKS['overlaps'].reduce = overlaps_reduce
