#!/usr/bin/env python3
"""Example packs: .schem round trip, sealing, determinism, manifest (plain asserts; also run by pytest)."""
import hashlib, json, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atlantis import config, pack, schem
from atlantis import export_tiles as E

ctx = config.load()

TINY = {'title': 'Tiny', 'description': 'test', 'box': (-20, -20, 11, 11), 'section': (16, 32), 'seal': True,
        'poi': [('Centre', 0, 5, 0)], 'recommended': {'floor_y': -56, 'note': ''}}


def _build(tmp, jobs=1):
    pack.PACKS['atlantis-tiny'] = TINY
    try:
        return pack.build('atlantis-tiny', tmp, jobs, '0.0.1')
    finally:
        del pack.PACKS['atlantis-tiny']


def test_varints():
    assert schem.varints([0, 1, 127], 128) == bytes([0, 1, 127])
    assert schem.varints([0, 128, 300], 301) == bytes([0, 0x80, 0x01, 0xAC, 0x02])


def test_round_trip_seal_and_manifest():
    tmp = tempfile.mkdtemp()
    m = _build(tmp)
    assert m['format'] == 1 and m['id'] == 'atlantis-tiny' and m['size'] == [32, ctx.y_max - ctx.y_min + 1, 32]
    assert [s['file'] for s in m['sections']] == ['atlantis-tiny-s000.schem', 'atlantis-tiny-s001.schem']
    pack._init()
    for sec in m['sections']:
        data = open(os.path.join(tmp, 'atlantis-tiny', sec['file']), 'rb').read()
        assert hashlib.sha256(data).hexdigest() == sec['sha256'] and len(data) == sec['bytes']
        s, states, cells = schem.decode(data)
        w, h, l = sec['size']
        assert (s['Version'], s['Width'], s['Height'], s['Length']) == (3, w, h, l)
        assert s['Offset'] == sec['offset'] and s['Blocks']['BlockEntities'] == []
        assert len(cells) == w * h * l and states == sorted(states)
        assert not any(st.startswith('minecraft:bedrock') for st in states)
        x1, y1, z1 = sec['offset']
        vol = E.build_box(ctx, (x1, y1, z1, x1 + w - 1, y1 + h - 1, z1 + l - 1), pack._features)
        k = 0
        for y in range(y1, y1 + h):
            for z in range(z1, z1 + l):
                for x in range(x1, x1 + w):
                    got = states[cells[k]]; k += 1
                    want = vol.get(x, y, z)
                    face = x in (-20, 11) or z in (-20, 11) or y == ctx.y_max
                    if face and want.split('[')[0] in pack.NON_SOLID:
                        assert got == 'minecraft:glass', (x, y, z, got, want)
                    elif want == 'bedrock':
                        assert got == 'minecraft:deepslate'
                    else:
                        assert got == schem.canonical(want), (x, y, z, got, want)
    assert m['total_blocks'] == sum(s['blocks'] for s in m['sections']) == 32 * 32 * (ctx.y_max - ctx.y_min + 1)
    assert m['points_of_interest'] == [{'name': 'Centre', 'at': [0, 5, 0]}]
    json.dumps(m)


def test_deterministic():
    a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
    ma, mb = _build(a), _build(b)
    assert [s['sha256'] for s in ma['sections']] == [s['sha256'] for s in mb['sections']]
    assert open(os.path.join(a, 'atlantis-tiny', 'manifest.json')).read() == \
        open(os.path.join(b, 'atlantis-tiny', 'manifest.json')).read()


def test_outside_area_is_void_and_skipped():
    secs = pack.sections(pack.PACKS['atlantis-full'])
    assert len(secs) == 100
    spec = dict(TINY, box=(560, 560, 591, 591), section=(32, 32))
    pack.PACKS['atlantis-edge'] = spec
    try:
        m = pack.build('atlantis-edge', tempfile.mkdtemp(), 1, '0')
    finally:
        del pack.PACKS['atlantis-edge']
    sec = m['sections'][0]
    assert sec['blocks'] < 32 * 32 * 127 and 'minecraft:structure_void' in m['blocks_used']


if __name__ == '__main__':
    for k, fn in sorted(globals().items()):
        if k.startswith('test_') and callable(fn):
            fn(); print('ok', k)
    print('all ok')
