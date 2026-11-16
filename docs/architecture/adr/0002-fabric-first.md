# 0002 — Fabric first; Paper as a later adapter

**Status:** Accepted (2026-11-16)

**Context.** The original brief proposes Paper as the first server type. The maintainer's reference installation,
which this project grows out of, runs Fabric 1.21.1 with server-side mods (performance and content mods) that
have no Paper equivalent. Fabric mods can register server-side commands with Brigadier and need no client mod.

**Decision.** The first integration is a **server-side-only Fabric mod** (`minecraft-mod/`, Java 21, Fabric API,
Fabric Loom), targeting Minecraft 1.21.1 first. It provides the `/ai` commands, permission checks and the event
stream to the gateway. The gateway protocol is server-agnostic. A Paper plugin implementing the same protocol is on
the roadmap as a second adapter, and a reduced-feature RCON + log adapter may support vanilla servers.

**Consequences.**
- Players connect with an unmodified client.
- Fabric has no built-in permission system: the mod uses vanilla permission levels, plus optional
  [fabric-permissions-api](https://github.com/lucko/fabric-permissions-api) nodes when LuckPerms is present, plus
  the platform's own RBAC in the gateway, which is authoritative.
- Mod builds are tied to Minecraft versions. CI builds a matrix of supported versions.
- This deviates from the brief, by decision of the project owner.
