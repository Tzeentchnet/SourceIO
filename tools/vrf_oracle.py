"""Optional, pinned ValveResourceFormat CLI differential oracle.

No executable is discovered from ``PATH``.  Differential mode runs only when
an explicit path or ``SOURCEIO_VRF_CLI`` is supplied.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


VRF_PATH_ENV = "SOURCEIO_VRF_CLI"
VRF_SHA256_ENV = "SOURCEIO_VRF_CLI_SHA256"
VRF_TIMEOUT_ENV = "SOURCEIO_VRF_TIMEOUT"
DEFAULT_TIMEOUT_SECONDS = 30.0
RESOURCE_PATH_PATTERN = re.compile(
    r"(?P<path>[A-Za-z0-9_./\\-]+\.(?:"
    r"vtex|vmat|vmesh|vmdl|vanim|vagrp|vseq|vsnd|vphys|vwrld|vmap|vdata"
    r")(?:_c)?)",
    re.IGNORECASE,
)


class VRFOracleError(RuntimeError):
    pass


class VRFOracleExecutionError(VRFOracleError):
    pass


class VRFOracleParseError(VRFOracleError):
    pass


@dataclass(frozen=True)
class VRFCLIState:
    status: str
    configured: bool
    available: bool
    path: str | None
    sha256: str | None
    expected_sha256: str | None
    reason: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_vrf_cli(
    explicit_path: str | Path | None = None,
    *,
    expected_sha256: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> VRFCLIState:
    """Resolve only a user-configured executable and optionally verify its hash."""
    environment = os.environ if environ is None else environ
    configured_value = explicit_path or environment.get(VRF_PATH_ENV)
    expected = (expected_sha256 or environment.get(VRF_SHA256_ENV) or "").lower() or None
    if not configured_value:
        return VRFCLIState(
            "unconfigured",
            False,
            False,
            None,
            None,
            expected,
            f"{VRF_PATH_ENV} is not set and no --vrf-cli path was supplied",
        )

    path = Path(configured_value).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return VRFCLIState(
            "invalid",
            True,
            False,
            str(path),
            None,
            expected,
            "configured VRF CLI path does not exist",
        )
    if not resolved.is_file():
        return VRFCLIState(
            "invalid",
            True,
            False,
            str(resolved),
            None,
            expected,
            "configured VRF CLI path is not a file",
        )

    actual = _sha256_file(resolved)
    if expected is not None and actual.lower() != expected:
        return VRFCLIState(
            "invalid",
            True,
            False,
            str(resolved),
            actual,
            expected,
            "configured VRF CLI SHA-256 does not match the pinned value",
        )
    return VRFCLIState(
        "ready",
        True,
        True,
        str(resolved),
        actual,
        expected,
        "configured VRF CLI is available",
    )


def _timeout_seconds(environ: Mapping[str, str] | None = None) -> float:
    environment = os.environ if environ is None else environ
    raw_value = environment.get(VRF_TIMEOUT_ENV)
    if raw_value is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw_value)
    except ValueError as error:
        raise VRFOracleExecutionError(
            f"{VRF_TIMEOUT_ENV} must be a positive number, got {raw_value!r}"
        ) from error
    if value <= 0:
        raise VRFOracleExecutionError(f"{VRF_TIMEOUT_ENV} must be positive")
    return value


def run_vrf_cli(
    state: VRFCLIState,
    resource_path: str | Path,
    *,
    timeout_seconds: float | None = None,
) -> str:
    """Run ``Decompiler -i <resource> -a`` and return its UTF-8 output."""
    if not state.available or state.path is None:
        raise VRFOracleExecutionError(f"VRF CLI is not runnable: {state.reason}")
    resource = Path(resource_path).resolve(strict=True)
    timeout = _timeout_seconds() if timeout_seconds is None else timeout_seconds
    if timeout <= 0:
        raise VRFOracleExecutionError("timeout must be positive")
    command = [state.path, "-i", str(resource), "-a"]
    try:
        completed = subprocess.run(
            command,
            cwd=resource.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VRFOracleExecutionError(
            f"VRF CLI exceeded the {timeout:g}-second timeout"
        ) from error
    except OSError as error:
        raise VRFOracleExecutionError(f"failed to execute VRF CLI: {error}") from error
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        detail = stderr[-2_000:] if stderr else "no stderr output"
        raise VRFOracleExecutionError(
            f"VRF CLI exited with code {completed.returncode}: {detail}"
        )
    output = completed.stdout
    if not output.strip():
        raise VRFOracleExecutionError("VRF CLI produced no stdout")
    return output


def _empty_oracle_summary() -> dict[str, Any]:
    return {
        "identity": {
            "resource_type": None,
            "header_version": None,
            "resource_version": None,
            "byte_size": None,
        },
        "blocks": [],
        "dependencies": [],
        "dimensions": {
            "width": None,
            "height": None,
            "depth": None,
            "mip_count": None,
        },
        "channels": {"count": None, "names": []},
        "mesh": {
            "mesh_count": None,
            "vertex_count": None,
            "index_count": None,
            "primitive_count": None,
        },
        "skeleton": {"bone_count": None},
        "animation": {
            "clip_count": None,
            "frame_count": None,
            "fps": None,
            "duration_seconds": None,
        },
        "sound": {
            "sample_rate": None,
            "channel_count": None,
            "sample_count": None,
            "duration_seconds": None,
            "bits_per_sample": None,
            "encoding": None,
        },
        "observed": [],
    }


def _resource_type(value: str) -> str:
    normalized = value.strip().lower().replace("resource", "").strip(" :_-")
    aliases = {
        "vtex": "texture",
        "vmat": "material",
        "vmesh": "mesh",
        "vmdl": "model",
        "vanim": "animation",
        "vsnd": "sound",
        "vdata": "data",
    }
    return aliases.get(normalized, normalized.replace(" ", "_"))


def _set_observed(summary: dict[str, Any], section: str, name: str, value: Any) -> None:
    summary[section][name] = value
    path = f"{section}.{name}"
    if path not in summary["observed"]:
        summary["observed"].append(path)


def _from_normalized_json(value: Mapping[str, Any]) -> dict[str, Any]:
    source = value.get("summary", value)
    if not isinstance(source, Mapping):
        raise VRFOracleParseError("JSON oracle output must contain an object summary")
    oracle = _empty_oracle_summary()
    for section in (
        "identity",
        "dimensions",
        "channels",
        "mesh",
        "skeleton",
        "animation",
        "sound",
    ):
        section_value = source.get(section)
        if not isinstance(section_value, Mapping):
            continue
        for name in oracle[section]:
            if name in section_value and section_value[name] is not None:
                _set_observed(oracle, section, name, section_value[name])

    blocks = source.get("blocks")
    if isinstance(blocks, list):
        names = []
        for block in blocks:
            if isinstance(block, Mapping) and block.get("name") is not None:
                names.append(str(block["name"]))
            elif isinstance(block, str):
                names.append(block)
        oracle["blocks"] = names
        oracle["observed"].append("blocks")

    dependencies = source.get("dependencies")
    if isinstance(dependencies, list):
        oracle["dependencies"] = sorted(str(item).replace("\\", "/") for item in dependencies)
        oracle["observed"].append("dependencies")
    oracle["observed"] = sorted(set(oracle["observed"]))
    return oracle


def _number_pattern(label: str) -> re.Pattern[str]:
    return re.compile(
        rf"\b{label}\b\s*(?:[:=]|\bis\b)\s*(-?\d+(?:\.\d+)?)",
        re.IGNORECASE,
    )


HUMAN_NUMERIC_FIELDS = (
    ("identity", "header_version", _number_pattern(r"header\s+version"), int),
    ("identity", "resource_version", _number_pattern(r"resource\s+version"), int),
    ("identity", "byte_size", _number_pattern(r"(?:file\s+)?size"), int),
    ("dimensions", "width", _number_pattern("width"), int),
    ("dimensions", "height", _number_pattern("height"), int),
    ("dimensions", "depth", _number_pattern("depth"), int),
    ("dimensions", "mip_count", _number_pattern(r"mip(?:map)?\s+count"), int),
    ("channels", "count", _number_pattern(r"channel\s+count"), int),
    ("mesh", "mesh_count", _number_pattern(r"mesh\s+count"), int),
    ("mesh", "vertex_count", _number_pattern(r"vert(?:ex|ices)\s+count"), int),
    ("mesh", "index_count", _number_pattern(r"index\s+count"), int),
    ("mesh", "primitive_count", _number_pattern(r"primitive\s+count"), int),
    ("skeleton", "bone_count", _number_pattern(r"bone\s+count"), int),
    ("animation", "clip_count", _number_pattern(r"clip\s+count"), int),
    ("animation", "frame_count", _number_pattern(r"frame\s+count"), int),
    ("animation", "fps", _number_pattern(r"(?:fps|frame\s+rate)"), float),
    (
        "animation",
        "duration_seconds",
        _number_pattern(r"(?:animation\s+)?duration(?:\s+seconds)?"),
        float,
    ),
    ("sound", "sample_rate", _number_pattern(r"sample\s+rate"), int),
    ("sound", "channel_count", _number_pattern(r"channels?"), int),
    ("sound", "sample_count", _number_pattern(r"sample\s+count"), int),
    (
        "sound",
        "duration_seconds",
        _number_pattern(r"(?:sound\s+)?duration(?:\s+seconds)?"),
        float,
    ),
    ("sound", "bits_per_sample", _number_pattern(r"bits\s+per\s+sample"), int),
)


def parse_vrf_output(output: str) -> dict[str, Any]:
    """Normalize VRF JSON wrappers or the CLI's human-readable block dump."""
    stripped = output.lstrip("\ufeff\r\n\t ")
    if stripped.startswith("{"):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise VRFOracleParseError(f"invalid JSON oracle output: {error}") from error
        if not isinstance(decoded, Mapping):
            raise VRFOracleParseError("JSON oracle output must be an object")
        oracle = _from_normalized_json(decoded)
        if not oracle["observed"]:
            raise VRFOracleParseError("JSON oracle output contains no comparable fields")
        return oracle

    oracle = _empty_oracle_summary()
    resource_type_match = re.search(
        r"\b(?:resource\s+type|type)\b\s*[:=]\s*([A-Za-z][A-Za-z0-9 _-]+)",
        output,
        re.IGNORECASE,
    )
    if resource_type_match:
        _set_observed(
            oracle,
            "identity",
            "resource_type",
            _resource_type(resource_type_match.group(1).splitlines()[0]),
        )

    for section, name, pattern, converter in HUMAN_NUMERIC_FIELDS:
        match = pattern.search(output)
        if match:
            _set_observed(oracle, section, name, converter(float(match.group(1))))

    block_patterns = (
        re.compile(r"\bblock\s*[:#]?\s*[\"']?([A-Za-z0-9]{4})[\"']?", re.IGNORECASE),
        re.compile(r"^\s*\[\d+\]\s+([A-Za-z0-9]{4})\b", re.MULTILINE),
        re.compile(r"^\s*([A-Z][A-Za-z0-9]{3})\s+(?:block|offset\b)", re.MULTILINE),
    )
    block_names = []
    for pattern in block_patterns:
        for match in pattern.finditer(output):
            name = match.group(1)
            if name.upper() not in {"SIZE", "TYPE"} and name not in block_names:
                block_names.append(name)
    if block_names:
        oracle["blocks"] = block_names
        oracle["observed"].append("blocks")

    dependencies = []
    in_dependency_section = False
    for line in output.splitlines():
        lowered = line.lower()
        if "external reference" in lowered or lowered.strip().startswith("dependencies"):
            in_dependency_section = True
            continue
        if in_dependency_section and (
            re.match(r"^\s*(?:---+|block\b)", line, re.IGNORECASE)
            or re.match(r"^\s*[A-Z0-9]{4}\s*:", line)
        ):
            in_dependency_section = False
        if in_dependency_section:
            for match in RESOURCE_PATH_PATTERN.finditer(line):
                path = match.group("path").replace("\\", "/")
                if path not in dependencies:
                    dependencies.append(path)
    if dependencies:
        oracle["dependencies"] = sorted(dependencies)
        oracle["observed"].append("dependencies")

    channel_names_match = re.search(
        r"\bchannels?\b\s*[:=]\s*((?:[RGBAI](?:\s*,\s*|\s+))+[RGBAI])\b",
        output,
        re.IGNORECASE,
    )
    if channel_names_match:
        names = re.findall(r"[RGBAI]", channel_names_match.group(1).upper())
        _set_observed(oracle, "channels", "names", names)
        if oracle["channels"]["count"] is None:
            _set_observed(oracle, "channels", "count", len(names))

    encoding_match = re.search(
        r"\b(?:encoding|codec)\b\s*[:=]\s*([A-Za-z0-9_.-]+)",
        output,
        re.IGNORECASE,
    )
    if encoding_match:
        _set_observed(oracle, "sound", "encoding", encoding_match.group(1).lower())

    oracle["observed"] = sorted(set(oracle["observed"]))
    required = {"identity.resource_type", "blocks"}
    if not required.issubset(oracle["observed"]):
        missing = ", ".join(sorted(required - set(oracle["observed"])))
        raise VRFOracleParseError(
            f"VRF output lacks required comparable fields: {missing}"
        )
    return oracle


