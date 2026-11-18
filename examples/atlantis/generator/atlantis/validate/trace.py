"""Last-writer tracing for the validator: a Volume subclass recording, per cell, which feature wrote it last.

TracedVolume.writer[i]  = 1-based index into TracedVolume.order (the intersecting features sorted by (layer, name),
                          the exact order export_tiles.build_box uses); 0 = never written (background).
TracedVolume.touched[i] = 1 if any feature wrote the cell ("explicit" content, e.g. explicit air).
Because `order` is sorted by layer, `writer >= first_of_layer(L)` means "written last by a feature of layer >= L".
Only the validator uses this; the generator pipeline is untouched.
"""
from array import array

from ..volume import Volume
from .. import export_tiles as E


class TracedVolume(Volume):
    def __init__(self, *box):
        super().__init__(*box)
        n = self.sx * self.sy * self.sz
        self.writer = array('H', bytes(2 * n))
        self.touched = bytearray(n)
        self.cur = 0
        self.order = []

    def set(self, x, y, z, block):
        if self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2 and self.z1 <= z <= self.z2:
            i = self._i(x, y, z)
            self.cells[i] = self._pid(block)
            if block is None:
                self.writer[i] = 0; self.touched[i] = 0
            else:
                self.writer[i] = self.cur; self.touched[i] = 1

    def fill(self, x1, y1, z1, x2, y2, z2, block, replace=None):
        c = self.clip(x1, y1, z1, x2, y2, z2)
        if not c: return
        p = self._pid(block)
        rep = None if replace is None else {self._pid(b) for b in replace}
        cells, writer, touched = self.cells, self.writer, self.touched
        w = c[3] - c[0] + 1
        wv = 0 if block is None else self.cur
        tv = 0 if block is None else 1
        for y in range(c[1], c[4] + 1):
            for z in range(c[2], c[5] + 1):
                i = self._i(c[0], y, z)
                if rep is None:
                    cells[i:i + w] = array('H', [p]) * w
                    writer[i:i + w] = array('H', [wv]) * w
                    touched[i:i + w] = bytes([tv]) * w
                else:
                    for k in range(i, i + w):
                        if cells[k] in rep:
                            cells[k] = p; writer[k] = wv; touched[k] = tv

    def first_of_layer(self, layer):
        """Smallest writer value whose feature has layer >= `layer` (len(order)+1 if none)."""
        for k, f in enumerate(self.order, 1):
            if f.layer >= layer: return k
        return len(self.order) + 1


def build_traced(ctx, box, features):
    """Same result as export_tiles.build_box (cells identical), plus the writer/touched trace."""
    vol = TracedVolume(*box)
    vol.order = sorted((f for f in features if E.feature_intersects(f, box)), key=lambda f: (f.layer, f.name))
    for k, f in enumerate(vol.order, 1):
        vol.cur = k
        f.build(vol, ctx)
    vol.cur = 0
    E.fill_background(ctx, vol)
    return vol


def get_trace(ctx, vol, features):
    """The TracedVolume for `vol`: vol itself if it is one, else a rebuild of the same box (cached on vol)."""
    if hasattr(vol, 'writer'): return vol
    t = getattr(vol, '_trace', None)
    if t is None:
        t = build_traced(ctx, (vol.x1, vol.y1, vol.z1, vol.x2, vol.y2, vol.z2), features)
        try:
            vol._trace = t
        except AttributeError:
            pass
    return t
