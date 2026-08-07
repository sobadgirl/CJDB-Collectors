from __future__ import annotations

import json

import typer
from typer.main import get_command
from typer.testing import CliRunner

from cjdb_collectors import cli
from cjdb_collectors.cli.output import (
    CLIResult,
    OutputFormat,
    format_option,
    output_command,
)
from cjdb_collectors.cli.results import aweme_list_result


runner = CliRunner()
sample_app = typer.Typer()


@sample_app.command()
@output_command
def sample(
    output_format: OutputFormat = format_option(),
) -> CLIResult:
    return CLIResult(
        text="面向用户的结果",
        json={"name": "structured-result", "ready": True},
    )


def test_output_command_renders_text_by_default_and_json_on_request() -> None:
    text_result = runner.invoke(sample_app, [])
    json_result = runner.invoke(sample_app, ["--format=json"])

    assert text_result.exit_code == 0
    assert text_result.stdout == "面向用户的结果\n"
    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == {
        "name": "structured-result",
        "ready": True,
    }


def test_output_command_rejects_unknown_format() -> None:
    result = runner.invoke(sample_app, ["--format=yaml"])

    assert result.exit_code == 2
    assert "Invalid value for '--format'" in result.stderr


def test_all_public_leaf_commands_support_format_option() -> None:
    root = get_command(cli.app)
    missing: list[str] = []

    def visit(command, path: list[str]) -> None:
        if getattr(command, "commands", None):
            for name, child in command.commands.items():
                if not child.hidden:
                    visit(child, [*path, name])
            return
        option_names = {
            option.name
            for option in command.params
            if hasattr(option, "opts")
        }
        if "output_format" not in option_names:
            missing.append(" ".join(path))

    visit(root, ["cjdb"])

    assert missing == []


def test_runtime_commands_support_format_when_invoked_directly() -> None:
    root = get_command(cli.app)

    for name in ("webui", "worker"):
        command = root.commands[name]
        option_names = {
            option.name
            for option in command.params
            if hasattr(option, "opts")
        }
        assert "output_format" in option_names


def test_list_json_contract_excludes_internal_model_fields() -> None:
    result = aweme_list_result(
        [
            {
                "id": "aweme-1",
                "platform": "douyin",
                "title": "示例作品",
                "collection_status": "succeeded",
                "collection_run_token": "internal-token",
                "extra_data_json": {"raw": "payload"},
            }
        ],
        page=2,
        size=20,
    )

    assert result.json == {
        "items": [
            {
                "id": "aweme-1",
                "platform": "douyin",
                "title": "示例作品",
                "collection_status": "succeeded",
            }
        ],
        "pagination": {
            "page": 2,
            "size": 20,
            "returned": 1,
        },
    }
    assert "internal-token" not in result.text
