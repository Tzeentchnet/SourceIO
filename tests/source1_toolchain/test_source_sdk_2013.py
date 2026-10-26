from __future__ import annotations

import json
from pathlib import Path

import pytest

from SourceIO.tests.source1_toolchain.source_sdk_2013 import (
    MULTIPLAYER,
    SINGLEPLAYER,
    SourceSdk2013DiscoveryError,
    SourceSdk2013Installation,
    create_disposable_fixture,
)


def _write_installation(
        tmp_path: Path,
        profile,
        *,
        manifest_app_id: int | None = None,
        content_app_id: int | None = None,
        install_directory: str | None = None,
) -> Path:
    steamapps = tmp_path / "steamapps"
    root = steamapps / "common" / profile.name
    game_root = root / profile.game_directory
    tool_root = root / "bin"
    game_root.mkdir(parents=True)
    tool_root.mkdir()
    manifest_install_directory = install_directory or profile.name
    (steamapps / f"appmanifest_{profile.install_app_id}.acf").write_text(
        '"AppState"\n'
        "{\n"
        f'    "appid" "{manifest_app_id or profile.install_app_id}"\n'
        f'    "installdir" "{manifest_install_directory}"\n'
        '    "buildid" "123456"\n'
        "}\n",
        encoding="utf-8",
    )
    (game_root / "gameinfo.txt").write_text(
        '"GameInfo"\n'
        "{\n"
        '    "FileSystem"\n'
        "    {\n"
        f'        "SteamAppId" "{content_app_id or profile.content_app_id}"\n'
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    (tool_root / "vtex.exe").write_bytes(b"MZvtex")
    (tool_root / "studiomdl.exe").write_bytes(b"MZstudiomdl")
    for relative_path in profile.fingerprint_files:
        path = root.joinpath(*relative_path.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative_path.encode("ascii"))
    return root


@pytest.mark.parametrize("profile", (SINGLEPLAYER, MULTIPLAYER))
def test_discovers_strict_sdk_profile_and_reports_hashes(tmp_path, profile):
    root = _write_installation(tmp_path, profile)

    installation = SourceSdk2013Installation.discover(profile, root)
    report = installation.installation_report()

    assert installation.root == root.resolve()
    assert installation.build_id == "123456"
    assert report["install_app_id"] == profile.install_app_id
    assert report["content_app_id"] == profile.content_app_id
    assert report["files"]["vtex.exe"]["sha256"]
    assert report["files"]["studiomdl.exe"]["sha256"]
    assert all(entry["exists"] for entry in report["files"].values())
    json.dumps(report)


def test_rejects_manifest_for_another_app(tmp_path):
    root = _write_installation(
        tmp_path,
        SINGLEPLAYER,
        manifest_app_id=MULTIPLAYER.install_app_id,
    )

    with pytest.raises(SourceSdk2013DiscoveryError, match="manifest app ID"):
        SourceSdk2013Installation.discover(SINGLEPLAYER, root)


def test_rejects_manifest_install_directory_mismatch(tmp_path):
    root = _write_installation(
        tmp_path,
        SINGLEPLAYER,
        install_directory="Legacy Source SDK",
    )

    with pytest.raises(SourceSdk2013DiscoveryError, match="manifest resolves"):
        SourceSdk2013Installation.discover(SINGLEPLAYER, root)


def test_rejects_wrong_content_profile(tmp_path):
    root = _write_installation(
        tmp_path,
        MULTIPLAYER,
        content_app_id=SINGLEPLAYER.content_app_id,
    )

    with pytest.raises(SourceSdk2013DiscoveryError, match="content SteamAppId"):
        SourceSdk2013Installation.discover(MULTIPLAYER, root)


def test_rejects_missing_compiler(tmp_path):
    root = _write_installation(tmp_path, SINGLEPLAYER)
    (root / "bin" / "studiomdl.exe").unlink()

    with pytest.raises(SourceSdk2013DiscoveryError, match="compiler is unavailable"):
        SourceSdk2013Installation.discover(SINGLEPLAYER, root)


def test_creates_isolated_profile_correct_fixture(tmp_path):
    root = _write_installation(tmp_path, MULTIPLAYER)
    installation = SourceSdk2013Installation.discover(MULTIPLAYER, root)
    workspace = tmp_path / "fixture"

    fixture = create_disposable_fixture(installation, workspace)
    gameinfo = (fixture.game_root / "gameinfo.txt").read_text(encoding="utf-8")

    assert '"SteamAppId" "243750"' in gameinfo
    assert str(installation.game_root) in gameinfo
    assert str(installation.tool_root) in gameinfo
    assert fixture.content_root.is_dir()
    assert fixture.plan.compile_steps
    assert not fixture.model_path.exists()
