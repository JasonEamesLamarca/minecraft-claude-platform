# 0009 — AI builds: structured plans, approval, mod-side execution, structure snapshots

**Status:** Accepted (2026-11-17)

**Context.** Players want the AI to build things. The previous private setup gave Claude a shell and the server
console, which ADR-0006 rules out. Builds must be previewed, approved, logged and reversible, and must not freeze
the server.

**Decision.**
- **Plans, not commands.** Claude designs a build as a JSON plan (action schema v1: `fill` with
  replace/hollow/outline, and `setblock`). The plan contains block ids with optional block states, and nothing
  else: no NBT, no other namespaces, no commands. `src/mcai/plan.py` validates it against a denied-block list,
  coordinate bounds, a size limit, a block budget, a distance limit and protected areas.
- **Two tools only.** For builds, Claude Code runs with no built-in tools and one MCP server (stdio, inside the
  worker, no network) offering `validate_plan` and `propose_build`. The worker reads the proposal and the
  gateway re-validates it.
- **Approval.** The gateway sends a preview: the summary, plus a particle outline that only the requester sees.
  Nothing is built until `/ai approve`. Proposals expire (default 15 min). Only the requester, while still
  allowed to build, or an operator of level 4 may approve or undo.
- **Execution in the mod.** The mod parses block strings with the game's own block parser (NBT disabled) and
  places blocks with `setBlockState`, `MCAI_BLOCKS_PER_TICK` at a time on the server thread. Containers are
  emptied before replacement, as `/fill` does, so undo can't duplicate items. No RCON or console commands are
  involved.
- **Undo.** Before placing anything, the mod saves the bounding box as a vanilla structure template
  (`world/generated/mcai/structures/undo/<id>.nbt`). `/ai undo` places it back.
- **Authorization (interim).** Building needs vanilla permission level `AI_BUILD_MIN_OP_LEVEL`, default 4: the
  console and the owner, the only operator by default. Phase 4 RBAC replaces this.

**Consequences.**
- Builds are limited to what the schema can express. Fills and setblocks cover structures well. Entities,
  container contents and signs with text are out of scope for v1.
- Undo restores the area as it was at approval time. Changes players made inside the area after the build are
  lost on undo.
- Restoring a large snapshot happens in a single tick: up to the size limit, about 147,000 blocks with the
  defaults. That can cause a short lag spike on slow hosts.
- Undo snapshots accumulate in the world folder. Pruning them is a Phase 4 management task.
