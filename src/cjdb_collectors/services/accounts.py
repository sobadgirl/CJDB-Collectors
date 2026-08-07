from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from sqlmodel import select

from cjdb_collectors.domains.data_provider import (
    AwemeData,
    DataProviderType,
    DouyinAccountProviderMixin,
    FetchAccountAwemesRequest,
    FetchAccountRequest,
    PageStopPolicy,
    WeChatChannelsAccountProviderMixin,
    WeChatMpAccountProviderMixin,
    XiaohongshuAccountProviderMixin,
)
from cjdb_collectors.models import (
    Account,
    Aweme,
    AwemeDataSource,
    ProjectAweme,
    ProjectAccount,
    Platform,
    TaskStatus,
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
from .store_relations import ensure_account_store_relations
from .store_relations import ensure_aweme_store_relations


_ACCOUNT_PROVIDER_TYPES = {
    Platform.DOUYIN: DataProviderType.DOUYIN_ACCOUNT_COLLECT,
    Platform.XIAOHONGSHU: DataProviderType.XIAOHONGSHU_ACCOUNT_COLLECT,
    Platform.WECHAT_CHANNELS: DataProviderType.WECHAT_CHANNELS_ACCOUNT_COLLECT,
    Platform.WECHAT_MP: DataProviderType.WECHAT_MP_ACCOUNT_COLLECT,
}


class AccountService:
    def __init__(
        self,
        session_factory: SessionFactory,
        data_providers: DataProviderService,
    ) -> None:
        self._session = session_factory
        self.data_providers = data_providers

    def create(
        self,
        profile_url: str | None = None,
        *,
        url: str | None = None,
        platform: Platform | str | None = None,
        platform_account_id: str | None = None,
        project_ids: list[UUID | str] | None = None,
    ) -> Account:
        selected_url = profile_url or url or platform_account_id
        if not selected_url:
            raise ValueError("profile_url is required")
        host = (urlparse(selected_url).hostname or "unknown").lower()
        selected_platform = self._normalize_platform(platform or host, selected_url)
        with self._session() as session:
            if platform_account_id:
                existing = session.exec(
                    select(Account)
                    .where(
                        Account.platform == selected_platform,
                        Account.platform_account_id == platform_account_id,
                        Account.deleted_at.is_(None),
                    )
                    .limit(1)
                ).first()
                if existing:
                    self._add_projects(session, existing.id, project_ids or [])
                    session.refresh(existing)
                    return existing
            account = Account(
                profile_url=selected_url,
                platform=selected_platform,
                platform_account_id=platform_account_id,
                collection_status=TaskStatus.NOT_REQUESTED,
            )
            session.add(account)
            session.flush()
            self._set_projects(session, account.id, project_ids or [])
            session.refresh(account)
            return account

    def list(
        self,
        project_ids: list[UUID | str] | None = None,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Account]:
        with self._session() as session:
            statement = select(Account).where(Account.deleted_at.is_(None))
            if project_ids:
                ids = [as_uuid(value) for value in project_ids]
                statement = (
                    statement.join(ProjectAccount)
                    .where(ProjectAccount.project_id.in_(ids))
                    .distinct()
                )
            if status:
                statement = statement.where(
                    Account.collection_status == TaskStatus(status)
                )
            statement = statement.order_by(Account.created_at.desc()).offset(offset)
            if limit is not None:
                statement = statement.limit(limit)
            return list(session.exec(statement).all())

    def get(self, account_id: UUID | str) -> Account:
        with self._session() as session:
            account = session.get(Account, as_uuid(account_id))
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            return account

    def search(
        self,
        keyword: str,
        *,
        project_ids: list[UUID | str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Account]:
        needle = keyword.strip().lower()
        if not needle:
            return []
        matches: list[Account] = []
        for item in self.list(project_ids=project_ids):
            haystack = " ".join(
                str(value or "")
                for value in (
                    item.display_name,
                    item.profile_url,
                    item.platform.value,
                    item.platform_account_id,
                )
            ).lower()
            if needle in haystack:
                matches.append(item)
            return matches[offset : offset + limit]

    def list_by_platform_account_ids(
        self,
        keys: list[tuple[Platform | str, str]],
    ) -> dict[str, Account]:
        normalized: list[tuple[Platform, str]] = []
        for platform, platform_account_id in keys:
            if not platform_account_id:
                continue
            selected_platform = platform if isinstance(platform, Platform) else Platform(platform)
            normalized.append((selected_platform, platform_account_id))
        if not normalized:
            return {}
        ids = sorted({platform_account_id for _, platform_account_id in normalized})
        platforms = sorted({platform for platform, _ in normalized}, key=lambda value: value.value)
        with self._session() as session:
            accounts = session.exec(
                select(Account).where(
                    Account.deleted_at.is_(None),
                    Account.platform.in_(platforms),
                    Account.platform_account_id.in_(ids),
                )
            ).all()
            return {
                self.platform_account_key(account.platform, account.platform_account_id): account
                for account in accounts
                if account.platform_account_id
            }

    def request_platform_account_collection(
        self,
        *,
        platform: Platform | str,
        platform_account_id: str,
        project_ids: list[UUID | str] | None = None,
    ) -> Account:
        account = self.create(
            platform=platform,
            platform_account_id=platform_account_id,
            project_ids=project_ids or [],
        )
        return self.request_collection(account.id)

    def update(self, account_id: UUID | str, **changes) -> Account:
        allowed = {
            "profile_url",
            "display_name",
            "avatar_url",
            "avatar_path",
            "signature",
            "location",
            "ip_location",
            "gender",
            "verified",
            "follower_count",
            "following_count",
            "work_count",
            "like_count",
            "collect_count",
            "comment_count",
            "share_count",
            "total_favorited",
            "extra_data_json",
        }
        with self._session() as session:
            account = session.get(Account, as_uuid(account_id))
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            apply_changes(account, changes, allowed)
            session.add(account)
            session.flush()
            session.refresh(account)
            return account

    def delete(self, account_id: UUID | str) -> None:
        with self._session() as session:
            account = session.get(Account, as_uuid(account_id))
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            account.deleted_at = now_utc()
            session.add(account)

    def request_collection(self, account_id: UUID | str) -> Account:
        with self._session() as session:
            account = session.get(Account, as_uuid(account_id))
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            # V1.0 发布隐藏：账号/作者采集入口暂不开放，不创建 pending 任务。
            # account.collection_status = TaskStatus.PENDING
            # account.collection_next_run_at = None
            # account.collection_error = None
            # account.collection_run_token = None
            # session.add(account)
            # session.flush()
            session.refresh(account)
            return account

    retry = request_collection

    def fetch_data(self, account: Account) -> Account:
        if not account.id:
            raise InvalidOperationError(
                "account must be persisted before collection"
            )
        # V1.0 发布隐藏：账号/作者采集不执行；保留原实现用于后续版本恢复。
        return account
        run_token = self._begin_collection(account.id)
        try:
            request = FetchAccountRequest(
                platform=account.platform,
                profile_url=account.profile_url,
                platform_account_id=account.platform_account_id,
            )
            provider_type = _ACCOUNT_PROVIDER_TYPES.get(account.platform)
            if provider_type is None:
                raise InvalidOperationError(
                    f"unsupported account platform: {account.platform}"
                )
            project_ids = self.project_ids(account.id)
            provider = self.data_providers.get_provider(
                provider_type,
                str(project_ids[0]) if project_ids else None,
            )
            if account.platform == Platform.DOUYIN:
                if not isinstance(provider, DouyinAccountProviderMixin):
                    raise InvalidOperationError(
                        f"provider {provider.namespace} does not support Douyin accounts"
                    )
                data = provider.fetch_douyin_account(request)
            elif account.platform == Platform.XIAOHONGSHU:
                if not isinstance(provider, XiaohongshuAccountProviderMixin):
                    raise InvalidOperationError(
                        f"provider {provider.namespace} does not support Xiaohongshu accounts"
                    )
                data = provider.fetch_xiaohongshu_account(request)
            elif account.platform == Platform.WECHAT_CHANNELS:
                if not isinstance(provider, WeChatChannelsAccountProviderMixin):
                    raise InvalidOperationError(
                        f"provider {provider.namespace} does not support WeChat Channels accounts"
                    )
                data = provider.fetch_wechat_channels_account(request)
            elif account.platform == Platform.WECHAT_MP:
                if not isinstance(provider, WeChatMpAccountProviderMixin):
                    raise InvalidOperationError(
                        f"provider {provider.namespace} does not support WeChat MP accounts"
                    )
                data = provider.fetch_wechat_mp_account(request)
            else:
                raise InvalidOperationError(
                    f"unsupported account platform: {account.platform}"
                )
        except Exception as exc:
            self._fail_collection(account.id, run_token, exc)
            raise
        with self._session() as session:
            current = session.get(Account, account.id)
            if not current or current.deleted_at:
                raise NotFoundError("account not found")
            if current.collection_run_token != run_token:
                return current
            if data.platform_account_id:
                current.platform_account_id = data.platform_account_id
            current.display_name = data.display_name
            current.avatar_url = data.avatar_url
            current.signature = data.signature
            current.location = data.location
            current.ip_location = data.ip_location
            current.gender = data.gender
            current.verified = data.verified
            current.follower_count = data.follower_count
            current.following_count = data.following_count
            current.work_count = data.work_count
            current.like_count = data.like_count
            current.collect_count = data.collect_count
            current.comment_count = data.comment_count
            current.share_count = data.share_count
            current.total_favorited = data.total_favorited
            current.extra_data_json = data.extra_data_json
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

    fetch_account = fetch_data

    def request_published_history(
        self,
        account_id: UUID | str,
        *,
        latest: bool = False,
        page_size: int = 20,
        max_count: int | None = None,
        max_pages: int | None = 1,
        earliest_date: datetime | None = None,
    ) -> Account:
        with self._session() as session:
            account = session.get(Account, as_uuid(account_id))
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            # V1.0 发布隐藏：账号历史作品采集暂不开放，不创建 pending 任务。
            session.refresh(account)
            return account
            # 原逻辑保留，后续恢复账号采集时打开。
            if account.history_status in {
                TaskStatus.PENDING,
                TaskStatus.RUNNING,
                TaskStatus.RETRY_WAIT,
            }:
                raise InvalidOperationError("account published history is already running")
            account.history_status = TaskStatus.PENDING
            account.history_next_run_at = None
            account.history_error = None
            account.history_run_token = None
            account.history_request_json = {
                "mode": "latest" if latest else "backfill",
                "page_size": max(1, min(50, int(page_size))),
                "max_count": max_count,
                "max_pages": max_pages,
                "earliest_date": earliest_date.isoformat() if earliest_date else None,
            }
            session.add(account)
            session.flush()
            session.refresh(account)
            return account

    def process_published_history(self, account: Account) -> dict[str, object]:
        # V1.0 发布隐藏：账号历史作品采集不执行；保留原实现用于后续版本恢复。
        return {"account": account, "items": 0, "created": 0, "updated": 0}
        request = dict(account.history_request_json or {})
        earliest_date = request.get("earliest_date")
        parsed_earliest_date = (
            datetime.fromisoformat(str(earliest_date).replace("Z", "+00:00"))
            if earliest_date
            else None
        )
        latest = request.get("mode") == "latest"
        if latest:
            return self.fetch_latest_published_history(
                account.id,
                page_size=int(request.get("page_size") or 20),
                max_count=request.get("max_count"),
                max_pages=request.get("max_pages") or 1,
                earliest_date=parsed_earliest_date,
            )
        return self.fetch_published_history(
            account.id,
            page_size=int(request.get("page_size") or 20),
            max_count=request.get("max_count"),
            max_pages=request.get("max_pages") or 1,
            earliest_date=parsed_earliest_date,
        )

    def fetch_published_history(
        self,
        account_id: UUID | str,
        *,
        page_size: int = 20,
        max_count: int | None = None,
        max_pages: int | None = 1,
        earliest_date: datetime | None = None,
    ) -> dict[str, object]:
        return self._fetch_published_history(
            account_id,
            page_size=page_size,
            max_count=max_count,
            max_pages=max_pages,
            earliest_date=earliest_date,
            latest=False,
        )

    def fetch_latest_published_history(
        self,
        account_id: UUID | str,
        *,
        page_size: int = 20,
        max_count: int | None = None,
        max_pages: int | None = 1,
        earliest_date: datetime | None = None,
    ) -> dict[str, object]:
        return self._fetch_published_history(
            account_id,
            page_size=page_size,
            max_count=max_count,
            max_pages=max_pages,
            earliest_date=earliest_date,
            latest=True,
        )

    def _fetch_published_history(
        self,
        account_id: UUID | str,
        *,
        page_size: int,
        max_count: int | None,
        max_pages: int | None,
        earliest_date: datetime | None,
        latest: bool,
    ) -> dict[str, object]:
        account_uuid = as_uuid(account_id)
        with self._session() as session:
            account = session.get(Account, account_uuid)
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            account.history_status = TaskStatus.RUNNING
            account.history_heartbeat_at = now_utc()
            account.history_error = None
            session.add(account)
            session.flush()
            platform = account.platform
            profile_url = account.profile_url
            platform_account_id = account.platform_account_id
            progress_payload = (
                {}
                if latest
                else dict(account.history_backfill_progress_json or {})
            )
            cursor = None if latest else account.history_cursor
            project_ids = list(
                session.exec(
                    select(ProjectAccount.project_id).where(
                        ProjectAccount.account_id == account.id
                    )
                ).all()
            )

        try:
            page = self._fetch_remote_history(
                platform=platform,
                profile_url=profile_url,
                platform_account_id=platform_account_id,
                cursor=cursor,
                page_size=page_size,
                progress_payload=progress_payload,
                stop_policy=PageStopPolicy(
                    max_count=max_count,
                    max_pages=max_pages,
                    earliest_date=earliest_date,
                ),
                latest=latest,
                project_id=str(project_ids[0]) if project_ids else None,
            )
        except Exception as exc:
            with self._session() as session:
                current = session.get(Account, account_uuid)
                if current and not current.deleted_at:
                    current.history_status = TaskStatus.FAILED
                    current.history_finished_at = now_utc()
                    current.history_heartbeat_at = None
                    current.history_run_token = None
                    current.history_error = str(exc)
                    session.add(current)
            raise

        created = 0
        updated = 0
        with self._session() as session:
            current = session.get(Account, account_uuid)
            if not current or current.deleted_at:
                raise NotFoundError("account not found")
            for data in page.awemes:
                is_created = self._upsert_history_aweme(
                    session,
                    account=current,
                    data=data,
                    project_ids=project_ids,
                )
                if is_created:
                    created += 1
                else:
                    updated += 1
            current.history_status = (
                TaskStatus.SUCCEEDED if page.done else TaskStatus.PENDING
            )
            if not latest:
                current.history_cursor = page.next_cursor
                current.history_has_more = bool(page.has_more and page.next_cursor)
                current.history_fetched_count = (
                    current.history_fetched_count or 0
                ) + len(page.awemes)
                current.history_backfill_progress_json = page.progress_payload
            else:
                current.history_latest_progress_json = {
                    **page.progress_payload,
                    "last_request": page.request,
                    "last_synced_at": now_utc().isoformat(),
                }
            current.history_last_fetched_at = now_utc()
            current.history_finished_at = now_utc()
            current.history_heartbeat_at = now_utc()
            current.history_run_token = None
            current.history_error = None
            current.collection_status = TaskStatus.SUCCEEDED
            current.collection_finished_at = now_utc()
            current.collection_heartbeat_at = now_utc()
            current.collection_error = None
            current.collection_run_token = None
            current.last_collected_at = now_utc()
            session.add(current)
            session.flush()
            session.refresh(current)
            return {
                "account": current,
                "created": created,
                "updated": updated,
                "has_more": current.history_has_more,
                "next_cursor": current.history_cursor,
                "items": len(page.awemes),
                "done": page.done,
                "latest": latest,
            }

    def _fetch_remote_history(
        self,
        *,
        platform: Platform,
        profile_url: str,
        platform_account_id: str | None,
        cursor: str | None,
        page_size: int,
        progress_payload: dict[str, object],
        stop_policy: PageStopPolicy,
        latest: bool = False,
        project_id: str | None = None,
    ):
        provider_type = _ACCOUNT_PROVIDER_TYPES.get(platform)
        if provider_type is None:
            raise InvalidOperationError(f"unsupported account platform: {platform}")
        provider = self.data_providers.get_provider(provider_type, project_id)
        request = FetchAccountAwemesRequest(
            platform=platform,
            profile_url=profile_url,
            platform_account_id=platform_account_id,
            cursor=cursor,
            page_size=max(1, min(50, int(page_size))),
            stop_policy=stop_policy,
            progress_payload=progress_payload,
        )
        if platform == Platform.DOUYIN:
            if not isinstance(provider, DouyinAccountProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support Douyin accounts"
                )
            return (
                provider.fetch_latest_douyin_account_awemes(request)
                if latest
                else provider.fetch_douyin_account_awemes(request)
            )
        if platform == Platform.XIAOHONGSHU:
            if not isinstance(provider, XiaohongshuAccountProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support Xiaohongshu accounts"
                )
            return (
                provider.fetch_latest_xiaohongshu_account_awemes(request)
                if latest
                else provider.fetch_xiaohongshu_account_awemes(request)
            )
        if platform == Platform.WECHAT_CHANNELS:
            if not isinstance(provider, WeChatChannelsAccountProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support WeChat Channels accounts"
                )
            return (
                provider.fetch_latest_wechat_channels_account_awemes(request)
                if latest
                else provider.fetch_wechat_channels_account_awemes(request)
            )
        if platform == Platform.WECHAT_MP:
            if not isinstance(provider, WeChatMpAccountProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support WeChat MP accounts"
                )
            return (
                provider.fetch_latest_wechat_mp_account_awemes(request)
                if latest
                else provider.fetch_wechat_mp_account_awemes(request)
            )
        raise InvalidOperationError(f"unsupported account platform: {platform}")

    @staticmethod
    def _upsert_history_aweme(
        session,
        *,
        account: Account,
        data: AwemeData,
        project_ids: list[UUID],
    ) -> bool:
        extra = {
            key: value
            for key, value in dict(data.extra_data_json or {}).items()
            if value is not None
        }
        source_url = (
            extra.get("source_url")
            or extra.get("aweme_url")
            or data.platform_aweme_id
            or account.profile_url
        )
        existing = None
        if data.platform_aweme_id:
            existing = session.exec(
                select(Aweme)
                .where(
                    Aweme.platform == account.platform,
                    Aweme.platform_aweme_id == data.platform_aweme_id,
                    Aweme.deleted_at.is_(None),
                )
                .limit(1)
            ).first()
        aweme = existing or Aweme(
            platform=account.platform,
            source_url=str(source_url),
            aweme_url=str(source_url),
            platform_aweme_id=data.platform_aweme_id,
            collection_status=TaskStatus.SUCCEEDED,
            data_source=AwemeDataSource.ACCOUNT_HISTORY,
        )
        aweme.platform_account_id = data.platform_account_id or account.platform_account_id
        aweme.content_type = data.content_type
        aweme.title = data.title
        aweme.description = data.description
        aweme.published_at = data.published_at
        aweme.video_url = data.video_url
        aweme.cover_url = data.cover_url
        aweme.photos = data.photos
        aweme.play_count = data.play_count
        aweme.like_count = data.like_count
        aweme.collect_count = data.collect_count
        aweme.share_count = data.share_count
        aweme.comment_count = data.comment_count
        aweme.extra_data_json = extra
        aweme.collection_finished_at = now_utc()
        aweme.collection_heartbeat_at = now_utc()
        aweme.collection_error = None
        aweme.last_collected_at = now_utc()
        session.add(aweme)
        session.flush()
        existing_projects = {
            value
            for value in session.exec(
                select(ProjectAweme.project_id).where(ProjectAweme.aweme_id == aweme.id)
            ).all()
        }
        for project_id in set(project_ids) - existing_projects:
            session.add(ProjectAweme(project_id=project_id, aweme_id=aweme.id))
        ensure_aweme_store_relations(session, aweme.id, set(project_ids) | existing_projects)
        return existing is None

    def _begin_collection(self, account_id: UUID) -> str:
        from uuid import uuid4

        with self._session() as session:
            current = session.get(Account, account_id)
            if not current or current.deleted_at:
                raise NotFoundError("account not found")
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

    def _fail_collection(
        self,
        account_id: UUID,
        run_token: str,
        exc: Exception,
    ) -> None:
        with self._session() as session:
            current = session.get(Account, account_id)
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

    def set_projects(
        self, account_id: UUID | str, project_ids: list[UUID | str]
    ) -> Account:
        with self._session() as session:
            account = session.get(Account, as_uuid(account_id))
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            self._set_projects(session, account.id, project_ids)
            session.refresh(account)
            return account

    def project_ids(self, account_id: UUID | str) -> list[UUID]:
        account_uuid = as_uuid(account_id)
        with self._session() as session:
            return list(
                session.exec(
                    select(ProjectAccount.project_id).where(
                        ProjectAccount.account_id == account_uuid
                    )
                ).all()
            )

    @staticmethod
    def _set_projects(session, account_id: UUID, project_ids: list[UUID | str]) -> None:
        desired = {as_uuid(value) for value in project_ids}
        existing = list(
            session.exec(
                select(ProjectAccount).where(ProjectAccount.account_id == account_id)
            ).all()
        )
        current = {item.project_id for item in existing}
        for item in existing:
            if item.project_id not in desired:
                session.delete(item)
        for project_id in desired - current:
            session.add(ProjectAccount(project_id=project_id, account_id=account_id))
        ensure_account_store_relations(session, account_id, desired)

    @staticmethod
    def _add_projects(session, account_id: UUID, project_ids: list[UUID | str]) -> None:
        desired = {as_uuid(value) for value in project_ids}
        if not desired:
            return
        existing = {
            value
            for value in session.exec(
                select(ProjectAccount.project_id).where(
                    ProjectAccount.account_id == account_id
                )
            ).all()
        }
        for project_id in desired - existing:
            session.add(ProjectAccount(project_id=project_id, account_id=account_id))
        ensure_account_store_relations(session, account_id, existing | desired)

    @staticmethod
    def platform_account_key(
        platform: Platform | str | None,
        platform_account_id: str | None,
    ) -> str:
        platform_value = platform.value if isinstance(platform, Platform) else platform
        return f"{platform_value or ''}:{platform_account_id or ''}"

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
            value in {"wechat_channels", "weixin_channels", "weixin.qq.com"}
            or host == "weixin.qq.com"
        ):
            return Platform.WECHAT_CHANNELS
        raise InvalidOperationError(f"unsupported platform: {platform}")
