from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

from sqlmodel import select

from cjdb_collectors.data_provider import (
    AccountProviderMixin,
    DataProviderType,
    FetchAccountRequest,
)
from cjdb_collectors.models import (
    Account,
    GroupAccount,
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
        group_ids: list[UUID | str] | None = None,
    ) -> Account:
        selected_url = profile_url or url
        if not selected_url:
            raise ValueError("profile_url is required")
        host = (urlparse(selected_url).hostname or "unknown").lower()
        selected_platform = self._normalize_platform(platform or host, selected_url)
        with self._session() as session:
            account = Account(
                profile_url=selected_url,
                platform=selected_platform,
                collection_status=TaskStatus.NOT_REQUESTED,
            )
            session.add(account)
            session.flush()
            self._set_groups(session, account.id, group_ids or [])
            session.refresh(account)
            return account

    def list(
        self,
        group_ids: list[UUID | str] | None = None,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Account]:
        with self._session() as session:
            statement = select(Account).where(Account.deleted_at.is_(None))
            if group_ids:
                ids = [as_uuid(value) for value in group_ids]
                statement = (
                    statement.join(GroupAccount)
                    .where(GroupAccount.group_id.in_(ids))
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
        group_ids: list[UUID | str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Account]:
        needle = keyword.strip().lower()
        if not needle:
            return []
        matches: list[Account] = []
        for item in self.list(group_ids=group_ids):
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

    def update(self, account_id: UUID | str, **changes) -> Account:
        allowed = {
            "profile_url",
            "display_name",
            "avatar_url",
            "avatar_path",
            "profile_data_json",
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
            account.collection_status = TaskStatus.PENDING
            account.collection_next_run_at = None
            account.collection_error = None
            account.collection_run_token = None
            session.add(account)
            session.flush()
            session.refresh(account)
            return account

    retry = request_collection

    def fetch_data(self, account: Account) -> Account:
        if not account.id:
            raise InvalidOperationError(
                "account must be persisted before collection"
            )
        run_token = self._begin_collection(account.id)
        try:
            provider = self.data_providers.get_provider(
                DataProviderType.ACCOUNT_COLLECT
            )
            if not isinstance(provider, AccountProviderMixin):
                raise InvalidOperationError(
                    f"provider {provider.namespace} does not support accounts"
                )
            data = provider.fetch_account(
                FetchAccountRequest(
                    platform=account.platform,
                    profile_url=account.profile_url,
                    platform_account_id=account.platform_account_id,
                )
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
            current.platform_account_id = (
                data.platform_account_id or current.platform_account_id
            )
            current.display_name = data.display_name
            current.avatar_url = data.avatar_url
            current.profile_data_json = data.profile_data_json
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

    def set_groups(
        self, account_id: UUID | str, group_ids: list[UUID | str]
    ) -> Account:
        with self._session() as session:
            account = session.get(Account, as_uuid(account_id))
            if not account or account.deleted_at:
                raise NotFoundError("account not found")
            self._set_groups(session, account.id, group_ids)
            session.refresh(account)
            return account

    def group_ids(self, account_id: UUID | str) -> list[UUID]:
        account_uuid = as_uuid(account_id)
        with self._session() as session:
            return list(
                session.exec(
                    select(GroupAccount.group_id).where(
                        GroupAccount.account_id == account_uuid
                    )
                ).all()
            )

    @staticmethod
    def _set_groups(session, account_id: UUID, group_ids: list[UUID | str]) -> None:
        desired = {as_uuid(value) for value in group_ids}
        existing = list(
            session.exec(
                select(GroupAccount).where(GroupAccount.account_id == account_id)
            ).all()
        )
        current = {item.group_id for item in existing}
        for item in existing:
            if item.group_id not in desired:
                session.delete(item)
        for group_id in desired - current:
            session.add(GroupAccount(group_id=group_id, account_id=account_id))
        ensure_account_store_relations(session, account_id, desired)

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
