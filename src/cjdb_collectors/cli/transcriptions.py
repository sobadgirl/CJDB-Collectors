from __future__ import annotations

from pathlib import Path

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
from .results import transcription_list_result

app = typer.Typer(no_args_is_help=True, help="管理视频转写任务。")


@app.command("add")
@output_command
def add(
    file: Path | None = typer.Option(
        None,
        "--file",
        exists=True,
        dir_okay=False,
    ),
    url: str | None = typer.Option(None, "--url"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    if bool(file) == bool(url):
        raise typer.BadParameter("exactly one of --file or --url is required")
    return cli_result(
        get_services().transcriptions.create(
            video_path=str(file.resolve()) if file else None,
            url=url,
        ),
        view="transcription",
    )


@app.command("aweme")
@output_command
def aweme(
    aweme_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().transcriptions.transcribe_aweme(aweme_id),
        view="transcription",
    )


@app.command("list")
@output_command
def list_items(
    page: int = typer.Option(1, "--page", min=1),
    size: int = typer.Option(50, "--size", min=1, max=500),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    items = get_services().transcriptions.list(
        limit=size,
        offset=page_offset(page, size),
    )
    return transcription_list_result(items, page=page, size=size)


@app.command("show")
@output_command
def show(
    transcription_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().transcriptions.get(transcription_id),
        view="transcription",
    )


@app.command("run")
@output_command
def run(
    transcription_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().transcriptions.run(transcription_id),
        view="transcription",
    )


@app.command("retry")
@output_command
def retry(
    transcription_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().transcriptions.retry(transcription_id),
        view="transcription",
    )


@app.command("cancel")
@output_command
def cancel(
    transcription_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().transcriptions.cancel(transcription_id),
        view="transcription",
    )
