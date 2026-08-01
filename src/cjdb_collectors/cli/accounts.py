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
from .results import account_list_result, aweme_list_result

app = typer.Typer(no_args_is_help=True, help="管理对标账号。")


@app.command("add")
@output_command
def add(
    url: str,
    platform: str | None = typer.Option(None, "--platform"),
    group: list[str] = typer.Option([], "--group"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().accounts.create(
            url=url,
            platform=platform,
            group_ids=group,
        ),
        view="account",
    )


@app.command("list")
@output_command
def list_items(
    group: list[str] = typer.Option([], "--group"),
    page: int = typer.Option(1, "--page", min=1),
    size: int = typer.Option(50, "--size", min=1, max=500),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    items = get_services().accounts.list(
        group_ids=group,
        limit=size,
        offset=page_offset(page, size),
    )
    return account_list_result(items, page=page, size=size)


@app.command("search")
@output_command
def search(
    keyword: str,
    group: list[str] = typer.Option([], "--group"),
    page: int = typer.Option(1, "--page", min=1),
    size: int = typer.Option(50, "--size", min=1, max=500),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    items = get_services().accounts.search(
        keyword,
        group_ids=group,
        limit=size,
        offset=page_offset(page, size),
    )
    return account_list_result(items, page=page, size=size)


@app.command("show")
@output_command
def show(
    account_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().accounts.get(account_id),
        view="account",
    )


@app.command("update")
@output_command
def update(
    account_id: str,
    name: str | None = typer.Option(None, "--name"),
    profile_url: str | None = typer.Option(None, "--url"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    changes = {
        key: value
        for key, value in {
            "display_name": name,
            "profile_url": profile_url,
        }.items()
        if value is not None
    }
    return cli_result(
        get_services().accounts.update(account_id, **changes),
        view="account",
    )


@app.command("delete")
@output_command
def delete(
    account_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    get_services().accounts.delete(account_id)
    return cli_result(
        {
            "id": account_id,
            "deleted": True,
            "message": "账号已删除。",
        },
        view="message",
    )


@app.command("fetch")
@output_command
def fetch(
    account_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.accounts.fetch_data(services.accounts.get(account_id)),
        view="account",
    )


@app.command("awemes")
@output_command
def awemes(
    account_id: str,
    page: int = typer.Option(1, "--page", min=1),
    size: int = typer.Option(50, "--size", min=1, max=500),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    items = get_services().awemes.list(
        account_id=account_id,
        limit=size,
        offset=page_offset(page, size),
    )
    return aweme_list_result(items, page=page, size=size)


@app.command("set-groups")
@output_command
def set_groups(
    account_id: str,
    group: list[str] = typer.Option(..., "--group"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().accounts.set_groups(account_id, group),
        view="account",
    )
