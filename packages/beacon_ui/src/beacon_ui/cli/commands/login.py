"""`beacon login` command."""

from __future__ import annotations

import http.server
import secrets
import socketserver
import urllib.parse
import webbrowser
from typing import Any

import click

from beacon_ui.cli.ctx import default_ctx_path, load_context, save_context
from beacon_ui.cli.http import CliHttpClient, CliHttpError


class _CallbackServer(socketserver.TCPServer):
    allow_reuse_address = True


def _run_local_callback_server(port: int) -> dict[str, str]:
    captured: dict[str, str] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/cli-callback":
                self.send_response(404)
                self.end_headers()
                return

            query = urllib.parse.parse_qs(parsed.query)
            for key, values in query.items():
                if values:
                    captured[key] = values[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Beacon login complete.</h1>"
                b"<p>You can close this tab.</p></body></html>"
            )

        def log_message(self, *_args: Any, **_kwargs: Any) -> None:
            return

    with _CallbackServer(("127.0.0.1", port), _Handler) as server:
        server.timeout = 300
        server.handle_request()
    return captured


@click.command()
@click.option("--api-base", default="http://localhost:8000", help="Beacon API base URL.")
@click.option("--api-key", default=None, help="Use this API key directly.")
@click.option(
    "--callback-port",
    default=8765,
    type=int,
    help="Localhost port for browser-login callback.",
)
def login(api_base: str, api_key: str | None, callback_port: int) -> None:
    """Authenticate and store credentials in the local context file."""
    ctx = load_context()
    ctx.api_base = api_base

    if api_key:
        _save_api_key_context(ctx_api_base=api_base, api_key=api_key)
        return

    state = secrets.token_urlsafe(16)
    cli_redirect = f"http://127.0.0.1:{callback_port}/cli-callback"
    start_url = (
        f"{api_base.rstrip('/')}/v1/auth/oidc/start"
        f"?cli_callback={urllib.parse.quote(cli_redirect)}"
        f"&cli_state={state}"
    )
    click.echo(f"Opening browser: {start_url}")
    webbrowser.open(start_url)
    click.echo(f"Waiting on {cli_redirect} for the IdP redirect.")
    params = _run_local_callback_server(callback_port)
    if params.get("state") != state:
        click.echo("Login failed: no valid callback received.", err=True)
        raise click.exceptions.Exit(1)

    callback_key = params.get("api_key")
    if callback_key is None:
        click.echo("Login failed: API key missing from callback.", err=True)
        raise click.exceptions.Exit(1)
    _save_api_key_context(ctx_api_base=api_base, api_key=callback_key)


def _save_api_key_context(*, ctx_api_base: str, api_key: str) -> None:
    try:
        client = CliHttpClient(base_url=ctx_api_base, api_key=api_key)
        me = client.get("/v1/me")
    except CliHttpError as exc:
        click.echo(f"Authentication failed: {exc.message}", err=True)
        raise click.exceptions.Exit(1) from exc

    ctx = load_context()
    ctx.api_base = ctx_api_base
    ctx.api_key = api_key
    save_context(ctx)
    user = me.get("user", {}) if isinstance(me, dict) else {}
    email = user.get("email", "unknown") if isinstance(user, dict) else "unknown"
    click.echo(f"Authenticated as {email}")
    click.echo(f"Saved context to {default_ctx_path()}")
