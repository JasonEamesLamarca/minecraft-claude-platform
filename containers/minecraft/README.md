# minecraft service

This service uses the upstream [`itzg/minecraft-server`](https://github.com/itzg/docker-minecraft-server) image,
pinned by tag and digest in [`compose.yaml`](../../compose.yaml). We don't build our own image. Minecraft
and Fabric are downloaded by the image at first start, after the EULA is accepted. They are never redistributed
by this repository.

Hardening applied in `compose.yaml`:

- Runs as the installing user's UID/GID, not root.
- Read-only root filesystem, with `tmpfs` for `/tmp`. Only `/data` is writable.
- All Linux capabilities dropped, plus `no-new-privileges`, a PID limit, and memory and CPU limits.
- Bridge networking: the game port is published, and RCON is on the `internal` network only, with its password
  from a Docker secret file.
- `online-mode`, whitelist enforcement and secure chat profiles are on, and the owner is the only operator (the ops list is reset to the owner at each start).

The server-side `mcai` Fabric mod (Phase 2) will be added through `MC_MODS`, or mounted from a release asset.
