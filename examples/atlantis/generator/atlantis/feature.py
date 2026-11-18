"""Features: the unit every generator produces.

A Feature has a name, a local bounding box, a layer (build order) and build(vol, ctx). The orchestrator builds
one tile at a time: for each tile it calls build() on every feature whose bbox intersects the tile, in layer
order, with a Volume covering that tile. build() must be deterministic (seed from ctx.seed + feature name) and
may draw outside the tile: Volume clips. For speed, large features should loop only over vol.clip(bbox).

Layers (lower builds first, later layers overwrite):
  0 terrain / seabed      10 foundation        20 dome shell + perimeter
  30 roads, plazas        40 buildings         50 landmarks          60 interiors (inside their building)
  70 detailing            80 lighting (hidden light blocks)          90 vegetation / ocean decoration
"""

LAYERS = dict(terrain=0, foundation=10, dome=20, roads=30, buildings=40, landmarks=50,
              interiors=60, detail=70, lighting=80, vegetation=90)

_REGISTRY = []


class Feature:
    layer = 40

    def __init__(self, name, bbox, layer=None, meta=None):
        self.name = name
        self.bbox = tuple(bbox)            # (x1, y1, z1, x2, y2, z2), inclusive, local coords
        if layer is not None: self.layer = layer
        self.meta = meta or {}             # free-form: district, kind, entrances, etc. (used by validators)

    def build(self, vol, ctx):
        raise NotImplementedError


class FnFeature(Feature):
    """Feature from a plain function fn(vol, ctx)."""

    def __init__(self, name, bbox, fn, layer=40, meta=None):
        super().__init__(name, bbox, layer, meta)
        self.fn = fn

    def build(self, vol, ctx):
        self.fn(vol, ctx)


def register(*features):
    _REGISTRY.extend(features)
    return features[0] if len(features) == 1 else features


def registry():
    return list(_REGISTRY)


def clear():
    _REGISTRY.clear()
