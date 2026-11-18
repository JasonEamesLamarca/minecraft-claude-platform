"""TEMPORARY demo generator to exercise the tile pipeline end to end. Will be replaced by the real generators."""
import math
from .feature import FnFeature, register
from . import palette as P


def _tower(vol, ctx):
    r, h = 10, 30
    c = vol.clip(-r, 0, -r, r, h, r)
    if not c: return
    for y in range(c[1], c[4] + 1):
        for z in range(c[2], c[5] + 1):
            for x in range(c[0], c[3] + 1):
                d = x * x + z * z
                if d <= r * r:
                    vol.set(x, y, z, P.WHITE if d > (r - 1) * (r - 1) or y in (0, h) else 'air')


def _roads(vol, ctx):
    n = int(ctx.city_radius)
    vol.fill(-n, 0, -2, n, 0, 2, P.GREY)
    vol.fill(-2, 0, -n, 2, 0, n, P.GREY)


def _ring(vol, ctx):
    r = 40
    c = vol.clip(-r - 1, 1, -r - 1, r + 1, 1, r + 1)
    if not c: return
    for z in range(c[2], c[5] + 1):
        for x in range(c[0], c[3] + 1):
            if abs(math.hypot(x, z) - r) < 0.5:
                vol.set(x, 1, z, P.LAMP)


n = 560
register(FnFeature('demo_tower', (-10, 0, -10, 10, 30, 10), _tower, layer=40),
         FnFeature('demo_roads', (-n, 0, -n, n, 0, n), _roads, layer=30),
         FnFeature('demo_ring', (-41, 1, -41, 41, 1, 41), _ring, layer=80))
