from __future__ import annotations

import typer

from .common import (
    get_services,
    page_offset,
)
from .output import (
    CLIResult,
    OutputFormat,
    cli_result,
    format_option,
    output_command,
)
from .results import aweme_list_result

app = typer.Typer(no_args_is_help=True, help="管理作品及其采集、下载和转写。")


@app.command("add")
@output_command
def add(
    url: str,
    platform: str | None = typer.Option(None, "--platform"),
    content_type: str | None = typer.Option(
        None,
        "--type",
        help="小红书可指定 video、image；默认自动识别。",
    ),
    project: list[str] = typer.Option([], "--project"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().awemes.add(
            url=url,
            platform=platform,
            content_type=content_type,
            project_ids=project,
        ),
        view="aweme",
    )


@app.command("list")
@output_command
def list_items(
    project: list[str] = typer.Option([], "--project"),
    page: int = typer.Option(1, "--page", min=1),
    size: int = typer.Option(50, "--size", min=1, max=500),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    items = get_services().awemes.list(
        project_ids=project,
        limit=size,
        offset=page_offset(page, size),
    )
    return aweme_list_result(items, page=page, size=size)


@app.command("search")
@output_command
def search(
    keyword: str,
    project: list[str] = typer.Option([], "--project"),
    page: int = typer.Option(1, "--page", min=1),
    size: int = typer.Option(50, "--size", min=1, max=500),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    items = get_services().awemes.search(
        keyword,
        project_ids=project,
        limit=size,
        offset=page_offset(page, size),
    )
    return aweme_list_result(items, page=page, size=size)


@app.command("show")
@output_command
def show(
    aweme_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().awemes.get(aweme_id),
        view="aweme",
    )


@app.command("update")
@output_command
def update(
    aweme_id: str,
    title: str | None = typer.Option(None, "--title"),
    description: str | None = typer.Option(None, "--description"),
    content_type: str | None = typer.Option(None, "--type"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    changes = {
        key: value
        for key, value in {
            "title": title,
            "description": description,
            "content_type": content_type,
        }.items()
        if value is not None
    }
    return cli_result(
        get_services().awemes.update(aweme_id, **changes),
        view="aweme",
    )


@app.command("delete")
@output_command
def delete(
    aweme_id: str,
    delete_files: bool = typer.Option(False, "--delete-files"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    get_services().awemes.delete(
        aweme_id,
        delete_downloaded_files=delete_files,
    )
    return cli_result(
        {
            "id": aweme_id,
            "deleted": True,
            "files_deleted": delete_files,
            "message": "作品已删除。",
        },
        view="message",
    )


@app.command("fetch")
@output_command
def fetch(
    aweme_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.awemes.fetch_data(services.awemes.get(aweme_id)),
        view="aweme",
    )


@app.command("fetch-comments")
@output_command
def fetch_comments(
    aweme_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.awemes.fetch_comments(services.awemes.get(aweme_id)),
        view="aweme",
    )


@app.command("download-video")
@output_command
def download_video(
    aweme_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.awemes.download_video(services.awemes.get(aweme_id)),
        view="aweme",
    )


@app.command("download-images")
@output_command
def download_images(
    aweme_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.awemes.download_images(services.awemes.get(aweme_id)),
        view="aweme",
    )


@app.command("transcription")
@output_command
def transcription(
    aweme_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().transcriptions.transcribe_aweme(aweme_id),
        view="transcription",
    )


@app.command("set-projects")
@output_command
def set_projects(
    aweme_id: str,
    project: list[str] = typer.Option(..., "--project"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().awemes.set_projects(aweme_id, project),
        view="aweme",
    )
