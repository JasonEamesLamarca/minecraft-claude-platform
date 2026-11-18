"""Block-state helpers: build, parse, rotate and mirror Minecraft 1.21.1 block-state strings.

States are strings without the 'minecraft:' prefix, e.g. 'quartz_stairs[facing=north,half=bottom,shape=straight]'.
Property order in generated strings is alphabetical (the order Mojang writes them), so equal states compare equal.

Directions: north = -z, east = +x, south = +z, west = -x.  A "quarter turn" is 90 degrees clockwise seen from above
(north -> east -> south -> west).
"""

DIRS = {'north': (0, -1), 'east': (1, 0), 'south': (0, 1), 'west': (-1, 0)}
ORDER = ['north', 'east', 'south', 'west']
OPPOSITE = {'north': 'south', 'south': 'north', 'east': 'west', 'west': 'east'}


# ---- parse / format -------------------------------------------------------------------------------------------
def parse(state):
    """'name[a=b,c=d]' -> ('name', {'a': 'b', 'c': 'd'})."""
    if '[' not in state:
        return state, {}
    name, rest = state.split('[', 1)
    props = {}
    for kv in rest.rstrip(']').split(','):
        if kv:
            k, v = kv.split('=', 1)
            props[k] = v
    return name, props


def fmt(name, props):
    if not props:
        return name
    return name + '[' + ','.join('%s=%s' % (k, _s(props[k])) for k in sorted(props)) + ']'


def _s(v):
    if v is True: return 'true'
    if v is False: return 'false'
    return str(v)


def with_props(state, **kw):
    """Return state with some properties set/overridden."""
    name, p = parse(state)
    p.update(kw)
    return fmt(name, p)


def base_name(state):
    return state.split('[', 1)[0]


# ---- direction helpers ----------------------------------------------------------------------------------------
def facing_from_vector(dx, dz):
    """Horizontal facing nearest to a vector (dominant axis; ties go to the z axis; zero vector -> 'north')."""
    if dx == 0 and dz == 0:
        return 'north'
    if abs(dz) >= abs(dx):
        return 'south' if dz > 0 else 'north'
    return 'east' if dx > 0 else 'west'


def opposite(facing):
    return OPPOSITE[facing]


def turn(facing, quarter_turns):
    """Rotate a facing name clockwise by quarter turns."""
    return ORDER[(ORDER.index(facing) + quarter_turns) % 4]


def quarter_turns(facing):
    """Number of clockwise quarter turns that takes 'north' to `facing`."""
    return ORDER.index(facing)


# ---- families (stairs / slab names for a full block) ----------------------------------------------------------
_FAMILY = {
    'smooth_quartz': ('smooth_quartz_stairs', 'smooth_quartz_slab'),
    'quartz_block': ('quartz_stairs', 'quartz_slab'),
    'quartz_pillar': ('quartz_stairs', 'quartz_slab'),
    'quartz_bricks': ('quartz_stairs', 'quartz_slab'),
    'chiseled_quartz_block': ('quartz_stairs', 'quartz_slab'),
    'white_concrete': ('smooth_quartz_stairs', 'smooth_quartz_slab'),
    'calcite': ('smooth_quartz_stairs', 'smooth_quartz_slab'),
    'white_terracotta': ('smooth_quartz_stairs', 'smooth_quartz_slab'),
    'light_gray_concrete': ('polished_andesite_stairs', 'polished_andesite_slab'),
    'cyan_concrete': ('dark_prismarine_stairs', 'dark_prismarine_slab'),
    'iron_block': ('polished_deepslate_stairs', 'polished_deepslate_slab'),
}
_HAS_FAMILY = ('polished_deepslate', 'deepslate_bricks', 'deepslate_tiles', 'prismarine', 'prismarine_bricks',
               'dark_prismarine', 'polished_diorite', 'diorite', 'stone', 'stone_bricks', 'smooth_stone',
               'polished_andesite', 'andesite', 'sandstone', 'smooth_sandstone', 'waxed_oxidized_cut_copper',
               'cobbled_deepslate', 'deepslate', 'blackstone', 'polished_blackstone')


def family(name):
    """(stairs_name, slab_name) matching a full block, or None if it has no sensible match."""
    name = base_name(name)
    if name.endswith('_stairs'):
        return name, name[:-7] + '_slab'
    if name.endswith('_slab'):
        return name[:-5] + '_stairs', name
    if name in _FAMILY:
        return _FAMILY[name]
    if name in _HAS_FAMILY:
        return name + '_stairs', name + '_slab'
    return None


# ---- builders -------------------------------------------------------------------------------------------------
def stairs(name, facing, half='bottom', shape='straight', waterlogged=False):
    """Stair state. `name` may be a full block ('smooth_quartz') or a stairs name. `facing` is the direction the
    stair ascends toward (its tall back is on that side)."""
    fam = family(name)
    sname = fam[0] if fam else (name if name.endswith('_stairs') else name + '_stairs')
    assert facing in DIRS, facing
    assert half in ('bottom', 'top'), half
    assert shape in ('straight', 'inner_left', 'inner_right', 'outer_left', 'outer_right'), shape
    p = {'facing': facing, 'half': half, 'shape': shape}
    if waterlogged: p['waterlogged'] = 'true'
    return fmt(sname, p)


