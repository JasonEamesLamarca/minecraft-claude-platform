#!/usr/bin/env python3
"""Tiny read-only Anvil (.mca) reader, stdlib only, for 1.18+ worlds. Used to survey terrain."""
import struct, zlib, os, math


def _nbt(buf, i, t):
    if t == 1: return struct.unpack_from('>b', buf, i)[0], i + 1
    if t == 2: return struct.unpack_from('>h', buf, i)[0], i + 2
    if t == 3: return struct.unpack_from('>i', buf, i)[0], i + 4
    if t == 4: return struct.unpack_from('>q', buf, i)[0], i + 8
    if t == 5: return struct.unpack_from('>f', buf, i)[0], i + 4
    if t == 6: return struct.unpack_from('>d', buf, i)[0], i + 8
    if t == 7:
        n = struct.unpack_from('>i', buf, i)[0]; return buf[i + 4:i + 4 + n], i + 4 + n
    if t == 8:
        n = struct.unpack_from('>H', buf, i)[0]; return buf[i + 2:i + 2 + n].decode('utf-8', 'replace'), i + 2 + n
    if t == 9:
        et, n = struct.unpack_from('>bi', buf, i); i += 5; out = []
        for _ in range(n):
            v, i = _nbt(buf, i, et); out.append(v)
        return out, i
    if t == 10:
        d = {}
        while True:
            et = buf[i]; i += 1
            if et == 0: return d, i
            n = struct.unpack_from('>H', buf, i)[0]; name = buf[i + 2:i + 2 + n].decode('utf-8', 'replace'); i += 2 + n
            d[name], i = _nbt(buf, i, et)
    if t == 11:
        n = struct.unpack_from('>i', buf, i)[0]; return list(struct.unpack_from(f'>{n}i', buf, i + 4)), i + 4 + 4 * n
    if t == 12:
        n = struct.unpack_from('>i', buf, i)[0]; return list(struct.unpack_from(f'>{n}q', buf, i + 4)), i + 4 + 8 * n
    raise ValueError(t)


class Region:
    def __init__(self, path):
        self.data = open(path, 'rb').read()

    def chunk(self, lx, lz):
        off = struct.unpack_from('>I', self.data, 4 * (lx + 32 * lz))[0]
        if not off: return None
        o = (off >> 8) * 4096
        n, comp = struct.unpack_from('>ib', self.data, o)
        raw = self.data[o + 5:o + 4 + n]
        raw = zlib.decompress(raw) if comp == 2 else raw
        return _nbt(raw, 3 + struct.unpack_from('>H', raw, 1)[0], 10)[0]


def section_blocks(sec):
    """Return list of 4096 block names (index y*256+z*16+x) or None if absent."""
    bs = sec.get('block_states')
    if not bs: return None
    pal = [('flowing_water' if p['Name'] == 'minecraft:water' and p.get('Properties', {}).get('level', '0') != '0'
            else p['Name'].replace('minecraft:', '')) for p in bs['palette']]
    if len(pal) == 1: return [pal[0]] * 4096
    bits = max(4, math.ceil(math.log2(len(pal)))); per = 64 // bits; mask = (1 << bits) - 1
    out = []
    for l in bs['data']:
        l &= (1 << 64) - 1
        for k in range(per):
            out.append(pal[(l >> (k * bits)) & mask])
            if len(out) == 4096: return out
    return out


class World:
    def __init__(self, region_dir):
        self.dir = region_dir; self.regions = {}; self.cache = {}

    def chunk_sections(self, cx, cz):
        key = (cx, cz)
        if key not in self.cache:
            rk = (cx >> 5, cz >> 5)
            if rk not in self.regions:
                p = os.path.join(self.dir, f'r.{rk[0]}.{rk[1]}.mca')
                self.regions[rk] = Region(p) if os.path.exists(p) else None
            r = self.regions[rk]; secs = {}
            c = r.chunk(cx & 31, cz & 31) if r else None
            if c and (c.get('xPos'), c.get('zPos')) != (cx, cz):
                raise RuntimeError(f'chunk {cx},{cz}: region copy is inconsistent (got {c.get("xPos")},{c.get("zPos")}) - copy it again with save-off')
            if c:
                for s in c.get('sections', []):
                    b = section_blocks(s)
                    if b: secs[s['Y']] = b
            self.cache[key] = secs
        return self.cache[key]

    def block(self, x, y, z):
        secs = self.chunk_sections(x >> 4, z >> 4)
        s = secs.get(y >> 4)
        if s is None: return None
        return s[(y & 15) * 256 + (z & 15) * 16 + (x & 15)]

    def column(self, x, z, y0=-64, y1=319):
        return [(y, self.block(x, y, z)) for y in range(y0, y1 + 1)]
