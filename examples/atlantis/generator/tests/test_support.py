#!/usr/bin/env python3
"""Support pass tests (plain asserts). Run from the project root:  python3 tests/test_support.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from atlantis import config, support
from atlantis.volume import Volume

ctx = config.load()


def vol():
    v = Volume(-4, 0, -4, 4, 6, 4)
    v.fill(-4, 0, -4, 4, 0, 4, 'quartz_block')
    return v


def test_ladder():
    v = vol(); v.set(1, 2, 0, 'quartz_block'); v.set(0, 2, 0, 'ladder[facing=east]')   # wall is east, not west
    support.fix(v, ctx); assert v.get(0, 2, 0) == 'ladder[facing=west]', v.get(0, 2, 0)
    v.set(0, 3, 0, 'ladder[facing=east]'); v.set(1, 3, 0, 'air')                          # nothing around
    support.fix(v, ctx); assert v.get(0, 3, 0) == 'air'


def test_plants_and_crops():
    v = vol(); v.set(0, 1, 0, 'flowering_azalea'); v.set(1, 1, 0, 'wheat[age=7]')
    support.fix(v, ctx)
    assert v.get(0, 0, 0) == 'moss_block' and v.get(0, 1, 0) == 'flowering_azalea'
    assert v.get(1, 0, 0).startswith('farmland')


def test_lantern():
    v = vol(); v.set(0, 1, 0, 'smooth_quartz_slab[type=bottom]'); v.set(0, 2, 0, 'lantern[hanging=false]')
    support.fix(v, ctx); assert v.get(0, 1, 0) == 'smooth_quartz_slab[type=top]'
    v.set(2, 4, 0, 'lantern[hanging=false]'); v.set(2, 5, 0, 'quartz_block')
    support.fix(v, ctx); assert v.get(2, 4, 0) == 'lantern[hanging=true]'


def test_beds():
    v = vol(); v.set(0, 1, 0, 'white_bed[facing=north,part=foot]'); v.set(0, 1, 1, 'white_bed[facing=north,part=head]')
    support.fix(v, ctx)
    assert v.get(0, 1, 0) == 'white_bed[facing=south,part=foot]' and v.get(0, 1, 1) == 'white_bed[facing=south,part=head]'
    # split over the tile edge (x -1 | 0): removed
    v.set(-1, 2, 2, 'white_bed[facing=east,part=foot]'); v.set(0, 2, 2, 'white_bed[facing=east,part=head]')
    support.fix(v, ctx); assert v.get(-1, 2, 2) == 'air' and v.get(0, 2, 2) == 'air'


if __name__ == '__main__':
    for n, f in list(globals().items()):
        if n.startswith('test_'): f(); print('ok', n)
    print('all support tests passed')
