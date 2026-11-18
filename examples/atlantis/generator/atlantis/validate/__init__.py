"""Atlantis validation: per-tile checks + whole-city reduce steps.  See core.py for the design, runner.py for the API.

    from atlantis.validate import validate, resolve_checks, Issue
    report = validate(ctx, tiles, features, 'all', jobs=4)

Rules for generator authors (what the checks assume):
  * Dry air outside the dome (sealed pockets, basements under the seabed, interiors of sunken buildings): write it
    with an explicit `air` / `cave_air` set()/fill() so it counts as intentional dry air; it must then be sealed by
    non-water blocks on every face, also across tile borders. Never rely on the background for air outside the dome.
  * `light`, torches, signs, carpets, panes, slabs, stairs count as dry only INSIDE the dome (placing them in the open
    sea is fine and not a leak).
  * Decorative water inside the dome (lagoons, fountains, pools): give the feature
    meta={'water_ok': True} (its bbox may hold water, waterlogged blocks and wet/dry contact; keep the water inside the
    bbox) or meta={'water_ok': [(x1,y1,z1,x2,y2,z2), ...]} for explicit boxes.
  * Features of layer >= 30 must stay under ctx.max_building_top(x, z) inside the dome footprint; exceptions need
    meta={'clearance_ok': True}.
  * Layer 40/50 footprints (bbox in x/z) must not overlap; allow it with meta={'allow_overlap': True} or
    meta={'allow_overlap': ['other_feature_name', ...]} on either feature.
  * Unknown block names are treated as full opaque cubes; non-full blocks are recognised by name patterns in
    classify.py (extend the lists there when a new kind of block is used).
  * Walkability (connectivity / accessibility checks, model in walk.py).  Every standable place of the city must be
    reachable on foot from the Tower Plaza.  Feature meta keys (layer >= 40 features only):
      meta['entrances']   [(x, y, z), ...]  city-local FEET cell of each doorway/entrance (the cell the player's feet
                          are in, i.e. the floor block is at y - 1).  Each must be a standable node (+-0.5 block) that is
                          in the main walk component, else error.  At least one interior node must then be reachable.
      meta['interior']    True: warn if fewer than 80% of the standable interior nodes are reachable.
      meta['interior_box']  (x1, y1, z1, x2, y2, z2) optional: where the interior is (default: the feature bbox; give a
                          tight box when the bbox includes walkable ground outside the walls, or the check passes trivially).
      meta['roof_y']      optional y of the roof layer; interior nodes need their head below it (default bbox top y2).
      meta['islands_ok']  True: walkable areas of this feature (e.g. a sealed vault, an unreachable roof) are not
                          reported as unreachable islands.
    Unreachable walkable components of more than 50 nodes on a deck or inside a layer >= 40 bbox are errors; roofs
    and balconies that are not meant to be walked on should be fenced off (fence/wall/pane >= 1.5 blocks) or islands_ok.
    Doors are always passable; climbing needs ladders/vines/scaffolding, a full block up needs a jump (3 free cells),
    drops up to 3 blocks are allowed, swimming is a warning.

CLI:  cd examples/atlantis/generator && python3 -m atlantis.orchestrate validate [--tiles ...] [--checks ...] [--jobs N] [--json out.json]
"""
from .core import (Issue, Partial, Check, CHECKS, register_check, resolve_checks, configure, set_all_features,
                   OPTIONS)
from .runner import check_tile, reduce_all, validate, format_table, summarize, write_json

resolve_checks('all')        # import every check module so CHECKS is populated

__all__ = ['Issue', 'Partial', 'Check', 'CHECKS', 'register_check', 'resolve_checks', 'configure',
           'set_all_features', 'OPTIONS', 'check_tile', 'reduce_all', 'validate', 'format_table', 'summarize',
           'write_json']
