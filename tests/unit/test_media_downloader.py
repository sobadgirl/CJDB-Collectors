import httpx
import pytest

from cjdb_collectors.domains.media import HttpMediaDownloader, MediaDownloadError


def test_downloader_prefers_content_type_extension_for_extensionless_image_url(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/webp"},
            content=b"image",
        )

    downloader = HttpMediaDownloader(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = downloader.download("https://sns.example/image-token?format=webp", media_type="image")

    assert result.path.suffix == ".webp"
    assert result.path.read_bytes() == b"image"
    assert result.content_type == "image/webp"


def test_downloader_uses_declared_media_type_when_content_type_is_generic(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"image",
        )

    downloader = HttpMediaDownloader(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = downloader.download("https://cdn.example/image-token", media_type="image")

    assert result.path.suffix == ".jpg"


def test_downloader_rejects_content_type_that_conflicts_with_declared_media_type(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=b"video",
        )

    downloader = HttpMediaDownloader(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(MediaDownloadError, match="expected image media"):
        downloader.download("https://cdn.example/file", media_type="image")

    assert list(tmp_path.iterdir()) == []


def test_downloader_can_write_to_safe_subdirectory(tmp_path):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "video/mp4"},
            content=b"video",
        )

    downloader = HttpMediaDownloader(
        tmp_path,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = downloader.download(
        "https://cdn.example/video",
        media_type="video",
        subdir="731234/video",
    )

    assert result.path.parent == tmp_path / "731234" / "video"
    assert result.path.suffix == ".mp4"


def test_downloader_rejects_escaping_subdirectory(tmp_path):
    downloader = HttpMediaDownloader(tmp_path)

    with pytest.raises(MediaDownloadError, match="escapes media directory"):
        downloader.download(
            "https://cdn.example/video.mp4",
            media_type="video",
            subdir="../outside",
        )
