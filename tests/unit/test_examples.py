"""Example packs: manifest schema, .schem validation (including hostile files), download URL allowlist, catalog."""
import gzip
import hashlib
import json
import struct
import sys
from pathlib import Path

import pytest

from mcai.examples import (MAX_NBT_DEPTH, PackError, allowed_url, validate_catalog, validate_manifest,
                           validate_section)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "examples" / "atlantis" / "generator"))
from atlantis import nbtw, schem  # noqa: E402  (the generator's writer: packs made by it must pass)

STONE, GLASS, VOID = "minecraft:stone", "minecraft:glass", "minecraft:structure_void"


def section(states=(STONE, GLASS), size=(4, 3, 5), fill=None, **over):
    w, h, l = size
    cells = fill if fill is not None else [(i % len(states)) for i in range(w * h * l)]
    return schem.encode(w, h, l, list(states), cells, **over)


def raw_schem(body: dict) -> bytes:
    t, p = nbtw._payload({"Schematic": body})
    return gzip.compress(struct.pack(">bH", t, 0) + p)


def base_body(states=(STONE,), size=(2, 2, 2), data=None):
    w, h, l = size
    return {"Version": nbtw.Int(3), "DataVersion": nbtw.Int(3955), "Width": nbtw.Short(w), "Height": nbtw.Short(h),
            "Length": nbtw.Short(l), "Offset": nbtw.IntArray([0, 0, 0]),
            "Blocks": {"Palette": {s: nbtw.Int(i) for i, s in enumerate(states)},
                       "Data": nbtw.ByteArray(data if data is not None else bytes(w * h * l)), "BlockEntities": []}}


def entry(data: bytes, size=(4, 3, 5), offset=(0, 0, 0), blocks=None, name="p-s000.schem"):
    w, h, l = size
    return {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "offset": list(offset),
            "size": list(size), "blocks": w * h * l if blocks is None else blocks}


def manifest(sections, size=(8, 3, 5), origin=(0, 0, 0), **over) -> dict:
    m = {"format": 1, "id": "tiny-town", "version": "1.0.0", "title": "Tiny town", "description": "A test pack.",
         "license": "Apache-2.0", "minecraft": "1.21.1", "data_version": 3955, "anchor": "south-west corner",
         "origin": list(origin), "size": list(size), "sections": sections,
         "total_blocks": sum(s["blocks"] for s in sections), "total_bytes": sum(s["bytes"] for s in sections),
         "points_of_interest": [{"name": "Gate", "at": [1, 1, 1]}]}
    m.update(over)
    return m


def mbytes(m: dict) -> bytes:
    return json.dumps(m).encode()


@pytest.fixture
def good():
    d1, d2 = section(), section(states=(GLASS, STONE))
    secs = [entry(d1), entry(d2, offset=(4, 0, 0), name="p-s001.schem")]
    return manifest(secs), {"p-s000.schem": d1, "p-s001.schem": d2}


def test_good_pack_passes(good):
    m, files = good
    v = validate_manifest(mbytes(m))
    for s in v["sections"]:
        assert validate_section(files[s["file"]], s, v)["blocks"] == 60


def test_structure_void_is_skipped_in_the_count():
    d = section(states=(STONE, VOID), size=(2, 2, 2), fill=[0, 1, 1, 1, 0, 0, 0, 1])
    m = manifest([entry(d, size=(2, 2, 2), blocks=4)], size=(2, 2, 2))
    validate_section(d, m["sections"][0], validate_manifest(mbytes(m)))


def test_large_palette_uses_multi_byte_varints():
    states = [f"minecraft:stone_bricks[a={i}]" for i in range(200)] + [STONE]
    cells = [i % 201 for i in range(4 * 3 * 5)]
    d = section(states=sorted(states), fill=cells)
    m = manifest([entry(d)], size=(4, 3, 5))
    assert validate_section(d, m["sections"][0], validate_manifest(mbytes(m)))["palette"] == 201


