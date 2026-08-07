"""HTTP media download implementation."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx


class MediaDownloadError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    sha256: str
    size_bytes: int
    content_type: str | None


class HttpMediaDownloader:
    _FALLBACK_SUFFIXES = {
        "image": ".jpg",
        "video": ".mp4",
        "audio": ".mp3",
    }

    def __init__(
        self,
        media_dir: str | Path,
        *,
        timeout_seconds: float = 120,
        max_bytes: int = 2 * 1024 * 1024 * 1024,
        client: httpx.Client | None = None,
    ) -> None:
        self.media_dir = Path(media_dir).resolve()
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self._client = client or httpx.Client(
            trust_env=False, timeout=timeout_seconds, follow_redirects=True
        )

    def download(
        self,
        url: str,
        *,
        media_type: Literal["image", "video", "audio"] | None = None,
        subdir: str | Path | None = None,
    ) -> DownloadResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MediaDownloadError("media URL must use HTTP or HTTPS")
        if media_type and media_type not in self._FALLBACK_SUFFIXES:
            raise MediaDownloadError(f"unsupported media type: {media_type}")
        target_dir = self.media_dir
        if subdir:
            target_dir = (self.media_dir / subdir).resolve()
            if not target_dir.is_relative_to(self.media_dir):
                raise MediaDownloadError("download subdir escapes media directory")
        target_dir.mkdir(parents=True, exist_ok=True)
        url_suffix = Path(parsed.path).suffix[:10]
        target: Path | None = None
        digest = hashlib.sha256()
        size = 0
        try:
            with self._client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type and not (
                    content_type.startswith(("video/", "audio/", "image/"))
                    or content_type == "application/octet-stream"
                ):
                    raise MediaDownloadError(
                        f"unsupported media content type: {content_type}"
                    )
                if (
                    media_type
                    and content_type
                    and content_type != "application/octet-stream"
                    and not content_type.startswith(f"{media_type}/")
                ):
                    raise MediaDownloadError(
                        f"expected {media_type} media but got {content_type}"
                    )
                content_type_for_suffix = (
                    "" if content_type == "application/octet-stream" else content_type
                )
                content_type_suffix = mimetypes.guess_extension(content_type_for_suffix)
                suffix = (
                    content_type_suffix
                    or url_suffix
                    or self._FALLBACK_SUFFIXES.get(media_type or "")
                    or ".bin"
                )
                target = target_dir / f"{uuid4().hex}{suffix}"
                with target.open("xb") as output:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.max_bytes:
                            raise MediaDownloadError(
                                "media exceeds configured size limit"
                            )
                        output.write(chunk)
                        digest.update(chunk)
        except (httpx.HTTPError, OSError) as exc:
            if target:
                target.unlink(missing_ok=True)
            raise MediaDownloadError(str(exc)) from exc
        except MediaDownloadError:
            if target:
                target.unlink(missing_ok=True)
            raise
        if target is None:
            raise MediaDownloadError("media download did not produce a file")
        return DownloadResult(target, digest.hexdigest(), size, content_type)

    def close(self) -> None:
        self._client.close()
