from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...source2.workshop import ContentRelativePath


class Source1AuthoringTargetError(ValueError):
    pass


def _resolved_beneath(
        root: Path,
        relative_path: ContentRelativePath,
        label: str,
) -> Path:
    resolved_root = root.resolve(strict=False)
    resolved_path = root.joinpath(*relative_path.parts).resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise Source1AuthoringTargetError(
            f"{label} escapes its configured root: {relative_path}"
        ) from exc
    return resolved_path


@dataclass(frozen=True, slots=True)
class Source1AuthoringTarget:
    game_root: Path
    content_root: Path

    def __post_init__(self):
        roots = {}
        for label, value in (
            ("game", self.game_root),
            ("SDK content", self.content_root),
        ):
            try:
                root = Path(value).expanduser().resolve(strict=True)
            except OSError as exc:
                raise Source1AuthoringTargetError(
                    f"Source 1 {label} root is unavailable: {value}"
                ) from exc
            if not root.is_dir():
                raise Source1AuthoringTargetError(
                    f"Source 1 {label} root is not a directory: {root}"
                )
            roots[label] = root

        gameinfo = roots["game"] / "gameinfo.txt"
        if not gameinfo.is_file():
            raise Source1AuthoringTargetError(
                f"Source 1 game root has no gameinfo.txt: {roots['game']}"
            )

        object.__setattr__(self, "game_root", roots["game"])
        object.__setattr__(self, "content_root", roots["SDK content"])

    def game_path(self, relative_path: str | ContentRelativePath) -> Path:
        return _resolved_beneath(
            self.game_root,
            ContentRelativePath(relative_path),
            "Game artifact",
        )

    def content_path(self, relative_path: str | ContentRelativePath) -> Path:
        return _resolved_beneath(
            self.content_root,
            ContentRelativePath(relative_path),
            "SDK content artifact",
        )