def slab(name, type='bottom', waterlogged=False):
    """Slab state; `name` may be a full block or a slab name."""
    assert type in ('bottom', 'top', 'double'), type
    fam = family(name)
    sname = fam[1] if fam else (name if name.endswith('_slab') else name + '_slab')
    p = {'type': type}
    if waterlogged: p['waterlogged'] = 'true'
    return fmt(sname, p)


def pane(name, north=False, east=False, south=False, west=False):
    """Glass pane / iron bars with explicit connections (the placer does not auto-connect them)."""
    return fmt(base_name(name), {'north': north, 'east': east, 'south': south, 'west': west})


def fence(name, north=False, east=False, south=False, west=False):
    return fmt(base_name(name), {'north': north, 'east': east, 'south': south, 'west': west})


def wall(name, north='none', east='none', south='none', west='none', up=True):
    """Wall with explicit connections ('none' | 'low' | 'tall'; True means 'low')."""
    def c(v): return 'low' if v is True else ('none' if v is False else v)
    return fmt(base_name(name), {'north': c(north), 'east': c(east), 'south': c(south), 'west': c(west), 'up': up})


def pillar(name, axis='y'):
    return fmt(base_name(name), {'axis': axis})


# ---- rotate / mirror ------------------------------------------------------------------------------------------
def rotate_state(state, quarter_turns):
    """Rotate a state clockwise (seen from above) by quarter turns: facing, axis (x<->z on odd turns), the
    north/east/south/west connection properties of panes, walls and fences, and sign/banner `rotation`."""
    q = quarter_turns % 4
    if q == 0 or '[' not in state:
        return state
    name, p = parse(state)
    out = {}
    for k, v in p.items():
        if k == 'facing' and v in DIRS:
            v = ORDER[(ORDER.index(v) + q) % 4]
        elif k == 'axis' and v in ('x', 'z') and q % 2:
            v = 'z' if v == 'x' else 'x'
        elif k == 'rotation':
            v = str((int(v) + 4 * q) % 16)
        elif k in DIRS:
            k = ORDER[(ORDER.index(k) + q) % 4]
        out[k] = v
    return fmt(name, out)


_SWAP_LR = {'inner_left': 'inner_right', 'inner_right': 'inner_left', 'outer_left': 'outer_right',
            'outer_right': 'outer_left', 'left': 'right', 'right': 'left'}


def mirror_state(state, axis='x'):
    """Mirror a state. axis='x': flip east<->west (mirror in the plane x = const); axis='z': flip north<->south.
    Stair shapes and door hinges swap left/right."""
    if '[' not in state:
        return state
    flip = ('east', 'west') if axis == 'x' else ('north', 'south')
    name, p = parse(state)
    out = {}
    for k, v in p.items():
        if k == 'facing' and v in flip:
            v = OPPOSITE[v]
        elif k in flip:
            k = OPPOSITE[k]
        elif k in ('shape', 'hinge') and v in _SWAP_LR and (k == 'hinge' or v.startswith(('inner', 'outer'))):
            v = _SWAP_LR[v]
        elif k == 'rotation':
            r = int(v)
            v = str(((16 - r) if axis == 'x' else (8 - r)) % 16)
        out[k] = v
    return fmt(name, out)


# ---- validation -----------------------------------------------------------------------------------------------
import re
_NAME = re.compile(r'^[a-z0-9_]+$')
_PROP = re.compile(r'^[a-z_]+=[a-z0-9_]+$')
_DOMAIN = {
    'facing': {'north', 'east', 'south', 'west', 'up', 'down'},
    'half': {'bottom', 'top', 'upper', 'lower'},
    'shape': {'straight', 'inner_left', 'inner_right', 'outer_left', 'outer_right'},
    'type': {'bottom', 'top', 'double'},
    'axis': {'x', 'y', 'z'},
}


def is_valid_state(state):
    """Syntactic check plus the value domains of the properties we generate (stairs, slabs, axis, facing)."""
    if not isinstance(state, str) or state.startswith('minecraft:'):
        return False
    name, _, rest = state.partition('[')
    if not _NAME.match(name):
        return False
    if rest:
        if not rest.endswith(']'):
            return False
        for kv in rest[:-1].split(','):
            if not _PROP.match(kv):
                return False
        _, p = parse(state)
        for k, dom in _DOMAIN.items():
            if k in p and p[k] not in dom:
                return False
        if name.endswith('_stairs') and 'shape' in p and p['shape'] not in _DOMAIN['shape']:
            return False
        if name.endswith('_slab') and 'type' not in p:
            return False
    else:
        if name.endswith('_stairs') or name.endswith('_slab'):
            return True   # defaults are valid
    if name.endswith('_stairs') and rest:
        _, p = parse(state)
        if p.get('facing') not in DIRS or p.get('half') not in ('bottom', 'top'):
            return False
    return True
