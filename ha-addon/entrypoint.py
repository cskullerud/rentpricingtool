"""Start the Rent Pricing app inside the Home Assistant app container.

Reads the options Home Assistant saved in /data/options.json, checks them, turns them into the
environment variables the app reads, and starts uvicorn. Run by run.sh.

Secrets: the API key and the UI secret are passed to the app only through its environment.
Nothing here prints option values; errors name the option, never its content.
"""
import json
import os
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

import uvicorn

from app.security import DEFAULT_SUPERVISOR_PEER, parse_allowed_peers

OPTIONS_PATH = Path("/data/options.json")
DATA_DIR = Path("/data")
LOG_CONFIG_PATH = Path(__file__).resolve().parent / "log_config.json"

HOST = "0.0.0.0"  # inside the container; no port is published to the host
PORT = 8099  # the ingress_port in config.yaml

DATA_PROVIDERS = ("mock", "rentcast")
LOG_LEVELS = ("debug", "info", "warning", "error")
DATABASE_FILE = "rentpricingtool.db"  # created fresh in /data on first start
SECRET_FILE = "ui_secret"



class ConfigurationError(Exception):
    """The saved options cannot be used. The message never contains an option's value."""


def load_options(path: Path = OPTIONS_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise ConfigurationError(f"The options file {path} does not exist") from None
    except (OSError, ValueError):
        raise ConfigurationError(f"The options file {path} could not be read as JSON") from None
    if not isinstance(data, dict):
        raise ConfigurationError("The options file must contain a JSON object")
    return data


def _text(options: Mapping, key: str) -> str:
    """An option as trimmed text; missing, null and blank all mean ''. Anything that is not
    text is refused rather than quietly replaced by a default."""
    value = options.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigurationError(f"Option {key} must be text")
    return value.strip()


def _choice(options: Mapping, key: str, allowed: tuple[str, ...], default: str) -> str:
    value = _text(options, key).lower() or default
    if value not in allowed:
        raise ConfigurationError(f"Option {key} must be one of: {', '.join(allowed)}")
    return value


def _min_comparables(options: Mapping) -> int:
    value = options.get("min_comparables_required", 3)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        raise ConfigurationError("Option min_comparables_required must be a whole number from 1 to 50")
    return value


def ensure_ui_secret(data_dir: Path = DATA_DIR) -> str:
    """The key that signs CSRF tokens when none is configured: made once, kept in /data.

    Persisting it means a form left open across a restart or update is still accepted.
    """
    path = Path(data_dir) / SECRET_FILE
    try:
        existing = path.read_text().strip()
    except FileNotFoundError:
        existing = ""
    if len(existing) >= 32:
        return existing
    value = secrets.token_urlsafe(48)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(value)
    os.chmod(path, 0o600)
    return value


def build_environment(options: Mapping, data_dir: Path = DATA_DIR) -> dict[str, str]:
    """The environment variables for the app, from the options. Raises ConfigurationError."""
    provider = _choice(options, "data_provider", DATA_PROVIDERS, "mock")
    api_key = _text(options, "rentcast_api_key")
    if provider == "rentcast" and not api_key:
        raise ConfigurationError("Option rentcast_api_key is required when data_provider is rentcast")

    peers = _text(options, "allowed_peers") or DEFAULT_SUPERVISOR_PEER  # blank never disables the check
    try:
        parse_allowed_peers(peers)
    except ValueError:
        raise ConfigurationError("Option allowed_peers is not a valid list of addresses") from None

    environment = {
        "DATA_PROVIDER": provider,
        "MIN_COMPARABLES_REQUIRED": str(_min_comparables(options)),
        "DATABASE_PATH": str(Path(data_dir) / DATABASE_FILE),
        "ALLOWED_PEERS": peers,
        "UI_SECRET_KEY": _text(options, "ui_secret_key") or ensure_ui_secret(data_dir),
    }
    if api_key:
        environment["RENTCAST_API_KEY"] = api_key
    return environment


def load_log_config(level: str, path: Path = LOG_CONFIG_PATH) -> dict:
    """The logging setup, with the app's own loggers at the chosen level (so the per-valuation
    funnel lines show in the app's Log tab at the default level)."""
    config = json.loads(Path(path).read_text())
    config["loggers"]["app"]["level"] = level.upper()
    return config


def main() -> int:
    try:
        options = load_options(OPTIONS_PATH)
        environment = build_environment(options, DATA_DIR)
        level = _choice(options, "log_level", LOG_LEVELS, "info")
    except ConfigurationError as error:
        print(f"ERROR: {error}. The app was not started.", file=sys.stderr, flush=True)
        return 1

    os.environ.update(environment)
    log_config = load_log_config(level, LOG_CONFIG_PATH)
    print(
        f"Rent Pricing: data source {environment['DATA_PROVIDER']}, "
        f"minimum comparables {environment['MIN_COMPARABLES_REQUIRED']}, "
        f"accepting connections only from {environment['ALLOWED_PEERS']}",
        flush=True,
    )
    if environment["DATA_PROVIDER"] == "rentcast":
        print("RentCast live data is on: each new area uses one request from your plan (cached 24 hours).", flush=True)

    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        log_config=log_config,
        log_level=level,
        proxy_headers=False,  # keep the real peer address, which the ALLOWED_PEERS check relies on
        server_header=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
