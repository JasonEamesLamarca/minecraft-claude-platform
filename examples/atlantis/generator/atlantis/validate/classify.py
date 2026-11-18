"""Block classification used by the validators (name-pattern heuristics + data/blocks_1.21.1.json).

Classes (BlockInfo.cls):
  FULL    full solid cube (also glass, leaves, ice: full cubes), the default for unknown names
  AIR     air, cave_air, void_air, light[...]      (no collision, nothing visible)
  NOCOLL  non-full, no collision: torches, flowers, signs, buttons, rails, vines, ladders, plants...
  PART    non-full with collision: slabs, stairs, panes, walls, fences, doors, trapdoors, carpets, beds, lanterns...
  WATER   the block `water`
  WPLANT  blocks that contain water: kelp, seagrass, bubble_column
  VOID    structure_void (outside the work mask)
Unknown names default to FULL/opaque: that can hide a leak but never invents one.
"""
import json, os, re

from .. import palette
from ..util import parse_state
from ..config import ROOT

FULL, AIR, NOCOLL, PART, WATER, WPLANT, VOID = range(7)
CLASS_NAMES = ['full', 'air', 'nocoll', 'part', 'water', 'wplant', 'void']

PLAIN_AIR = {'air', 'cave_air', 'void_air'}
AIR_LIKE = PLAIN_AIR | {'light'}
WATER_PLANTS = {'kelp', 'kelp_plant', 'seagrass', 'tall_seagrass', 'bubble_column'}

_NOCOLL_SUFFIX = ('_torch', '_sign', '_button', '_banner', '_pressure_plate', '_sapling', '_coral', '_coral_fan',
                  '_coral_wall_fan', '_mushroom', '_vein', '_tulip', '_bush')
_NOCOLL_NAMES = {
    'torch', 'wall_torch', 'redstone_wall_torch', 'ladder', 'vine', 'glow_lichen', 'sculk_vein', 'lever', 'tripwire',
    'tripwire_hook', 'string', 'redstone_wire', 'rail', 'powered_rail', 'detector_rail', 'activator_rail', 'fire',
    'soul_fire', 'short_grass', 'grass', 'fern', 'tall_grass', 'large_fern', 'dead_bush', 'dandelion', 'poppy',
    'blue_orchid', 'allium', 'azure_bluet', 'oxeye_daisy', 'cornflower', 'lily_of_the_valley', 'wither_rose',
    'sunflower', 'lilac', 'rose_bush', 'peony', 'torchflower', 'pitcher_plant', 'sea_pickle', 'small_amethyst_bud',
    'medium_amethyst_bud', 'large_amethyst_bud', 'amethyst_cluster', 'hanging_roots', 'spore_blossom', 'frogspawn',
    'nether_portal', 'end_portal', 'end_gateway', 'cobweb', 'sweet_berry_bush', 'snow', 'moss_carpet',
    'pale_moss_carpet'}
_PART_SUFFIX = ('_slab', '_stairs', '_pane', '_wall', '_fence', '_fence_gate', '_carpet', '_door', '_trapdoor', '_bed',
                '_head', '_skull', '_lantern', '_candle', '_cauldron', '_hanging_sign', '_chest', '_campfire',
                '_anvil')
_PART_NAMES = {
    'lantern', 'soul_lantern', 'chain', 'end_rod', 'iron_bars', 'candle', 'cake', 'repeater', 'comparator',
    'daylight_detector', 'conduit', 'turtle_egg', 'scaffolding', 'composter', 'dragon_egg', 'end_portal_frame',
    'lily_pad', 'pointed_dripstone', 'sculk_sensor', 'calibrated_sculk_sensor', 'decorated_pot', 'farmland',
    'dirt_path', 'cactus', 'bell', 'lectern', 'grindstone', 'stonecutter', 'hopper', 'brewing_stand',
    'enchanting_table', 'flower_pot', 'lightning_rod', 'cauldron', 'chest', 'ender_chest', 'trapped_chest', 'anvil',
    'campfire', 'soul_campfire', 'heavy_core', 'creaking_heart', 'bamboo', 'sugar_cane', 'cocoa', 'chorus_flower',
    'chorus_plant', 'big_dripleaf', 'small_dripleaf', 'big_dripleaf_stem', 'azalea', 'flowering_azalea'}
_TRANSPARENT_FULL_SUFFIX = ('glass', 'leaves', 'ice')
_OPAQUE_EXCEPT = {'tinted_glass'}
_TRANSPARENT_FULL_NAMES = {'slime_block', 'honey_block', 'barrier', 'frosted_ice', 'beacon', 'spawner', 'lava'}

