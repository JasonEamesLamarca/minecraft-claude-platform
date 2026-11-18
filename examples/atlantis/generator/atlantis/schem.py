"""Sponge Schematic v3 (`.schem`) writer, plus a small reader for tests.

Layout (https://github.com/SpongePowered/Schematic-Specification, version 3):

    {"Schematic": {Version: 3, DataVersion, Metadata: {...}, Width, Height, Length (shorts), Offset: int[3],
                   Blocks: {Palette: {"minecraft:stone": 0, ...}, Data: varint bytes, BlockEntities: []}}}

Block index = x + z * Width + y * Width * Length, which is the generator's own cell order (y, then z, then x),
so a Volume converts without reshuffling. Output is deterministic: sorted palette, gzip with mtime 0.
"""
import gzip, struct
from array import array

from . import nbtw
from .util import parse_state, format_state

VERSION = 3


def canonical(state):
    """'quartz_stairs[half=bottom,facing=north]' -> 'minecraft:quartz_stairs[facing=north,half=bottom]'."""
    name, props = parse_state(state)
    return 'minecraft:' + format_state(name, props)


def varints(cells, n_palette):
    """Encode palette indices as unsigned LEB128 varints (one byte each while the palette has < 128 entries)."""
    if n_palette <= 128:
        return array('B', cells).tobytes()
    table = []
    for v in range(n_palette):
        b = bytearray()
        while True:
            if v < 0x80:
                b.append(v); break
            b.append((v & 0x7F) | 0x80); v >>= 7
        table.append(bytes(b))
    return b''.join(table[c] for c in cells)


def encode(width, height, length, states, cells, offset=(0, 0, 0), data_version=3955, metadata=None):
    """Gzip-compressed .schem bytes. `states`: palette (canonical strings, index = position); `cells`: indices in
    x + z*W + y*W*L order."""
    if not (0 < width <= 0xFFFF and 0 < height <= 0xFFFF and 0 < length <= 0xFFFF):
        raise ValueError('schematic dimensions out of range')
    if len(cells) != width * height * length:
        raise ValueError('cell count does not match the dimensions')
    root = {'Schematic': {
        'Version': nbtw.Int(VERSION),
        'DataVersion': nbtw.Int(data_version),
        'Metadata': dict(metadata or {}),
        'Width': nbtw.Short(width), 'Height': nbtw.Short(height), 'Length': nbtw.Short(length),
        'Offset': nbtw.IntArray(list(offset)),
        'Blocks': {
            'Palette': {s: nbtw.Int(i) for i, s in enumerate(states)},
            'Data': nbtw.ByteArray(varints(cells, len(states))),
            'BlockEntities': [],
        },
    }}
    t, payload = nbtw._payload(root)
    raw = struct.pack('>bH', t, 0) + payload
    return gzip.compress(raw, compresslevel=9, mtime=0)


# ---------------------------------------------------------------- reader (tests and tools only)
def _read(buf, pos, t):
    if t == 1: return struct.unpack_from('>b', buf, pos)[0], pos + 1
    if t == 2: return struct.unpack_from('>h', buf, pos)[0], pos + 2
    if t == 3: return struct.unpack_from('>i', buf, pos)[0], pos + 4
    if t == 4: return struct.unpack_from('>q', buf, pos)[0], pos + 8
    if t == 5: return struct.unpack_from('>f', buf, pos)[0], pos + 4
    if t == 6: return struct.unpack_from('>d', buf, pos)[0], pos + 8
    if t == 7:
        n = struct.unpack_from('>i', buf, pos)[0]; pos += 4
        return bytes(buf[pos:pos + n]), pos + n
    if t == 8:
        n = struct.unpack_from('>H', buf, pos)[0]; pos += 2
        return buf[pos:pos + n].decode('utf-8'), pos + n
    if t == 9:
        et, n = struct.unpack_from('>bi', buf, pos); pos += 5
        out = []
        for _ in range(n):
            v, pos = _read(buf, pos, et); out.append(v)
        return out, pos
    if t == 10:
        out = {}
        while True:
            ct = buf[pos]; pos += 1
            if ct == 0: return out, pos
            n = struct.unpack_from('>H', buf, pos)[0]; pos += 2
            name = buf[pos:pos + n].decode('utf-8'); pos += n
            out[name], pos = _read(buf, pos, ct)
    if t == 11:
        n = struct.unpack_from('>i', buf, pos)[0]; pos += 4
        return list(struct.unpack_from('>%di' % n, buf, pos)), pos + 4 * n
    if t == 12:
        n = struct.unpack_from('>i', buf, pos)[0]; pos += 4
        return list(struct.unpack_from('>%dq' % n, buf, pos)), pos + 8 * n
    raise ValueError('bad NBT tag %d' % t)


def decode(data):
    """-> (schematic dict, list of palette states by index, list of cell indices)."""
    raw = gzip.decompress(data)
    if raw[0] != 10: raise ValueError('not an NBT compound')
    n = struct.unpack_from('>H', raw, 1)[0]
    root, _ = _read(raw, 3 + n, 10)
    s = root['Schematic']
    pal = s['Blocks']['Palette']
    states = [None] * len(pal)
    for k, v in pal.items(): states[v] = k
    data, cells, i, v, shift = s['Blocks']['Data'], [], 0, 0, 0
    for b in data:
        b &= 0xFF
        v |= (b & 0x7F) << shift
        if b & 0x80: shift += 7; continue
        cells.append(v); v = shift = 0
    return s, states, cells
