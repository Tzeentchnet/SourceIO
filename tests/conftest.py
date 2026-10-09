"""Leave tests/archive out of a run unless it is named on the command line (``pytest SourceIO/tests/archive``).

The archive keeps unit tests an end-to-end runner in tests/e2e already covers; see tests/archive/README.md.
"""
from pathlib import Path

ARCHIVE = Path(__file__).resolve().parent / "archive"


def _names_archive(config) -> bool:
    for arg in config.args:
        path = Path(arg.split("::")[0]).resolve()
        if path == ARCHIVE or ARCHIVE in path.parents:
            return True
    return False


def pytest_ignore_collect(collection_path, config):
    if (collection_path == ARCHIVE or ARCHIVE in collection_path.parents) and not _names_archive(config):
        return True
    return None
