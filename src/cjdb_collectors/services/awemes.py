from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlmodel import select

from cjdb_collectors.domains.data_provider import (
    AwemeData,
    FetchAwemeRequest,
    FetchCommentsRequest,
    PageStopPolicy,
)
from cjdb_collectors.domains.media import HttpMediaDownloader
from cjdb_collectors.models import (
    Aweme,
    Comment,
    CommentKind,
    ContentType,
    ProjectAweme,
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
        project_ids: list[UUID | str] | None = None,
        download_video: bool = False,
        comments: bool = False,
        collect_comments: bool | None = None,
        comment_max_count: int | None = None,
        transcribe: bool = False,
    ) -> Aweme:
        selected_url = source_url or url or content or platform_aweme_id
        if not selected_url:
            raise ValueError("source_url or platform_aweme_id is required")
        # V1.0：作品触发转写必须先完成本地视频下载，转写任务只消费本地路径。
        if transcribe:
            download_video = True
        host = (urlparse(selected_url).hostname or "unknown").lower()
        selected_platform = self._normalize_platform(platform or host, selected_url)
        if selected_platform == Platform.WECHAT_CHANNELS:
            raise InvalidOperationError("视频号作品采集暂不支持")
        selected_content_type = ContentType(
            content_type or self._default_content_type(selected_platform, selected_url)
        )
        selected_platform_aweme_id = (
            platform_aweme_id
            or self._extract_platform_aweme_id(selected_platform, selected_url)
        )
        # V1.0 发布隐藏：忽略评论采集请求，避免创建作品时直接排入评论采集队列。
        # comments_requested = comments if collect_comments is None else collect_comments
        comments_requested = False
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
                video_transcription_status=TaskStatus.NOT_REQUESTED,
                comment_provider_state_json={},
            )
            session.add(aweme)
            session.flush()
            self._set_projects(
                session,
                aweme.id,
                project_ids or [],
                collect_comments_enabled=bool(comments_requested),
                comment_limit=max(1, int(comment_max_count)) if comment_max_count else None,
                download_video_enabled=bool(download_video or transcribe),
                transcribe_enabled=bool(transcribe),
            )
            session.refresh(aweme)
            return aweme

    add = create

    def list(
        self,
        project_ids: list[UUID | str] | None = None,
        *,
        account_id: str | None = None,
        platform_account_id: str | None = None,
        platform: Platform | str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Aweme]:
        with self._session() as session:
            statement = select(Aweme).where(Aweme.deleted_at.is_(None))
            selected_account_id = platform_account_id or account_id
            if selected_account_id:
                statement = statement.where(
                    Aweme.platform_account_id == selected_account_id
                )
            if platform:
                statement = statement.where(
                    Aweme.platform == self._normalize_platform(platform, "")
                )
            if project_ids:
                ids = [as_uuid(value) for value in project_ids]
                statement = (
                    statement.join(ProjectAweme)
                    .where(ProjectAweme.project_id.in_(ids))
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
        project_ids: list[UUID | str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Aweme]:
        needle = keyword.strip().lower()
        if not needle:
            return []
        matches: list[Aweme] = []
        for item in self.list(project_ids=project_ids):
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
            "platform_account_id",
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
        for index, photo_item in enumerate(aweme.photo_paths, start=1):
            photo_path = (
                photo_item.get("local_path")
                if isinstance(photo_item, dict)
                else photo_item
            )
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

    def request_media_download(self, aweme_id: UUID | str) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            if not aweme.cover_url and not aweme.video_url and not aweme.photos:
                raise InvalidOperationError("aweme has no downloadable media")
            aweme.media_download_status = TaskStatus.PENDING
            aweme.media_download_next_run_at = None
            aweme.media_download_error = None
            aweme.media_download_run_token = None
            session.add(aweme)
            session.flush()
            session.refresh(aweme)
            return aweme

    retry_media_download = request_media_download

    def cancel_media_download(self, aweme_id: UUID | str) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            aweme.media_download_status = TaskStatus.CANCELLED
            aweme.media_download_next_run_at = None
            aweme.media_download_run_token = None
            aweme.media_download_heartbeat_at = None
            session.add(aweme)
            session.flush()
            session.refresh(aweme)
            return aweme

    def request_comment_collection(
        self,
        aweme_id: UUID | str,
        *,
        max_comments: int | None = None,
        max_pages: int | None = None,
        earliest_date: datetime | str | None = None,
    ) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            # V1.0 发布隐藏：评论采集暂不开放，不创建 pending 任务。
            session.refresh(aweme)
            return aweme
            # 原逻辑保留，后续恢复评论采集时打开。
            if max_comments is not None:
                state = dict(aweme.comment_provider_state_json or {})
                default_state = dict(state.get("default") or {})
                default_state["max_comments"] = max(1, int(max_comments))
                if max_pages is not None:
                    default_state["max_pages"] = max(1, int(max_pages))
                if earliest_date:
                    default_state["earliest_date"] = (
                        earliest_date.isoformat()
                        if isinstance(earliest_date, datetime)
                        else str(earliest_date)
                    )
                state["default"] = default_state
                aweme.comment_provider_state_json = state
                self._raise_project_comment_limit(session, aweme.id, max_comments)
            elif max_pages is not None or earliest_date:
                state = dict(aweme.comment_provider_state_json or {})
                default_state = dict(state.get("default") or {})
                if max_pages is not None:
                    default_state["max_pages"] = max(1, int(max_pages))
                if earliest_date:
                    default_state["earliest_date"] = (
                        earliest_date.isoformat()
                        if isinstance(earliest_date, datetime)
                        else str(earliest_date)
                    )
                state["default"] = default_state
                aweme.comment_provider_state_json = state
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
        # V1.0 发布隐藏：评论采集不执行；保留原实现用于后续版本恢复。
        return aweme
        return self._fetch_comments(aweme, latest=False)

    def fetch_latest_comments(
        self,
        aweme: Aweme,
    ) -> Aweme:
        # V1.0 发布隐藏：评论采集不执行；保留原实现用于后续版本恢复。
        return aweme
        return self._fetch_comments(aweme, latest=True)

    def _fetch_comments(
        self,
        aweme: Aweme,
        *,
        latest: bool,
    ) -> Aweme:
        self._require_persisted(aweme)
        run_token = self._begin_comment_collection(aweme.id)
        platform = self._normalize_platform(aweme.platform, aweme.source_url)
        platform_id = self._require_platform_id(aweme)
        try:
            project_ids = self.project_ids(aweme.id)
            provider = self.data_providers.get_comment_provider(
                platform,
                aweme.source_url,
                str(project_ids[0]) if project_ids else None,
            )
            provider_state = self._comment_provider_state(aweme, provider.namespace)
            max_comments = self._comment_max_count(provider_state)
            max_comments = self._effective_comment_max_count(aweme.id, max_comments)
            stop_policy = PageStopPolicy(
                max_count=max_comments,
                max_pages=provider_state.get("max_pages") or 1,
                earliest_date=provider_state.get("earliest_date"),
            )
            request = FetchCommentsRequest(
                platform=platform,
                platform_aweme_id=platform_id,
                source_url=aweme.source_url,
                cursor=None
                if latest
                else provider_state.get("cursor") or aweme.comments_cursor,
                max_comments=max_comments,
                stop_policy=stop_policy,
                progress_payload={}
                if latest
                else dict(aweme.comment_history_progress_json or {}),
                extra=dict(provider_state.get("extra") or {}),
            )
            page = (
                provider.fetch_latest_comments(request)
                if latest
                else provider.fetch_history_comments(request)
            )
            comments = page.comments
            next_cursor = page.next_cursor
            if max_comments is not None and len(comments) > max_comments:
                comments = comments[:max_comments]
        except Exception as exc:
            self._fail_comment_collection(aweme.id, run_token, exc)
            raise

        with self._session() as session:
            current = session.get(Aweme, aweme.id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            if current.comment_collection_run_token != run_token:
                return current
            current.comments_json = comments
            current.comments_cursor = next_cursor
            if latest:
                current.comment_latest_progress_json = {
                    **page.progress_payload,
                    "last_request": page.request,
                    "last_synced_at": now_utc().isoformat(),
                }
            else:
                current.comment_history_progress_json = page.progress_payload
                current.comment_provider_state_json = self._set_comment_provider_state(
                    current.comment_provider_state_json,
                    provider.namespace,
                    {
                        **provider_state,
                        "cursor": next_cursor,
                        "max_comments": max_comments,
                    },
                )
            current.comments_collected_at = now_utc()
            current.comment_collection_status = (
                TaskStatus.SUCCEEDED if page.done else TaskStatus.PENDING
            )
            current.comment_collection_finished_at = now_utc()
            current.comment_collection_error = None
            current.comment_collection_run_token = None
            session.add(current)
            self._upsert_comments(
                session,
                aweme_id=current.id,
                provider_namespace=provider.namespace,
                comments=comments,
            )
            session.flush()
            session.refresh(current)
            return current

    @staticmethod
    def _comment_provider_state(aweme: Aweme, namespace: str) -> dict:
        state = dict(aweme.comment_provider_state_json or {})
        default_state = dict(state.get("default") or {})
        provider_state = dict(state.get(namespace) or {})
        return {**default_state, **provider_state}

    @staticmethod
    def _set_comment_provider_state(
        state: dict | None,
        namespace: str,
        provider_state: dict,
    ) -> dict:
        values = dict(state or {})
        values[namespace] = provider_state
        return values

    @staticmethod
    def _comment_max_count(provider_state: dict) -> int | None:
        value = provider_state.get("max_comments")
        if value in (None, ""):
            return None
        return max(1, int(value))

    @classmethod
    def _upsert_comments(
        cls,
        session,
        *,
        aweme_id: UUID,
        provider_namespace: str,
        comments: list[dict],
    ) -> None:
        parent_by_external_id: dict[str, UUID] = {}
        for index, item in enumerate(comments):
            comment = cls._upsert_comment(
                session,
                aweme_id=aweme_id,
                provider_namespace=provider_namespace,
                item=item,
                sort_order=index,
                parent_comment_id=None,
                reply_to_comment_id=None,
            )
            if comment:
                parent_by_external_id[comment.platform_comment_id] = comment.id
            replies = item.get("replies")
            if not isinstance(replies, list) or not comment:
                continue
            for reply_index, reply in enumerate(replies):
                if not isinstance(reply, dict):
                    continue
                reply_to_external_id = str(
                    reply.get("reply_to_id")
                    or reply.get("reply_to_comment_id")
                    or reply.get("replyToId")
                    or ""
                )
                cls._upsert_comment(
                    session,
                    aweme_id=aweme_id,
                    provider_namespace=provider_namespace,
                    item=reply,
                    sort_order=reply_index,
                    parent_comment_id=comment.id,
                    reply_to_comment_id=parent_by_external_id.get(
                        reply_to_external_id
                    ),
                )

    @staticmethod
    def _upsert_comment(
        session,
        *,
        aweme_id: UUID,
        provider_namespace: str,
        item: dict,
        sort_order: int,
        parent_comment_id: UUID | None,
        reply_to_comment_id: UUID | None,
    ) -> Comment | None:
        platform_comment_id = str(
            item.get("id")
            or item.get("comment_id")
            or item.get("commentId")
            or item.get("content_id")
            or ""
        )
        if not platform_comment_id:
            return None
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        existing = session.exec(
            select(Comment).where(
                Comment.aweme_id == aweme_id,
                Comment.provider_namespace == provider_namespace,
                Comment.platform_comment_id == platform_comment_id,
            )
        ).first()
        comment = existing or Comment(
            aweme_id=aweme_id,
            provider_namespace=provider_namespace,
            platform_comment_id=platform_comment_id,
        )
        comment.parent_comment_id = parent_comment_id
        comment.reply_to_comment_id = reply_to_comment_id
        comment.kind = (
            CommentKind.REPLY if parent_comment_id is not None else CommentKind.COMMENT
        )
        comment.author_id = str(author.get("id") or "") or None
        comment.author_name = author.get("name")
        comment.author_avatar_url = author.get("avatar_url")
        comment.text = item.get("text") or item.get("content")
        comment.like_count = item.get("like_count")
        comment.reply_count = item.get("reply_count")
        created_at = item.get("created_at")
        comment.published_at = created_at if isinstance(created_at, datetime) else None
        comment.sort_order = sort_order
        comment.raw_json = item
        session.add(comment)
        session.flush()
        return comment

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
        return self.download_media(aweme)

    def download_media(
        self,
        aweme: Aweme,
    ) -> Aweme:
        self._require_persisted(aweme)
        run_token = self._begin_media_download(aweme.id)
        if not aweme.cover_url and not aweme.video_url and not aweme.photos:
            exc = InvalidOperationError("aweme has no downloadable media")
            self._fail_media_download(aweme.id, run_token, exc)
            raise exc
        media_subdir = self._media_subdir(aweme)
        cover_result = None
        video_result = None
        photo_paths: list[dict[str, str]] = []
        try:
            if aweme.cover_url:
                cover_result = self.media_downloader.download(
                    aweme.cover_url,
                    media_type="image",
                    subdir=Path(media_subdir) / "cover",
                )
            if aweme.video_url:
                video_result = self.media_downloader.download(
                    aweme.video_url,
                    media_type="video",
                    subdir=Path(media_subdir) / "video",
                )
            for url in aweme.photos:
                result = self.media_downloader.download(
                    url,
                    media_type="image",
                    subdir=Path(media_subdir) / "photo",
                )
                photo_paths.append({"url": url, "local_path": str(result.path)})
        except Exception as exc:
            self._fail_media_download(aweme.id, run_token, exc)
            raise
        with self._session() as session:
            current = session.get(Aweme, aweme.id)
            if not current or current.deleted_at:
                raise NotFoundError("aweme not found")
            if current.media_download_run_token != run_token:
                return current
            if cover_result:
                current.cover_path = str(cover_result.path)
            if video_result:
                current.video_path = str(video_result.path)
            if photo_paths:
                current.photo_paths = photo_paths
            current.media_download_status = TaskStatus.SUCCEEDED
            current.media_download_finished_at = now_utc()
            current.media_download_error = None
            current.media_download_run_token = None
            if video_result:
                pending = session.exec(
                    select(VideoTranscription).where(
                        VideoTranscription.aweme_id == current.id,
                        VideoTranscription.video_path.is_(None),
                    )
                ).all()
                for transcription in pending:
                    transcription.video_path = str(video_result.path)
                    transcription.video_sha256 = video_result.sha256
                    session.add(transcription)
            session.add(current)
            session.flush()
            session.refresh(current)
            return current

    def download_images(self, aweme: Aweme) -> Aweme:
        self._require_persisted(aweme)
        run_token = self._begin_media_download(aweme.id)
        if not aweme.cover_url and not aweme.photos:
            exc = InvalidOperationError("aweme has no downloadable images")
            self._fail_media_download(aweme.id, run_token, exc)
            raise exc
        media_subdir = self._media_subdir(aweme)
        cover_result = None
        try:
            if aweme.cover_url:
                cover_result = self.media_downloader.download(
                    aweme.cover_url,
                    media_type="image",
                    subdir=Path(media_subdir) / "cover",
                )
            paths = [
                {
                    "url": url,
                    "local_path": str(
                        self.media_downloader.download(
                            url,
                            media_type="image",
                            subdir=Path(media_subdir) / "photo",
                        ).path
                    ),
                }
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
            if cover_result:
                current.cover_path = str(cover_result.path)
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

    def set_projects(self, aweme_id: UUID | str, project_ids: list[UUID | str]) -> Aweme:
        with self._session() as session:
            aweme = session.get(Aweme, as_uuid(aweme_id))
            if not aweme or aweme.deleted_at:
                raise NotFoundError("aweme not found")
            self._set_projects(session, aweme.id, project_ids)
            session.refresh(aweme)
            return aweme

    def project_ids(self, aweme_id: UUID | str) -> list[UUID]:
        aweme_uuid = as_uuid(aweme_id)
        with self._session() as session:
            return list(
                session.exec(
                    select(ProjectAweme.project_id).where(ProjectAweme.aweme_id == aweme_uuid)
                ).all()
            )

    def _fetch_remote_aweme(self, aweme: Aweme) -> AwemeData:
        platform = self._normalize_platform(aweme.platform, aweme.source_url)
        project_ids = self.project_ids(aweme.id)
        provider = self.data_providers.get_aweme_provider(
            platform,
            aweme.source_url,
            str(project_ids[0]) if project_ids else None,
        )
        return provider.fetch_aweme(
            FetchAwemeRequest(
                platform=platform,
                platform_aweme_id=aweme.platform_aweme_id,
                content_type=aweme.content_type,
                source_url=aweme.source_url,
            )
        )

    @staticmethod
    def _apply_fetched_data(aweme: Aweme, data: AwemeData) -> None:
        aweme.platform_aweme_id = data.platform_aweme_id
        aweme.platform_account_id = data.platform_account_id
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

    @classmethod
    def _media_subdir(cls, aweme: Aweme) -> Path:
        raw = aweme.platform_aweme_id or str(aweme.id)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
        return Path(safe or str(aweme.id))

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
    def _effective_comment_max_count(
        self,
        aweme_id: UUID,
        fallback: int | None,
    ) -> int | None:
        with self._session() as session:
            values = [
                item
                for item in session.exec(
                    select(ProjectAweme.comment_limit).where(
                        ProjectAweme.aweme_id == aweme_id,
                        ProjectAweme.collect_comments_enabled.is_(True),
                    )
                ).all()
                if item is not None
            ]
        if not values:
            return fallback
        candidates = [*values]
        if fallback is not None:
            candidates.append(fallback)
        return max(candidates)

    @staticmethod
    def _raise_project_comment_limit(
        session,
        aweme_id: UUID,
        max_comments: int,
    ) -> None:
        relations = list(
            session.exec(
                select(ProjectAweme).where(ProjectAweme.aweme_id == aweme_id)
            ).all()
        )
        for relation in relations:
            relation.collect_comments_enabled = True
            relation.comment_limit = max(
                max_comments,
                relation.comment_limit or 0,
            )
            session.add(relation)

    @staticmethod
    def _set_projects(
        session,
        aweme_id: UUID,
        project_ids: list[UUID | str],
        *,
        collect_comments_enabled: bool = False,
        comment_limit: int | None = None,
        download_video_enabled: bool = False,
        transcribe_enabled: bool = False,
    ) -> None:
        desired = {as_uuid(value) for value in project_ids}
        existing = list(
            session.exec(
                select(ProjectAweme).where(ProjectAweme.aweme_id == aweme_id)
            ).all()
        )
        current = {item.project_id for item in existing}
        for item in existing:
            if item.project_id not in desired:
                session.delete(item)
            else:
                item.collect_comments_enabled = (
                    item.collect_comments_enabled or collect_comments_enabled
                )
                if comment_limit is not None:
                    item.comment_limit = max(comment_limit, item.comment_limit or 0)
                item.download_video_enabled = (
                    item.download_video_enabled or download_video_enabled
                )
                item.transcribe_enabled = item.transcribe_enabled or transcribe_enabled
                session.add(item)
        for project_id in desired - current:
            session.add(
                ProjectAweme(
                    project_id=project_id,
                    aweme_id=aweme_id,
                    collect_comments_enabled=collect_comments_enabled,
                    comment_limit=comment_limit,
                    download_video_enabled=download_video_enabled,
                    transcribe_enabled=transcribe_enabled,
                )
            )
        ensure_aweme_store_relations(session, aweme_id, desired)
