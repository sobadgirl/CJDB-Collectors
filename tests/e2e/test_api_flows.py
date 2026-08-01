from pathlib import Path

from fastapi.testclient import TestClient


def _create_group(client: TestClient, name: str = "爆款") -> dict:
    response = client.post(
        "/api/v1/groups",
        json={"name": name, "description": "E2E 分组", "color": "#570df8"},
    )
    assert response.status_code == 201
    return response.json()


def _create_storer(client: TestClient, name: str = "Notion 主库") -> dict:
    response = client.post(
        "/api/v1/data-storers",
        json={
            "name": name,
            "type": "notion",
            "secret_ref": "notion_token",
            "container_config": {"database_id": "e2e-database"},
            "field_mapping": {"title": "名称"},
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_store(
    client: TestClient,
    name: str,
    *,
    default: bool = False,
) -> dict:
    response = client.post(
        "/api/v1/stores",
        json={
            "name": name,
            "type": "notion",
            "default": default,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_application_health_schema_and_server_rendered_pages(
    api_client: TestClient,
    tmp_path: Path,
) -> None:
    assert api_client.get("/health/live").json() == {"status": "ok"}
    assert api_client.get("/health/ready").status_code == 200

    service_health = api_client.get("/api/v1/services/health")
    assert service_health.status_code == 200
    assert service_health.json()["collector"]["ready"] is False
    assert service_health.json()["transcription"]["ready"] is False

    openapi = api_client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    required_paths = {
        "/api/v1/accounts",
        "/api/v1/awemes",
        "/api/v1/awemes/{aweme_id}/fetch",
        "/api/v1/awemes/{aweme_id}/comments/fetch",
        "/api/v1/awemes/{aweme_id}/video/download",
        "/api/v1/groups",
        "/api/v1/groups/{group_id}/stores",
        "/api/v1/store-providers",
        "/api/v1/stores",
        "/api/v1/stores/defaults",
        "/api/v1/stores/{store_id}/default",
        "/api/v1/data-storer-types",
        "/api/v1/data-storers",
        "/api/v1/video-transcriptions",
        "/api/v1/local-media",
        "/api/v1/providers",
        "/api/v1/providers/services",
        "/api/v1/providers/status",
        "/api/v1/providers/{provider_type}/status",
        "/api/v1/providers/{provider_type}/logs",
        "/api/v1/providers/{provider_type}/setup",
        "/api/v1/providers/selection",
        "/api/v1/system/checks",
        "/api/v1/sync",
        "/api/v1/worker-tasks",
        "/api/v1/config",
    }
    assert required_paths <= set(paths)

    config_update = api_client.patch(
        "/api/v1/config",
        json={
            "key": "worker_tasks.data_sync.process_limit",
            "value": 3,
        },
    )
    assert config_update.status_code == 200
    assert config_update.json()["worker_tasks"]["data_sync"]["process_limit"] == 3

    provider_selection = api_client.patch(
        "/api/v1/providers/selection",
        json={"type": "douyin_aweme_collect", "namespace": "tikhub"},
    )
    assert provider_selection.status_code == 200
    assert provider_selection.json()["selected"] == "tikhub"

    provider_state = api_client.get(
        "/api/v1/providers/douyin_aweme_collect/status"
    )
    assert provider_state.status_code == 200
    assert provider_state.json()["provider"]["namespace"] == "tikhub"
    assert provider_state.json()["provider"]["parameters"][0]["type"] == "password"

    empty_logs = api_client.get(
        "/api/v1/providers/douyin_aweme_collect/logs",
        params={"limit": 2},
    )
    assert empty_logs.status_code == 200
    assert empty_logs.json()["lines"] == []
    log_path = Path(empty_logs.json()["path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(f"line-{index}" for index in range(5)),
        encoding="utf-8",
    )
    latest_logs = api_client.get(
        "/api/v1/providers/douyin_aweme_collect/logs",
        params={"limit": 2},
    ).json()
    assert [line["text"] for line in latest_logs["lines"]] == [
        "line-3",
        "line-4",
    ]
    earlier_logs = api_client.get(
        "/api/v1/providers/douyin_aweme_collect/logs",
        params={"limit": 2, "before": latest_logs["start"]},
    ).json()
    assert [line["text"] for line in earlier_logs["lines"]] == [
        "line-1",
        "line-2",
    ]

    for page in ("/", "/transcriptions", "/settings"):
        response = api_client.get(page)
        assert response.status_code == 200
        assert "超级对标" in response.text

    dashboard = api_client.get("/")
    assert "采集服务" in dashboard.text
    assert "当前服务使用的 Provider 状态" in dashboard.text
    assert "open-provider-config" in dashboard.text
    assert "初始化日志" in dashboard.text
    assert "配置数据采集服务" not in dashboard.text
    assert "校验并保存" not in dashboard.text
    assert "inline-flex min-h-10" in dashboard.text
    assert "group flex w-full items-center gap-4" not in dashboard.text
    assert dashboard.text.index("采集服务") < dashboard.text.index('role="tablist"')


def test_aweme_account_group_and_sync_relationship_flow(
    api_client: TestClient, tmp_path: Path
) -> None:
    group = _create_group(api_client)
    storer = _create_storer(api_client)

    bind = api_client.put(
        f"/api/v1/groups/{group['id']}/data-storers",
        json={"ids": [storer["id"]]},
    )
    assert bind.status_code == 200

    aweme_response = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.douyin.com/video/731234567890",
            "platform": "douyin",
            "group_ids": [group["id"]],
            "download_video": True,
            "collect_comments": True,
            "transcribe": True,
        },
    )
    assert aweme_response.status_code == 202
    aweme = aweme_response.json()
    assert aweme["collection_status"] == "not_requested"
    assert aweme["platform"] == "douyin"
    assert aweme["platform_aweme_id"] == "731234567890"
    assert aweme["aweme_url"] == "https://www.douyin.com/video/731234567890"
    assert aweme["content_type"] == "video"
    assert aweme["media_download_status"] == "pending"
    assert aweme["comment_collection_status"] == "pending"
    assert aweme["video_transcription_status"] == "pending"

    xhs_response = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.xiaohongshu.com/explore/64b40886000000001002a0a8",
            "platform": "xiaohongshu",
            "content_type": "video",
        },
    )
    assert xhs_response.status_code == 202
    xhs_aweme = xhs_response.json()
    assert xhs_aweme["platform"] == "xiaohongshu"
    assert xhs_aweme["content_type"] == "video"

    cancel_collection_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/collect/cancel",
        data={"next_url": "/"},
        follow_redirects=False,
    )
    cancel_comments_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/comments/collect/cancel",
        data={"next_url": "/"},
        follow_redirects=False,
    )
    assert cancel_collection_action.status_code == 303
    assert cancel_comments_action.status_code == 303

    account_response = api_client.post(
        "/api/v1/accounts",
        json={
            "url": "https://www.douyin.com/user/MS4wLjABAAAA-demo",
            "platform": "douyin",
            "group_ids": [group["id"]],
        },
    )
    assert account_response.status_code == 202
    account = account_response.json()
    account_collect_action = api_client.post(
        f"/actions/accounts/{account['id']}/collect",
        data={"next_url": "/?tab=accounts"},
        follow_redirects=False,
    )
    assert account_collect_action.status_code == 303

    all_records_page = api_client.get("/", params={"tab": "awemes"})
    assert all_records_page.status_code == 200
    assert "请选择分组" not in all_records_page.text
    assert "https://www.douyin.com/video/731234567890" in all_records_page.text
    assert "作品 URL" in all_records_page.text
    assert '<option value="douyin">抖音</option>' in all_records_page.text
    assert '<option value="xiaohongshu">小红书</option>' in all_records_page.text
    assert '<option value="wechat_mp">公众号</option>' in all_records_page.text
    assert '<option value="wechat_channels">视频号</option>' in all_records_page.text
    assert "无封面" in all_records_page.text
    assert 'name="platform_aweme_id"' not in all_records_page.text
    assert "小红书类型" in all_records_page.text
    assert '<option value="unknown">自动识别</option>' in all_records_page.text
    assert '<option value="image">图文笔记</option>' in all_records_page.text
    assert '<option value="video">视频笔记</option>' in all_records_page.text

    aweme_list = api_client.get(
        "/api/v1/awemes", params={"group_id": group["id"]}
    ).json()
    account_list = api_client.get(
        "/api/v1/accounts", params={"group_id": group["id"]}
    ).json()
    assert [item["id"] for item in aweme_list] == [aweme["id"]]
    assert [item["id"] for item in account_list] == [account["id"]]

    rendered_list = api_client.get(
        "/", params={"tab": "awemes", "group_id": group["id"]}
    )
    assert rendered_list.status_code == 200
    assert "编辑" in rendered_list.text
    assert "删除" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/collect" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/delete" in rendered_list.text
    assert f'hx-post="/actions/awemes/{aweme["id"]}/collect"' in rendered_list.text
    assert "data-async-list-action" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/collect/cancel" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/comments/collect" in rendered_list.text
    assert (
        f"/actions/awemes/{aweme['id']}/comments/collect/cancel"
        in rendered_list.text
    )

    collect_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/collect",
        data={"next_url": f"/?tab=awemes&group_id={group['id']}"},
        follow_redirects=False,
    )
    assert collect_action.status_code == 303
    failed_aweme = api_client.get(f"/api/v1/awemes/{aweme['id']}").json()
    assert failed_aweme["collection_status"] == "failed"
    assert failed_aweme["collection_attempt_count"] == 1
    assert failed_aweme["collection_error"]

    htmx_collect_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/collect",
        data={"next_url": f"/?tab=awemes&group_id={group['id']}"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert htmx_collect_action.status_code == 200
    assert "作品" in htmx_collect_action.text
    assert "数据采集失败" in htmx_collect_action.text
    assert "/actions/awemes/" in htmx_collect_action.text

    assert 'x-data="groupNavigation($el)"' in rendered_list.text
    assert f"""data-selected='["{group["id"]}"]'""" in rendered_list.text
    assert 'x-teleport="body"' in rendered_list.text
    assert "个存储" in rendered_list.text

    settings_page = api_client.get("/settings")
    assert settings_page.status_code == 200
    assert "服务状态" in settings_page.text
    assert "Provider 服务" not in settings_page.text
    assert "/api/v1/providers/selection" in settings_page.text
    assert "保存绑定" in settings_page.text

    aweme_syncs = api_client.get(f"/api/v1/awemes/{aweme['id']}/syncs").json()
    account_syncs = api_client.get(f"/api/v1/accounts/{account['id']}/syncs").json()
    assert len(aweme_syncs) == 1
    assert len(account_syncs) == 1
    assert aweme_syncs[0]["data_storer_id"] == storer["id"]
    assert account_syncs[0]["data_storer_id"] == storer["id"]

    # Removing group membership must not remove the direct sync relationship.
    assert (
        api_client.put(
            f"/api/v1/awemes/{aweme['id']}/groups", json={"ids": []}
        ).status_code
        == 200
    )
    assert (
        api_client.put(
            f"/api/v1/accounts/{account['id']}/groups", json={"ids": []}
        ).status_code
        == 200
    )
    assert len(api_client.get(f"/api/v1/awemes/{aweme['id']}/syncs").json()) == 1
    assert len(api_client.get(f"/api/v1/accounts/{account['id']}/syncs").json()) == 1
    assert (
        api_client.get(f"/api/v1/awemes/{aweme['id']}/syncs").json()[0][
            "enabled"
        ]
        is False
    )
    assert (
        api_client.get(f"/api/v1/accounts/{account['id']}/syncs").json()[0][
            "enabled"
        ]
        is False
    )

    disposable = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.douyin.com/video/888888888888",
            "platform": "douyin",
        },
    ).json()
    video = tmp_path / "downloaded-video.mp4"
    cover = tmp_path / "downloaded-cover.jpg"
    photo = tmp_path / "downloaded-photo.jpg"
    for path in (video, cover, photo):
        path.write_text("local", encoding="utf-8")
    api_client.patch(
        f"/api/v1/awemes/{disposable['id']}",
        json={
            "video_path": str(video),
            "cover_path": str(cover),
            "photos": ["https://cdn.test/remote-photo.jpg"],
            "photo_paths": [str(photo)],
        },
    )

    delete_preview = api_client.get("/", params={"tab": "awemes"})
    assert "同步删除已下载文件" in delete_preview.text
    assert "视频" in delete_preview.text
    assert "封面" in delete_preview.text
    assert "图片 1" in delete_preview.text
    assert str(video) in delete_preview.text
    assert str(cover) in delete_preview.text
    assert str(photo) in delete_preview.text

    delete_action = api_client.post(
        f"/actions/awemes/{disposable['id']}/delete",
        data={
            "next_url": "/?tab=awemes",
            "delete_downloaded_files": "true",
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert delete_action.status_code == 200
    assert api_client.get(f"/api/v1/awemes/{disposable['id']}").status_code == 404
    assert not video.exists()
    assert not cover.exists()
    assert not photo.exists()


def test_default_and_group_store_scope_is_reconciled(
    api_client: TestClient,
) -> None:
    group = _create_group(api_client, "Store 范围")
    group_store = _create_store(api_client, "分组 Store")
    assert (
        api_client.put(
            f"/api/v1/groups/{group['id']}/stores",
            json={"ids": [group_store["id"]]},
        ).status_code
        == 200
    )

    plain_aweme = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.douyin.com/video/10001",
            "platform": "douyin",
        },
    ).json()
    plain_account = api_client.post(
        "/api/v1/accounts",
        json={
            "url": "https://www.douyin.com/user/plain-account",
            "platform": "douyin",
        },
    ).json()
    assert api_client.get(
        f"/api/v1/awemes/{plain_aweme['id']}/syncs"
    ).json() == []
    assert api_client.get(
        f"/api/v1/accounts/{plain_account['id']}/syncs"
    ).json() == []

    default_store = _create_store(api_client, "默认 Store", default=True)
    plain_aweme_syncs = api_client.get(
        f"/api/v1/awemes/{plain_aweme['id']}/syncs"
    ).json()
    plain_account_syncs = api_client.get(
        f"/api/v1/accounts/{plain_account['id']}/syncs"
    ).json()
    assert {
        item["data_storer_id"]: item["enabled"] for item in plain_aweme_syncs
    } == {default_store["id"]: True}
    assert {
        item["data_storer_id"]: item["enabled"] for item in plain_account_syncs
    } == {default_store["id"]: True}

    grouped_aweme = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.douyin.com/video/10002",
            "platform": "douyin",
            "group_ids": [group["id"]],
        },
    ).json()
    grouped_account = api_client.post(
        "/api/v1/accounts",
        json={
            "url": "https://www.douyin.com/user/grouped-account",
            "platform": "douyin",
            "group_ids": [group["id"]],
        },
    ).json()

    for path in (
        f"/api/v1/awemes/{grouped_aweme['id']}/syncs",
        f"/api/v1/accounts/{grouped_account['id']}/syncs",
    ):
        states = {
            item["data_storer_id"]: item["enabled"]
            for item in api_client.get(path).json()
        }
        assert states == {
            default_store["id"]: True,
            group_store["id"]: True,
        }

    assert (
        api_client.delete(
            f"/api/v1/stores/{default_store['id']}/default"
        ).status_code
        == 200
    )
    grouped_states = {
        item["data_storer_id"]: item["enabled"]
        for item in api_client.get(
            f"/api/v1/awemes/{grouped_aweme['id']}/syncs"
        ).json()
    }
    assert grouped_states == {
        default_store["id"]: False,
        group_store["id"]: True,
    }

    assert (
        api_client.put(
            f"/api/v1/stores/{default_store['id']}/default"
        ).status_code
        == 200
    )
    assert (
        api_client.put(
            f"/api/v1/awemes/{grouped_aweme['id']}/groups",
            json={"ids": []},
        ).status_code
        == 200
    )
    assert (
        api_client.put(
            f"/api/v1/accounts/{grouped_account['id']}/groups",
            json={"ids": []},
        ).status_code
        == 200
    )
    for path in (
        f"/api/v1/awemes/{grouped_aweme['id']}/syncs",
        f"/api/v1/accounts/{grouped_account['id']}/syncs",
    ):
        states = {
            item["data_storer_id"]: item["enabled"]
            for item in api_client.get(path).json()
        }
        assert states == {
            default_store["id"]: True,
            group_store["id"]: False,
        }

    assert (
        api_client.put(
            f"/api/v1/awemes/{grouped_aweme['id']}/groups",
            json={"ids": [group["id"]]},
        ).status_code
        == 200
    )
    assert (
        api_client.delete(f"/api/v1/stores/{group_store['id']}").status_code
        == 204
    )
    states_after_store_delete = {
        item["data_storer_id"]: item["enabled"]
        for item in api_client.get(
            f"/api/v1/awemes/{grouped_aweme['id']}/syncs"
        ).json()
    }
    assert states_after_store_delete == {
        default_store["id"]: True,
        group_store["id"]: False,
    }


