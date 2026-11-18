"""Example packs (ADR-0012): verified storage and installation of downloadable builds such as Atlantis.

A pack is a manifest (JSON) plus Sponge v3 `.schem` sections. Nothing in a pack is ever executed: sections are
block data only, checked here against a strict schema and a block deny-list, and placed by the mod through the
game's own block API.

Trust chain:
  * The platform ships `examples/catalog.json`: pack id, version, the manifest's download URL (GitHub releases of
    this project only) and the manifest's SHA-256. The `mcai` client downloads from those URLs only.
  * The manifest lists every section file with its SHA-256 and size. The gateway checks each upload against it,
    then parses the file with hard limits (decompressed size, NBT depth, palette size, dimensions) and rejects
    block entities, entities and denied blocks (command blocks, structure blocks, spawners, TNT, lava, bedrock...).
  * A pack whose manifest hash matches the catalog is "official"; others (built locally) are labelled unofficial
    and pass the same checks.

Installs: the admin chooses where the pack's anchor goes. The gateway checks world height, protected areas and
overlap with other installs, then tells the mod to place it. The mod saves a structure snapshot of every chunk
piece before changing it, changes only cells that differ, and reports progress. Placing again repairs; rollback
restores the snapshots. One install job runs at a time.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import re
import shutil
import struct
import threading
import time
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlparse

from .plan import BLOCK_RE, DENIED_BLOCKS

log = logging.getLogger("mcai.examples")

FORMAT = 1
PACK_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
FILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}\.schem$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
INSTALL_RE = re.compile(r"^[0-9a-f]{8}$")
DIM_RE = re.compile(r"^[a-z0-9_.-]{1,32}:[a-z0-9_./-]{1,64}$")
MC_RE = re.compile(r"^1\.\d{1,2}(\.\d{1,2})?$")
SKIP = "minecraft:structure_void"
# packs may use invisible light blocks and structure_void ("leave the world as it is"); everything else the AI may
# not place is denied here too
DENIED_PACK_BLOCKS = frozenset(DENIED_BLOCKS - {"light", "structure_void"})

MAX_MANIFEST_BYTES = 256 * 1024
MAX_SECTIONS = 256
MAX_SECTION_SIZE = (256, 384, 256)
MAX_PACK_SIZE = (4096, 384, 4096)
MAX_PALETTE = 4096
MAX_NBT_DEPTH = 8
MAX_NBT_TAGS = 20_000
MAX_POI = 32
WORLD_Y = (-64, 319)

# Download sources the mcai client may use (also checked for every catalog entry): this project's GitHub releases.
ALLOWED_DOWNLOAD_HOSTS = ("github.com",)
ALLOWED_REDIRECT_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")
RELEASE_PATH_RE = re.compile(r"^/[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}/releases/download/[A-Za-z0-9._+-]{1,100}/"
                             r"[A-Za-z0-9._-]{1,100}$")

PACK_STATES = ("uploading", "ready", "invalid")
INSTALL_STATES = ("queued", "placing", "placed", "failed", "interrupted", "rolling_back", "rolled_back",
                  "rollback_failed")
BUSY = ("queued", "placing", "rolling_back")
OCCUPYING = ("queued", "placing", "placed", "failed", "interrupted", "rolling_back", "rollback_failed")


class PackError(ValueError):
    pass


class ExampleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ====================================================================== validation
def allowed_url(url: str, redirect: bool = False) -> bool:
    """https, this project's release downloads (or GitHub's asset hosts after a redirect)."""
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password or u.port not in (None, 443):
        return False
    if any(seg in (".", "..") for seg in u.path.split("/")):
        return False
    if redirect:
        return u.hostname in ALLOWED_REDIRECT_HOSTS or (u.hostname in ALLOWED_DOWNLOAD_HOSTS
                                                         and bool(RELEASE_PATH_RE.match(u.path)))
    return u.hostname in ALLOWED_DOWNLOAD_HOSTS and bool(RELEASE_PATH_RE.match(u.path)) and not u.query


def _text(v, where: str, max_len: int) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > max_len:
        raise PackError(f"{where} must be text of at most {max_len} characters")
    if any(ord(c) < 32 or 0x7F <= ord(c) < 0xA0 or c in "​‎‏‪‫‬‭‮⁦"
           "⁧⁨⁩§" for c in v):
        raise PackError(f"{where} contains control or formatting characters")
    return v


def _int(v, where: str, lo: int, hi: int) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise PackError(f"{where} must be a whole number between {lo} and {hi}")
    return v


def _vec(v, where: str, lo, hi) -> list:
    if not isinstance(v, list) or len(v) != 3:
        raise PackError(f"{where} must be [x, y, z]")
    lo = lo if isinstance(lo, (list, tuple)) else (lo, lo, lo)
    hi = hi if isinstance(hi, (list, tuple)) else (hi, hi, hi)
    return [_int(c, f"{where}[{i}]", lo[i], hi[i]) for i, c in enumerate(v)]


def _keys(obj, required: set, optional: set, where: str) -> None:
    if not isinstance(obj, dict):
        raise PackError(f"{where} must be an object")
    unknown = set(obj) - required - optional
    if unknown:
        raise PackError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    missing = required - set(obj)
    if missing:
        raise PackError(f"{where}: missing {', '.join(sorted(missing))}")


def validate_manifest(raw: bytes) -> dict:
    """Parse and check a manifest. Raises PackError with a reason."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_MANIFEST_BYTES:
        raise PackError(f"the manifest must be at most {MAX_MANIFEST_BYTES // 1024} KB")
    try:
        m = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PackError("the manifest is not valid JSON") from None
    _keys(m, {"format", "id", "version", "title", "description", "license", "minecraft", "data_version", "anchor",
              "origin", "size", "sections", "total_blocks", "total_bytes"},
          {"recommended", "points_of_interest", "blocks_used"}, "manifest")
    if m["format"] != FORMAT:
        raise PackError(f"unsupported manifest format {m['format']!r} (this platform reads format {FORMAT})")
    if not isinstance(m["id"], str) or not PACK_RE.match(m["id"]):
        raise PackError("manifest id must be lower-case letters, digits and dashes")
    if not isinstance(m["version"], str) or not VERSION_RE.match(m["version"]):
        raise PackError("manifest version must look like 1.0.0")
    _text(m["title"], "title", 80)
    _text(m["description"], "description", 1000)
    _text(m["license"], "license", 40)
    _text(m["anchor"], "anchor", 200)
    if not isinstance(m["minecraft"], str) or not MC_RE.match(m["minecraft"]):
        raise PackError("minecraft must be a version like 1.21.1")
    _int(m["data_version"], "data_version", 1, 100_000)
    size = _vec(m["size"], "size", 1, MAX_PACK_SIZE)
    origin = _vec(m["origin"], "origin", (-MAX_PACK_SIZE[0], -MAX_PACK_SIZE[1], -MAX_PACK_SIZE[2]), MAX_PACK_SIZE)
    hi = [origin[i] + size[i] for i in range(3)]          # exclusive
    secs = m["sections"]
    if not isinstance(secs, list) or not 1 <= len(secs) <= MAX_SECTIONS:
        raise PackError(f"sections must be a list of 1 to {MAX_SECTIONS} entries")
    names, boxes, total_blocks, total_bytes = set(), [], 0, 0
    for i, s in enumerate(secs):
        w = f"sections[{i}]"
        _keys(s, {"file", "sha256", "bytes", "offset", "size", "blocks"}, set(), w)
        if not isinstance(s["file"], str) or not FILE_RE.match(s["file"]):
            raise PackError(f"{w}.file must be a plain name like part-1.schem (no folders)")
        if s["file"] in names:
            raise PackError(f"{w}.file {s['file']} is listed twice")
        names.add(s["file"])
        if not isinstance(s["sha256"], str) or not SHA_RE.match(s["sha256"]):
            raise PackError(f"{w}.sha256 must be 64 lower-case hex digits")
        _int(s["bytes"], f"{w}.bytes", 1, 1 << 30)
        sz = _vec(s["size"], f"{w}.size", 1, MAX_SECTION_SIZE)
        off = _vec(s["offset"], f"{w}.offset", origin, hi)
        if any(off[k] + sz[k] > hi[k] for k in range(3)):
            raise PackError(f"{w} reaches outside the pack's size")
        n = sz[0] * sz[1] * sz[2]
        _int(s["blocks"], f"{w}.blocks", 0, n)
        box = (off, [off[k] + sz[k] for k in range(3)])
        for j, (a, b) in enumerate(boxes):
            if all(box[0][k] < b[k] and a[k] < box[1][k] for k in range(3)):
                raise PackError(f"{w} overlaps sections[{j}]")
        boxes.append(box)
        total_blocks += s["blocks"]
        total_bytes += s["bytes"]
    if m["total_blocks"] != total_blocks or m["total_bytes"] != total_bytes:
        raise PackError("total_blocks / total_bytes don't match the sections")
    if "recommended" in m:
        r = m["recommended"]
        _keys(r, set(), {"floor_y", "note"}, "recommended")
        if "floor_y" in r:
            _int(r["floor_y"], "recommended.floor_y", *WORLD_Y)
        if "note" in r:
            _text(r["note"], "recommended.note", 400)
    for i, p in enumerate(m.get("points_of_interest", [])[:MAX_POI + 1]):
        if i == MAX_POI:
            raise PackError(f"at most {MAX_POI} points of interest")
        _keys(p, {"name", "at"}, set(), f"points_of_interest[{i}]")
        _text(p["name"], f"points_of_interest[{i}].name", 80)
        _vec(p["at"], f"points_of_interest[{i}].at", origin, hi)
    if "blocks_used" in m and (not isinstance(m["blocks_used"], list) or len(m["blocks_used"]) > MAX_PALETTE
                               or not all(isinstance(b, str) and len(b) <= 300 for b in m["blocks_used"])):
        raise PackError("blocks_used must be a list of block states")
    return m


