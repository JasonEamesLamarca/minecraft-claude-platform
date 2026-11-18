"""Build the downloadable example packs: Sponge v3 `.schem` sections plus a manifest with SHA-256 checksums.

    cd examples/atlantis/generator
    python3 -m atlantis.pack lite --out ../../../dist/examples          # Atlantis Lite (~1 minute)
    python3 -m atlantis.pack full --out ../../../dist/examples          # Atlantis Full (several minutes)

Coordinates in the manifest are relative to the pack's **anchor**: the city centre (axis of the central tower) at
city floor level (local y 0). Players walk at anchor y + 5 on the decks. The installer places the anchor at the
world position the admin chooses; section `offset`s say where each section's minimum corner goes.

Packs are deterministic: the same generator commit and settings give the same content (sorted palettes, gzip
mtime 0). The compressed bytes also depend on the zlib version, so `examples/catalog.json` pins both the release
files' hashes (what downloads are checked against) and a compression-independent `content_sha256`
(`python3 -m atlantis.pack digest <pack folder>`), which CI recomputes from source.

Cell rules on export:
  * `bedrock` (the generator's bottom layer) becomes `deepslate`: an example must stay removable.
  * `structure_void` means "leave the world as it is" (cells outside the work area). The platform installer skips
    them; other tools such as WorldEdit place an invisible structure-void block there.
  * Lite is a cutaway strip of the real city. Its open sides and top are sealed with glass so it can be placed
    anywhere (on land or under water) without flooding: every air, water or light cell on those faces becomes glass.
"""
import argparse, hashlib, json, multiprocessing, os, sys, time
from array import array

from . import config, schem
from . import export_tiles as E

FORMAT = 1
LICENSE = 'Apache-2.0'
WALK = 5

PACKS = {
    'atlantis-lite': {
        'title': 'Atlantis Lite',
        'description': ('A 96-block-wide cutaway of the city: the central tower, the Fountain & Arrival district, the '
                        'north pier with its landing pads and skiff hangar, the dome wall and a strip of sea floor '
                        'outside it. Sealed with glass, so it can be placed on land or under water.'),
        'box': (-48, -640, 47, 127),          # x1, z1, x2, z2 (local, inclusive)
        'section': (96, 128),                 # section size along x, z
        'seal': True,
        'poi': [
            ('Central tower plaza', 0, WALK, -40),
            ('Fountain Plaza and the Trident Arch', 0, WALK, -205),
            ('North pier root', 0, WALK, -300),
            ('Landing pads and skiff hangar', 0, WALK, -420),
            ('Pier tip', 0, WALK, -531),
            ('Dome wall and the sea', 0, WALK, -585),
        ],
        'recommended': {'floor_y': -56, 'note': 'Deep ocean is ideal: the glass roof then sits at sea level (y 62). '
                                                'Any flat area works; the strip is 96 x 768 blocks and 127 tall.'},
    },
    'atlantis-full': {
        'title': 'Atlantis Full',
        'description': ('The whole city under a 1,200-block glass dome, with ten districts, six piers and the sea '
                        'floor around it, in sections. Needs a deep ocean about 1,600 x 1,600 blocks wide.'),
        'box': (-800, -800, 799, 799),
        'section': (160, 160),
        'seal': False,
        'poi': [
            ('Central tower plaza', 0, WALK, -40),
            ('Fountain Plaza and the Trident Arch', 0, WALK, -205),
            ('Civic Square', 0, WALK, 215),
            ('Botanical Court', -186, WALK, 108),
            ('North pier tip', 0, WALK, -531),
        ],
        'recommended': {'floor_y': -56, 'note': 'Deep ocean, 1,600 x 1,600 blocks, nothing built there. Take a '
                                                'backup first (mcai backups create).'},
    },
}

_ctx = None
_features = None
NON_SOLID = ('air', 'cave_air', 'void_air', 'water', 'light', 'structure_void')


def _init():
    global _ctx, _features
    from .orchestrate import load_generators
    _ctx = config.load()
    _features = load_generators()


def sections(spec):
    x1, z1, x2, z2 = spec['box']
    sw, sl = spec['section']
    out = []
    for z in range(z1, z2 + 1, sl):
        for x in range(x1, x2 + 1, sw):
            out.append((x, z, min(x + sw - 1, x2), min(z + sl - 1, z2)))
    return out


def _seal(vol, ctx, spec):
    """Glass on the pack's outer faces (sides and top) wherever the cell is not solid."""
    px1, pz1, px2, pz2 = spec['box']
    glass = vol._pid('glass')
    soft = {i for i, s in enumerate(vol.palette) if s is not None and s.split('[')[0] in NON_SOLID}
    sx, sz = vol.sx, vol.sz
    cells = vol.cells
    for y in range(vol.y1, vol.y2 + 1):
        top = y == ctx.y_max
        for z in range(vol.z1, vol.z2 + 1):
            edge_z = z in (pz1, pz2)
            base = ((y - vol.y1) * sz + (z - vol.z1)) * sx
            for x in range(vol.x1, vol.x2 + 1):
                if not (top or edge_z or x in (px1, px2)): continue
                k = base + x - vol.x1
                if cells[k] in soft: cells[k] = glass


