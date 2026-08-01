from __future__ import annotations

from pathlib import Path

from .base import InvalidOperationError, NotFoundError


class LocalFileService:
    def __init__(
        self,
        roots: list[Path],
        allowed_extensions: set[str],
        *,
        max_entries: int = 500,
    ) -> None:
        self._roots = [root.expanduser().resolve() for root in roots]
        self._allowed_extensions = {
            extension.lower().lstrip(".") for extension in allowed_extensions
        }
        self._max_entries = max_entries

    def browse(self, root_id: str | None = None, path: str = "") -> dict:
        roots = self._root_descriptions()
        if root_id is None:
            return {
                "roots": roots,
                "current": None,
                "parent_path": None,
                "entries": [],
            }

        root = self._root(root_id)
        directory = self._resolve(root, path)
        if not directory.is_dir():
            raise InvalidOperationError("selected path is not a directory")

        entries = []
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.casefold()),
            )
        except OSError as exc:
            raise InvalidOperationError("directory cannot be read") from exc

        for child in children:
            if child.name.startswith("."):
                continue
            try:
                resolved = child.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    continue
                relative_path = resolved.relative_to(root).as_posix()
                if resolved.is_dir():
                    entries.append(
                        {
                            "name": child.name,
                            "path": relative_path,
                            "kind": "directory",
                        }
                    )
                elif self._is_allowed_file(resolved):
                    entries.append(
                        {
                            "name": child.name,
                            "path": relative_path,
                            "kind": "file",
                            "size_bytes": resolved.stat().st_size,
                        }
                    )
            except OSError:
                continue
            if len(entries) >= self._max_entries:
                break

        relative_directory = directory.relative_to(root).as_posix()
        if relative_directory == ".":
            relative_directory = ""
        parent_path = (
            Path(relative_directory).parent.as_posix() if relative_directory else None
        )
        if parent_path == ".":
            parent_path = ""
        return {
            "roots": roots,
            "current": {
                "root_id": root_id,
                "root_name": self._root_name(root),
                "path": relative_directory,
            },
            "parent_path": parent_path,
            "entries": entries,
        }

    def resolve_file(self, root_id: str, path: str) -> Path:
        root = self._root(root_id)
        selected = self._resolve(root, path)
        if not selected.is_file():
            raise InvalidOperationError("selected path is not a file")
        if not self._is_allowed_file(selected):
            raise InvalidOperationError("selected file type is not supported")
        return selected

    def _root_descriptions(self) -> list[dict[str, str]]:
        return [
            {"id": str(index), "name": self._root_name(root)}
            for index, root in enumerate(self._roots)
            if root.is_dir()
        ]

    def _root(self, root_id: str) -> Path:
        try:
            root = self._roots[int(root_id)]
        except (ValueError, IndexError) as exc:
            raise NotFoundError("local media root not found") from exc
        if not root.is_dir():
            raise NotFoundError("local media root not found")
        return root

    @staticmethod
    def _root_name(root: Path) -> str:
        return root.name or str(root)

    @staticmethod
    def _resolve(root: Path, path: str) -> Path:
        relative = Path(path or ".")
        if relative.is_absolute():
            raise InvalidOperationError("absolute paths are not accepted")
        try:
            selected = (root / relative).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise NotFoundError("local path not found") from exc
        if not selected.is_relative_to(root):
            raise InvalidOperationError("path is outside the configured media root")
        return selected

    def _is_allowed_file(self, path: Path) -> bool:
        return path.suffix.lower().lstrip(".") in self._allowed_extensions
