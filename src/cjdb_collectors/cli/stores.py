from __future__ import annotations

from pathlib import Path

import typer

from .common import (
    get_services,
    parse_values,
)
from .output import (
    CLIResult,
    OutputFormat,
    cli_result,
    format_option,
    output_command,
)
from .results import (
    store_list_result,
    store_type_list_result,
    sync_list_result,
)

app = typer.Typer(no_args_is_help=True, help="管理 Store 及数据写入。")
default_app = typer.Typer(no_args_is_help=True, help="管理全局默认 Store。")
sync_app = typer.Typer(no_args_is_help=True, help="管理 Store 同步任务。")
app.add_typer(default_app, name="default")
app.add_typer(sync_app, name="sync")


def _store_view(services, item, *, default_ids: set | None = None) -> dict:
    return {
        **item.model_dump(mode="json"),
        "default": (
            item.id in default_ids
            if default_ids is not None
            else services.stores.is_default(item.id)
        ),
    }


@app.command("types")
@output_command
def types(output_format: OutputFormat = format_option()) -> CLIResult:
    return store_type_list_result(get_services().stores.types())


@app.command("list")
@output_command
def list_items(
    include_disabled: bool = typer.Option(False, "--all"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    default_ids = set(services.stores.default_ids())
    items = [
        _store_view(services, item, default_ids=default_ids)
        for item in services.stores.list(include_disabled)
    ]
    return store_list_result(items)


@app.command("show")
@output_command
def show(
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        _store_view(services, services.stores.get(store_id)),
        view="store",
    )


@app.command("add")
@output_command
def add(
    provider_type: str,
    name: str = typer.Option(..., "--name"),
    values: list[str] = typer.Argument([], metavar="[KEY=VALUE]..."),
    values_file: Path | None = typer.Option(None, "--values-file"),
    default: bool = typer.Option(False, "--default"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    item = services.stores.add(
        type=provider_type,
        name=name,
        setup_values=parse_values(values, values_file=values_file),
        default=default,
    )
    return cli_result(
        _store_view(services, item),
        view="store",
    )


@app.command("setup")
@output_command
def setup(
    store_id: str,
    values: list[str] = typer.Argument([], metavar="[KEY=VALUE]..."),
    values_file: Path | None = typer.Option(None, "--values-file"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().stores.setup(
            store_id,
            parse_values(values, values_file=values_file),
        ),
        view="setup_result",
    )


@app.command("update")
@output_command
def update(
    store_id: str,
    name: str | None = typer.Option(None, "--name"),
    status: str | None = typer.Option(None, "--status"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    changes = {
        key: value
        for key, value in {"name": name, "status": status}.items()
        if value is not None
    }
    services = get_services()
    item = services.stores.update(store_id, **changes)
    return cli_result(
        _store_view(services, item),
        view="store",
    )


@app.command("delete")
@output_command
def delete(
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    get_services().stores.delete(store_id)
    return cli_result(
        {
            "id": store_id,
            "deleted": True,
            "message": "Store 已停用。",
        },
        view="message",
    )


@app.command("status")
@output_command
def status(
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().stores.status(store_id),
        view="store_status",
    )


@app.command("aweme")
@output_command
def store_aweme(
    aweme_id: str,
    store_id: str = typer.Option(..., "--to"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.stores.store_aweme(
            services.awemes.get(aweme_id),
            store_id,
        ),
        view="store_result",
    )


@app.command("account")
@output_command
def store_account(
    account_id: str,
    store_id: str = typer.Option(..., "--to"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.stores.store_account(
            services.accounts.get(account_id),
            store_id,
        ),
        view="store_result",
    )


@app.command("transcription")
@output_command
def store_transcription(
    transcription_id: str,
    store_id: str = typer.Option(..., "--to"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    services = get_services()
    return cli_result(
        services.stores.store_transcription(
            services.transcriptions.get(transcription_id),
            store_id,
        ),
        view="store_result",
    )


@default_app.command("list")
@output_command
def list_defaults(output_format: OutputFormat = format_option()) -> CLIResult:
    services = get_services()
    default_ids = set(services.stores.default_ids())
    items = [
        _store_view(
            services,
            services.stores.get(store_id),
            default_ids=default_ids,
        )
        for store_id in default_ids
    ]
    return store_list_result(items)


@default_app.command("set")
@output_command
def set_default(
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    result = get_services().stores.set_default(store_id, True)
    return cli_result(
        {**result, "message": "已设为默认 Store。"},
        view="message",
    )


@default_app.command("unset")
@output_command
def unset_default(
    store_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    result = get_services().stores.set_default(store_id, False)
    return cli_result(
        {**result, "message": "已取消默认 Store。"},
        view="message",
    )


@sync_app.command("list")
@output_command
def list_syncs(
    aweme: str | None = typer.Option(None, "--aweme"),
    account: str | None = typer.Option(None, "--account"),
    transcription: str | None = typer.Option(None, "--transcription"),
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return sync_list_result(
        get_services().sync.list(
            aweme_id=aweme,
            account_id=account,
            transcription_id=transcription,
        )
    )


@sync_app.command("retry")
@output_command
def retry_sync(
    sync_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().sync.retry(sync_id),
        view="sync",
    )


@sync_app.command("enable")
@output_command
def enable_sync(
    sync_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().sync.enable(sync_id),
        view="sync",
    )


@sync_app.command("disable")
@output_command
def disable_sync(
    sync_id: str,
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return cli_result(
        get_services().sync.disable(sync_id),
        view="sync",
    )
