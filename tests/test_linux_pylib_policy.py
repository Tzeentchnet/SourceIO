from pathlib import Path

import pytest

from SourceIO.tools.check_linux_pylib import CompatibilityError, required_versions, validate_compatibility


def _elf(tmp_path: Path, *versions: str) -> Path:
    path = tmp_path / "pylib.abi3.so"
    path.write_bytes(b"\x7fELF" + b"\0".join(version.encode("ascii") for version in versions))
    return path


def test_collects_required_symbol_versions(tmp_path):
    path = _elf(tmp_path, "GLIBC_2.17", "GLIBC_2.28", "GLIBCXX_3.4.29", "CXXABI_1.3.13")
    versions = required_versions(path)
    assert versions["GLIBC"] == {(2, 17), (2, 28)}
    assert versions["GLIBCXX"] == {(3, 4, 29)}
    assert versions["CXXABI"] == {(1, 3, 13)}


def test_accepts_policy_limits_and_static_cpp_runtime(tmp_path):
    assert validate_compatibility(
        _elf(tmp_path, "GLIBC_2.28", "GLIBCXX_3.4.29", "CXXABI_1.3.13")
    ) == {
        "GLIBC": (2, 28),
        "GLIBCXX": (3, 4, 29),
        "CXXABI": (1, 3, 13),
    }
    assert validate_compatibility(_elf(tmp_path, "GLIBC_2.17")) == {
        "GLIBC": (2, 17),
        "GLIBCXX": None,
        "CXXABI": None,
    }


@pytest.mark.parametrize("version", ["GLIBC_2.29", "GLIBCXX_3.4.30", "CXXABI_1.3.14"])
def test_rejects_newer_symbol_versions(tmp_path, version):
    with pytest.raises(CompatibilityError, match=rf"{version} exceeds"):
        validate_compatibility(_elf(tmp_path, version))


def test_rejects_non_elf_input(tmp_path):
    path = tmp_path / "pylib.abi3.so"
    path.write_bytes(b"not an ELF")
    with pytest.raises(CompatibilityError, match="not an ELF binary"):
        required_versions(path)


def test_bundled_linux_pylib_meets_policy():
    binary = Path(__file__).resolve().parents[1] / "library/utils/pylib/linux/pylib.abi3.so"
    assert validate_compatibility(binary) == {
        "GLIBC": (2, 27),
        "GLIBCXX": None,
        "CXXABI": None,
    }
