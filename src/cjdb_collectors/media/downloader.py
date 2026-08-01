"""HTTP media download implementation."""

from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
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

    def download(self, url: str) -> DownloadResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MediaDownloadError("media URL must use HTTP or HTTPS")
        self.media_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(parsed.path).suffix[:10] or ".mp4"
        target = self.media_dir / f"{uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        size = 0
        try:
            with self._client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type")
                if content_type and not (
                    content_type.startswith("video/")
                    or content_type.startswith("audio/")
                    or content_type.startswith("image/")
                    or content_type == "application/octet-stream"
                ):
                    raise MediaDownloadError(
                        f"unsupported media content type: {content_type}"
                    )
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
            target.unlink(missing_ok=True)
            raise MediaDownloadError(str(exc)) from exc
        except MediaDownloadError:
            target.unlink(missing_ok=True)
            raise
        if not target.suffix:
            guessed = mimetypes.guess_extension(content_type or "")
            if guessed:
                renamed = target.with_suffix(guessed)
                target.rename(renamed)
                target = renamed
        return DownloadResult(target, digest.hexdigest(), size, content_type)

    def close(self) -> None:
        self._client.close()