# emitters not in palette.EMISSION that generators may reasonably use
EXTRA_EMISSION = {'torch': 14, 'wall_torch': 14, 'soul_torch': 10, 'soul_wall_torch': 10, 'lava': 15, 'fire': 15,
                  'soul_fire': 10, 'jack_o_lantern': 15, 'magma_block': 3, 'redstone_torch': 7,
                  'redstone_wall_torch': 7, 'candle[lit=true]': 3, 'campfire[lit=true]': 15,
                  'soul_campfire[lit=true]': 10, 'respawn_anchor': 15, 'sea_lantern': 15, 'end_rod': 14}


class BlockInfo:
    __slots__ = ('state', 'name', 'cls', 'wl', 'opaque', 'emit', 'glass', 'plain_air')

    def __init__(self, state, name, cls, wl, opaque, emit, glass, plain_air):
        self.state, self.name, self.cls, self.wl, self.opaque = state, name, cls, wl, opaque
        self.emit, self.glass, self.plain_air = emit, glass, plain_air

    @property
    def wet(self):
        """Contains water (water block, water plant, or waterlogged=true)."""
        return self.cls in (WATER, WPLANT) or self.wl


def _cls_of(name):
    if name in AIR_LIKE: return AIR
    if name == 'water': return WATER
    if name in WATER_PLANTS: return WPLANT
    if name == 'structure_void': return VOID
    if name in _NOCOLL_NAMES or name.endswith(_NOCOLL_SUFFIX): return NOCOLL
    if name in _PART_NAMES or name.endswith(_PART_SUFFIX) or name.startswith('potted_'): return PART
    return FULL


def _opaque_of(name, cls):
    if cls == VOID: return True
    if cls != FULL: return False
    if name in _OPAQUE_EXCEPT: return True
    return not (name in _TRANSPARENT_FULL_NAMES or name.endswith(_TRANSPARENT_FULL_SUFFIX))


# ---- emission lookup
_EM_INDEX = None


def _em_index():
    global _EM_INDEX
    if _EM_INDEX is None:
        idx = {}
        for table in (EXTRA_EMISSION, palette.EMISSION):
            for k, v in table.items():
                n, props = parse_state(k)
                idx.setdefault(n[10:], {})[tuple(sorted(props.items()))] = v
        _EM_INDEX = idx
    return _EM_INDEX


def emission_of(name, props):
    if name == 'light': return int(props.get('level', '15'))
    ent = _em_index().get(name)
    if not ent: return 0
    best = 0
    for req, lvl in ent.items():
        if all(props.get(k) == v for k, v in req): best = max(best, lvl)
    return best


_CACHE = {}


def info(state):
    """BlockInfo of a state string (None is treated as plain air)."""
    r = _CACHE.get(state)
    if r is None:
        if state is None:
            r = BlockInfo(None, 'air', AIR, False, False, 0, False, True)
        else:
            try:
                n, props = parse_state(state)
            except Exception:
                n, props = 'minecraft:' + str(state), {}
            name = n[10:] if n.startswith('minecraft:') else n
            cls = _cls_of(name)
            r = BlockInfo(state, name, cls, props.get('waterlogged') == 'true', _opaque_of(name, cls),
                          emission_of(name, props), 'glass' in name, name in PLAIN_AIR)
        _CACHE[state] = r
    return r


# ---- block state data (every valid block -> property -> allowed values)
_BLOCKS = None


def blocks():
    global _BLOCKS
    if _BLOCKS is None:
        with open(os.path.join(ROOT, 'data', 'blocks_1.21.1.json')) as f:
            _BLOCKS = json.load(f)
    return _BLOCKS


_STATE_RE = re.compile(r'^([a-z0-9_.:/-]+)(?:\[([^\[\]]*)\])?$')


def state_problems(state):
    """List of problems (strings) of a block-state string; empty = valid. Omitted properties are fine
    (vanilla fills defaults), and the data lists no 'required' ones, so a missing property is never an error."""
    if state is None: return []
    m = _STATE_RE.match(state)
    if not m: return [f'malformed state {state!r}']
    name, rest = m.group(1), m.group(2)
    if ':' in name:
        ns, name = name.split(':', 1)
        if ns != 'minecraft': return [f'namespace {ns!r} is not minecraft in {state!r}']
    props = blocks().get(name)
    if props is None: return [f'unknown block {name!r}']
    out = []
    if rest:
        seen = set()
        for p in rest.split(','):
            if '=' not in p:
                out.append(f'malformed property {p!r} in {state!r}'); continue
            k, v = p.split('=', 1)
            if k in seen: out.append(f'duplicate property {k!r} in {state!r}')
            seen.add(k)
            if k not in props: out.append(f'{name} has no property {k!r} (allowed: {sorted(props) or "none"})')
            elif v not in props[k]: out.append(f'{name}[{k}={v}] invalid (allowed: {"|".join(props[k])})')
    return out
