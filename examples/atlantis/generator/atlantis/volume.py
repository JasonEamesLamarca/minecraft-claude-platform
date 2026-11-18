"""A box of blocks (normally one 32x32 full-height tile) that generators write into.

Writes outside the box are silently ignored, so a generator can draw a whole feature and only the part inside
the current tile is kept. Blocks are state strings without the 'minecraft:' prefix, e.g.
'quartz_stairs[facing=north,half=bottom]'. None means "not set" -> the exporter fills it with the background
(air inside the dome, water outside, stone below the seabed).
"""
from array import array


class Volume:
    def __init__(self, x1, y1, z1, x2, y2, z2):
        self.x1, self.y1, self.z1, self.x2, self.y2, self.z2 = x1, y1, z1, x2, y2, z2
        self.sx, self.sy, self.sz = x2 - x1 + 1, y2 - y1 + 1, z2 - z1 + 1
        self.palette = [None]
        self.index = {None: 0}
        self.cells = array('H', bytes(2 * self.sx * self.sy * self.sz))

    # ---- bounds
    def contains(self, x, y, z):
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2 and self.z1 <= z <= self.z2

    def intersects(self, x1, y1, z1, x2, y2, z2):
        return not (x2 < self.x1 or x1 > self.x2 or y2 < self.y1 or y1 > self.y2 or z2 < self.z1 or z1 > self.z2)

    def clip(self, x1, y1, z1, x2, y2, z2):
        """Intersection of a box with this volume, or None."""
        a = (max(min(x1, x2), self.x1), max(min(y1, y2), self.y1), max(min(z1, z2), self.z1),
             min(max(x1, x2), self.x2), min(max(y1, y2), self.y2), min(max(z1, z2), self.z2))
        return a if a[0] <= a[3] and a[1] <= a[4] and a[2] <= a[5] else None

    def _i(self, x, y, z):
        return ((y - self.y1) * self.sz + (z - self.z1)) * self.sx + (x - self.x1)

    def _pid(self, block):
        p = self.index.get(block)
        if p is None:
            p = self.index[block] = len(self.palette); self.palette.append(block)
        return p

    # ---- access
    def set(self, x, y, z, block):
        if self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2 and self.z1 <= z <= self.z2:
            self.cells[self._i(x, y, z)] = self._pid(block)

    def get(self, x, y, z):
        if self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2 and self.z1 <= z <= self.z2:
            return self.palette[self.cells[self._i(x, y, z)]]
        return None

    def fill(self, x1, y1, z1, x2, y2, z2, block, replace=None):
        """Fill a box. replace: None = overwrite everything; a set of block strings (may include None)
        = only overwrite cells currently holding one of those."""
        c = self.clip(x1, y1, z1, x2, y2, z2)
        if not c: return
        p = self._pid(block)
        rep = None if replace is None else {self._pid(b) for b in replace}
        cells = self.cells
        for y in range(c[1], c[4] + 1):
            for z in range(c[2], c[5] + 1):
                i = self._i(c[0], y, z)
                if rep is None:
                    for k in range(i, i + c[3] - c[0] + 1): cells[k] = p
                else:
                    for k in range(i, i + c[3] - c[0] + 1):
                        if cells[k] in rep: cells[k] = p

    def set_if_empty(self, x, y, z, block):
        if self.get(x, y, z) is None: self.set(x, y, z, block)

    def items(self):
        """Yield (x, y, z, block) for every set cell."""
        pal, cells = self.palette, self.cells
        i = 0
        for y in range(self.y1, self.y2 + 1):
            for z in range(self.z1, self.z2 + 1):
                for x in range(self.x1, self.x2 + 1):
                    p = cells[i]; i += 1
                    if p: yield x, y, z, pal[p]
