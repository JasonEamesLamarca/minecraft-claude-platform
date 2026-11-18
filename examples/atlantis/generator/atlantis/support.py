"""Support pass: fix blocks that Minecraft would break as soon as the tile is placed.

The Wave 10 read-back found ~780 blocks that popped off every time their tile was placed (ladders with no wall
behind them, plants on quartz, lanterns standing on bottom slabs, beds with head and foot swapped, carpets under
water). Rather than patch every generator, export runs this pass over each tile after all features are built:

  ladder / wall torch   no full block behind it -> turn it to face away from a full neighbour, else remove it
  plants                on a full block that is not soil -> that block becomes moss (crops: farmland), else removed
  lantern (standing)    on a bottom slab -> the slab becomes a top slab; else hang it if the block above holds it,
                        else remove it
  lantern (hanging)     nothing above to hang from -> stand it if the block below holds it, else remove it
  bed                   head not where the foot's facing points -> turn both parts to the head; lone parts removed;
                        beds whose two parts fall in different tiles are removed (placing one tile breaks the half)
  carpets               next to water -> become water outside the dome (they would be washed away), air inside

The volume must have a one-block halo around the cells being fixed (`fix(vol, ctx, inner_box)`), so blocks at a
tile edge see their real neighbours. Deterministic: depends only on the volume contents.
"""
from .util import base_name

DIRS = {'north': (0, 0, -1), 'south': (0, 0, 1), 'east': (1, 0, 0), 'west': (-1, 0, 0)}
OPP = {'north': 'south', 'south': 'north', 'east': 'west', 'west': 'east'}

NONFULL_NAMES = {
    None, 'air', 'cave_air', 'void_air', 'structure_void', 'water', 'lava', 'light', 'bubble_column', 'ladder',
    'lantern', 'soul_lantern', 'chain', 'end_rod', 'torch', 'wall_torch', 'soul_torch', 'soul_wall_torch',
    'flower_pot', 'lectern', 'enchanting_table', 'anvil', 'bell', 'cauldron', 'water_cauldron', 'hopper',
    'brewing_stand', 'scaffolding', 'snow', 'sea_pickle', 'kelp', 'kelp_plant', 'seagrass', 'tall_seagrass',
    'campfire', 'soul_campfire', 'conduit', 'iron_bars', 'cobweb', 'rail', 'powered_rail', 'detector_rail',
    'activator_rail', 'redstone_wire', 'lever', 'tripwire', 'tripwire_hook', 'repeater', 'comparator',
    'daylight_detector', 'stonecutter', 'grindstone', 'chest', 'trapped_chest', 'ender_chest', 'decorated_pot',
    'dirt_path', 'farmland', 'soul_sand', 'mud', 'honey_block', 'cactus', 'bamboo', 'sugar_cane', 'vine',
    'glow_lichen', 'sculk_vein', 'pointed_dripstone', 'small_amethyst_bud', 'medium_amethyst_bud',
    'large_amethyst_bud', 'amethyst_cluster', 'heavy_core', 'pitcher_plant', 'big_dripleaf', 'small_dripleaf',
    'spore_blossom', 'hanging_roots', 'moss_carpet', 'pink_petals', 'turtle_egg', 'frogspawn', 'lily_pad',
    'cake', 'candle', 'skeleton_skull', 'player_head', 'dragon_egg', 'piston_head', 'moving_piston',
}
NONFULL_SUFFIX = ('_slab', '_stairs', '_pane', '_fence', '_fence_gate', '_wall', '_door', '_trapdoor', '_carpet',
                  '_button', '_pressure_plate', '_sign', '_banner', '_bed', '_candle', '_sapling', '_flower',
                  '_coral', '_coral_fan', '_torch', '_head', '_skull', '_rod', '_cake', '_bars', '_tulip',
                  '_mushroom', '_roots', '_fungus', '_sprouts', '_vines', '_plant', '_bush', '_grass', '_fern',
                  '_petals', '_leaves_pile', '_pot')
PLANTS = {'azalea', 'flowering_azalea', 'fern', 'short_grass', 'tall_grass', 'large_fern', 'dead_bush',
          'dandelion', 'poppy', 'blue_orchid', 'allium', 'azure_bluet', 'red_tulip', 'orange_tulip', 'white_tulip',
          'pink_tulip', 'oxeye_daisy', 'cornflower', 'lily_of_the_valley', 'sunflower', 'lilac', 'rose_bush',
          'peony', 'sweet_berry_bush', 'oak_sapling', 'spruce_sapling', 'birch_sapling', 'jungle_sapling',
          'acacia_sapling', 'dark_oak_sapling', 'cherry_sapling', 'mangrove_propagule', 'torchflower', 'pink_petals'}
CROPS = {'wheat', 'carrots', 'potatoes', 'beetroots'}
SOIL = {'grass_block', 'dirt', 'coarse_dirt', 'podzol', 'rooted_dirt', 'moss_block', 'mud', 'muddy_mangrove_roots',
        'farmland', 'mycelium', 'clay'}


def _props(state):
    if not state or '[' not in state: return {}
    return dict(kv.split('=', 1) for kv in state[state.index('[') + 1:-1].split(','))


def _with(state, **kw):
    name = state.split('[', 1)[0]
    p = _props(state); p.update(kw)
    return name + ('[' + ','.join(f'{k}={v}' for k, v in sorted(p.items())) + ']' if p else '')


def full(state):
    n = base_name(state) if state else None
    if n in NONFULL_NAMES: return False
    if n.endswith('_slab'): return _props(state).get('type') == 'double'
    return not n.endswith(NONFULL_SUFFIX)


