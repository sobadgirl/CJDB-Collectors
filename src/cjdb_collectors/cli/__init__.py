"""CJDB command-line interface."""

import typer

from .accounts import app as account_app
from .awemes import app as aweme_app
from .projects import app as project_app
from .providers import app as provider_app
from .runtime import webui_app, worker_app
from .settings import app as settings_app
from .stores import app as store_app
from .transcriptions import app as transcription_app

app = typer.Typer(name="cjdb", no_args_is_help=True)
app.add_typer(aweme_app, name="aweme")
app.add_typer(account_app, name="account")
app.add_typer(transcription_app, name="transcription")
app.add_typer(project_app, name="project")
app.add_typer(provider_app, name="provider")
app.add_typer(store_app, name="store")
app.add_typer(worker_app, name="worker")
app.add_typer(webui_app, name="webui")
app.add_typer(settings_app, name="settings")


def main() -> None:
    app()


__all__ = ["app", "main"]
