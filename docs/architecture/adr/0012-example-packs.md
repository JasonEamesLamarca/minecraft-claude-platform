# ADR-0012: Example packs (Atlantis): format, trust and installation

**Status:** Accepted (Phase 6, 18 Nov 2026)

## Context

The brief asks for a downloadable Atlantis example that can be validated, installed and rolled back with
`mcai examples …`. Downloads must come from an allowlist, have size caps, and must never run scripts. ADR-0005 makes
the published Atlantis an original design in two sizes (Lite, and Full in sections), with SHA-256 checksums and the
files published as release assets, not in Git.

The private build of the city used vanilla structure templates (`.nbt`). It placed them with console commands
(`/place template`) and checked the result by reading region files on the server's disk. None of that fits the
platform: the AI and the gateway have no console, the world is not mounted in the gateway, and the gateway has no
internet route.

## Decision

**Format: Sponge Schematic v3 (`.schem`) sections + a JSON manifest.** `.schem` is the common exchange format
(WorldEdit, other tools), stores a palette with varint indices and compresses well. The full city's 255 million
blocks are 4.7 MB in 90 sections, versus about 500 MB as structure templates. The manifest lists every section with
its offset, size, SHA-256, byte size and block count, plus the pack's anchor, recommended placement and places of
interest. `structure_void` means "leave the world as it is" (outside the city's work area). Packs carry no block
entities, entities or biomes.

**Built by the project's generator, reproducibly.** `examples/atlantis/generator` (stdlib Python) builds both packs.
Palettes are sorted and gzip runs with mtime 0, so the same source gives the same content. The compressed bytes
also depend on the zlib version (1.2.13 and 1.3.1 differ), so the catalog pins two hashes per pack: the manifest's
SHA-256, which identifies the exact release files that downloads are checked against, and `content_sha256`, a
digest of the uncompressed content. `scripts/build-examples.sh --check` (CI) rebuilds Lite and compares
`content_sha256`.

**Lite is a cutaway of the real city.** The generator's geometry is fixed in absolute radii (core 130, piers to
540, dome 600), so a scaled-down city would be a different design. Lite is a 96 x 768 strip through the centre
along the north pier: tower, the Fountain & Arrival district, landing pads, the dome wall and the sea floor. Its
cut faces and top are sealed with glass, so it can be placed anywhere without flooding.

**Trust chain.**

1. `examples/catalog.json` ships with the platform (in the gateway image). For each pack it holds the manifest URL
   and the manifest's SHA-256.
2. `mcai examples download` runs **in the admin's client**, because the gateway deliberately has no internet route.
   It accepts only `https://github.com/<owner>/<repo>/releases/download/<tag>/<file>`, and GitHub's asset hosts only
   as redirect targets. Every redirect is checked, and sizes are capped. It verifies the manifest against the
   catalog and each section against the manifest before uploading anything. `--from <folder>` sends a local build:
   allowed, but labelled *unofficial* when its manifest hash isn't the catalog's (a build with another zlib version
   is unofficial too, although its content is the same).
3. Files reach the gateway over SSH (`examples upload`, `examples.manage`) through a separate `/v1/mgmt/upload`
   route with its own size cap. The gateway does not trust the client. It validates the manifest strictly: unknown
   keys, path-like file names, overlapping or out-of-bounds sections, and totals that don't add up are all refused.
   It then checks each file's hash and size, and parses the `.schem` with hard limits: decompressed size from the
   declared dimensions, NBT depth and tag count, palette size, exact varint length and index range. It refuses
   block entities, entities and any block on the deny-list (the AI build deny-list minus `light` and
   `structure_void`: command and structure blocks, jigsaw, spawners, TNT, lava, fire, portals, bedrock...).
4. The mod fetches sections from the gateway, checks the SHA-256 again, parses them with an `NbtSizeTracker`
   limit, parses every palette entry with the game's block parser (NBT disabled) and applies the same deny-list.

**Installation in the mod, with undo.** The admin chooses where the anchor goes (`--at X Y Z`). The gateway first
checks the world height, protected areas, overlap with other installs, and `EXAMPLES_MAX_INSTALL_BLOCKS`. The mod
works through each section chunk by chunk:

- Before a chunk piece is changed for the first time, its current content is saved as a vanilla structure template
  under `world/generated/mcai/structures/examples/<install>/`.
- Only cells that differ from the pack are set, `EXAMPLES_BLOCKS_PER_TICK` per tick, with `FORCE_STATE` (the
  generator's explicit pane and wall connections are kept) and `SKIP_DROPS` (containers are cleared first, as
  `/fill` does).
- Placing again is therefore a **repair**. A snapshot that already exists is never overwritten, so resuming after
  an interruption keeps the original state.
- **Rollback** places the snapshots back, one piece per tick, then deletes them. **Forget** deletes them without
  restoring.

The next section is downloaded and parsed in the background while the current one is placed. One job runs at a
time. The mod reports progress, and the heartbeat says which install it is working on. An install the mod no
longer knows (server restart) becomes `interrupted`, and so does a running job when the gateway restarts.

**Automations.**

- An area trigger may name an example (`area = {example = "atlantis-lite"}`); it follows that pack's latest install.
- `example_tour` tells the triggering player the places of interest with world coordinates (read-only).
- `example_restore` repairs the latest install. It changes the world, so it is privileged (owner approval), like
  `ai_build`.
- Three Atlantis automations (arrival, tour, weekly restore) ship next to the pack.

**Permissions.** `examples.read` (auditor, operator, admin, owner), `examples.manage` and `examples.install`
(admin, owner). Uploads, refusals, installs, repairs, rollbacks and snapshot deletions are audited.

## Consequences

- No new container, port, secret or network route. The gateway still has no internet access, and nothing in a
  pack can run code or commands.
- Undo snapshots take disk space: roughly the pack's footprint as structure templates (tens of MB for Lite,
  several hundred for Full) until rolled back or forgotten. A rollback also undoes anything players built in the
  area afterwards; the admin guide says so and `rollback` asks for `--yes`.
- Full is big (255 million cells, 30-80 minutes at the default speed). It is meant for a dedicated ocean, after a
  backup, and is limited by `EXAMPLES_MAX_INSTALL_BLOCKS`.
- While the repository is private, release downloads need GitHub credentials. Until publication, packs are
  installed from a local build (`--from dist/examples`).
- A generator change changes the hashes. `scripts/build-examples.sh` updates the catalog, a new release tag
  carries the files, and CI fails if the catalog is stale.
