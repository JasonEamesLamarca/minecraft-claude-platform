"""The mod, the Python package, the CLI and its pyproject carry one version (the release workflow checks the tag)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _find(path, pattern):
    m = re.search(pattern, (ROOT / path).read_text(), re.M)
    assert m, f"no version in {path}"
    return m.group(1)


def test_versions_agree():
    versions = {
        "mod": _find("minecraft-mod/gradle.properties", r"^mod_version=(\S+)"),
        "python": _find("src/mcai/__init__.py", r'^__version__ = "([^"]+)"'),
        "cli": _find("management-cli/mcai_cli.py", r'^__version__ = "([^"]+)"'),
        "cli package": _find("management-cli/pyproject.toml", r'^version = "([^"]+)"'),
    }
    assert len(set(versions.values())) == 1, versions
