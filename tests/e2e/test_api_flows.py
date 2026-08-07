from pathlib import Path

from fastapi.testclient import TestClient


def _create_project(client: TestClient, name: str = "爆款") -> dict:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "description": "E2E 项目", "color": "#570df8"},
    )
    assert response.status_code == 201
    return response.json()


def _create_provider(
    client: TestClient,
    project_id: str,
    name: str = "Notion 主库",
    provider_type: str = "store_aweme",
) -> dict:
    response = client.post(
        "/api/v1/providers",
        json={
            "name": name,
            "namespace": "notion",
            "project_id": project_id,
            "provider_type": provider_type,
            "values": {},
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
    health = service_health.json()
    assert health["collector"]["ready"] is False
    assert isinstance(health["transcription"]["ready"], bool)

    openapi = api_client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = openapi.json()["paths"]
    required_paths = {
        "/api/v1/accounts",
        "/api/v1/awemes",
        "/api/v1/awemes/{aweme_id}/fetch",
        "/api/v1/awemes/{aweme_id}/comments/fetch",
        "/api/v1/awemes/{aweme_id}/video/download",
                "/api/v1/projects",
        "/api/v1/projects/{project_id}/providers",
        "/api/v1/accounts/{account_id}/projects",
        "/api/v1/awemes/{aweme_id}/projects",
        "/api/v1/projects/{project_id}/providers/{provider_id}",
        "/api/v1/projects/{project_id}/providers/importable",
        "/api/v1/video-transcriptions",
        "/api/v1/local-media",
        "/api/v1/providers",
        "/api/v1/providers/services",
        "/api/v1/providers/status",
        "/api/v1/providers/status/refresh",
        "/api/v1/providers/{provider_type}/status",
        "/api/v1/providers/{provider_type}/status/refresh",
        "/api/v1/providers/{provider_type}/logs",
        "/api/v1/providers/{provider_type}/setup",
        "/api/v1/providers/selection",
        "/api/v1/system/checks",
        "/api/v1/sync",
        "/api/v1/worker-tasks",
        "/api/v1/worker-tasks/logs",
        "/api/v1/settings",
    }
    assert required_paths <= set(paths)

    config_update = api_client.patch(
        "/api/v1/settings",
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
    log_path.write_text("", encoding="utf-8")
    empty_file_logs = api_client.get(
        "/api/v1/providers/douyin_aweme_collect/logs",
        params={"limit": 2},
    )
    assert empty_file_logs.status_code == 200
    assert empty_file_logs.json()["lines"] == []
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

    for page in ("/awemes", "/accounts", "/transcriptions", "/settings"):
        response = api_client.get(page)
        assert response.status_code == 200
        assert "超级对标" in response.text

    root = api_client.get("/", follow_redirects=False)
    assert root.status_code == 303
    assert root.headers["location"] == "/awemes"

    dashboard = api_client.get("/awemes")
    accounts_dashboard = api_client.get("/accounts")
    transcriptions_dashboard = api_client.get("/transcriptions")
    assert "对标作品管理" in dashboard.text
    assert "对标账号管理" in accounts_dashboard.text
    assert "敬请期待" in accounts_dashboard.text
    assert "添加账号" not in accounts_dashboard.text
    assert "视频文字转写" in transcriptions_dashboard.text
    assert 'href="/awemes"' in dashboard.text
    assert 'href="/accounts"' in dashboard.text
    assert 'href="/transcriptions"' in dashboard.text
    assert 'role="tablist"' not in dashboard.text
    assert "采集服务" in dashboard.text
    assert "当前服务使用的 Provider 状态" in dashboard.text
    assert "open-provider-config" in dashboard.text
    assert "初始化日志" in dashboard.text
    assert "点击启动" in dashboard.text
    assert "启动后台采集调度" in dashboard.text
    assert "调度日志" in dashboard.text
    assert "任务日志" in dashboard.text
    assert 'class="absolute left-0 top-10 z-40 h-3 w-[30rem]"' in dashboard.text
    assert "配置数据采集服务" not in dashboard.text
    assert "校验并保存" not in dashboard.text
    assert "inline-flex min-h-10" in dashboard.text
    assert "group flex w-full items-center gap-4" not in dashboard.text
    assert "已添加的存储源" in accounts_dashboard.text
    assert "可同时启用多个 Provider" in accounts_dashboard.text
    assert "store_account" in accounts_dashboard.text
    assert "抖音账号存储" not in accounts_dashboard.text
    assert "小红书账号存储" not in accounts_dashboard.text
    assert "请选择数据类型" not in accounts_dashboard.text
    assert "await this.loadStoreCatalog()" in accounts_dashboard.text
    assert "storeStatusItem" not in accounts_dashboard.text
    assert "store_connector_context" not in accounts_dashboard.text
    assert "sidebar_store_status" not in accounts_dashboard.text
    assert "已添加的存储源" in accounts_dashboard.text
    assert "storeProviderPopover" in accounts_dashboard.text
    assert "/api/v1/providers?type=" in accounts_dashboard.text
    assert "statusDotClass" in accounts_dashboard.text
    assert "cardClass" in accounts_dashboard.text
    assert "border-emerald-200" in accounts_dashboard.text
    assert "border-amber-200" in accounts_dashboard.text
    assert "border-red-200" in accounts_dashboard.text
    assert "selectedProviders.length" in accounts_dashboard.text


def test_aweme_account_project_and_sync_relationship_flow(
    api_client: TestClient, tmp_path: Path
) -> None:
    project = _create_project(api_client)
    storer = _create_provider(api_client, project["id"])
    account_storer = _create_provider(
        api_client,
        project["id"],
        "Notion 账号库",
        "store_account",
    )

    aweme_response = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.douyin.com/video/731234567890",
            "platform": "douyin",
            "project_ids": [project["id"]],
            "download_video": True,
            "collect_comments": True,
            "comment_max_count": 120,
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
    assert aweme["comment_collection_status"] == "not_requested"
    assert aweme["comment_provider_state_json"] == {}
    assert aweme["video_transcription_status"] == "not_requested"

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

    id_only_aweme_response = api_client.post(
        "/api/v1/awemes",
        json={
            "platform": "douyin",
            "platform_aweme_id": "731234567891",
        },
    )
    assert id_only_aweme_response.status_code == 202
    id_only_aweme = id_only_aweme_response.json()
    assert id_only_aweme["platform_aweme_id"] == "731234567891"
    assert id_only_aweme["source_url"] == "731234567891"

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
            "project_ids": [project["id"]],
        },
    )
    assert account_response.status_code == 202
    account = account_response.json()
    account_collect_action = api_client.post(
        f"/actions/accounts/{account['id']}/collect",
        data={"next_url": "/accounts"},
        follow_redirects=False,
    )
    assert account_collect_action.status_code == 303

    author_collect_action = api_client.post(
        "/actions/accounts",
        data={
            "platform": "douyin",
            "platform_account_id": "MS4wLjABAAAA-author",
            "collect": "true",
            "next_url": "/awemes",
            "project_ids": [project["id"]],
        },
        follow_redirects=False,
    )
    assert author_collect_action.status_code == 303
    author_account = next(
        item
        for item in api_client.get("/api/v1/accounts").json()
        if item["platform_account_id"] == "MS4wLjABAAAA-author"
    )
    assert author_account["collection_status"] == "not_requested"

    all_records_page = api_client.get("/awemes")
    assert all_records_page.status_code == 200
    assert "请选择项目" not in all_records_page.text
    assert "项目" in all_records_page.text
    assert "https://www.douyin.com/video/731234567890" in all_records_page.text
    assert f"/partials/awemes/{aweme['id']}/detail" in all_records_page.text
    assert "作品详情" not in all_records_page.text
    assert "选择平台" in all_records_page.text
    assert "platforms: [" in all_records_page.text
    assert "小红书" in all_records_page.text
    assert "视频号" in all_records_page.text
    assert "公众号" in all_records_page.text
    assert "抖音" in all_records_page.text
    assert "无封面" in all_records_page.text
    assert 'name="platform_aweme_id"' not in all_records_page.text
    assert "小红书类型" in all_records_page.text
    assert '<option value="unknown">自动识别</option>' in all_records_page.text
    assert '<option value="image">图文笔记</option>' in all_records_page.text
    assert '<option value="video">视频笔记</option>' in all_records_page.text

    aweme_list = api_client.get(
        "/api/v1/awemes", params={"project_id": project["id"]}
    ).json()
    account_list = api_client.get(
        "/api/v1/accounts", params={"project_id": project["id"]}
    ).json()
    assert [item["id"] for item in aweme_list] == [aweme["id"]]
    assert {item["id"] for item in account_list} == {
        account["id"],
        author_account["id"],
    }

    rendered_list = api_client.get(
        "/awemes", params={"project_id": project["id"]}
    )
    assert rendered_list.status_code == 200
    assert "编辑" in rendered_list.text
    assert "删除" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/collect" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/delete" in rendered_list.text
    assert f"/partials/awemes/{aweme['id']}/detail" in rendered_list.text
    assert "record_detail_modal.html" not in rendered_list.text
    detail_partial = api_client.get(f"/partials/awemes/{aweme['id']}/detail")
    assert detail_partial.status_code == 200
    assert "作品详情" in detail_partial.text
    assert "作品 URL" in detail_partial.text
    assert "modal-open" in detail_partial.text
    assert f'hx-post="/actions/awemes/{aweme["id"]}/collect"' in rendered_list.text
    assert "敬请期待" in rendered_list.text
    assert "评论数" in rendered_list.text
    assert "评论采集状态" in rendered_list.text
    assert 'name="comment_max_count"' not in rendered_list.text
    assert "data-async-list-action" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/collect/cancel" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/comments/collect" not in rendered_list.text
    assert (
        f"/actions/awemes/{aweme['id']}/comments/collect/cancel"
        not in rendered_list.text
    )

    collect_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/collect",
        data={"next_url": f"/awemes?project_id={project['id']}"},
        follow_redirects=False,
    )
    assert collect_action.status_code == 303
    queued_aweme = api_client.get(f"/api/v1/awemes/{aweme['id']}").json()
    assert queued_aweme["collection_status"] == "pending"
    assert queued_aweme["collection_attempt_count"] == 0
    assert queued_aweme["collection_error"] is None

    htmx_collect_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/collect",
        data={"next_url": f"/awemes?project_id={project['id']}"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert htmx_collect_action.status_code == 200
    assert "作品" in htmx_collect_action.text
    assert "等待调度采集" in htmx_collect_action.text
    assert 'hx-trigger="every' not in htmx_collect_action.text
    assert "/actions/awemes/" in htmx_collect_action.text

    assert "切换项目" in rendered_list.text
    assert "项目设置" in rendered_list.text
    assert project["name"] in rendered_list.text
    assert "2 Provider" in rendered_list.text
    header_start = rendered_list.text.index("对标作品管理</h1>")
    header_end = rendered_list.text.index("管理对标作品的采集、下载、转写和同步。")
    header_store_area = rendered_list.text[header_start:header_end]
    assert storer["name"] not in header_store_area
    assert account_storer["name"] not in header_store_area
    assert "存储 Provider" in header_store_area
    assert "subjectType: 'aweme'" in rendered_list.text
    assert "subjectType: 'account'" in rendered_list.text
    assert "subjectType: 'video_transcription'" in rendered_list.text
    assert "cjdb.activeProjectIds" not in rendered_list.text
    assert "withActiveProject" not in rendered_list.text
    assert "视频转写</th>" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/transcribe" in rendered_list.text
    assert f"/actions/awemes/{aweme['id']}/transcribe/cancel" not in rendered_list.text
    assert "未创建" in rendered_list.text
    assert "发起转写" in rendered_list.text
    video_for_transcription = tmp_path / "aweme-transcription.mp4"
    video_for_transcription.write_bytes(b"video")
    api_client.patch(
        f"/api/v1/awemes/{aweme['id']}",
        json={"video_path": str(video_for_transcription)},
    )
    transcribe_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/transcribe",
        data={"next_url": f"/awemes?project_id={project['id']}"},
        follow_redirects=False,
    )
    assert transcribe_action.status_code == 303
    transcription = api_client.get(
        "/api/v1/video-transcriptions",
        params={"aweme_id": aweme["id"]},
    ).json()[0]
    rendered_with_transcription = api_client.get(
        "/awemes", params={"project_id": project["id"]}
    )
    assert (
        f"/partials/transcriptions/{transcription['id']}/detail"
        in rendered_with_transcription.text
    )
    assert (
        f"/actions/transcriptions/{transcription['id']}/retry"
        in rendered_with_transcription.text
    )
    assert (
        f"/actions/transcriptions/{transcription['id']}/cancel"
        in rendered_with_transcription.text
    )
    transcription_detail = api_client.get(
        f"/partials/transcriptions/{transcription['id']}/detail"
    )
    assert transcription_detail.status_code == 200
    assert "视频转写详情" in transcription_detail.text
    assert "open-store-picker" in rendered_list.text
    assert "/actions/data-storers/" not in rendered_list.text

    config_page = api_client.get("/settings")
    assert config_page.status_code == 200
    assert "服务状态" in config_page.text
    assert "Provider 服务" not in config_page.text
    assert "请选择 Provider" in config_page.text
    assert "/api/v1/providers/selection" in config_page.text
    assert "从其他项目导入" in rendered_list.text

    aweme_syncs = api_client.get(f"/api/v1/awemes/{aweme['id']}/syncs").json()
    account_syncs = api_client.get(f"/api/v1/accounts/{account['id']}/syncs").json()
    assert {item["provider_id"] for item in aweme_syncs} == {storer["id"]}
    assert {item["provider_id"] for item in account_syncs} == {
        account_storer["id"]
    }

    # Removing project membership must not remove the direct sync relationship.
    assert (
        api_client.put(
            f"/api/v1/awemes/{aweme['id']}/projects", json={"ids": []}
        ).status_code
        == 200
    )
    assert (
        api_client.put(
            f"/api/v1/accounts/{account['id']}/projects", json={"ids": []}
        ).status_code
        == 200
    )
    assert all(
        item["enabled"] is False
        for item in api_client.get(
            f"/api/v1/awemes/{aweme['id']}/syncs"
        ).json()
    )
    assert all(
        item["enabled"] is False
        for item in api_client.get(
            f"/api/v1/accounts/{account['id']}/syncs"
        ).json()
    )

    disposable = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.douyin.com/video/888888888888",
            "platform": "douyin",
            "project_ids": [project["id"]],
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

    delete_preview = api_client.get("/awemes")
    assert "同步删除已下载文件" in delete_preview.text
    assert "/api/media?path=" in delete_preview.text
    assert "视频" in delete_preview.text
    assert "封面" in delete_preview.text
    assert "图片 1" in delete_preview.text
    assert str(video) in delete_preview.text
    assert str(cover) in delete_preview.text
    assert str(photo) in delete_preview.text
    disposable_detail = api_client.get(f"/partials/awemes/{disposable['id']}/detail")
    assert disposable_detail.status_code == 200
    assert 'poster="/api/media?path=' in disposable_detail.text
    assert "downloaded-cover.jpg" in disposable_detail.text
    local_media = api_client.get("/api/media", params={"path": str(photo)})
    assert local_media.status_code == 200
    assert local_media.content == b"local"

    delete_action = api_client.post(
        f"/actions/awemes/{disposable['id']}/delete",
        data={
            "next_url": "/awemes",
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


def test_provider_can_be_imported_between_projects_and_filtered(
    api_client: TestClient,
) -> None:
    source = _create_project(api_client, "来源项目")
    target = _create_project(api_client, "目标项目")
    created = api_client.post(
        "/api/v1/providers",
        json={
            "namespace": "notion",
            "name": "共享 Notion",
            "project_id": source["id"],
            "provider_type": "store_aweme",
            "values": {},
        },
    )
    assert created.status_code == 201
    provider = created.json()

    source_list = api_client.get(
        "/api/v1/providers",
        params={"project_id": source["id"], "type": "store_aweme"},
    ).json()
    target_list = api_client.get(
        "/api/v1/providers",
        params={"project_id": target["id"], "type": "store_aweme"},
    ).json()
    assert [item["provider_id"] for item in source_list["providers"]] == [
        provider["provider_id"]
    ]
    assert source_list["selection_mode"] == "multiple"
    assert source_list["selected"] == [provider["provider_id"]]
    cleared = api_client.patch(
        "/api/v1/providers/selection",
        json={
            "type": "store_aweme",
            "project_id": source["id"],
            "selected": False,
        },
    )
    assert cleared.status_code == 200
    assert cleared.json()["provider_id"] is None
    assert cleared.json()["selected"] == []
    assert (
        api_client.get(
            "/api/v1/providers",
            params={"project_id": source["id"], "type": "store_aweme"},
        ).json()["selected"]
        == []
    )
    reselected = api_client.patch(
        "/api/v1/providers/selection",
        json={
            "type": "store_aweme",
            "project_id": source["id"],
            "provider_id": provider["provider_id"],
            "selected": True,
        },
    )
    assert reselected.status_code == 200
    assert reselected.json()["selected"] == [provider["provider_id"]]
    assert source_list["providers"][0]["setup_payload"] == {
        "upload_image_attachments": False,
        "upload_video_attachments": False,
    }
    assert source_list["providers"][0]["namespace"] == "notion"
    assert source_list["provider_classes"][0]["type"] == "notion"
    assert [
        parameter["key"]
        for parameter in source_list["provider_classes"][0]["parameters"]
    ] == [
        "token",
        "data_source_id",
        "upload_image_attachments",
        "upload_video_attachments",
    ]
    other_type = api_client.get(
        "/api/v1/providers",
        params={
            "project_id": source["id"],
            "type": "store_account",
        },
    ).json()
    assert other_type["providers"][0]["provider_id"] == provider["provider_id"]
    assert other_type["selected"] == []
    assert [
        item["provider_id"]
        for item in api_client.get(
            f"/api/v1/projects/{source['id']}/providers",
            params={"type": "store_aweme"},
        ).json()
    ] == [provider["provider_id"]]
    assert target_list["providers"] == []

    importable = api_client.get(
        f"/api/v1/projects/{target['id']}/providers/importable",
        params={"subject_type": "aweme"},
    ).json()
    assert [item["provider_id"] for item in importable["providers"]] == [
        provider["provider_id"]
    ]
    assert importable["providers"][0]["projects"] == [
        {"id": source["id"], "name": source["name"]}
    ]

    typed_importable = api_client.get(
        "/api/v1/providers",
        params={
            "project_id": target["id"],
            "type": "store_aweme",
            "importable": "true",
        },
    ).json()
    assert typed_importable["providers"][0]["projects"] == [
        {"id": source["id"], "name": source["name"]}
    ]

    imported = api_client.post(
        f"/api/v1/projects/{target['id']}/providers/{provider['provider_id']}",
        params={"type": "store_aweme"},
    )
    assert imported.status_code == 200
    target_list = api_client.get(
        "/api/v1/providers",
        params={"project_id": target["id"], "type": "store_aweme"},
    ).json()
    assert [item["provider_id"] for item in target_list["providers"]] == [
        provider["provider_id"]
    ]
    second = _create_provider(api_client, target["id"], "第二个 Notion")
    selected = api_client.get(
        "/api/v1/providers",
        params={"project_id": target["id"], "type": "store_aweme"},
    ).json()["selected"]
    assert set(selected) == {provider["provider_id"], second["provider_id"]}

    removed = api_client.delete(
        f"/api/v1/projects/{target['id']}/providers/{provider['provider_id']}"
    )
    assert removed.status_code == 204
    remaining = api_client.get(
        "/api/v1/providers",
        params={"project_id": target["id"], "type": "store_aweme"},
    ).json()
    assert [item["provider_id"] for item in remaining["providers"]] == [
        second["provider_id"]
    ]
    assert remaining["selected"] == [second["provider_id"]]


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
    url_created = api_client.post(
        "/api/v1/video-transcriptions",
        json={"url": "https://example.com/a.mp4"},
    )
    assert url_created.status_code == 202
    assert url_created.json()["source_url"] == "https://example.com/a.mp4"
    assert url_created.json()["video_path"] is None

    transcription_list = api_client.get("/api/v1/video-transcriptions")
    assert transcription_list.status_code == 200
    assert "text" not in transcription_list.json()[0]
    assert "normalized_text" not in transcription_list.json()[0]
    assert "segments_json" not in transcription_list.json()[0]
    assert "text_summary" in transcription_list.json()[0]
    assert "duration_seconds" in transcription_list.json()[0]

    transcription_page = api_client.get("/transcriptions")
    assert "转写摘要" in transcription_page.text
    assert "耗时" in transcription_page.text
    assert "暂无摘要" in transcription_page.text
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

    config_page = api_client.get("/settings")
    assert "模型安装管理" not in config_page.text

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

    project = _create_project(api_client, "转写 Provider")
    storer = _create_provider(api_client, project["id"], "Notion 待配置库")
    validation = api_client.post(
        f"/api/v1/providers/instances/{storer['id']}/status/refresh"
    )
    assert validation.status_code == 200
    assert validation.json()["ready"] is False

    assert api_client.delete(
        f"/api/v1/projects/{project['id']}/providers/{storer['id']}"
    ).status_code == 204
    deleted = api_client.delete(f"/api/v1/providers/instances/{storer['id']}")
    assert deleted.status_code == 204


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


def test_project_switcher_keeps_large_project_lists_scrollable(
    api_client: TestClient,
) -> None:
    for index in range(10):
        _create_project(api_client, f"项目 {index + 1}")

    ten_projects = api_client.get("/awemes")
    assert "切换项目" in ten_projects.text
    assert "max-h-72 space-y-1 overflow-auto" in ten_projects.text

    _create_project(api_client, "项目 11")
    eleven_projects = api_client.get("/awemes")
    assert "项目 11" in eleven_projects.text


def test_dashboard_project_selection_uses_cookie_without_url_project_id(
    api_client: TestClient,
) -> None:
    _create_project(api_client, "空项目")
    active_project = _create_project(api_client, "当前项目")
    aweme_response = api_client.post(
        "/api/v1/awemes",
        json={
            "url": "https://www.douyin.com/video/732222222222",
            "platform": "douyin",
            "project_ids": [active_project["id"]],
        },
    )
    assert aweme_response.status_code == 202
    aweme = aweme_response.json()

    selected_page = api_client.get(
        "/awemes",
        params={"project_id": active_project["id"]},
    )
    assert selected_page.status_code == 200
    assert api_client.cookies.get("cjdb_active_project_id") == active_project["id"]

    cookie_backed_page = api_client.get("/awemes")
    assert cookie_backed_page.status_code == 200
    assert "https://www.douyin.com/video/732222222222" in cookie_backed_page.text

    htmx_action = api_client.post(
        f"/actions/awemes/{aweme['id']}/collect",
        data={"next_url": "/awemes"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert htmx_action.status_code == 200
    assert "https://www.douyin.com/video/732222222222" in htmx_action.text
    assert api_client.cookies.get("cjdb_active_project_id") == active_project["id"]
