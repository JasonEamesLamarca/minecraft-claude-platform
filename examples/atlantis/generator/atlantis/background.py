"""What a cell holds when no feature set it. The exporter calls background(ctx, x, y, z) for every None cell.

  inside the dome air volume           -> air
  at or below the seabed surface       -> the seabed fill (terrain module can replace seabed_height / seabed_block)
  everything else up to ctx.y_max      -> water
Inside the dome footprint the seabed is the city floor (y 0) so the city sits on a level base; the foundation
feature dresses it.
"""

def default_seabed_height(ctx, x, z):
    """Flat seabed at the city floor. terrain.py installs a relief function via set_seabed()."""
    return 0


def default_seabed_block(ctx, x, y, z, surface):
    if y == surface: return 'sand'
    if y >= surface - 3: return 'sandstone'
    return 'deepslate' if y < ctx.y_min + 6 else 'stone'


_seabed_height = default_seabed_height
_seabed_block = default_seabed_block


def set_seabed(height_fn=None, block_fn=None):
    global _seabed_height, _seabed_block
    if height_fn: _seabed_height = height_fn
    if block_fn: _seabed_block = block_fn


def seabed_height(ctx, x, z):
    return _seabed_height(ctx, x, z)


def background(ctx, x, y, z):
    if ctx.inside_dome(x, y, z):
        return 'air'
    if y == ctx.y_min:
        return 'bedrock'
    s = _seabed_height(ctx, x, z)
    if y <= s:
        return _seabed_block(ctx, x, y, z, s)
    return 'water'
