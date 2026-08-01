from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlmodel import select

from cjdb_collectors.data_provider import (
    AwemeData,
    AwemeProviderMixin,
    CommentProviderMixin,
    FetchAwemeRequest,
    FetchCommentsRequest,
    ResolveVideoRequest,
)
from cjdb_collectors.media import HttpMediaDownloader
from cjdb_collectors.models import (
    Aweme,
    ContentType,
    GroupAweme,
    Platform,
    TaskStatus,
    VideoTranscription,
)

from .base import (
    InvalidOperationError,
    NotFoundError,
    SessionFactory,
    apply_changes,
    as_uuid,
    now_utc,
)
from .data_providers import DataProviderService
from .store_relations import ensure_aweme_store_relations


class AwemeService:
    def __init__(
        self,
        session_factory: SessionFactory,
        data_providers: DataProviderService,
        media_downloader: HttpMediaDownloader,
    ) -> None:
        self._session = session_factory
        self.data_providers = data_providers
        self.media_downloader = media_downloader

    def create(
        self,
        source_url: str | None = None,
        *,
        url: str | None = None,
        aweme_url: str | None = None,
        content: str | None = None,
        platform: Platform | str | None = None,
        platform_aweme_id: str | None = None,
        content_type: ContentType | str | None = None,
        group_ids: list[UUID | str] | None = None,
        download_video: bool = False,
        comments: bool = False,
        collect_comments: bool | None = None,
        transcribe: bool = False,
    ) -> Aweme:
        selected_url = source_url or url or content
        if not selected_url:
            raise ValueError("source_url is required")
        host = (urlparse(selected_url).hostname or "unknown").lower()
        selected_platform = self._normalize_platform(platform or host, selected_url)
        selected_content_type = ContentType(
            content_type or self._default_content_type(selected_platform, selected_url)
        )
        selected_platform_aweme_id = (
            platform_aweme_id
            or self._extract_platform_aweme_id(selected_platform, selected_url)
        )
        comments_requested = comments if collect_comments is None else collect_comments
        with self._session() as session:
            aweme = Aweme(
                source_url=selected_url,
                aweme_url=aweme_url or selected_url,
                platform=selected_platform,
                platform_aweme_id=selected_platform_aweme_id,
                content_type=selected_content_type,
                collection_status=TaskStatus.NOT_REQUESTED,
                media_download_status=(
                    TaskStatus.PENDING
                    if download_video or transcribe
                    else TaskStatus.NOT_REQUESTED
                ),
                comment_collection_status=(
                    TaskStatus.PENDING
                    if comments_requested
                    else TaskStatus.NOT_REQUESTED
                ),
                video_transcription_status=(
                    TaskStatus.PENDING if transcribe else TaskStatus.NOT_REQUESTED
                ),
            )
            session.add(aweme)
            session.flush()
            self._set_groups(session, aweme.id, group_ids or [])
            if transcribe:
                session.add(VideoTranscription(aweme_id=aweme.id))
            session.refresh(aweme)
            return aweme

    add = create

    def list(
        self,
        group_ids: list[UUID | str] | None = None,
        *,
        account_id: UUID | str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Aweme]:
        with self._session() as session:
            statement = select(Aweme).where(Aweme.deleted_at.is_(None))
            if account_id:
                statement = statement.where(Aweme.account_id == as_uuid(account_id))
            if group_ids:
                ids = [as_uuid(value) for value in group_ids]
                statement = (
                    statement.join(GroupAweme)
                    .where(GroupAweme.group_id.in_(ids))
                    .distinct()
                )
            if status:
                statement = statement.where(
                    Aweme.collection_status == TaskStatus(status)
                )
            statement = statement.order_by(Aweme.created_at.desc()).offset(offset)
            if limit is not None:
                statement = statement.limit(limit)
            return list(session.exec(statement).all())

    def get(self, aweme_id: UUID | str) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            return aweme

    def search(
        self,
        keyword: str,
        *,
        group_ids: list[UUID | str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Aweme]:
        needle = keyword.strip().lower()
        if not needle:
            return []
        matches: list[Aweme] = []
        for item in self.list(group_ids=group_ids):
            haystack = " ".join(
                str(value or "")
                for value in (
                    item.title,
                    item.description,
                    item.source_url,
                    item.platform.value,
                    item.platform_aweme_id,
                )
            ).lower()
            if needle in haystack:
                matches.append(item)
        return matches[offset : offset + limit]

    def update(self, aweme_id: UUID | str, **changes) -> Aweme:
        allowed = {
            "title",
            "description",
            "account_id",
            "video_url",
            "video_path",
            "cover_url",
            "cover_path",
            "photos",
            "photo_paths",
            "aweme_url",
            "platform",
            "platform_aweme_id",
            "content_type",
            "extra_data_json",
        }
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            if "platform" in changes and changes["platform"] is not None:
                changes["platform"] = self._normalize_platform(
                    changes["platform"],
                    changes.get("source_url") or aweme.source_url,
                )
            apply_changes(aweme, changes, allowed)
            session.add(aweme)
            session.flush()
            session.refresh(aweme)
            return aweme

    def delete(
        self,
        aweme_id: UUID | str,
        *,
        delete_downloaded_files: bool = False,
    ) -> None:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            files = self.deletion_files(aweme)
            aweme.deleted_at = now_utc()
            session.add(aweme)
        if delete_downloaded_files:
            self._delete_local_files(files)

    def deletion_files(self, aweme: Aweme) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        candidates = (
            ("video", "视频", aweme.video_path),
            ("cover", "封面", aweme.cover_path),
        )
        for kind, label, value in candidates:
            self._append_local_file(files, kind, label, value)
        for index, photo_path in enumerate(aweme.photo_paths, start=1):
            self._append_local_file(files, "photo", f"图片 {index}", photo_path)
        return files

    def request_collection(self, aweme_id: UUID | str) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            aweme.collection_status = TaskStatus.PENDING
            aweme.collection_next_run_at = None
            aweme.collection_error = None
            aweme.collection_run_token = None
            session.add(aweme)
            session.flush()
            session.refresh(aweme)
            return aweme

    retry = request_collection

    def request_comment_collection(self, aweme_id: UUID | str) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            aweme.comment_collection_status = TaskStatus.PENDING
            aweme.comment_collection_next_run_at = None
            aweme.comment_collection_error = None
            aweme.comment_collection_run_token = None
            session.add(aweme)
            session.flush()
            session.refresh(aweme)
            return aweme

    def cancel_collection(self, aweme_id: UUID | str) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            aweme.collection_status = TaskStatus.CANCELLED
            aweme.collection_next_run_at = None
            aweme.collection_run_token = None
            aweme.collection_heartbeat_at = None
            session.add(aweme)
            session.flush()
            session.refresh(aweme)
            return aweme

    def cancel_comment_collection(self, aweme_id: UUID | str) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            aweme.comment_collection_status = TaskStatus.CANCELLED
            aweme.comment_collection_next_run_at = None
            aweme.comment_collection_run_token = None
            aweme.comment_collection_heartbeat_at = None
            session.add(aweme)
            session.flush()
            session.refresh(aweme)
            return aweme

    def fetch_aweme(
        self,
        aweme: Aweme,
    ) -> Aweme:
        """Fetch and persist one already-created Aweme."""

        self._require_persisted(aweme)
        run_token = self._begin_collection(aweme.id)
        try:
            data = self._fetch_remote_aweme(aweme)
        except Exception as exc:
            self._fail_collection(aweme.id, run_token, exc)
            raise
        with self._session() as session:
            current = session.get(Aweme, aweme.id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            if current.collection_run_token != run_token:
                return current
            self._apply_fetched_data(current, data)
            current.collection_status = TaskStatus.SUCCEEDED
            current.collection_finished_at = now_utc()
            current.collection_heartbeat_at = now_utc()
            current.collection_error = None
            current.collection_run_token = None
            current.last_collected_at = now_utc()
            session.add(current)
            session.flush()
            session.refresh(current)
            return current

    fetch_data = fetch_aweme

    def fetch_comments(
        self,
        aweme: Aweme,
    ) -> Aweme:
        self._require_persisted(aweme)
        run_token = self._begin_comment_collection(aweme.id)
        platform = self._normalize_platform(aweme.platform, aweme.source_url)
        platform_id = self._require_platform_id(aweme)
        try:
            provider = self.data_providers.get_comment_provider(
                platform,
                aweme.source_url,
            )
            if not isinstance(provider, CommentProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support comments"
                )
            page = provider.fetch_comments(
                FetchCommentsRequest(
                    platform=platform,
                    platform_aweme_id=platform_id,
                    source_url=aweme.source_url,
                    cursor=aweme.comments_cursor,
                )
            )
        except Exception as exc:
            self._fail_comment_collection(aweme.id, run_token, exc)
            raise

        with self._session() as session:
            current = session.get(Aweme, aweme.id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            if current.comment_collection_run_token != run_token:
                return current
            current.comments_json = page.comments
            current.comments_cursor = page.next_cursor
            current.comments_collected_at = now_utc()
            current.comment_collection_status = TaskStatus.SUCCEEDED
            current.comment_collection_finished_at = now_utc()
            current.comment_collection_error = None
            current.comment_collection_run_token = None
            session.add(current)
            session.flush()
            session.refresh(current)
            return current

    def _begin_collection(self, aweme_id: UUID) -> str:
        with self._session() as session:
            current = session.get(Aweme, aweme_id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            claimed = bool(
                current.collection_run_token
                and current.collection_status == TaskStatus.RUNNING
            )
            run_token = current.collection_run_token if claimed else uuid4().hex
            current.collection_status = TaskStatus.RUNNING
            current.collection_started_at = now_utc()
            current.collection_heartbeat_at = now_utc()
            current.collection_next_run_at = None
            current.collection_error = None
            current.collection_run_token = run_token
            if not claimed:
                current.collection_attempt_count += 1
            session.add(current)
            return run_token

    def _fail_collection(self, aweme_id: UUID, run_token: str, exc: Exception) -> None:
        with self._session() as session:
            current = session.get(Aweme, aweme_id)
            if (
                not current
                or current.deleted_at
                or current.collection_run_token != run_token
            ):
                return
            current.collection_status = TaskStatus.FAILED
            current.collection_finished_at = now_utc()
            current.collection_error = str(exc)
            current.collection_run_token = None
            current.collection_next_run_at = None
            session.add(current)

    def _begin_comment_collection(self, aweme_id: UUID) -> str:
        with self._session() as session:
            current = session.get(Aweme, aweme_id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            claimed = bool(
                current.comment_collection_run_token
                and current.comment_collection_status == TaskStatus.RUNNING
            )
            run_token = (
                current.comment_collection_run_token if claimed else uuid4().hex
            )
            current.comment_collection_status = TaskStatus.RUNNING
            current.comment_collection_started_at = now_utc()
            current.comment_collection_heartbeat_at = now_utc()
            current.comment_collection_next_run_at = None
            current.comment_collection_error = None
            current.comment_collection_run_token = run_token
            if not claimed:
                current.comment_collection_attempt_count += 1
            session.add(current)
            return run_token

    def _fail_comment_collection(
        self, aweme_id: UUID, run_token: str, exc: Exception
    ) -> None:
        with self._session() as session:
            current = session.get(Aweme, aweme_id)
            if (
                not current
                or current.deleted_at
                or current.comment_collection_run_token != run_token
            ):
                return
            current.comment_collection_status = TaskStatus.FAILED
            current.comment_collection_finished_at = now_utc()
            current.comment_collection_error = str(exc)
            current.comment_collection_run_token = None
            current.comment_collection_next_run_at = None
            session.add(current)

    def download_video(
        self,
        aweme: Aweme,
    ) -> Aweme:
        self._require_persisted(aweme)
        run_token = self._begin_media_download(aweme.id)
        video_url = aweme.video_url
        platform = self._normalize_platform(aweme.platform, aweme.source_url)
        try:
            provider = self.data_providers.get_aweme_provider(
                platform,
                aweme.source_url,
            )
            if not isinstance(provider, AwemeProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support awemes"
                )
            resolved = provider.resolve_video(
                ResolveVideoRequest(
                    platform=platform,
                    platform_aweme_id=self._require_platform_id(aweme),
                    source_url=aweme.source_url,
                    media_url=video_url,
                )
            )
            if resolved:
                video_url = resolved.url
        except Exception as exc:
            self._fail_media_download(aweme.id, run_token, exc)
            raise
        if not video_url:
            exc = InvalidOperationError("aweme has no downloadable video URL")
            self._fail_media_download(aweme.id, run_token, exc)
            raise exc

        try:
            result = self.media_downloader.download(video_url)
        except Exception as exc:
            self._fail_media_download(aweme.id, run_token, exc)
            raise
        with self._session() as session:
            current = session.get(Aweme, aweme.id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            if current.media_download_run_token != run_token:
                return current
            current.video_url = video_url
            current.video_path = str(result.path)
            current.media_download_status = TaskStatus.SUCCEEDED
            current.media_download_finished_at = now_utc()
            current.media_download_error = None
            current.media_download_run_token = None
            pending = session.exec(
                select(VideoTranscription).where(
                    VideoTranscription.aweme_id == current.id,
                    VideoTranscription.video_path.is_(None),
                )
            ).all()
            for transcription in pending:
                transcription.video_path = str(result.path)
                transcription.video_sha256 = result.sha256
                session.add(transcription)
            session.add(current)
            session.flush()
            session.refresh(current)
            return current

    def download_images(self, aweme: Aweme) -> Aweme:
        self._require_persisted(aweme)
        run_token = self._begin_media_download(aweme.id)
        if not aweme.photos:
            exc = InvalidOperationError("aweme has no downloadable images")
            self._fail_media_download(aweme.id, run_token, exc)
            raise exc
        try:
            paths = [
                str(self.media_downloader.download(url).path)
                for url in aweme.photos
            ]
        except Exception as exc:
            self._fail_media_download(aweme.id, run_token, exc)
            raise
        with self._session() as session:
            current = session.get(Aweme, aweme.id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            if current.media_download_run_token != run_token:
                return current
            current.photo_paths = paths
            current.media_download_status = TaskStatus.SUCCEEDED
            current.media_download_finished_at = now_utc()
            current.media_download_error = None
            current.media_download_run_token = None
            session.add(current)
            session.flush()
            session.refresh(current)
            return current

    def _begin_media_download(self, aweme_id: UUID) -> str:
        with self._session() as session:
            current = session.get(Aweme, aweme_id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            claimed = bool(
                current.media_download_run_token
                and current.media_download_status == TaskStatus.RUNNING
            )
            run_token = current.media_download_run_token if claimed else uuid4().hex
            current.media_download_status = TaskStatus.RUNNING
            current.media_download_started_at = now_utc()
            current.media_download_heartbeat_at = now_utc()
            current.media_download_next_run_at = None
            current.media_download_error = None
            current.media_download_run_token = run_token
            if not claimed:
                current.media_download_attempt_count += 1
            session.add(current)
            return run_token

    def _fail_media_download(
        self, aweme_id: UUID, run_token: str, exc: Exception
    ) -> None:
        with self._session() as session:
            current = session.get(Aweme, aweme_id)
            if (
                not current
                or current.deleted_at
                or current.media_download_run_token != run_token
            ):
                return
            current.media_download_status = TaskStatus.FAILED
            current.media_download_finished_at = now_utc()
            current.media_download_error = str(exc)
            current.media_download_run_token = None
            current.media_download_next_run_at = None
            session.add(current)

    def set_groups(self, aweme_id: UUID | str, group_ids: list[UUID | str]) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            self._set_groups(session, aweme.id, group_ids)
            session.refresh(aweme)
            return aweme

    def group_ids(self, aweme_id: UUID | str) -> list[UUID]:
        aweme_uuid = as_uuid(aweme_id)
        with self._session() as session:
            return list(
                session.exec(
                    select(GroupAweme.group_id).where(GroupAweme.aweme_id == aweme_uuid)
                ).all()
            )

    def _fetch_remote_aweme(self, aweme: Aweme) -> AwemeData:
        platform = self._normalize_platform(aweme.platform, aweme.source_url)
        platform_id = self._require_platform_id(aweme)
        provider = self.data_providers.get_aweme_provider(
            platform,
            aweme.source_url,
        )
        if not isinstance(provider, AwemeProviderMixin):
            raise InvalidOperationError(
                f"provider {provider.namespace} does not support awemes"
            )
        return provider.fetch_aweme(
            FetchAwemeRequest(
                platform=platform,
                platform_aweme_id=platform_id,
                content_type=aweme.content_type,
                source_url=aweme.source_url,
            )
        )

    @staticmethod
    def _apply_fetched_data(aweme: Aweme, data: AwemeData) -> None:
        aweme.platform_aweme_id = data.platform_aweme_id
        aweme.content_type = data.content_type
        aweme.title = data.title
        aweme.description = data.description
        aweme.published_at = data.published_at
        aweme.video_url = data.video_url
        aweme.photos = data.photos
        aweme.cover_url = data.cover_url
        aweme.play_count = data.play_count
        aweme.like_count = data.like_count
        aweme.collect_count = data.collect_count
        aweme.share_count = data.share_count
        aweme.comment_count = data.comment_count
        aweme.extra_data_json = {
            key: value
            for key, value in data.extra_data_json.items()
            if value is not None
        }

    @staticmethod
    def _require_persisted(aweme: Aweme) -> None:
        if not aweme.id:
            raise InvalidOperationError("aweme must be persisted before collection")

    @staticmethod
    def _require_platform_id(aweme: Aweme) -> str:
        platform = AwemeService._normalize_platform(aweme.platform, aweme.source_url)
        if platform == Platform.WECHAT_MP:
            return aweme.platform_aweme_id or aweme.source_url
        platform_id = aweme.platform_aweme_id or AwemeService._extract_platform_aweme_id(
            platform,
            aweme.aweme_url or aweme.source_url,
        )
        if not platform_id:
            raise InvalidOperationError(
                "platform_aweme_id is required; provide a platform ID or a supported work URL"
            )
        return platform_id

    @staticmethod
    def _append_local_file(
        files: list[dict[str, str]],
        kind: str,
        label: str,
        value: str | None,
    ) -> None:
        if not value or value.startswith(("http://", "https://")):
            return
        path = Path(value).expanduser()
        if path.exists() and path.is_file():
            files.append({"kind": kind, "label": label, "path": str(path)})

    @staticmethod
    def _delete_local_files(files: list[dict[str, str]]) -> None:
        for file in files:
            path = Path(file["path"]).expanduser()
            try:
                if path.exists() and path.is_file():
                    path.unlink()
            except OSError:
                pass

    @staticmethod
    def _extract_platform_aweme_id(platform: str, source_url: str) -> str | None:
        value = source_url.strip()
        if not value:
            return None
        if "://" not in value and "/" not in value and "?" not in value:
            return value

        normalized = AwemeService._normalize_platform(platform, value)
        if normalized == Platform.DOUYIN:
            for pattern in (
                r"/video/(\d+)",
                r"/note/(\d+)",
                r"[?&](?:aweme_id|modal_id|item_id)=(\d+)",
            ):
                match = re.search(pattern, value)
                if match:
                    return match.group(1)
        if normalized == Platform.XIAOHONGSHU:
            for pattern in (
                r"/explore/([^/?#]+)",
                r"/discovery/item/([^/?#]+)",
                r"[?&]note_id=([^&#]+)",
            ):
                match = re.search(pattern, value)
                if match:
                    return match.group(1)
        if normalized == Platform.WECHAT_CHANNELS:
            for pattern in (
                r"[?&](?:object_id|objectId|feed_id|feedId)=([^&#]+)",
                r"/([^/?#]+)$",
            ):
                match = re.search(pattern, value)
                if match:
                    return match.group(1)
        if normalized == Platform.WECHAT_MP:
            return value
        return None

    @staticmethod
    def _default_content_type(platform: Platform, source_url: str) -> ContentType:
        if platform == Platform.WECHAT_MP:
            return ContentType.ARTICLE
        if platform in {Platform.DOUYIN, Platform.WECHAT_CHANNELS}:
            return ContentType.VIDEO
        normalized_url = source_url.lower()
        if "video" in normalized_url:
            return ContentType.VIDEO
        return ContentType.UNKNOWN

    @staticmethod
    def _normalize_platform(platform: Platform | str, source_url: str) -> Platform:
        if isinstance(platform, Platform):
            return platform
        value = platform.lower().strip()
        host = (urlparse(source_url).hostname or "").lower()
        if value in {"douyin", "www.douyin.com", "v.douyin.com"} or host.endswith(
            "douyin.com"
        ):
            return Platform.DOUYIN
        if value in {
            "xiaohongshu",
            "www.xiaohongshu.com",
            "xhslink.com",
        } or host.endswith(("xiaohongshu.com", "xhslink.com")):
            return Platform.XIAOHONGSHU
        if value in {"wechat_mp", "mp.weixin.qq.com"} or host == "mp.weixin.qq.com":
            return Platform.WECHAT_MP
        if (
            value
            in {
                "wechat_channels",
                "weixin_channels",
                "weixin.qq.com",
            }
            or host == "weixin.qq.com"
        ):
            return Platform.WECHAT_CHANNELS
        raise InvalidOperationError(f"unsupported platform: {platform}")

    @staticmethod
    def _set_groups(session, aweme_id: UUID, group_ids: list[UUID | str]) -> None:
        desired = {as_uuid(value) for value in group_ids}
        existing = list(
            session.exec(
                select(GroupAweme).where(GroupAweme.aweme_id == aweme_id)
            ).all()
        )
        current = {item.group_id for item in existing}
        for item in existing:
            if item.group_id not in desired:
                session.delete(item)
        for group_id in desired - current:
            session.add(GroupAweme(group_id=group_id, aweme_id=aweme_id))
        ensure_aweme_store_relations(session, aweme_id, desired)