def _section_job(job):
    name, idx, box = job
    spec = PACKS[name]
    x1, z1, x2, z2 = box
    t0 = time.time()
    vol = E.build_box(_ctx, (x1, _ctx.y_min, z1, x2, _ctx.y_max, z2), _features)
    if spec['seal']:
        _seal(vol, _ctx, spec)
    # canonical palette, bedrock -> deepslate
    states = {}
    lut = []
    for s in vol.palette:
        if s is None:
            lut.append(None); continue
        c = schem.canonical('deepslate' if s.split('[')[0] == 'bedrock' else s)
        lut.append(c)
    used = sorted({lut[c] for c in set(vol.cells)})
    index = {s: i for i, s in enumerate(used)}
    remap = [index.get(c, 0) if c is not None else 0 for c in lut]
    cells = array('H', (remap[c] for c in vol.cells))
    void = index.get('minecraft:structure_void', -1)
    placed = len(cells) - (cells.count(void) if void >= 0 else 0)
    w, h, l = x2 - x1 + 1, _ctx.y_max - _ctx.y_min + 1, z2 - z1 + 1
    offset = [x1, _ctx.y_min, z1]
    data = schem.encode(w, h, l, used, cells, offset=offset,
                        data_version=_ctx.profile.get('data_version', 3955),
                        metadata={'Name': f'{spec["title"]} section {idx}', 'Generator': 'mcai atlantis'})
    return idx, box, data, placed, used, time.time() - t0


def build(name, out_dir, jobs, version):
    spec = PACKS[name]
    ctx = config.load()
    secs = sections(spec)
    # sections that lie completely outside the work area are all structure_void: skip them
    def inside(b):
        x1, z1, x2, z2 = b
        nx = min(max(0, x1), x2) if x1 <= 0 <= x2 else (x1 if x1 > 0 else x2)
        nz = min(max(0, z1), z2) if z1 <= 0 <= z2 else (z1 if z1 > 0 else z2)
        return nx * nx + nz * nz <= ctx.area_radius ** 2
    secs = [b for b in secs if inside(b)]
    pack_dir = os.path.join(out_dir, name)
    os.makedirs(pack_dir, exist_ok=True)
    for fn in os.listdir(pack_dir):
        if fn.endswith('.schem') or fn == 'manifest.json':
            os.remove(os.path.join(pack_dir, fn))
    t0 = time.time()
    results = []
    work = [(name, i, b) for i, b in enumerate(secs)]
    if jobs <= 1:
        _init()
        it = map(_section_job, work)
    else:
        pool = multiprocessing.Pool(jobs, initializer=_init)
        it = pool.imap_unordered(_section_job, work)
    palette = set()
    for k, (idx, box, data, placed, used, dt) in enumerate(it, 1):
        fn = f'{name}-s{idx:03d}.schem'
        with open(os.path.join(pack_dir, fn), 'wb') as f:
            f.write(data)
        x1, z1, x2, z2 = box
        results.append({'file': fn, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                        'offset': [x1, ctx.y_min, z1],
                        'size': [x2 - x1 + 1, ctx.y_max - ctx.y_min + 1, z2 - z1 + 1], 'blocks': placed})
        palette.update(used)
        print(f'  {k}/{len(work)} {fn} {len(data) / 1e6:.1f} MB {placed:,} blocks {dt:.1f}s', flush=True)
    if jobs > 1:
        pool.close(); pool.join()
    results.sort(key=lambda r: r['file'])
    x1, z1, x2, z2 = spec['box']
    manifest = {
        'format': FORMAT,
        'id': name,
        'version': version,
        'title': spec['title'],
        'description': spec['description'],
        'license': LICENSE,
        'minecraft': ctx.profile.get('minecraft', '1.21.1'),
        'data_version': ctx.profile.get('data_version', 3955),
        'anchor': 'city centre (axis of the central tower) at city floor level; players walk at anchor y + 5',
        'origin': [x1, ctx.y_min, z1],
        'size': [x2 - x1 + 1, ctx.y_max - ctx.y_min + 1, z2 - z1 + 1],
        'recommended': spec['recommended'],
        'points_of_interest': [{'name': n, 'at': [x, y, z]} for n, x, y, z in spec['poi']],
        'sections': results,
        'total_blocks': sum(r['blocks'] for r in results),
        'total_bytes': sum(r['bytes'] for r in results),
        'blocks_used': sorted(palette),
    }
    with open(os.path.join(pack_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=1, sort_keys=False)
        f.write('\n')
    mb = manifest['total_bytes'] / 1e6
    print(f'{name} {version}: {len(results)} sections, {manifest["total_blocks"]:,} blocks, {mb:.1f} MB, '
          f'{time.time() - t0:.0f}s -> {pack_dir}')
    return manifest


def content_digest(pack_dir):
    """SHA-256 of what a pack contains, independent of how gzip compressed it (zlib versions compress the same data
    to different bytes): the manifest without byte sizes and file hashes, plus each section's uncompressed NBT."""
    import gzip
    m = json.load(open(os.path.join(pack_dir, 'manifest.json')))
    h = hashlib.sha256()
    meta = dict(m, total_bytes=None, sections=[{k: v for k, v in s.items() if k not in ('sha256', 'bytes')}
                                               for s in m['sections']])
    h.update(json.dumps(meta, sort_keys=True).encode())
    for s in m['sections']:
        with open(os.path.join(pack_dir, s['file']), 'rb') as f:
            h.update(s['file'].encode() + b'\0' + hashlib.sha256(gzip.decompress(f.read())).digest())
    return h.hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(prog='atlantis.pack', description=__doc__.split('\n\n')[0])
    ap.add_argument('pack', choices=['lite', 'full', 'digest'], help="'digest DIR' prints a pack folder's content digest")
    ap.add_argument('dir', nargs='?')
    ap.add_argument('--out', default=os.path.join(config.ROOT, 'out', 'packs'))
    ap.add_argument('--version', default='1.0.0')
    ap.add_argument('--jobs', type=int, default=max(1, (os.cpu_count() or 4) - 2))
    a = ap.parse_args(argv)
    if a.pack == 'digest':
        print(content_digest(a.dir))
        return 0
    build('atlantis-' + a.pack, a.out, a.jobs, a.version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
