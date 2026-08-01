from __future__ import annotations

import typer

from .common import get_services
from .output import (
    CLIResult,
    OutputFormat,
    cli_result,
    format_option,
    output_command,
)
from .results import group_list_result

app = typer.Typer(no_args_is_help=True, help="管理分组及其 Store 绑定。")
store_app = typer.Typer(no_args_is_help=True, help="管理分组 Store。")
app.add_typer(store_app, name="store")


@app.command("list")
@output_command
def list_items(output_format: OutputFormat = format_option()) -> CLIResult:
    return group_list_result(get_services().groups.list())


@app.command("add")
@output_command
def add(
    name: str,
    description: str | None = typer.Option(None, "--description"),
    color: str | None = typer.Option(None, "--color"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().groups.create(
            name,
            description=description,
            color=color,
        ),
        view="group",
    )


@app.command("show")
@output_command
def show(
    group_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().groups.get(group_id),
        view="group",
    )


@app.command("update")
@output_command
def update(
    group_id: str,
    name: str | None = typer.Option(None, "--name"),
    description: str | None = typer.Option(None, "--description"),
    color: str | None = typer.Option(None, "--color"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    changes = {
        key: value
        for key, value in {
            "name": name,
            "description": description,
            "color": color,
        }.items()
        if value is not None
    }
    return cli_result(
        get_services().groups.update(group_id, **changes),
        view="group",
    )


@app.command("delete")
@output_command
def delete(
    group_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    get_services().groups.delete(group_id)
    return cli_result(
        {
            "id": group_id,
            "deleted": True,
            "message": "分组已删除。",
        },
        view="message",
    )


@store_app.command("list")
@output_command
def list_stores(
    group_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    store_ids = get_services().groups.store_ids(group_id)
    return cli_result(
        store_ids,
        text_value={
            "group_id": group_id,
            "items": store_ids,
            "title": "绑定的 Store",
            "empty": "该分组未绑定 Store。",
        },
        json_value={
            "group_id": group_id,
            "store_ids": store_ids,
        },
        view="id_list",
    )


@store_app.command("add")
@output_command
def add_store(
    group_id: str,
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().groups.bind_store(group_id, store_id),
        view="group",
    )


@store_app.command("remove")
@output_command
def remove_store(
    group_id: str,
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().groups.unbind_store(group_id, store_id),
        view="group",
    )
