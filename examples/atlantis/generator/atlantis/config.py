"""Load the design config and a placement profile into one context object (Ctx).

City-local coordinates: x east, z south, origin (0, 0) = city centre.
y = 0 is the city floor block (players walk at y = 1). Every generator works in these coordinates;
only the exporter and the deployer convert to world coordinates (via the profile).
"""
import json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the generator folder


class Ctx:
    def __init__(self, cfg, profile):
        self.cfg, self.profile = cfg, profile
        self.seed = cfg['seed']
        self.detail = cfg['detail']
        p = profile
        # vertical extent of the generated volume (local y)
        self.y_min = p['world_min_y'] - p['floor_y']                       # e.g. -8
        self.y_max = p['water_top_y'] - p['floor_y']                       # e.g. 118 (top water block)
        # dome: spherical cap with base radius a and height h, sphere radius R, sphere centre at y = h - R
        self.dome_radius = cfg['dome']['radius']
        budget = self.y_max - p['water_cover_min']                         # highest allowed apex (outer surface)
        self.dome_height = min(cfg['dome']['height_max'], budget)
        a, h = self.dome_radius, self.dome_height
        self.dome_R = (a * a + h * h) / (2 * h)
        self.dome_cy = h - self.dome_R
        self.dome_thickness = cfg['dome']['thickness']
        self.city_radius = cfg['city']['radius']
        t = cfg['tower']
        self.tower_height = int(min(self.dome_height * t['height_ratio'],
                                    self.dome_inner_y(0, 0) - t['min_clearance']))
        self.tile = cfg['tiles']['size']
        m = cfg['environment']['exterior_margin']
        self.area_radius = self.dome_radius + m                            # half-width of the generated square

    # ---- dome geometry (outer surface = dome_outer_y, inner surface = dome_inner_y)
    def dome_outer_y(self, x, z):
        """Outer shell surface height at (x, z), or None outside the dome footprint."""
        d2 = self.dome_R ** 2 - x * x - z * z
        if d2 < 0: return None
        y = self.dome_cy + math.sqrt(d2)
        return y if y >= 0 else None

    def dome_inner_y(self, x, z):
        d2 = (self.dome_R - self.dome_thickness) ** 2 - x * x - z * z
        if d2 < 0: return None
        y = self.dome_cy + math.sqrt(d2)
        return y if y >= 0 else None

    def inside_dome(self, x, y, z):
        """True if (x, y, z) is inside the inner shell surface (the dry air volume, above the floor)."""
        dx, dy, dz = x, y - self.dome_cy, z
        r = self.dome_R - self.dome_thickness
        return y >= 1 and dx * dx + dy * dy + dz * dz < r * r

    def clearance(self, x, z):
        """Free height above the floor at (x, z) under the inner dome surface (0 outside)."""
        y = self.dome_inner_y(x, z)
        return max(0, int(y) - 1) if y is not None else 0

    def max_building_top(self, x, z):
        """Highest local y a building may reach at (x, z)."""
        return self.clearance(x, z) - self.cfg['city']['height_margin_under_dome']

    # ---- work mask: cells outside it are never written to the world (exported as structure_void = skipped)
    def in_area(self, x, z):
        if x * x + z * z > self.area_radius * self.area_radius: return False
        p = self.profile
        if p.get('origin_x') is not None:
            wx, wz = x + p['origin_x'], z + p['origin_z']
            for b in p.get('protect', []):
                m = b.get('margin', 0)
                if b['x1'] - m <= wx <= b['x2'] + m and b['z1'] - m <= wz <= b['z2'] + m: return False
        return True

    # ---- world mapping (only exporter / deployer use these)
    def to_world(self, x, y, z):
        p = self.profile
        return x + p['origin_x'], y + p['floor_y'], z + p['origin_z']

    def summary(self):
        return {'y_min': self.y_min, 'y_max': self.y_max, 'dome_radius': self.dome_radius,
                'dome_height': self.dome_height, 'dome_R': round(self.dome_R, 1),
                'tower_height': self.tower_height, 'area_radius': self.area_radius,
                'clearance_r300': self.clearance(300, 0), 'clearance_r500': self.clearance(500, 0)}


def load(cfg_path=None, profile_path=None):
    cfg_path = cfg_path or os.path.join(ROOT, 'config', 'atlantis.json')
    profile_path = profile_path or os.path.join(ROOT, 'profiles', 'default.json')
    return Ctx(json.load(open(cfg_path)), json.load(open(profile_path)))


if __name__ == '__main__':
    print(json.dumps(load().summary(), indent=1))
