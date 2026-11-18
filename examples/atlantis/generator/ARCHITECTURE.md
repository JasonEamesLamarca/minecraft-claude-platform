# Atlantis generator: architecture and contracts

The generator is plain Python 3 (standard library only). It builds the city cell by cell and writes the example
packs (Sponge v3 `.schem` sections plus a manifest). Every module follows the rules below.

## Coordinates
- **City-local** coordinates everywhere: x east, z south, origin (0, 0) = city centre (axis of the central tower).
- y = 0 is the city floor block; the decks' walking level is y 5 (`planner.WALK_Y`). The generated volume runs
  `ctx.y_min..ctx.y_max` (with `profiles/default.json`: -8..118, i.e. world -64..62 when the anchor is at y -56).
- Packs are position-independent: the installer chooses where the anchor (local 0, 0, 0) goes in the world.
- Never hardcode dome numbers: read them from `ctx` (`atlantis.config.load()`).

## Core modules
| Module | What it gives you |
| --- | --- |
| `config.py` | `load() -> Ctx`. Fields: `seed, detail, y_min, y_max, dome_radius, dome_height, dome_R, dome_cy, dome_thickness, city_radius, tower_height, tile, area_radius`. Methods: `dome_outer_y(x,z)`, `dome_inner_y(x,z)`, `inside_dome(x,y,z)`, `clearance(x,z)`, `max_building_top(x,z)`. |
| `volume.py` | `Volume(x1,y1,z1,x2,y2,z2)`: `set`, `get`, `fill(..., replace=None)`, `set_if_empty`, `clip`, `intersects`, `items`. Writes outside the box are ignored. Blocks are state strings **without** `minecraft:` (e.g. `quartz_stairs[facing=north,half=bottom]`). `None` = unset. |
| `feature.py` | `Feature(name, bbox, layer, meta)` with `build(vol, ctx)`; `FnFeature`; `register()`; `registry()`; `LAYERS`. |
| `background.py` | `background(ctx,x,y,z)`: what an unset cell becomes (air inside the dome, seabed below `seabed_height`, water above). |
| `palette.py` | Named materials; use these names. `EMISSION` gives light levels. |
| `export_tiles.py` | Builds boxes and tiles (`build_box`), fills the background, writes vanilla structure templates for development. |
| `schem.py`, `pack.py` | The Sponge v3 writer and the pack builder (`python3 -m atlantis.pack lite|full`). |

## Generation model
1. Generator modules (`generators.py`) create `Feature`s, each with a tight bbox, and `register()` them. Importing
   a module must be cheap; heavy work goes in `build()`.
2. A box (a tile for development, a section for packs) becomes a `Volume`. `build()` runs on every feature that
   intersects it, in layer order, then unset cells get `background()`.
3. `build()` must be **deterministic**: use `random.Random(hash_str(ctx.seed, feature.name))` (a stable hash, not
   Python's `hash()`). Never use global random state, and never depend on the order boxes are built in.
4. Big features must loop only over `vol.clip(bbox)`, not their whole bbox, or every box pays for the whole feature.

## Layers (build order, later overwrites earlier)
0 terrain · 10 foundation · 20 dome · 30 roads · 40 buildings · 50 landmarks · 60 interiors · 70 detail ·
80 lighting · 90 vegetation

## Packs
- `pack.py` splits the pack's box into sections (Lite: 96 x 128 columns; Full: 160 x 160), builds each in a worker
  process and writes `<pack>-sNNN.schem` plus `manifest.json` (sizes, offsets, SHA-256, block counts, places of
  interest). Palettes are sorted and gzip runs with mtime 0, so a build is byte-for-byte reproducible.
- Export rules: `bedrock` becomes `deepslate`; `structure_void` means "leave the world as it is"; Lite's cut
  faces and top are sealed with glass.
- `scripts/build-examples.sh` (repository root) builds both packs and pins the manifests' hashes in
  `examples/catalog.json`.

## Angle conventions (important)
- **City plan** (districts, piers; `planner.py`): degrees clockwise from **north** (−z). 0 = north, 90 = east.
- **geometry.py / architecture.py**: degrees from **east** (+x) towards south (+z). 0 = east, 90 = south.
- Convert: `geom_angle = plan_angle - 90`. Use `atlantis.planner`'s helpers rather than converting by hand.
- Stair `facing` = the side of the tall back (vanilla). Panes, walls and fences must get explicit connection
  states (`blocks.pane`, `Pen.finish()`): nothing recomputes them after placement, and box edges can't see their
  neighbours.

## Component library
`geometry.py` (clip-aware shapes: discs, rings, arcs, cylinders, `tower(rfunc)`, frustum, terraces, spheres,
`dome_cap`, boxes, `hollow_box`, `line3d`, `stairs_run`, `spiral_stairs`, `ramp`, `bridge_deck`, `facade_rows`,
`facade_rows_arc`, `Pen` local frame) and `architecture.py` (`COMPONENTS`: atlantean_column, curved_support,
decorative_rib, window_module, entrance_portal, tower_cap, balcony, energy_conduit, light_fixture,
bridge_section, railing, floor_pattern, wall_panel, console).

## Tests
`python3 tests/run_all.py` runs every test file in its own process (features register globally on import).
A single file: `python3 tests/test_pack.py`.