def _source_value(source: Mapping[str, Any], field: str) -> Any:
    if field == "blocks":
        return [block["name"] for block in source["blocks"]]
    if field == "dependencies":
        return sorted(str(item).replace("\\", "/") for item in source["dependencies"])
    section, name = field.split(".", 1)
    return source[section][name]


def compare_summaries(
    source_summary: Mapping[str, Any],
    oracle_summary: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Strictly compare every field the oracle says it observed."""
    mismatches = []
    for field in oracle_summary["observed"]:
        source_value = _source_value(source_summary, field)
        if field in {"blocks", "dependencies"}:
            oracle_value = oracle_summary[field]
        else:
            section, name = field.split(".", 1)
            oracle_value = oracle_summary[section][name]
        if field == "blocks":
            source_value = sorted(source_value)
            oracle_value = sorted(oracle_value)
        if source_value != oracle_value:
            mismatches.append(
                {
                    "field": field,
                    "sourceio": source_value,
                    "vrf": oracle_value,
                }
            )
    return mismatches


def compare_with_vrf(
    resource_path: str | Path,
    source_summary: Mapping[str, Any],
    *,
    explicit_path: str | Path | None = None,
    expected_sha256: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return matched/mismatched/skipped/error without hiding configuration state."""
    state = resolve_vrf_cli(
        explicit_path,
        expected_sha256=expected_sha256,
        environ=environ,
    )
    state_value = asdict(state)
    if state.status == "unconfigured":
        return {
            "status": "skipped",
            "reason": state.reason,
            "vrf_cli": state_value,
            "mismatches": [],
        }
    if not state.available:
        return {
            "status": "error",
            "reason": state.reason,
            "vrf_cli": state_value,
            "mismatches": [],
        }
    try:
        output = run_vrf_cli(state, resource_path)
        oracle_summary = parse_vrf_output(output)
    except VRFOracleError as error:
        return {
            "status": "error",
            "reason": str(error),
            "vrf_cli": state_value,
            "mismatches": [],
        }
    mismatches = compare_summaries(source_summary, oracle_summary)
    return {
        "status": "mismatched" if mismatches else "matched",
        "reason": (
            f"{len(mismatches)} normalized field mismatch(es)"
            if mismatches
            else "all normalized VRF fields matched"
        ),
        "vrf_cli": state_value,
        "oracle_output_sha256": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "observed_fields": oracle_summary["observed"],
        "mismatches": mismatches,
    }


def self_test() -> dict[str, Any]:
    sample = """
Resource Type: Texture
Header Version: 12
Resource Version: 1
File Size: 128
Block: RERL
Block: DATA
Width: 2
Height: 2
Depth: 1
Mipmap Count: 1
Channel Count: 4
"""
    parsed = parse_vrf_output(sample)
    if parsed["identity"]["resource_type"] != "texture":
        raise AssertionError("resource type normalization failed")
    if parsed["blocks"] != ["RERL", "DATA"]:
        raise AssertionError("block normalization failed")
    if parsed["dimensions"]["width"] != 2:
        raise AssertionError("dimension normalization failed")
    json_parsed = parse_vrf_output(json.dumps({"summary": parsed}))
    if json_parsed["blocks"] != parsed["blocks"]:
        raise AssertionError("JSON normalization failed")
    return {"status": "passed", "observed_fields": parsed["observed"]}


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    state_parser = subparsers.add_parser("state", help="Report VRF configuration.")
    state_parser.add_argument("--vrf-cli", type=Path)
    state_parser.add_argument("--vrf-sha256")

    compare_parser = subparsers.add_parser("compare", help="Run a strict differential comparison.")
    compare_parser.add_argument("resource", type=Path)
    compare_parser.add_argument("--vrf-cli", type=Path)
    compare_parser.add_argument("--vrf-sha256")

    subparsers.add_parser("self-test", help="Test parsers without executing VRF.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "state":
        state = resolve_vrf_cli(args.vrf_cli, expected_sha256=args.vrf_sha256)
        _print_json(asdict(state))
        return 0 if state.available else 3
    if args.command == "self-test":
        _print_json(self_test())
        return 0
    if args.command == "compare":
        repository_parent = str(Path(__file__).resolve().parents[2])
        if repository_parent not in sys.path:
            sys.path.insert(0, repository_parent)
        with contextlib.redirect_stdout(sys.stderr):
            from SourceIO.tools.source2_conformance import summarize_path

        report = compare_with_vrf(
            args.resource,
            summarize_path(args.resource),
            explicit_path=args.vrf_cli,
            expected_sha256=args.vrf_sha256,
        )
        _print_json(report)
        if report["status"] == "skipped":
            return 3
        return 0 if report["status"] == "matched" else 1
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