def _gunzip(data: bytes, limit: int) -> bytes:
    if data[:2] != b"\x1f\x8b":
        raise PackError("not a gzip file")
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(data, limit + 1)
    except zlib.error:
        raise PackError("damaged gzip data") from None
    if len(out) > limit or d.unconsumed_tail:
        raise PackError("the file expands to more data than its dimensions allow")
    return out


class _Nbt:
    """Bounded NBT reader: depth, tag count and lengths are limited; nothing is trusted."""

    def __init__(self, buf: bytes):
        self.b, self.p, self.tags = buf, 0, 0

    def need(self, n: int) -> None:
        if n < 0 or self.p + n > len(self.b):
            raise PackError("truncated NBT data")

    def u(self, fmt: str):
        size = struct.calcsize(fmt)
        self.need(size)
        v = struct.unpack_from(fmt, self.b, self.p)
        self.p += size
        return v[0]

    def string(self) -> str:
        n = self.u(">H")
        self.need(n)
        s = self.b[self.p:self.p + n]
        self.p += n
        try:
            return s.decode("utf-8")
        except UnicodeDecodeError:
            raise PackError("invalid text in NBT data") from None

    def value(self, t: int, depth: int):
        self.tags += 1
        if self.tags > MAX_NBT_TAGS:
            raise PackError("too many NBT tags")
        if depth > MAX_NBT_DEPTH:
            raise PackError("NBT nested too deeply")
        if t == 1: return self.u(">b")
        if t == 2: return self.u(">h")
        if t == 3: return self.u(">i")
        if t == 4: return self.u(">q")
        if t == 5: return self.u(">f")
        if t == 6: return self.u(">d")
        if t == 7:
            n = self.u(">i")
            self.need(n)
            v = self.b[self.p:self.p + n]
            self.p += n
            return v
        if t == 8: return self.string()
        if t == 9:
            et, n = self.u(">b"), self.u(">i")
            if n < 0 or (n > 0 and et == 0) or n > len(self.b) - self.p:
                raise PackError("invalid NBT list")
            return [self.value(et, depth + 1) for _ in range(n)]
        if t == 10:
            out = {}
            while True:
                ct = self.u(">b")
                if ct == 0:
                    return out
                name = self.string()
                if name in out:
                    raise PackError("duplicate NBT key")
                out[name] = self.value(ct, depth + 1)
        if t in (11, 12):
            n = self.u(">i")
            width = 4 if t == 11 else 8
            if n < 0 or n * width > len(self.b) - self.p:
                raise PackError("invalid NBT array")
            v = list(struct.unpack_from(f">{n}{'i' if t == 11 else 'q'}", self.b, self.p))
            self.p += n * width
            return v
        raise PackError(f"unknown NBT tag type {t}")

    def root(self) -> dict:
        if self.u(">b") != 10:
            raise PackError("the file doesn't start with an NBT compound")
        self.string()
        v = self.value(10, 0)
        if self.p != len(self.b):
            raise PackError("trailing data after the NBT root")
        return v


