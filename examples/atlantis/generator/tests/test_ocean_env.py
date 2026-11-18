#!/usr/bin/env python3
"""ocean_env.py tests (plain asserts).  Run from the project root: python3 tests/test_ocean_env.py"""
import json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, background, planner as PL, ocean_env as O, terrain
from atlantis.volume import Volume
from atlantis.orchestrate import load_generators

ctx = config.load()
FEATS = load_generators()
MINE = O.features()
VALID = json.load(open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')))
GRAVITY = ('sand', 'red_sand', 'gravel', 'concrete_powder', 'suspicious_sand', 'suspicious_gravel', 'anvil')
CORAL_PLANT = ('_coral', '_coral_fan', '_coral_wall_fan')


def built(f):
    vol = Volume(f.bbox[0], ctx.y_min, f.bbox[2], f.bbox[3], ctx.y_max, f.bbox[5])
    # formations first so layer-90 guards see them, as in a real tile
    for g in FEATS:
        if g.layer < f.layer and g.bbox[0] <= f.bbox[3] and g.bbox[3] >= f.bbox[0] and g.bbox[2] <= f.bbox[5] and g.bbox[5] >= f.bbox[2]:
            g.build(vol, ctx)
    mark = set(k for k in ((x, y, z) for x, y, z, b in vol.items()))
    f.build(vol, ctx)
    return vol, {(x, y, z): b for x, y, z, b in vol.items() if (x, y, z) not in mark}


def test_registered_and_counts():
    names = {f.name for f in FEATS}
    assert {f.name for f in MINE} <= names and len(names) == len(FEATS)
    kinds = {}
    for f in MINE: kinds[f.meta['kind']] = kinds.get(f.meta['kind'], 0) + 1
    assert kinds['ocean_kelp'] >= 6 and kinds['ocean_meadow'] >= 6 and 3 <= kinds['ocean_reef'] <= 8
    assert kinds['ocean_marker'] >= 8 and 3 <= kinds['ocean_sensor'] <= 4
    for f in MINE:
        assert f.layer in (50, 90)
        assert all(isinstance(v, int) for v in f.bbox)
        assert max(abs(f.bbox[0]), abs(f.bbox[3]), abs(f.bbox[2]), abs(f.bbox[5])) <= 800


def test_determinism():
    for f in MINE[::4]:
        a = built(f)[1]; b = built(f)[1]
        assert a == b, f.name
    assert [f.bbox for f in O.make_features(ctx)] == [f.bbox for f in MINE]


def test_blocks_valid_and_no_gravity():
    for f in MINE:
        for b in set(built(f)[1].values()):
            name = b.split('[')[0]
            assert name in VALID, (f.name, b)
            assert name not in GRAVITY, (f.name, b)


def test_dome_distance_and_sectors():
    for f in MINE:
        for (x, y, z), b in built(f)[1].items():
            assert O.dome_distance(ctx, x, y, z) >= 25, (f.name, x, y, z)
            a = PL.plan_angle(x, z)
            assert not (80 <= a <= 100 or 200 <= a <= 220), (f.name, x, y, z, a)
            assert math.hypot(x, z) >= 600
            assert not O.in_station_approach(x, z), (f.name, x, z)
            assert not ctx.inside_dome(x, y, z)


def test_coral():
    n = 0
    for f in MINE:
        if f.meta['kind'] != 'ocean_reef': continue
        vol, cells = built(f)

        def wet(x, y, z):
            b = vol.get(x, y, z)
            if b is None: b = background.background(ctx, x, y, z)
            return b == 'water' or 'waterlogged=true' in b or b.startswith(('kelp', 'seagrass', 'tall_seagrass'))
        for (x, y, z), b in cells.items():
            name = b.split('[')[0]
            if name.endswith('_coral_block') and not name.startswith('dead_'):
                n += 1
                assert any(wet(x + dx, y + dy, z + dz) for dx, dy, dz in
                           ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))), (f.name, x, y, z)
            if name.endswith(CORAL_PLANT) and not name.startswith('dead_'):
                assert 'waterlogged=true' in b, (f.name, b)
    assert n > 100


def test_vegetation_supported_and_in_water():
    for f in MINE:
        if f.meta['kind'] not in ('ocean_kelp', 'ocean_meadow'): continue
        vol, cells = built(f)
        for (x, y, z), b in cells.items():
            below = vol.get(x, y - 1, z) or background.background(ctx, x, y - 1, z)
            assert below != 'water' and below != 'air', (f.name, x, y, z, b)
            assert background.background(ctx, x, y, z) == 'water'
            assert y <= ctx.y_max - 3, (f.name, y)


def test_structures_solid_and_apart():
    structs = [f for f in MINE if f.layer == 50]
    for f in structs:
        for g in FEATS:
            if g is f or g.layer not in (40, 50): continue
            assert g.bbox[3] < f.bbox[0] or g.bbox[0] > f.bbox[3] or g.bbox[5] < f.bbox[2] or g.bbox[2] > f.bbox[5], (f.name, g.name)
        vol, cells = built(f)
        for (x, y, z), b in cells.items():
            assert b != 'air' and 'light[' not in b
        if f.meta['kind'] == 'ocean_sensor':
            assert len(cells) > 50 and not any(b.endswith(('door', 'trapdoor')) for b in cells.values())


def test_terrain_sector_untouched():
    # relief of this wave fades out inside the reserved sectors
    for a in (82, 90, 98, 202, 210, 218):
        assert terrain.sector_gain(*[v for v in PL.from_polar(700, a)]) == 0.0


if __name__ == '__main__':
    import time
    for k, fn in sorted(globals().items()):
        if k.startswith('test_') and callable(fn):
            t = time.time(); fn(); print(f'ok  {k}  ({time.time() - t:.1f}s)')
    print('all ocean_env tests passed')
