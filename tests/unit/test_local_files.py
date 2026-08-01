from pathlib import Path

import pytest

from cjdb_collectors.services.base import InvalidOperationError
from cjdb_collectors.services.local_files import LocalFileService


def test_local_file_service_filters_files_and_blocks_escape(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "video.mp4").write_bytes(b"video")
    (root / "notes.txt").write_text("ignored", encoding="utf-8")
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")

    service = LocalFileService([root], {"mp4"})

    listing = service.browse("0")
    assert [(item["name"], item["kind"]) for item in listing["entries"]] == [
        ("nested", "directory"),
        ("video.mp4", "file"),
    ]
    assert service.resolve_file("0", "video.mp4") == root / "video.mp4"

    with pytest.raises(InvalidOperationError):
        service.resolve_file("0", "../outside.mp4")
