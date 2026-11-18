"""The restrained Atlantean material palette. Use these names, not ad-hoc ones,
so the look stays consistent and a palette change is one edit. All are valid in Minecraft 1.21.1."""

# primary architecture: white and pale grey
WHITE = 'smooth_quartz'
WHITE_BLOCK = 'quartz_block'
WHITE_PILLAR = 'quartz_pillar'
WHITE_BRICKS = 'quartz_bricks'
WHITE_CONCRETE = 'white_concrete'
PALE = 'calcite'
PALE_GREY = 'polished_diorite'
GREY = 'light_gray_concrete'
WHITE_TERRACOTTA = 'white_terracotta'
WHITE_STAIRS = 'smooth_quartz_stairs'
WHITE_SLAB = 'smooth_quartz_slab'
QUARTZ_STAIRS = 'quartz_stairs'
QUARTZ_SLAB = 'quartz_slab'
DIORITE_WALL = 'diorite_wall'

# glass and energy (light blue / cyan)
GLASS = 'glass'
GLASS_PANE = 'glass_pane'
GLASS_BLUE = 'light_blue_stained_glass'
GLASS_BLUE_PANE = 'light_blue_stained_glass_pane'
GLASS_CYAN = 'cyan_stained_glass'
GLASS_CYAN_PANE = 'cyan_stained_glass_pane'
GLASS_WHITE = 'white_stained_glass'
GLASS_TINTED = 'tinted_glass'
CYAN = 'cyan_concrete'
LIGHT_BLUE = 'light_blue_concrete'
CYAN_TERRACOTTA = 'cyan_terracotta'

# foundations and contrast
DARK = 'polished_deepslate'
DARK_BRICKS = 'deepslate_bricks'
DARK_TILES = 'deepslate_tiles'
PRISMARINE = 'prismarine'
PRISMARINE_BRICKS = 'prismarine_bricks'
DARK_PRISMARINE = 'dark_prismarine'
METAL = 'iron_block'
COPPER = 'waxed_oxidized_copper'
COPPER_CUT = 'waxed_oxidized_cut_copper'
COPPER_GRATE = 'waxed_oxidized_copper_grate'

# light sources (use sparingly; prefer hidden ones)
LAMP = 'sea_lantern'
ROD = 'end_rod'
FROG_BLUE = 'verdant_froglight'
FROG_WHITE = 'pearlescent_froglight'
FROG_WARM = 'ochre_froglight'
HIDDEN_LIGHT = 'light[level=15]'          # invisible light block; use light[level=N] for other levels
BEACON = 'beacon'

# natural / seabed
STONE = 'stone'
DEEPSLATE = 'deepslate'
GRAVEL = 'gravel'
SAND = 'sand'
CLAY = 'clay'
WATER = 'water'
AIR = 'air'
SEAGRASS = 'seagrass'
KELP = 'kelp_plant'
KELP_TOP = 'kelp'
CORAL_BLOCKS = ['tube_coral_block', 'brain_coral_block', 'bubble_coral_block', 'fire_coral_block', 'horn_coral_block']

# blocks a light source emits (for the lighting validator); 'light[level=N]' handled separately
EMISSION = {LAMP: 15, ROD: 14, FROG_BLUE: 15, FROG_WHITE: 15, FROG_WARM: 15, BEACON: 15, 'glowstone': 15,
            'shroomlight': 15, 'lantern': 15, 'soul_lantern': 10, 'redstone_lamp[lit=true]': 15,
            'conduit': 15, 'sea_pickle': 6, 'glow_lichen': 7, 'crying_obsidian': 10}