# ------------------------------------------------------------------ hostile manifests
@pytest.mark.parametrize("change, why", [
    (lambda m: m.update(format=2), "format"),
    (lambda m: m.update(id="../etc"), "id"),
    (lambda m: m.update(version="latest"), "version"),
    (lambda m: m.update(extra=1), "unknown key"),
    (lambda m: m.pop("sections"), "missing"),
    (lambda m: m.update(title="Hi‮evil"), "control"),
    (lambda m: m.update(title="§cRed"), "control"),
    (lambda m: m.update(description="x" * 1001), "at most"),
    (lambda m: m["sections"][0].update(file="../../gateway.db"), "plain name"),
    (lambda m: m["sections"][0].update(file="/etc/passwd"), "plain name"),
    (lambda m: m["sections"][0].update(file="sub/dir.schem"), "plain name"),
    (lambda m: m["sections"][0].update(file="a.schem\x00.txt"), "plain name"),
    (lambda m: m["sections"][0].update(file="script.py"), "plain name"),
    (lambda m: m["sections"][1].update(file="p-s000.schem"), "twice"),
    (lambda m: m["sections"][0].update(sha256="ABC"), "sha256"),
    (lambda m: m["sections"][0].update(bytes=-1), "bytes"),
    (lambda m: m["sections"][1].update(offset=[2, 0, 0]), "overlaps"),
    (lambda m: m["sections"][1].update(offset=[6, 0, 0]), "outside"),
    (lambda m: m["sections"][0].update(size=[300, 3, 5]), "size"),
    (lambda m: m["sections"][0].update(blocks=10**9), "blocks"),
    (lambda m: m["sections"][0].update(extra="x"), "unknown key"),
    (lambda m: m.update(total_blocks=1), "don't match"),
    (lambda m: m.update(sections=[]), "1 to"),
    (lambda m: m.update(size=[100_000, 3, 5]), "size"),
    (lambda m: m.update(points_of_interest=[{"name": "Far", "at": [999, 0, 0]}]), "points_of_interest"),
    (lambda m: m.update(points_of_interest=[{"name": "x", "at": [0, 0, 0]}] * 40), "at most"),
    (lambda m: m.update(minecraft="latest"), "minecraft"),
    (lambda m: m.update(recommended={"floor_y": 9999}), "floor_y"),
])
def test_hostile_manifests_rejected(good, change, why):
    m, _ = good
    change(m)
    with pytest.raises(PackError, match=why):
        validate_manifest(mbytes(m))


@pytest.mark.parametrize("raw", [b"", b"[]", b"not json", b"\xff\xfe", b'{"format": 1}', b" " * 300_000])
def test_garbage_manifests_rejected(raw):
    with pytest.raises(PackError):
        validate_manifest(raw)


# ------------------------------------------------------------------ hostile sections
def check(data: bytes, size=(2, 2, 2), blocks=None):
    m = manifest([entry(data, size=size, blocks=blocks)], size=size)
    validate_section(data, m["sections"][0], validate_manifest(mbytes(m)))


def test_hash_and_size_must_match(good):
    m, files = good
    v = validate_manifest(mbytes(m))
    with pytest.raises(PackError, match="SHA-256"):
        validate_section(files["p-s000.schem"] + b"x", v["sections"][0], v)
    with pytest.raises(PackError, match="SHA-256"):
        validate_section(files["p-s001.schem"], v["sections"][0], v)


@pytest.mark.parametrize("block", ["minecraft:command_block", "minecraft:repeating_command_block[facing=up]",
                                   "minecraft:structure_block", "minecraft:jigsaw", "minecraft:spawner",
                                   "minecraft:tnt", "minecraft:lava", "minecraft:bedrock", "minecraft:barrier",
                                   "minecraft:end_portal", "minecraft:nether_portal", "evil:block",
                                   "minecraft:chest{Items:[]}", "minecraft:Stone", "stone"])
def test_denied_or_malformed_blocks(block):
    with pytest.raises(PackError, match="not allowed"):
        check(raw_schem(base_body(states=(STONE, block))))


@pytest.mark.parametrize("edit, why", [
    (lambda b: b["Blocks"].update(BlockEntities=[{"Id": "minecraft:command_block", "Pos": nbtw.IntArray([0, 0, 0]),
                                                  "Data": {"Command": "op @a"}}]), "block entity"),
    (lambda b: b.update(Entities=[{"Id": "minecraft:creeper"}]), "entities"),
    (lambda b: b.update(Version=nbtw.Int(2)), "version 3"),
    (lambda b: b.update(DataVersion=nbtw.Int(1)), "DataVersion"),
    (lambda b: b.update(Width=nbtw.Short(3)), "dimensions"),
    (lambda b: b.update(Script="rm -rf /"), "unexpected"),
    (lambda b: b["Blocks"].update(Data=nbtw.ByteArray(bytes(7))), "wrong length"),
    (lambda b: b["Blocks"].update(Data=nbtw.ByteArray(bytes(9))), "wrong length"),
    (lambda b: b["Blocks"].update(Data=nbtw.ByteArray(bytes([5] * 8))), "palette entry"),
    (lambda b: b["Blocks"].update(Data=nbtw.ByteArray(bytes([0x80] * 8))), "invalid block data|wrong length"),
    (lambda b: b["Blocks"].update(Palette={STONE: nbtw.Int(3)}), "0..n-1"),
    (lambda b: b["Blocks"].update(Palette={}), "palette"),
    (lambda b: b["Blocks"].pop("Data"), "data missing"),
])
def test_hostile_schematics(edit, why):
    body = base_body()
    edit(body)
    with pytest.raises(PackError, match=why):
        check(raw_schem(body))