def _block_ok(state: str) -> bool:
    if not isinstance(state, str) or not BLOCK_RE.match(state):
        return False
    return state.split("[", 1)[0][len("minecraft:"):] not in DENIED_PACK_BLOCKS


def validate_section(data: bytes, sec: dict, manifest: dict) -> dict:
    """Check one .schem section against its manifest entry. Returns {"palette": n, "blocks": placed}."""
    if len(data) != sec["bytes"] or hashlib.sha256(data).hexdigest() != sec["sha256"]:
        raise PackError(f"{sec['file']}: size or SHA-256 doesn't match the manifest")
    w, h, l = sec["size"]
    cells = w * h * l
    raw = _gunzip(data, cells * 3 + 1024 * 1024)
    root = _Nbt(raw).root()
    if set(root) != {"Schematic"} or not isinstance(root["Schematic"], dict):
        raise PackError(f"{sec['file']}: not a Sponge v3 schematic (expected a 'Schematic' compound)")
    s = root["Schematic"]
    allowed = {"Version", "DataVersion", "Metadata", "Width", "Height", "Length", "Offset", "Blocks", "Biomes",
               "Entities"}
    if set(s) - allowed:
        raise PackError(f"{sec['file']}: unexpected field(s) {', '.join(sorted(set(s) - allowed))}")
    if s.get("Version") != 3:
        raise PackError(f"{sec['file']}: only Sponge schematic version 3 is supported")
    if s.get("DataVersion") != manifest["data_version"]:
        raise PackError(f"{sec['file']}: DataVersion differs from the manifest")
    dims = [s.get(k) for k in ("Width", "Height", "Length")]
    if not all(isinstance(d, int) for d in dims) or [d & 0xFFFF for d in dims] != [w, h, l]:
        raise PackError(f"{sec['file']}: dimensions differ from the manifest")
    if s.get("Entities"):
        raise PackError(f"{sec['file']}: entities are not allowed in example packs")
    b = s.get("Blocks")
    if not isinstance(b, dict) or set(b) - {"Palette", "Data", "BlockEntities"}:
        raise PackError(f"{sec['file']}: invalid Blocks container")
    if b.get("BlockEntities"):
        raise PackError(f"{sec['file']}: block entity data (chest contents, signs, commands...) is not allowed")
    pal = b.get("Palette")
    if not isinstance(pal, dict) or not 1 <= len(pal) <= MAX_PALETTE:
        raise PackError(f"{sec['file']}: the palette must have 1 to {MAX_PALETTE} entries")
    if sorted(pal.values()) != list(range(len(pal))):
        raise PackError(f"{sec['file']}: palette indexes must be 0..n-1")
    bad = sorted(k for k in pal if not _block_ok(k))
    if bad:
        raise PackError(f"{sec['file']}: blocks not allowed in example packs: {', '.join(bad[:5])}")
    data_ = b.get("Data")
    if not isinstance(data_, (bytes, bytearray)):
        raise PackError(f"{sec['file']}: block data missing")
    n = len(pal)
    skip = pal.get(SKIP, -1)
    if len(data_) == cells and max(data_, default=0) < min(n, 128):
        placed = cells - (data_.count(bytes([skip])) if 0 <= skip < 128 else 0)
    else:
        count, v, shift, placed = 0, 0, 0, 0
        for byte in data_:
            v |= (byte & 0x7F) << shift
            if byte & 0x80:
                shift += 7
                if shift > 21:
                    raise PackError(f"{sec['file']}: invalid block data")
                continue
            if v >= n:
                raise PackError(f"{sec['file']}: block data refers to a palette entry that doesn't exist")
            count += 1
            placed += v != skip
            v = shift = 0
        if count != cells or shift:
            raise PackError(f"{sec['file']}: block data has the wrong length")
    if placed != sec["blocks"]:
        raise PackError(f"{sec['file']}: block count differs from the manifest")
    return {"palette": n, "blocks": placed}