def holds_up(state):
    """Block below can carry a standing lantern (centre support on its top face)."""
    n = base_name(state) if state else None
    if n is None: return False
    if n.endswith('_slab'): return _props(state).get('type') in ('top', 'double')
    if n.endswith(('_fence', '_wall')) or n in ('chain', 'iron_bars', 'end_rod'): return True
    if n.endswith('_stairs'): return _props(state).get('half') == 'top'
    return full(state)


def holds_down(state):
    """Block above can carry a hanging lantern (centre support on its bottom face)."""
    n = base_name(state) if state else None
    if n is None: return False
    if n.endswith('_slab'): return _props(state).get('type') in ('bottom', 'double')
    if n.endswith(('_fence', '_wall')) or n in ('chain', 'iron_bars', 'end_rod'): return True
    if n.endswith('_stairs'): return _props(state).get('half') == 'bottom'
    return full(state)


def _wet(state):
    return state is not None and (base_name(state) == 'water' or 'waterlogged=true' in state)


def fix(vol, ctx, box=None):
    """Fix unsupported blocks in `box` (default: the whole volume minus a 1-block rim). Returns the change count."""
    x1, y1, z1, x2, y2, z2 = box or (vol.x1 + 1, vol.y1, vol.z1 + 1, vol.x2 - 1, vol.y2, vol.z2 - 1)
    get, set_ = vol.get, vol.set

    def g(x, y, z):
        return get(x, y, z) if vol.contains(x, y, z) else None

    targets = {}
    for i, s in enumerate(vol.palette):
        if s is None: continue
        n = base_name(s)
        if n in ('ladder', 'wall_torch', 'soul_wall_torch', 'lantern', 'soul_lantern') or n in PLANTS or \
                n in CROPS or n.endswith('_bed') or n.endswith('_carpet'):
            targets[i] = n
    if not targets: return 0
    cells, sx, sz = vol.cells, vol.sx, vol.sz
    todo = []
    for y in range(max(y1, vol.y1), min(y2, vol.y2) + 1):
        for z in range(z1, z2 + 1):
            base = ((y - vol.y1) * sz + (z - vol.z1)) * sx - vol.x1
            for x in range(x1, x2 + 1):
                if cells[base + x] in targets: todo.append((x, y, z))
    changed = 0
    for x, y, z in todo:
        s = get(x, y, z)
        if s is None: continue                       # removed earlier in this pass (a bed partner)
        n = base_name(s); p = _props(s)
        new = None; extra = None
        if n in ('ladder', 'wall_torch', 'soul_wall_torch'):
            f = p.get('facing', 'north')
            dx, _, dz = DIRS[OPP[f]]
            if not full(g(x + dx, y, z + dz)):
                new = 'air'
                for f2 in ('north', 'east', 'south', 'west'):
                    dx, _, dz = DIRS[OPP[f2]]
                    if full(g(x + dx, y, z + dz)): new = _with(s, facing=f2); break
        elif n in ('lantern', 'soul_lantern'):
            below, above = g(x, y - 1, z), g(x, y + 1, z)
            if p.get('hanging') == 'true':
                if not holds_down(above):
                    new = _with(s, hanging='false') if holds_up(below) else 'air'
            elif not holds_up(below):
                bn = base_name(below) if below else None
                if bn and bn.endswith('_slab') and _props(below).get('type') == 'bottom':
                    extra = ((x, y - 1, z), _with(below, type='top'))
                elif holds_down(above):
                    new = _with(s, hanging='true')
                else:
                    new = 'air'
        elif n in PLANTS or n in CROPS:
            below = g(x, y - 1, z)
            bn = base_name(below) if below else None
            ok = (bn == 'farmland') if n in CROPS else (bn in SOIL)
            if not ok:
                if full(below) and vol.contains(x, y - 1, z):
                    extra = ((x, y - 1, z), 'farmland[moisture=7]' if n in CROPS else 'moss_block')
                else:
                    new = 'air'
        elif n.endswith('_carpet'):
            if any(_wet(g(x + a, y + b, z + c)) for a, b, c in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0))):
                new = 'air' if ctx.inside_dome(x, y, z) else 'water'
        elif n.endswith('_bed'):
            f = p.get('facing', 'north'); part = p.get('part', 'foot')
            dx, _, dz = DIRS[f]
            sgn = 1 if part == 'foot' else -1
            mate = g(x + sgn * dx, y, z + sgn * dz)
            want = 'head' if part == 'foot' else 'foot'
            if not (mate and base_name(mate) == n and _props(mate).get('part') == want
                    and _props(mate).get('facing') == f):
                new = 'air'
                for f2, (ax, _, az) in DIRS.items():       # partner on another side: turn both towards it
                    mx, mz = x + sgn * ax, z + sgn * az
                    m = g(mx, y, mz)
                    if m and base_name(m) == n and _props(m).get('part') == want:
                        new = _with(s, facing=f2)
                        if vol.contains(mx, y, mz): extra = ((mx, y, mz), _with(m, facing=f2))
                        break
            if new is None or new != 'air':              # a bed split over two tiles breaks on placement
                f2 = _props(new or s).get('facing', f)
                ax, _, az = DIRS[f2]
                T = ctx.tile
                if (x + sgn * ax) // T != x // T or (z + sgn * az) // T != z // T: new, extra = 'air', None
        if new is not None and new != s:
            set_(x, y, z, new); changed += 1
        if extra and get(*extra[0]) != extra[1]:
            set_(*extra[0], extra[1]); changed += 1
    return changed
