"""Small shared helpers: stable hashing, seeded RNG, block-state string parsing."""
import hashlib, random


def hash_str(*parts):
    """Stable 64-bit hash of the parts (never Python's hash(), which is salted per process)."""
    h = hashlib.sha1('\x1f'.join(str(p) for p in parts).encode('utf-8')).digest()
    return int.from_bytes(h[:8], 'big')


def rng(ctx, name):
    """Deterministic random.Random for a feature: seeded from ctx.seed and the feature name."""
    return random.Random(hash_str(ctx.seed, name))


def parse_state(state):
    """'quartz_stairs[facing=north,half=bottom]' -> ('minecraft:quartz_stairs', {'facing': 'north', 'half': 'bottom'})."""
    if '[' not in state:
        name, props = state, {}
    else:
        name, rest = state.split('[', 1)
        rest = rest.rstrip(']')
        props = dict(p.split('=', 1) for p in rest.split(',')) if rest else {}
    if ':' not in name: name = 'minecraft:' + name
    return name, props


def format_state(name, props=None):
    """Inverse of parse_state, without the 'minecraft:' prefix and with sorted properties."""
    if name.startswith('minecraft:'): name = name[10:]
    if not props: return name
    return name + '[' + ','.join(f'{k}={v}' for k, v in sorted(props.items())) + ']'


def base_name(state):
    """Block id without namespace or properties: 'minecraft:quartz_stairs[facing=north]' -> 'quartz_stairs'."""
    s = state.split('[', 1)[0]
    return s[10:] if s.startswith('minecraft:') else s


def mp_pool(jobs, initializer=None, initargs=()):
    """multiprocessing.Pool that forks where possible. Python 3.14 made forkserver the Linux default, so workers
    no longer inherit features registered in the parent (tests register demo features that way)."""
    import multiprocessing
    try:
        ctx = multiprocessing.get_context('fork')
    except ValueError:
        ctx = multiprocessing.get_context()
    return ctx.Pool(jobs, initializer=initializer, initargs=initargs)