def validate_catalog(obj) -> list[dict]:
    """The catalog shipped with the platform. Entries with a bad URL are dropped (and logged)."""
    out = []
    packs = obj.get("packs") if isinstance(obj, dict) else None
    for e in packs if isinstance(packs, list) else []:
        try:
            _keys(e, {"id", "version", "title", "description", "manifest_url", "manifest_sha256"},
                  {"total_bytes", "total_blocks", "release", "content_sha256"}, "catalog entry")
            if not PACK_RE.match(str(e["id"])) or not VERSION_RE.match(str(e["version"])):
                raise PackError("bad id or version")
            _text(e["title"], "title", 80)
            _text(e["description"], "description", 1000)
            if not allowed_url(e["manifest_url"]):
                raise PackError("manifest_url is not an allowed download location")
            if not SHA_RE.match(str(e["manifest_sha256"])):
                raise PackError("manifest_sha256 must be 64 hex digits")
            out.append(dict(e))
        except PackError as err:
            log.error("catalog entry %r ignored: %s", e.get("id") if isinstance(e, dict) else e, err)
    return out


# ====================================================================== service
def _fmt_blocks(n: int) -> str:
    return f"{n / 1e6:.1f} million" if n >= 1e6 else f"{n:,}"


class Examples:
    def __init__(self, gw, root: Path, catalog_file: Path | None, blocks_per_tick: int = 4000,
                 max_file_bytes: int = 8 * 1024 * 1024, max_install_blocks: int = 300_000_000,
                 clock=time.monotonic):
        self.gw, self.store = gw, gw.store
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_file = catalog_file
        self.blocks_per_tick = blocks_per_tick
        self.max_file_bytes = max_file_bytes
        self.max_install_blocks = max_install_blocks
        self.clock = clock
        self.lock = threading.Lock()
        self.sent_at: dict[str, float] = {}           # install id -> when the current job was sent (monotonic)
        self.hb_misses: dict[str, int] = {}
        for row in self.store.example_installs(BUSY):    # the gateway restarted while the mod was working
            self.store.example_install_update(row["id"], BUSY, status="interrupted" if row["status"] != "rolling_back"
                                              else "rollback_failed", job=None, error="gateway restarted")

    # ------------------------------------------------------------ catalog and packs
    def catalog(self) -> list[dict]:
        if not self.catalog_file or not Path(self.catalog_file).is_file():
            return []
        try:
            return validate_catalog(json.loads(Path(self.catalog_file).read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            log.error("catalog unreadable: %s", e)
            return []

    def _dir(self, pid: str, version: str) -> Path:
        if not PACK_RE.match(pid) or not VERSION_RE.match(version):
            raise ExampleError("usage", "Invalid pack id or version.")
        return self.root / pid / version

    def pack(self, pid: str, version: str | None = None, ready: bool = False):
        rows = [r for r in self.store.example_packs(pid) if (version is None or r["version"] == version)
                and (not ready or r["status"] == "ready")]
        if not rows:
            what = f"{pid} {version}" if version else pid
            raise ExampleError("not_found", f"No {'ready ' if ready else ''}pack '{what}' on the server. "
                                            f"Get it with 'mcai examples download {pid}'.")
        return rows[0]

    @staticmethod
    def manifest(row) -> dict:
        return json.loads(row["manifest"])

    def upload(self, actor, pid: str, version: str, name: str, b64: str) -> str:
        """Store one file of a pack (manifest.json first). Files are checked before they are kept."""
        d = self._dir(pid, version)
        try:
            data = base64.b64decode(b64.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError):
            raise ExampleError("usage", "The upload is not valid base64 (use 'mcai examples download').") from None
        if len(data) > max(self.max_file_bytes, MAX_MANIFEST_BYTES):
            raise ExampleError("invalid", f"File larger than {self.max_file_bytes // (1024 * 1024)} MB.")
        row = self.store.example_pack(pid, version)
        if name == "manifest.json":
            if row is not None and row["status"] == "ready":
                raise ExampleError("exists", f"{pid} {version} is already on the server.")
            try:
                m = validate_manifest(data)
            except PackError as e:
                self.store.audit("examples.rejected", pack=pid, version=version, file=name, reason=str(e),
                                 by=actor.label)
                raise ExampleError("invalid", f"Manifest rejected: {e}") from None
            if (m["id"], m["version"]) != (pid, version):
                raise ExampleError("invalid", f"The manifest is for {m['id']} {m['version']}, not {pid} {version}.")
            if any(s["bytes"] > self.max_file_bytes for s in m["sections"]):
                raise ExampleError("invalid", "A section is larger than EXAMPLES_MAX_FILE_MB allows.")
            sha = hashlib.sha256(data).hexdigest()
            official = any(e["id"] == pid and e["version"] == version and e["manifest_sha256"] == sha
                           for e in self.catalog())
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
            (d / "manifest.json").write_bytes(data)
            self.store.example_pack_put(pid, version, actor.label, status="uploading", manifest=json.dumps(m),
                                        files="{}", official=int(official), error=None)
            self.store.audit("examples.manifest", pack=pid, version=version, official=official, by=actor.label,
                             sections=len(m["sections"]), sha256=sha)
            return (f"Manifest of {m['title']} {version} accepted ({'official' if official else 'unofficial'}); "
                    f"{len(m['sections'])} section file(s) to upload.")
        if row is None or row["status"] == "invalid":
            raise ExampleError("usage", "Upload manifest.json first.")
        m = self.manifest(row)
        sec = next((s for s in m["sections"] if s["file"] == name), None)
        if sec is None:
            raise ExampleError("invalid", f"{name} is not listed in the manifest.")
        try:
            stats = validate_section(data, sec, m)
        except PackError as e:
            self.store.example_pack_put(pid, version, actor.label, status="invalid", error=str(e)[:300])
            self.store.audit("examples.rejected", pack=pid, version=version, file=name, reason=str(e), by=actor.label)
            raise ExampleError("invalid", f"Rejected: {e}") from None
        tmp = d / (name + ".part")
        tmp.write_bytes(data)
        tmp.replace(d / name)
        files = json.loads(row["files"])
        files[name] = "ok"
        done = len(files) == len(m["sections"])
        self.store.example_pack_put(pid, version, actor.label, files=json.dumps(files),
                                    status="ready" if done else "uploading")
        if done:
            self.store.audit("examples.ready", pack=pid, version=version, by=actor.label, official=bool(row["official"]),
                             blocks=m["total_blocks"])
            return f"{name}: ok ({stats['palette']} block types). {m['title']} {version} is ready to install."
        return f"{name}: ok ({len(files)}/{len(m['sections'])})."

    def delete(self, actor, pid: str, version: str) -> str:
        row = self.pack(pid, version)
        if any(r["version"] == version for r in self.store.example_installs(BUSY, pack=pid)):
            raise ExampleError("busy", "An install of this pack is running.")
        d = self._dir(pid, version)
        if d.exists():
            shutil.rmtree(d)
        self.store.example_pack_delete(pid, version)
        self.store.audit("examples.deleted", pack=pid, version=row["version"], by=actor.label)
        return (f"Deleted {pid} {version} from the server. Installed copies stay in the world; repair and "
                f"rollback still work for them only after you download it again.")

    # ------------------------------------------------------------ installs
    def _bbox(self, m: dict, anchor: list) -> dict:
        lo = [anchor[i] + m["origin"][i] for i in range(3)]
        return {"from": lo, "to": [lo[i] + m["size"][i] - 1 for i in range(3)]}

    @staticmethod
    def _overlap(a: dict, b_from, b_to) -> bool:
        return all(a["from"][i] <= b_to[i] and b_from[i] <= a["to"][i] for i in range(3))

    def check_site(self, m: dict, dimension: str, anchor: list, ignore: str | None = None) -> dict:
        if not DIM_RE.match(dimension):
            raise ExampleError("usage", "Invalid dimension.")
        box = self._bbox(m, anchor)
        if box["from"][1] < WORLD_Y[0] or box["to"][1] > WORLD_Y[1]:
            lo = WORLD_Y[0] - m["origin"][1]
            hi = WORLD_Y[1] - m["origin"][1] - m["size"][1] + 1
            raise ExampleError("invalid", f"The pack would reach outside the world's height: y must be between "
                                          f"{lo} and {hi} for this pack.")
        if m["total_blocks"] > self.max_install_blocks:
            raise ExampleError("limited", f"This pack has {_fmt_blocks(m['total_blocks'])} blocks; "
                                          f"EXAMPLES_MAX_INSTALL_BLOCKS allows {_fmt_blocks(self.max_install_blocks)}.")
        for name, dim, a, b in self.gw.protected_areas():
            if dim in ("*", dimension) and self._overlap(box, a, b):
                raise ExampleError("protected", f"The pack would overlap the protected area '{name}'.")
        for r in self.store.example_installs(OCCUPYING):
            if r["id"] != ignore and r["dimension"] == dimension:
                ob = json.loads(r["bbox"])
                if self._overlap(box, ob["from"], ob["to"]):
                    raise ExampleError("overlap", f"The pack would overlap install {r['id']} ({r['pack']}); roll "
                                                  f"that back first or choose another place.")
        return box

    def preview(self, pid: str, version: str | None, dimension: str, anchor: list) -> dict:
        row = self.pack(pid, version, ready=True)
        m = self.manifest(row)
        box = self.check_site(m, dimension, anchor)
        chunks = ((box["to"][0] >> 4) - (box["from"][0] >> 4) + 1) * ((box["to"][2] >> 4) - (box["from"][2] >> 4) + 1)
        secs = m["total_blocks"] / (self.blocks_per_tick * 20.0)
        return {"pack": pid, "version": row["version"], "title": m["title"], "official": bool(row["official"]),
                "dimension": dimension, "anchor": anchor, "bbox": box, "blocks": m["total_blocks"], "chunks": chunks,
                "estimate_s": int(secs * 1.5) + 5, "walk_at": [anchor[0], anchor[1] + 5, anchor[2]]}

    def _busy(self) -> None:
        busy = self.store.example_installs(BUSY)
        if busy:
            raise ExampleError("busy", f"Install {busy[0]['id']} is {busy[0]['status']}; wait for it to finish or "
                                       f"cancel it ('mcai examples cancel {busy[0]['id']}').")
        if not self.gw.mod_online():
            raise ExampleError("unavailable", "The Minecraft server is not connected (no heartbeat from the mod).")

    def _send(self, kind: str, row, m: dict | None = None) -> None:
        ev = {"id": uuid.uuid4().hex[:8], "type": kind, "install_id": row["id"], "dimension": row["dimension"],
              "anchor": json.loads(row["anchor"])}
        if kind == "example_place":
            ev["sections"] = [{"file": s["file"], "sha256": s["sha256"], "bytes": s["bytes"], "offset": s["offset"],
                               "size": s["size"]} for s in m["sections"]]
            ev["blocks_per_tick"] = self.blocks_per_tick
        with self.gw.cond:
            self.gw.events.append(ev)
            self.gw.cond.notify_all()
        self.sent_at[row["id"]] = self.clock()
        self.hb_misses.pop(row["id"], None)

    def install(self, actor, pid: str, version: str | None, dimension: str, anchor: list) -> dict:
        with self.lock:
            self._busy()
            row = self.pack(pid, version, ready=True)
            m = self.manifest(row)
            box = self.check_site(m, dimension, anchor)
            iid = uuid.uuid4().hex[:8]
            self.store.example_install_create(iid, pid, row["version"], dimension, anchor, box, actor.label)
            self.store.example_install_update(iid, status="placing", job="place", snapshots="present",
                                              progress=json.dumps({"done": 0, "total": m["total_blocks"]}))
            self._send("example_place", self.store.example_install(iid), m)
        self.store.audit("examples.install_started", install=iid, pack=pid, version=row["version"],
                         dimension=dimension, anchor=anchor, bbox=box, blocks=m["total_blocks"], by=actor.label)
        return {"install": iid, "bbox": box, "blocks": m["total_blocks"]}

    def get(self, iid: str):
        if not INSTALL_RE.match(iid or ""):
            raise ExampleError("usage", "Install ids are 8 hex characters (see 'mcai examples installs').")
        row = self.store.example_install(iid)
        if row is None:
            raise ExampleError("not_found", f"No install {iid}.")
        return row

    def repair(self, actor, iid: str) -> str:
        """Place the pack again where it is: only cells that differ are changed; undo snapshots are kept."""
        with self.lock:
            self._busy()
            row = self.get(iid)
            if row["status"] not in ("placed", "failed", "interrupted"):
                raise ExampleError("state", f"Install {iid} is {row['status']}; only placed, failed or interrupted "
                                            f"installs can be repaired.")
            if row["snapshots"] != "present":
                raise ExampleError("state", "Its undo snapshots were deleted; install it again elsewhere instead.")
            p = self.pack(row["pack"], row["version"], ready=True)
            m = self.manifest(p)
            self.store.example_install_update(iid, status="placing", job="place", error=None, finished_at=None,
                                              progress=json.dumps({"done": 0, "total": m["total_blocks"]}))
            self._send("example_place", self.store.example_install(iid), m)
        self.store.audit("examples.repair_started", install=iid, pack=row["pack"], by=actor.label)
        return f"Repairing install {iid}: cells that differ from {row['pack']} are placed again."

    def rollback(self, actor, iid: str) -> str:
        with self.lock:
            self._busy()
            row = self.get(iid)
            if row["status"] not in ("placed", "failed", "interrupted", "rollback_failed"):
                raise ExampleError("state", f"Install {iid} is {row['status']}.")
            if row["snapshots"] != "present":
                raise ExampleError("state", "Its undo snapshots were deleted, so it can't be rolled back. Restore a "
                                            "backup instead ('mcai backups list').")
            self.store.example_install_update(iid, status="rolling_back", job="rollback", error=None,
                                              progress=json.dumps({}))
            self._send("example_rollback", self.store.example_install(iid))
        self.store.audit("examples.rollback_started", install=iid, pack=row["pack"], by=actor.label)
        return f"Rolling back install {iid}: the area is restored from the snapshots saved before it was placed."

    def cancel(self, actor, iid: str) -> str:
        row = self.get(iid)
        if row["status"] not in ("queued", "placing"):
            raise ExampleError("state", f"Install {iid} is {row['status']}; nothing to cancel.")
        with self.gw.cond:
            self.gw.events.append({"id": uuid.uuid4().hex[:8], "type": "example_cancel", "install_id": iid})
            self.gw.cond.notify_all()
        self.store.audit("examples.cancel_requested", install=iid, by=actor.label)
        return f"Asked the server to stop install {iid}. It stays partly placed: repair or roll it back afterwards."

    def forget(self, actor, iid: str) -> str:
        """Delete the undo snapshots of a placed install (frees disk space; rollback is then impossible)."""
        with self.lock:
            self._busy()
            row = self.get(iid)
            if row["status"] not in ("placed", "rolled_back") or row["snapshots"] != "present":
                raise ExampleError("state", f"Install {iid} is {row['status']} with snapshots {row['snapshots']}.")
            self.store.example_install_update(iid, job="forget")
            with self.gw.cond:
                self.gw.events.append({"id": uuid.uuid4().hex[:8], "type": "example_forget", "install_id": iid})
                self.gw.cond.notify_all()
            self.sent_at[iid] = self.clock()
        self.store.audit("examples.forget_requested", install=iid, by=actor.label)
        return f"Deleting the undo snapshots of install {iid}. It can't be rolled back afterwards."

    # ------------------------------------------------------------ mod side
    def file_for_mod(self, iid: str, name: str) -> bytes | None:
        row = self.store.example_install(iid) if INSTALL_RE.match(iid) else None
        if row is None or row["job"] != "place" or not FILE_RE.match(name):
            return None
        p = self.store.example_pack(row["pack"], row["version"])
        if p is None or p["status"] != "ready" or name not in {s["file"] for s in self.manifest(p)["sections"]}:
            return None
        try:
            return (self._dir(row["pack"], row["version"]) / name).read_bytes()
        except OSError:
            return None

    def report(self, iid: str, body: dict) -> dict:
        row = self.store.example_install(iid)
        if row is None:
            return {"ok": False}
        status = body.get("status")
        nums = {k: int(body[k]) for k in ("done", "total", "changed", "pieces") if isinstance(body.get(k), int)
                and not isinstance(body.get(k), bool) and 0 <= body[k] < 10**12}
        err = str(body.get("error", ""))[:300] or None
        self.sent_at[iid] = self.clock()
        now = self.store_now()
        nums = {**json.loads(row["progress"] or "{}"), **nums}       # the gateway keeps its own total for placing
        if status == "progress":
            self.store.example_install_update(iid, ("placing", "rolling_back"), progress=json.dumps(nums))
            return {"ok": True}
        if status == "placed" and self.store.example_install_update(iid, ("placing",), status="placed", job=None,
                                                                    progress=json.dumps(nums), finished_at=now):
            self.store.audit("examples.placed", install=iid, pack=row["pack"], **nums)
        elif status in ("failed", "cancelled") and self.store.example_install_update(
                iid, ("placing", "queued"), status="failed" if status == "failed" else "interrupted", job=None,
                progress=json.dumps(nums), error=err or status, finished_at=now):
            self.store.audit("examples.failed" if status == "failed" else "examples.cancelled", install=iid,
                             pack=row["pack"], error=err, **nums)
        elif status == "rolled_back" and self.store.example_install_update(
                iid, ("rolling_back",), status="rolled_back", job=None, snapshots="deleted",
                progress=json.dumps(nums), finished_at=now):
            self.store.audit("examples.rolled_back", install=iid, pack=row["pack"], **nums)
        elif status == "rollback_failed" and self.store.example_install_update(
                iid, ("rolling_back",), status="rollback_failed", job=None, error=err, finished_at=now):
            self.store.audit("examples.rollback_failed", install=iid, pack=row["pack"], error=err)
        elif status == "forgotten" and row["job"] == "forget":
            self.store.example_install_update(iid, job=None, snapshots="deleted")
            self.store.audit("examples.snapshots_deleted", install=iid, pack=row["pack"])
        else:
            return {"ok": False}
        self.sent_at.pop(iid, None)
        return {"ok": True}

    @staticmethod
    def store_now() -> str:
        from .store import utcnow
        return utcnow()

    def on_heartbeat(self, job) -> None:
        """The mod says which install it is working on. A busy install it doesn't know about was interrupted
        (server restart); two heartbeats in a row and at least a minute after the job was sent, to avoid races."""
        job = job if isinstance(job, str) and INSTALL_RE.match(job) else None
        for row in self.store.example_installs(BUSY):
            iid = row["id"]
            if iid == job or self.clock() - self.sent_at.get(iid, 0) < 60:
                self.hb_misses.pop(iid, None)
                continue
            self.hb_misses[iid] = self.hb_misses.get(iid, 0) + 1
            if self.hb_misses[iid] >= 2:
                new = "rollback_failed" if row["status"] == "rolling_back" else "interrupted"
                if self.store.example_install_update(iid, BUSY, status=new, job=None,
                                                     error="the server stopped working on it (restart?)"):
                    self.store.audit("examples.interrupted", install=iid, pack=row["pack"], previous=row["status"])
                self.hb_misses.pop(iid, None)

    # ------------------------------------------------------------ for automations
    def latest(self, pid: str):
        rows = self.store.example_installs(("placed",), pack=pid)
        return rows[0] if rows else None

    def area(self, pid: str) -> dict | None:
        row = self.latest(pid)
        if row is None:
            return None
        b = json.loads(row["bbox"])
        return {"dimension": row["dimension"], "from": b["from"], "to": b["to"], "install": row["id"]}

    def tour(self, pid: str) -> list[tuple[str, list]]:
        row = self.latest(pid)
        if row is None:
            return []
        p = self.store.example_pack(row["pack"], row["version"])
        if p is None:
            return []
        a = json.loads(row["anchor"])
        return [(poi["name"], [a[i] + poi["at"][i] for i in range(3)])
                for poi in self.manifest(p).get("points_of_interest", [])]
