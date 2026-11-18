#!/usr/bin/env bash
# Build the example packs (Atlantis Lite and Full) into dist/examples/ and pin their manifest hashes in
# examples/catalog.json. Deterministic: the same generator source gives the same files and hashes.
#
#   scripts/build-examples.sh            # both packs
#   scripts/build-examples.sh lite       # only Atlantis Lite
#   scripts/build-examples.sh --check    # rebuild Lite and fail if its content differs from the catalog (CI)
#
# The catalog pins the manifest's SHA-256 (exact release files) and content_sha256, a digest of the uncompressed
# content. Only the latter is compared by --check, because zlib versions compress the same data differently.
#
# Release assets: dist/examples/release/ (flat: <pack>-manifest.json and the .schem sections), uploaded to the
# GitHub release named in catalog.json (tag examples-<version>).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
OUT="$ROOT/dist/examples"
GEN="$ROOT/examples/atlantis/generator"
CHECK=0
PACKS=(lite full)
case "${1:-}" in
  --check) CHECK=1; PACKS=(lite) ;;
  lite|full) PACKS=("$1") ;;
  "") ;;
  *) echo "usage: $0 [lite|full|--check]" >&2; exit 2 ;;
esac
VERSION=$(python3 -c "import json;print(json.load(open('examples/catalog.json'))['version'])")
mkdir -p "$OUT/release"
for p in "${PACKS[@]}"; do
  (cd "$GEN" && python3 -m atlantis.pack "$p" --out "$OUT" --version "$VERSION")
done
python3 - "$OUT" "$CHECK" "${PACKS[@]}" <<'PY'
import hashlib, json, shutil, subprocess, sys
from pathlib import Path
out, check, packs = Path(sys.argv[1]), sys.argv[2] == "1", sys.argv[3:]
gen = Path("examples/atlantis/generator")
cat_path = Path("examples/catalog.json")
cat = json.loads(cat_path.read_text())
bad = []
for p in packs:
    pid = f"atlantis-{p}"
    d = out / pid
    raw = (d / "manifest.json").read_bytes()
    m = json.loads(raw)
    sha = hashlib.sha256(raw).hexdigest()
    digest = subprocess.run([sys.executable, "-m", "atlantis.pack", "digest", str(d)], cwd=gen, check=True,
                            capture_output=True, text=True).stdout.strip()
    e = next(e for e in cat["packs"] if e["id"] == pid)
    if check:
        if e.get("content_sha256") != digest:
            bad.append(f"{pid}: catalog content {str(e.get('content_sha256'))[:12]}..., the source builds {digest[:12]}...")
        continue
    e.update(version=m["version"], manifest_sha256=sha, content_sha256=digest, total_bytes=m["total_bytes"],
             total_blocks=m["total_blocks"])
    rel = out / "release"
    shutil.copy2(d / "manifest.json", rel / f"{pid}-manifest.json")
    for s in m["sections"]:
        shutil.copy2(d / s["file"], rel / s["file"])
    print(f"{pid}: manifest {sha}")
if bad:
    print("examples/catalog.json is out of date (run scripts/build-examples.sh):\n  " + "\n  ".join(bad), file=sys.stderr)
    sys.exit(1)
if not check:
    cat_path.write_text(json.dumps(cat, indent=2) + "\n")
    print(f"updated {cat_path}")
else:
    print("catalog.json matches the generator source")
PY
