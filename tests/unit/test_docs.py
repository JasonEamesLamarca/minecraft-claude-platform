"""Docs: the guides exist, link each other, and their Mermaid diagrams are well-formed enough for GitHub to draw.

Full rendering is checked by hand with mermaid-cli (see docs/development/test-results.md); this catches the usual
breakage: a deleted guide, an unclosed fence, a diagram without a type line.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDES = ["README.md", "docs/installation/README.md", "docs/administration/README.md", "docs/upgrade/README.md",
          "docs/troubleshooting/README.md", "docs/security/README.md", "docs/development/README.md",
          "docs/architecture/overview.md", "docs/development/test-results.md"]
TYPES = ("flowchart ", "sequenceDiagram", "stateDiagram-v2", "classDiagram", "erDiagram")


def _docs():
    skip = {".venv", "node_modules", "dist", "data", ".cache", "backups"}
    return [p for p in ROOT.glob("**/*.md") if not skip & set(p.relative_to(ROOT).parts)]


def test_every_guide_exists_and_the_readme_links_it():
    readme = (ROOT / "README.md").read_text()
    for g in GUIDES:
        assert (ROOT / g).is_file(), g
        if g != "README.md":
            assert f"({g})" in readme, f"README.md doesn't link {g}"


def test_mermaid_diagrams():
    diagrams = []
    for p in _docs():
        text = p.read_text()
        assert text.count("```") % 2 == 0, f"{p}: unbalanced code fence"
        diagrams += [(p, d) for d in re.findall(r"```mermaid\n(.*?)```", text, re.S)]
    assert len(diagrams) >= 9, f"only {len(diagrams)} diagrams"
    for p, d in diagrams:
        first = d.strip().splitlines()[0]
        assert first.startswith(TYPES), f"{p}: diagram starts with {first!r}"
        assert "\t" not in d, f"{p}: tab in a diagram"
        if first.startswith("flowchart"):
            assert d.count("subgraph") == len(re.findall(r"^\s*end\s*$", d, re.M)), f"{p}: subgraph without end"
