"""Validate the ELF symbol-version floor of the bundled Linux pylib."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


MAX_VERSIONS = {
    "GLIBC": (2, 28),
    "GLIBCXX": (3, 4, 29),
    "CXXABI": (1, 3, 13),
}
_VERSION_PATTERN = re.compile(
    rb"(GLIBCXX|GLIBC|CXXABI)_([0-9]+(?:\.[0-9]+)+)"
)


class CompatibilityError(RuntimeError):
    pass


def required_versions(path: Path) -> dict[str, set[tuple[int, ...]]]:
    data = path.read_bytes()
    if not data.startswith(b"\x7fELF"):
        raise CompatibilityError(f"{path} is not an ELF binary")

    versions = {family: set() for family in MAX_VERSIONS}
    for match in _VERSION_PATTERN.finditer(data):
        family = match.group(1).decode("ascii")
        version = tuple(int(part) for part in match.group(2).split(b"."))
        versions[family].add(version)
    return versions


def validate_compatibility(path: Path) -> dict[str, tuple[int, ...] | None]:
    versions = required_versions(path)
    highest = {
        family: max(required) if required else None
        for family, required in versions.items()
    }
    incompatible = [
        f"{family}_{'.'.join(map(str, version))} exceeds "
        f"{family}_{'.'.join(map(str, MAX_VERSIONS[family]))}"
        for family, version in highest.items()
        if version is not None and version > MAX_VERSIONS[family]
    ]
    if incompatible:
        raise CompatibilityError(f"{path}: {'; '.join(incompatible)}")
    return highest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    args = parser.parse_args(argv)

    try:
        highest = validate_compatibility(args.binary)
    except (OSError, CompatibilityError) as error:
        print(error, file=sys.stderr)
        return 1

    summary = ", ".join(
        f"{family}={'.'.join(map(str, version)) if version else 'not required'}"
        for family, version in highest.items()
    )
    print(f"{args.binary}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