def test_transcription_and_data_storer_management_flow(
    api_client: TestClient, tmp_path: Path
) -> None:
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"e2e-video-placeholder")
    ignored_path = tmp_path / "notes.txt"
    ignored_path.write_text("not a video", encoding="utf-8")

    roots = api_client.get("/api/v1/local-media")
    assert roots.status_code == 200
    assert roots.json()["roots"] == [{"id": "0", "name": tmp_path.name}]

    files = api_client.get("/api/v1/local-media", params={"root_id": "0"})
    assert files.status_code == 200
    assert [item["name"] for item in files.json()["entries"]] == ["demo.mp4"]

    web_created = api_client.post(
        "/actions/transcriptions",
        data={"local_root_id": "0", "local_path": "demo.mp4"},
        follow_redirects=False,
    )
    assert web_created.status_code == 303

    created = api_client.post(
        "/api/v1/video-transcriptions",
        json={"video_path": str(video_path)},
    )
    assert created.status_code == 202
    transcription = created.json()
    transcription_id = transcription["id"]
    assert transcription["status"] == "pending"

    transcription_page = api_client.get("/transcriptions")
    assert "关联作品" not in transcription_page.text
    assert "local_root_id" in transcription_page.text
    assert "转写服务" in transcription_page.text
    assert "当前服务使用的 Provider 状态" in transcription_page.text
    assert "open-provider-config" in transcription_page.text
    assert "初始化日志" in transcription_page.text
    assert "inline-flex min-h-10" in transcription_page.text
    assert "group flex w-full items-center gap-4" not in transcription_page.text
    assert transcription_page.text.index("转写服务") < transcription_page.text.index(
        "新建转写"
    )

    settings_page = api_client.get("/settings")
    assert "模型安装管理" not in settings_page.text

    fetched = api_client.get(f"/api/v1/video-transcriptions/{transcription_id}")
    assert fetched.status_code == 200
    assert fetched.json()["video_path"] == str(video_path)

    cancelled = api_client.post(
        f"/api/v1/video-transcriptions/{transcription_id}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    retried = api_client.post(f"/api/v1/video-transcriptions/{transcription_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending"

    types = api_client.get("/api/v1/data-storer-types")
    assert types.status_code == 200
    assert [item["type"] for item in types.json()] == ["notion"]

    storer = _create_storer(api_client, "Notion 待配置库")
    validation = api_client.post(f"/api/v1/data-storers/{storer['id']}/validate")
    assert validation.status_code == 200
    assert validation.json()["ready"] is False

    disabled = api_client.delete(f"/api/v1/data-storers/{storer['id']}")
    assert disabled.status_code == 204
    assert api_client.get("/api/v1/data-storers").json() == []


def test_transport_validation_and_not_found_envelope(
    api_client: TestClient,
) -> None:
    invalid = api_client.post(
        "/api/v1/video-transcriptions",
        json={"video_path": "/tmp/a.mp4", "url": "https://example.com/a.mp4"},
    )
    assert invalid.status_code == 422

    missing = api_client.get("/api/v1/awemes/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_group_list_only_scrolls_after_ten_items(api_client: TestClient) -> None:
    for index in range(10):
        _create_group(api_client, f"分组 {index + 1}")

    ten_groups = api_client.get("/")
    assert 'class="space-y-1 pr-1"' in ten_groups.text

    _create_group(api_client, "分组 11")
    eleven_groups = api_client.get("/")
    assert "max-h-[32.25rem] overflow-y-auto" in eleven_groups.text
