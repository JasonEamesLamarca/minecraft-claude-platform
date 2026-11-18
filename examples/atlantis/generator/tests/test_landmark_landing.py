#!/usr/bin/env python3
"""District G landing landmarks (landmark_landing.py) tests, plain asserts. Run: python3 tests/test_landmark_landing.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, planner as PL, blocks as B, landmark_landing as L, roads, export_tiles as E
from atlantis.orchestrate import load_generators
from atlantis.volume import Volume

ctx = config.load()
FEATS = load_generators()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
EMIT = {'sea_lantern': 15, 'end_rod': 14}
_VOLS = {}


def vol_for(f):
    if f.name not in _VOLS:
        bb = (f.bbox[0] - 14, 0, f.bbox[2] - 14, f.bbox[3] + 14, 40, f.bbox[5] + 14)
        _VOLS[f.name] = Volume(*bb)
        f.build(_VOLS[f.name], ctx)
    return _VOLS[f.name]


def is_air(b):
    return b is None or b.split('[')[0] in ('air', 'cave_air', 'light')


def cells(f):
    """Set (x, y, z) -> block of the feature's own blocks."""
    return {(x, y, z): b for x, y, z, b in vol_for(f).items()}


def test_registered():
    names = [f.name for f in L.FEATURES]
    assert len(set(names)) == len(names)
    assert all(any(g is f for g in FEATS) for f in L.FEATURES)
    assert all(f.layer == 50 for f in L.FEATURES)
    g_elev = [f for f in L.FEATURES if f.meta.get('elevated')]
    pads = [f for f in L.FEATURES if f.meta.get('kind') == 'landing_pad']
    assert len(g_elev) == 4 and len(pads) >= 6 + 5, (len(g_elev), len(pads))
    assert sum(1 for f in pads if f.meta.get('tip')) >= 7           # 2 G tip pads + 5 other piers
    assert {f.name for f in L.FEATURES if f.meta.get('kind') in ('hangar', 'control_tower')} == \
        {'landing_g_skiff_hangar', 'landing_g_control_tower'}
    for a in PL.PIER_ANGLES:
        assert any(f.meta.get('tip') and f.meta['district'] == PL._PIER_BY_ANGLE[a] for f in pads), a


def test_bbox_disjoint():
    fs = L.FEATURES
    for i, a in enumerate(fs):
        for b in fs[i + 1:]:
            assert a.bbox[3] < b.bbox[0] or b.bbox[3] < a.bbox[0] or a.bbox[5] < b.bbox[2] or b.bbox[5] < a.bbox[2], \
                (a.name, b.name)
        assert a.bbox[1] >= 3


def test_districts_and_radii():
    for f in L.FEATURES:
        cs = cells(f)
        assert cs, f.name
        own = [(x, y, z) for (x, y, z) in cs if f.bbox[0] <= x <= f.bbox[3] and f.bbox[2] <= z <= f.bbox[5]]
        assert len(own) == len(cs), f.name                           # nothing drawn outside the bbox
        for (x, y, z) in cs:
            if f.name.startswith('landing_g_'):
                assert PL.district_at(x, z) == 'G', (f.name, x, y, z)
            else:
                a = int(f.name.split('_')[2])
                assert PL.in_pier(x, z, a) == a, (f.name, x, z)
                assert 495 <= math.hypot(x, z) <= 540, (f.name, x, z, math.hypot(x, z))
            assert y <= ctx.max_building_top(x, z), (f.name, x, y, z, ctx.max_building_top(x, z))


def test_height_cap_is_met_with_margin():
    for f in L.FEATURES:
        top = max(y for (x, y, z) in cells(f))
        cap = min(ctx.max_building_top(x, z) for x in (f.bbox[0], f.bbox[3]) for z in (f.bbox[2], f.bbox[5]))
        assert top <= cap, (f.name, top, cap)


def test_streets_clear():
    for f in L.FEATURES:
        for (x, y, z) in cells(f):
            if y <= PL.DECK_TOP: continue
            k = roads.street_kind(x, z)
            if k in ('avenue', 'ring', 'radial'):
                # only the stair/ramp feet may touch the avenue edge; never its centre 11-wide carriageway
                assert False, (f.name, x, y, z, k)
            if f.name.startswith('landing_g_'):
                assert abs(x) >= 8, (f.name, x, z)
        # floor-level cells (y 4) may only repaint plain deck, not roads, except the hangar/tower floors
        if f.name in ('landing_g_skiff_hangar', 'landing_g_control_tower'): continue
        for (x, y, z) in cells(f):
            if y == PL.DECK_TOP and roads.street_kind(x, z) in ('avenue', 'ring', 'radial', 'plaza'):
                assert False, (f.name, x, z)


def test_blocks_valid():
    bad = set()
    for f in L.FEATURES:
        for b in cells(f).values():
            name = b.split('[')[0]
            if name not in VALID or not B.is_valid_state(b): bad.add(b)
    assert not bad, sorted(bad)[:10]


def test_entrances_and_light():
    for f in L.FEATURES:
        v = vol_for(f)
        ents = f.meta['entrances']
        assert ents, f.name
        for (x, y, z) in ents:
            below = v.get(x, y - 1, z)
            assert below is not None and below.split('[')[0] not in ('air',), (f.name, x, y, z, below)
            assert is_air(v.get(x, y, z)) and is_air(v.get(x, y + 1, z)), (f.name, x, y, z)
        if f.meta.get('interior'):
            assert f.meta['interior_box'] and f.meta['roof_y']
        assert len([b for b in cells(f).values() if b.split('[')[0] in EMIT]) >= 8, f.name


def test_determinism():
    for f in (L.FEATURES[0], L.FEATURES[4], L.FEATURES[5]):
        bb = (f.bbox[0] - 2, 0, f.bbox[2] - 2, f.bbox[3] + 2, 40, f.bbox[5] + 2)
        a, b = Volume(*bb), Volume(*bb)
        f.build(a, ctx); f.build(b, ctx)
        assert list(a.items()) == list(b.items())


def test_tile_independent():
    """Building a feature in two halves equals one pass (clip-aware loops)."""
    f = [g for g in L.FEATURES if g.name == 'landing_g_skiff_hangar'][0]
    def run(bb):
        v = Volume(*bb); f.build(v, ctx); return {(x, y, z): b for x, y, z, b in v.items()}
    whole = run((-60, 0, -450, -10, 30, -400))
    parts = {}
    for bb in ((-60, 0, -450, -10, 30, -425), (-60, 0, -424, -10, 30, -400)): parts.update(run(bb))
    assert whole == parts


def test_skiffs():
    hangar = cells([f for f in L.FEATURES if f.name == 'landing_g_skiff_hangar'][0])
    glass = [p for p, b in hangar.items() if b == L.DARKGLASS]
    assert len(glass) >= 3 * 6, len(glass)                           # canopy glass of 3 skiffs
    west = cells([f for f in L.FEATURES if f.name == 'landing_g_tip_pad_west'][0])
    assert any(b == L.DARKGLASS for b in west.values())              # parked skiff at the G tip
    east = cells([f for f in L.FEATURES if f.name == 'landing_g_tip_pad_east'][0])
    assert not any(b == L.DARKGLASS for b in east.values())


if __name__ == '__main__':
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith('test_') and callable(fn):
            try:
                fn(); print('ok  ', n)
            except Exception as e:
                import traceback; traceback.print_exc(); fails += 1; print('FAIL', n, e)
    sys.exit(1 if fails else 0)
