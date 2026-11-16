#!/usr/bin/env bash
# Build the server-side Fabric mod inside a pinned Gradle/JDK 21 container (no Java needed on the host)
# and copy the jar to dist/mod/, which compose.yaml mounts into the server as /mods.
# Usage: scripts/build-mod.sh [gradle tasks...]   (default: clean build, which also runs the mod's unit tests)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Loom 1.19 needs a JDK 25 runtime for Gradle; the mod itself is compiled for Java 21 (--release 21).
IMAGE="gradle:9.9.1-jdk25@sha256:39d599d8df1777d73b00b5bf928254ecd121802d518577fe08d41a3cf86aa45f"
tasks=("$@"); [[ ${#tasks[@]} -gt 0 ]] || tasks=(clean build)
# Gradle cache in a folder owned by the calling user (a fresh Docker volume would be root-owned and unwritable).
CACHE="${MCAI_GRADLE_CACHE:-$ROOT/.cache/gradle}"
mkdir -p "$CACHE"
docker run --rm -u "$(id -u):$(id -g)" \
  -e GRADLE_USER_HOME=/cache -v "$CACHE:/cache" \
  -v "$ROOT/minecraft-mod:/src" -v "$ROOT/LICENSE:/LICENSE:ro" -w /src \
  "$IMAGE" gradle --no-daemon --console=plain "${tasks[@]}"
mkdir -p "$ROOT/dist/mod"
find "$ROOT/dist/mod" -maxdepth 1 -name 'mcai-fabric-*.jar' -delete
cp "$ROOT"/minecraft-mod/build/libs/mcai-fabric-*.jar "$ROOT/dist/mod/" 2>/dev/null || true
find "$ROOT/dist/mod" -maxdepth 1 -name 'mcai-fabric-*.jar' ! -name '*-sources.jar' -print
find "$ROOT/dist/mod" -maxdepth 1 -name '*-sources.jar' -delete