def test_block_count_must_match_manifest():
    with pytest.raises(PackError, match="block count"):
        check(raw_schem(base_body()), blocks=7)


def test_gzip_bomb_rejected():
    bomb = gzip.compress(b"\x00" * (50 * 1024 * 1024))       # 50 MB of zeros for a 2x2x2 section
    with pytest.raises(PackError, match="expands"):
        check(bomb)


def test_not_gzip_and_damaged_gzip():
    with pytest.raises(PackError, match="gzip"):
        check(b"PK\x03\x04 zip file")
    with pytest.raises(PackError, match="gzip"):
        check(gzip.compress(b"x" * 100)[:10] + b"\xff" * 30)          # reserved deflate block type


def test_nbt_depth_bomb_rejected():
    deep = {}
    cur = deep
    for _ in range(MAX_NBT_DEPTH + 5):
        cur["x"] = {}
        cur = cur["x"]
    body = base_body()
    body["Metadata"] = deep
    with pytest.raises(PackError, match="deeply"):
        check(raw_schem(body))


def test_nbt_list_length_lies_rejected():
    t, p = nbtw._payload({"Schematic": base_body()})
    raw = bytearray(struct.pack(">bH", t, 0) + p)
    i = raw.find(b"BlockEntities") + len(b"BlockEntities")
    raw[i + 1:i + 5] = struct.pack(">i", 2_000_000_000)              # a list claiming two billion entries
    with pytest.raises(PackError, match="invalid NBT list"):
        check(gzip.compress(bytes(raw)))


def test_trailing_data_rejected():
    t, p = nbtw._payload({"Schematic": base_body()})
    with pytest.raises(PackError, match="trailing"):
        check(gzip.compress(struct.pack(">bH", t, 0) + p + b"\x00\x01"))


# ------------------------------------------------------------------ download locations
@pytest.mark.parametrize("url", [
    "https://github.com/owner/minecraft-claude-platform/releases/download/examples-v1/atlantis-lite.json",
])
def test_allowed_urls(url):
    assert allowed_url(url)


@pytest.mark.parametrize("url", [
    "http://github.com/o/r/releases/download/t/f.json",               # not https
    "https://evil.example/o/r/releases/download/t/f.json",            # other host
    "https://github.com.evil.example/o/r/releases/download/t/f.json",
    "https://github.com/o/r/archive/main.zip",                        # not a release asset
    "https://github.com/o/r/releases/download/t/../../x",
    "https://github.com/o/r/releases/download/../f.json",
    "https://user:pw@github.com/o/r/releases/download/t/f.json",
    "https://github.com:8443/o/r/releases/download/t/f.json",
    "https://github.com/o/r/releases/download/t/f.json?x=1",
    "file:///etc/passwd", "javascript:alert(1)", "",
])
def test_refused_urls(url):
    assert not allowed_url(url)


def test_redirect_hosts_only_after_redirect():
    u = "https://objects.githubusercontent.com/github-production-release-asset/1/2?sig=x"
    assert allowed_url(u, redirect=True) and not allowed_url(u)
    assert not allowed_url("https://evil.example/x", redirect=True)


def test_catalog_drops_bad_entries():
    ok = {"id": "atlantis-lite", "version": "1.0.0", "title": "Atlantis Lite", "description": "d",
          "manifest_url": "https://github.com/o/r/releases/download/t/atlantis-lite-manifest.json",
          "manifest_sha256": "a" * 64}
    bad_url = dict(ok, id="evil", manifest_url="https://evil.example/m.json")
    bad_sha = dict(ok, id="other", manifest_sha256="zz")
    assert [e["id"] for e in validate_catalog({"packs": [ok, bad_url, bad_sha, "x"]})] == ["atlantis-lite"]
    assert validate_catalog([]) == [] and validate_catalog({"packs": "x"}) == []


def test_shipped_catalog_is_valid():
    path = Path(__file__).resolve().parents[2] / "examples" / "catalog.json"
    data = json.loads(path.read_text())
    assert len(validate_catalog(data)) == len(data["packs"]) >= 1
