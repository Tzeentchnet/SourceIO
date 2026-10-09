"""The shipped code must import itself relatively: as an extension it is loaded as
``bl_ext.<repository>.sourceio`` and no top-level ``SourceIO`` module exists."""
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NOT_SHIPPED = ("tests/", "tools/", "samples/", "dist/", "wiki/")
ABSOLUTE = re.compile(r"^[ \t]*(?:from|import)[ \t]+SourceIO\b.*$", re.MULTILINE)


def test_no_absolute_sourceio_imports():
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO_ROOT, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    shipped = [rel for rel in files if not rel.startswith(NOT_SHIPPED)]
    assert len(shipped) > 400
    offenders = [f"{rel}: {m.group(0).strip()}"
                 for rel in shipped
                 for m in ABSOLUTE.finditer((REPO_ROOT / rel).read_text(encoding="utf-8"))]
    assert not offenders, "\n".join(offenders)
