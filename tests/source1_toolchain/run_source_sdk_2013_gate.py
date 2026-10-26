r"""Run the complete Source SDK Base 2013 acceptance gate.

Usage:
    python tests/source1_toolchain/run_source_sdk_2013_gate.py \
        --singleplayer-root "...\Source SDK Base 2013 Singleplayer" \
        --multiplayer-root "...\Source SDK Base 2013 Multiplayer" \
        --blender "...\blender.exe" \
        --json source-sdk-2013-gate.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT.parent))

from SourceIO.tests.source1_toolchain.source_sdk_2013 import (
    MULTIPLAYER,
    SINGLEPLAYER,
    SOURCE_SDK_2013_PROFILES,
    SourceSdk2013DiscoveryError,
    SourceSdk2013GateError,
    SourceSdk2013Installation,
    create_disposable_fixture,
    execute_disposable_fixture,
    file_fingerprint,
)

REIMPORT_SCRIPT = Path(__file__).with_name(
    "run_source_sdk_2013_reimport.py"
)
GAME_IMPORT_SCRIPT = REPO_ROOT / "tests" / "e2e" / "run_game_imports.py"
MAX_PROCESS_TAIL = 64 * 1024


class SourceSdk2013RunnerError(RuntimeError):
    pass


def _bounded_tail(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_PROCESS_TAIL:
        return value, False
    return encoded[-MAX_PROCESS_TAIL:].decode("utf-8", errors="replace"), True


def _run_process(command: list[str], *, timeout_seconds: float) -> dict:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceSdk2013RunnerError(
            f"Process timed out after {timeout_seconds:g} seconds: {command[0]}"
        ) from exc
    duration = time.perf_counter() - started
    stdout_tail, stdout_truncated = _bounded_tail(completed.stdout)
    stderr_tail, stderr_truncated = _bounded_tail(completed.stderr)
    report = {
        "command": command,
        "returncode": completed.returncode,
        "duration_seconds": duration,
        "stdout_bytes": len(completed.stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(completed.stderr.encode("utf-8", errors="replace")),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    if completed.returncode:
        report["stdout_tail"] = stdout_tail
        report["stderr_tail"] = stderr_tail
        diagnostic = stderr_tail.strip() or stdout_tail.strip()
        if diagnostic:
            diagnostic = diagnostic.splitlines()[-1]
        raise SourceSdk2013RunnerError(
            f"Process exited with {completed.returncode}: {command[0]}"
            + (f": {diagnostic}" if diagnostic else "")
        )
    return report


def _read_json(path: Path, expected_schema: str) -> dict:
    if not path.is_file():
        raise SourceSdk2013RunnerError(
            f"Expected child report is unavailable: {path}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceSdk2013RunnerError(
            f"Child report is invalid JSON: {path}: {exc}"
        ) from exc
    if not isinstance(data, dict) or data.get("schema") != expected_schema:
        raise SourceSdk2013RunnerError(
            f"Child report has unexpected schema: {path}"
        )
    return data


def _validate_game_report(
        installation: SourceSdk2013Installation,
        report: dict,
):
    expected_profiles = {
        SINGLEPLAYER.key: "sdk2013_sp",
        MULTIPLAYER.key: "sdk2013_mp",
    }
    expected_profile = expected_profiles[installation.profile.key]
    installed = report.get("installation", {})
    if installed.get("profile") != expected_profile:
        raise SourceSdk2013RunnerError(
            f"{installation.profile.name} game report selected "
            f"{installed.get('profile')!r}, expected {expected_profile!r}"
        )
    if installed.get("app_id") != installation.profile.install_app_id:
        raise SourceSdk2013RunnerError(
            f"{installation.profile.name} game report app ID is "
            f"{installed.get('app_id')!r}"
        )
    if installed.get("steam_manifest", {}).get("build_id") != installation.build_id:
        raise SourceSdk2013RunnerError(
            f"{installation.profile.name} game report build ID does not "
            "match discovery"
        )
    summary = report.get("summary", {})
    if summary.get("FAIL", 0) or summary.get("WARN", 0):
        raise SourceSdk2013RunnerError(
            f"{installation.profile.name} installed-game gate has "
            f"unexpected results: {summary}"
        )
    if summary.get("PASS") != 3 or summary.get("KNOWN") != 2:
        raise SourceSdk2013RunnerError(
            f"{installation.profile.name} installed-game gate selected an "
            f"unexpected corpus: {summary}"
        )


def _profile_gate(
        installation: SourceSdk2013Installation,
        workspace_root: Path,
        blender: Path,
        timeout_seconds: float,
) -> dict:
    profile_workspace = workspace_root / installation.profile.key
    fixture = create_disposable_fixture(
        installation,
        profile_workspace / "fixture",
    )
    fixture_report = execute_disposable_fixture(fixture)

    reimport_path = profile_workspace / "fixture-reimport.json"
    reimport_process = _run_process(
        [
            str(blender),
            "-b",
            "--factory-startup",
            "--python",
            str(REIMPORT_SCRIPT),
            "--",
            "--game-root",
            str(fixture.game_root),
            "--asset",
            fixture.asset_name,
            "--json",
            str(reimport_path),
        ],
        timeout_seconds=timeout_seconds,
    )
    blender_reimport = _read_json(
        reimport_path,
        "sourceio.source-sdk-2013-fixture-reimport",
    )
    if blender_reimport.get("status") != "PASS":
        raise SourceSdk2013RunnerError(
            f"{installation.profile.name} Blender fixture re-import failed"
        )

    game_report_path = profile_workspace / "game-import.json"
    expected_profiles = {
        SINGLEPLAYER.key: "sdk2013_sp",
        MULTIPLAYER.key: "sdk2013_mp",
    }
    expected_profile = expected_profiles[installation.profile.key]
    source_test_root = installation.root / "sourcetest"
    game_process = _run_process(
        [
            str(blender),
            "-b",
            "--factory-startup",
            "--python",
            str(GAME_IMPORT_SCRIPT),
            "--",
            "--game",
            str(source_test_root),
            "--expect-profile",
            expected_profile,
            "--json",
            str(game_report_path),
        ],
        timeout_seconds=timeout_seconds,
    )
    game_report = _read_json(
        game_report_path,
        "sourceio.game-import-report",
    )
    _validate_game_report(installation, game_report)
    return {
        "installation": installation.installation_report(),
        "fixture": fixture_report,
        "blender_reimport": blender_reimport,
        "installed_game": game_report,
        "processes": {
            "fixture_reimport": reimport_process,
            "installed_game": game_process,
        },
    }


def _write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--singleplayer-root",
        type=Path,
        default=os.environ.get(SINGLEPLAYER.environment_variable),
    )
    parser.add_argument(
        "--multiplayer-root",
        type=Path,
        default=os.environ.get(MULTIPLAYER.environment_variable),
    )
    parser.add_argument(
        "--blender",
        type=Path,
        default=os.environ.get("SOURCEIO_BLENDER"),
    )
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    if args.singleplayer_root is None:
        parser.error(
            "--singleplayer-root or "
            f"{SINGLEPLAYER.environment_variable} is required"
        )
    if args.multiplayer_root is None:
        parser.error(
            "--multiplayer-root or "
            f"{MULTIPLAYER.environment_variable} is required"
        )
    if args.blender is None:
        parser.error("--blender or SOURCEIO_BLENDER is required")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        blender = args.blender.expanduser().resolve(strict=True)
    except OSError as exc:
        parser.error(f"Blender is unavailable: {exc}")
    if not blender.is_file():
        parser.error(f"Blender is not a file: {blender}")

    report = {
        "schema": "sourceio.source-sdk-2013-gate",
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "FAIL",
        "repository": str(REPO_ROOT),
        "blender": file_fingerprint(blender),
        "profiles": {},
    }
    temporary = tempfile.TemporaryDirectory(prefix="sourceio_sdk2013_gate_")
    workspace_root = Path(temporary.name)
    exit_code = 1
    try:
        roots = {
            SINGLEPLAYER.key: args.singleplayer_root,
            MULTIPLAYER.key: args.multiplayer_root,
        }
        installations = {
            profile.key: SourceSdk2013Installation.discover(
                profile,
                roots[profile.key],
            )
            for profile in SOURCE_SDK_2013_PROFILES
        }
        for profile in SOURCE_SDK_2013_PROFILES:
            installation = installations[profile.key]
            report["profiles"][profile.key] = _profile_gate(
                installation,
                workspace_root,
                blender,
                args.timeout,
            )
        report["summary"] = {
            "profiles": len(report["profiles"]),
            "compiler_roundtrips": len(report["profiles"]),
            "blender_reimports": len(report["profiles"]),
            "installed_game_pass": sum(
                profile_report["installed_game"]["summary"]["PASS"]
                for profile_report in report["profiles"].values()
            ),
            "installed_game_known": sum(
                profile_report["installed_game"]["summary"]["KNOWN"]
                for profile_report in report["profiles"].values()
            ),
            "installed_game_warn": sum(
                profile_report["installed_game"]["summary"]["WARN"]
                for profile_report in report["profiles"].values()
            ),
            "installed_game_fail": sum(
                profile_report["installed_game"]["summary"]["FAIL"]
                for profile_report in report["profiles"].values()
            ),
        }
        report["status"] = "PASS"
        exit_code = 0
    except (
            OSError,
            ValueError,
            SourceSdk2013DiscoveryError,
            SourceSdk2013GateError,
            SourceSdk2013RunnerError,
    ) as exc:
        report["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
    finally:
        cleanup_error = None
        try:
            temporary.cleanup()
        except OSError as exc:
            cleanup_error = str(exc)
        workspace_removed = not workspace_root.exists()
        report["cleanup"] = {
            "workspace": str(workspace_root),
            "removed": workspace_removed,
        }
        if cleanup_error is not None:
            report["cleanup"]["error"] = cleanup_error
        if cleanup_error is not None or not workspace_removed:
            report["status"] = "FAIL"
            report["error"] = {
                "type": "SourceSdk2013CleanupError",
                "message": cleanup_error or (
                    f"Temporary workspace still exists: {workspace_root}"
                ),
            }
            exit_code = 1
        _write_report(args.json.expanduser().resolve(strict=False), report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
