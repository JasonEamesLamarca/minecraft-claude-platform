"""Minimal NBT writer (stdlib only). Values: use the typed wrappers for ambiguous types."""
import gzip, struct


class Int(int): pass
class Short(int): pass
class Byte(int): pass
class IntList(list): pass          # TAG_List of TAG_Int
class ByteArray(bytes): pass
class IntArray(list): pass        # TAG_Int_Array


def _payload(v):
    if isinstance(v, Byte): return 1, struct.pack('>b', v)
    if isinstance(v, Short): return 2, struct.pack('>h', v)
    if isinstance(v, bool): return 1, struct.pack('>b', int(v))
    if isinstance(v, int): return 3, struct.pack('>i', v)
    if isinstance(v, ByteArray): return 7, struct.pack('>i', len(v)) + bytes(v)
    if isinstance(v, str):
        b = v.encode('utf-8'); return 8, struct.pack('>H', len(b)) + b
    if isinstance(v, IntArray):
        return 11, struct.pack('>i', len(v)) + b''.join(struct.pack('>i', x) for x in v)
    if isinstance(v, IntList):
        return 9, struct.pack('>bi', 3, len(v)) + b''.join(struct.pack('>i', x) for x in v)
    if isinstance(v, list):
        if not v: return 9, struct.pack('>bi', 0, 0)
        parts = [_payload(x) for x in v]
        return 9, struct.pack('>bi', parts[0][0], len(v)) + b''.join(p[1] for p in parts)
    if isinstance(v, dict):
        out = b''
        for k, x in v.items():
            t, p = _payload(x); kb = k.encode('utf-8')
            out += struct.pack('>bH', t, len(kb)) + kb + p
        return 10, out + b'\0'
    raise TypeError(type(v))


def write(path, root, name=''):
    t, p = _payload(root); nb = name.encode()
    with gzip.open(path, 'wb') as f:
        f.write(struct.pack('>bH', t, len(nb)) + nb + p)
