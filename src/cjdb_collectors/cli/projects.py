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
from .results import project_list_result

app = typer.Typer(no_args_is_help=True, help="管理项目及其 Store 绑定。")
store_app = typer.Typer(no_args_is_help=True, help="管理项目 Store。")
app.add_typer(store_app, name="store")


@app.command("list")
@output_command
def list_items(output_format: OutputFormat = format_option()) -> CLIResult:
    return project_list_result(get_services().projects.list())


@app.command("add")
@output_command
def add(
    name: str,
    description: str | None = typer.Option(None, "--description"),
    color: str | None = typer.Option(None, "--color"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().projects.create(
            name,
            description=description,
            color=color,
        ),
        view="project",
    )


@app.command("show")
@output_command
def show(
    project_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().projects.get(project_id),
        view="project",
    )


@app.command("update")
@output_command
def update(
    project_id: str,
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
        get_services().projects.update(project_id, **changes),
        view="project",
    )


@app.command("delete")
@output_command
def delete(
    project_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    get_services().projects.delete(project_id)
    return cli_result(
        {
            "id": project_id,
            "deleted": True,
            "message": "项目已删除。",
        },
        view="message",
    )


@store_app.command("list")
@output_command
def list_stores(
    project_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    store_ids = get_services().projects.store_ids(project_id)
    return cli_result(
        store_ids,
        text_value={
            "project_id": project_id,
            "items": store_ids,
            "title": "绑定的 Store",
            "empty": "该项目未绑定 Store。",
        },
        json_value={
            "project_id": project_id,
            "store_ids": store_ids,
        },
        view="id_list",
    )


@store_app.command("add")
@output_command
def add_store(
    project_id: str,
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().projects.bind_store(project_id, store_id),
        view="project",
    )


@store_app.command("remove")
@output_command
def remove_store(
    project_id: str,
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().projects.unbind_store(project_id, store_id),
        view="project",
    )
