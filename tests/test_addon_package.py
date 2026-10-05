"""The Home Assistant app package: its files, its settings, the bundled copy of the app, the
entrypoint's option handling, and the scripts. Nothing here builds an image or calls any service."""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import textwrap
from importlib.metadata import distribution
from pathlib import Path

import pytest
import yaml

from app.security import DEFAULT_SUPERVISOR_PEER

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "ha-addon"
SYNC = ROOT / "scripts" / "sync_addon.sh"
BASE_IMAGE = "ghcr.io/home-assistant/base-python:3.12-alpine3.24-2026.08.0"

CONFIG = yaml.safe_load((ADDON / "config.yaml").read_text())
DOCKERFILE = (ADDON / "Dockerfile").read_text()
# The instructions only: the file's explanatory comments mention things it deliberately avoids.
DOCKER_INSTRUCTIONS = "\n".join(line for line in DOCKERFILE.splitlines() if not line.strip().startswith("#"))
RUN_SH = (ADDON / "run.sh").read_text()

EXPECTED_FILES = [
    "config.yaml", "Dockerfile", "run.sh", "entrypoint.py", "log_config.json", "requirements.txt",
    ".dockerignore", "DOCS.md", "README.md", "CHANGELOG.md", "translations/en.yaml", "icon.png", "logo.png",
]
SECRET_KEY_VALUE = "SECRET-KEY-VALUE-1234567890"


def load_entrypoint():
    """Import ha-addon/entrypoint.py by path, without leaving a __pycache__ in the package."""
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("addon_entrypoint", ADDON / "entrypoint.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


entry = load_entrypoint()


# --- the files exist -------------------------------------------------------------------------

@pytest.mark.parametrize("name", EXPECTED_FILES)
def test_expected_files_exist(name):
    assert (ADDON / name).is_file() and (ADDON / name).stat().st_size > 0


def test_repository_files_exist():
    assert (ROOT / "repository.yaml").is_file() and SYNC.is_file()


def test_build_yaml_is_not_used():
    assert not (ADDON / "build.yaml").exists()  # deprecated; the Dockerfile names its base image


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_icon_and_logo_sizes():
    assert png_size(ADDON / "icon.png") == (128, 128)  # square, as Home Assistant requires
    assert png_size(ADDON / "logo.png") == (250, 100)


# --- repository.yaml --------------------------------------------------------------------------

def test_repository_yaml():
    repository = yaml.safe_load((ROOT / "repository.yaml").read_text())
    assert repository["name"] == "Rent Pricing Tool"
    assert repository["url"] == "https://github.com/cskullerud/rentpricingtool"
    assert repository["maintainer"]
    assert "@" not in repository["maintainer"]  # no personal email address in a public file
    assert CONFIG["url"] == repository["url"]


# --- config.yaml: identity ------------------------------------------------------------------------

def test_identity():
    assert CONFIG["name"] == "Rent Pricing"
    assert CONFIG["slug"] == "rentpricingtool" and re.fullmatch(r"[a-z0-9_]+", CONFIG["slug"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", CONFIG["version"])
    assert sorted(CONFIG["arch"]) == ["aarch64", "amd64"]
    assert CONFIG["init"] is False  # required by the base images, which run their own supervisor
    assert CONFIG["startup"] == "application" and CONFIG["boot"] == "auto"
    assert CONFIG["description"]


def test_changelog_matches_the_version():
    changelog = (ADDON / "CHANGELOG.md").read_text()
    assert f"## {CONFIG['version']}" in changelog
    assert changelog.index(f"## {CONFIG['version']}") < changelog.index("## 0.1.1")  # newest first


def test_the_version_is_0_3_1_and_matches_the_app():
    from app.config import VERSION

    assert CONFIG["version"] == "0.3.1"
    if not os.getenv("VERSION"):  # the app default; an environment override would legitimately differ
        assert VERSION == CONFIG["version"]


# --- config.yaml: ingress -------------------------------------------------------------------------

def test_ingress_settings():
    assert CONFIG["ingress"] is True
    assert CONFIG["ingress_port"] == 8099
    assert CONFIG["ingress_entry"] == "ui/"


def test_sidebar_entry():
    assert CONFIG["panel_admin"] is True
    assert CONFIG["panel_title"] == "Rent Pricing"
    assert CONFIG["panel_icon"] == "mdi:home-currency-usd"


def test_ingress_port_matches_what_the_entrypoint_listens_on_and_the_watchdog_checks():
    assert entry.PORT == CONFIG["ingress_port"]
    assert CONFIG["watchdog"] == "http://[HOST]:[PORT:8099]/"
    assert "0.0.0.0" == entry.HOST  # inside the container only; nothing is published


def test_backup_is_cold_so_the_database_is_not_copied_mid_write():
    assert CONFIG["backup"] == "cold"


# --- config.yaml: nothing exposed, least privilege ---------------------------------------------------

def test_no_ports_are_published():
    assert "ports" not in CONFIG and "ports_description" not in CONFIG
    assert not CONFIG.get("host_network", False)
    assert "EXPOSE" not in DOCKER_INSTRUCTIONS


@pytest.mark.parametrize(
    "key",
    ["privileged", "full_access", "hassio_api", "homeassistant_api", "auth_api", "docker_api", "host_pid",
     "host_ipc", "host_dbus", "devices", "usb", "uart", "udev", "gpio", "kernel_modules", "video", "audio",
     "map", "tmpfs", "ingress_stream", "webui"],
)
def test_no_extra_privileges_or_mounts(key):
    assert not CONFIG.get(key)


def test_no_host_folders_are_mapped():
    """Fresh database and no automatic migration: the app needs nothing from the host."""
    assert "map" not in CONFIG


# --- options and schema ----------------------------------------------------------------------------

EXPECTED_SCHEMA = {
    "data_provider": "list(mock|rentcast)",
    "geocoder": "list(mock|census)",
    "rentcast_api_key": "password?",
    "min_comparables_required": "int(1,50)",
    "ui_secret_key": "password?",
    "log_level": "list(debug|info|warning|error)",
    "allowed_peers": "str",
}


def test_schema():
    assert CONFIG["schema"] == EXPECTED_SCHEMA


def test_option_defaults():
    assert CONFIG["options"] == {
        "data_provider": "mock", "geocoder": "census", "min_comparables_required": 3, "log_level": "info",
        "allowed_peers": "172.30.32.2",
    }


def test_the_default_data_source_is_mock_so_installing_costs_nothing():
    assert CONFIG["options"]["data_provider"] == "mock"


def test_csv_is_not_offered_because_it_is_not_implemented():
    assert "csv" not in CONFIG["schema"]["data_provider"]


def test_secrets_are_password_options_without_defaults():
    for key in ("rentcast_api_key", "ui_secret_key"):
        assert CONFIG["schema"][key] == "password?"
        assert key not in CONFIG["options"]


def test_every_default_has_a_schema_entry_and_every_required_schema_entry_has_a_default():
    assert set(CONFIG["options"]) <= set(CONFIG["schema"])
    required = {key for key, kind in CONFIG["schema"].items() if not kind.endswith("?")}
    assert required <= set(CONFIG["options"])


def test_the_default_allowed_peer_is_the_supervisor():
    assert CONFIG["options"]["allowed_peers"] == DEFAULT_SUPERVISOR_PEER == entry.DEFAULT_SUPERVISOR_PEER


def test_provider_choices_match_what_the_entrypoint_accepts():
    listed = re.fullmatch(r"list\((.*)\)", CONFIG["schema"]["data_provider"]).group(1).split("|")
    assert tuple(listed) == entry.DATA_PROVIDERS
    levels = re.fullmatch(r"list\((.*)\)", CONFIG["schema"]["log_level"]).group(1).split("|")
    assert tuple(levels) == entry.LOG_LEVELS
    geocoders = re.fullmatch(r"list\((.*)\)", CONFIG["schema"]["geocoder"]).group(1).split("|")
    assert tuple(geocoders) == entry.GEOCODERS


def test_translations_cover_every_option():
    translations = yaml.safe_load((ADDON / "translations" / "en.yaml").read_text())["configuration"]
    assert set(translations) == set(CONFIG["schema"])
    for key, text in translations.items():
        assert text["name"] and len(text["description"]) > 20, key


# --- Dockerfile --------------------------------------------------------------------------------------

def test_base_image_is_pinned_to_an_exact_release():
    froms = re.findall(r"^FROM\s+(\S+)", DOCKER_INSTRUCTIONS, re.M)
    assert froms == [BASE_IMAGE]
    assert ":latest" not in DOCKER_INSTRUCTIONS and "BUILD_FROM" not in DOCKER_INSTRUCTIONS


def test_dockerfile_installs_pinned_requirements_into_a_venv():
    assert "python -m venv /opt/venv" in DOCKERFILE
    assert "pip install --no-cache-dir -r /tmp/requirements.txt" in DOCKERFILE
    assert "PATH=/opt/venv/bin:$PATH" in DOCKERFILE


def test_dockerfile_starts_run_sh():
    assert re.search(r'^CMD \["/run.sh"\]', DOCKERFILE, re.M)
    assert re.search(r"^RUN chmod a\+x /run.sh", DOCKERFILE, re.M)


def test_everything_the_dockerfile_copies_exists_in_the_app_folder():
    sources = []
    for line in re.findall(r"^COPY\s+(.+)$", DOCKERFILE, re.M):
        parts = line.split()
        sources += parts[:-1]
    assert {"requirements.txt", "app", "entrypoint.py", "log_config.json", "run.sh"} <= set(sources)
    for source in sources:
        assert (ADDON / source).exists(), source


def test_dockerfile_holds_no_secrets_or_options():
    assert not re.search(r"(?i)(api_key|secret|password|token)\s*=", DOCKER_INSTRUCTIONS)
    assert "ARG " not in DOCKER_INSTRUCTIONS


def test_dockerignore_keeps_secrets_and_databases_out_of_the_build():
    lines = (ADDON / ".dockerignore").read_text().split()
    for pattern in (".env", ".env.*", "*.db", "**/__pycache__", "**/*.pyc"):
        assert pattern in lines


# --- run.sh -------------------------------------------------------------------------------------------

def test_run_sh_is_a_bashio_script_with_unix_line_endings():
    assert RUN_SH.startswith("#!/usr/bin/with-contenv bashio\n")
    assert "\r" not in RUN_SH


def test_run_sh_is_executable():
    assert os.access(ADDON / "run.sh", os.X_OK) and os.access(SYNC, os.X_OK)
    for path in ("ha-addon/run.sh", "scripts/sync_addon.sh"):
        staged = subprocess.run(["git", "ls-files", "-s", "--", path], cwd=ROOT, capture_output=True, text=True)
        if staged.returncode == 0 and staged.stdout.strip():  # only once the file is tracked
            assert staged.stdout.startswith("100755"), path


def test_run_sh_hands_over_to_the_entrypoint_and_prints_no_secrets():
    assert "exec python /opt/app/entrypoint.py" in RUN_SH
    for forbidden in ("echo", "printenv", "env\n", "set -x", "bashio::config", "cat /data/options.json", "options.json"):
        assert forbidden not in RUN_SH


# --- requirements ----------------------------------------------------------------------------------------

def requirement_pins():
    pins = {}
    for line in (ADDON / "requirements.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, version = line.split("==")
            pins[re.sub(r"[-_.]+", "-", name).lower()] = version
    return pins


def test_requirements_are_exact_pins_without_extras():
    for line in (ADDON / "requirements.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[0-9][A-Za-z0-9.\-]*", line.strip()), line


def test_requirements_hold_the_runtime_packages_and_no_test_tools():
    pins = requirement_pins()
    assert {"fastapi", "pydantic", "uvicorn", "jinja2", "python-multipart", "starlette"} <= set(pins)
    for tool in ("pytest", "httpx", "pyyaml", "pytest-asyncio", "packaging", "pluggy", "iniconfig", "uvloop", "watchfiles"):
        assert tool not in pins


def test_pins_match_the_versions_the_tests_run_against():
    for name, version in requirement_pins().items():
        assert distribution(name).version == version, name


def test_requirements_include_the_whole_dependency_tree():
    from packaging.requirements import Requirement

    pins = requirement_pins()
    for name in list(pins):
        for requirement in distribution(name).requires or []:
            parsed = Requirement(requirement)
            if parsed.marker is not None and not parsed.marker.evaluate({"extra": ""}):
                continue
            assert re.sub(r"[-_.]+", "-", parsed.name).lower() in pins, f"{name} needs {parsed.name}"


def test_the_dev_requirements_include_what_these_tests_need():
    text = (ROOT / "requirements.txt").read_text()
    assert re.search(r"^pyyaml", text, re.M)


# --- the bundled copy of the app ---------------------------------------------------------------------------

def tree_hashes(base):
    found = {}
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base)
        if path.is_dir() or "__pycache__" in relative.parts or path.suffix in (".pyc", ".pyo"):
            continue
        found[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def test_the_bundle_matches_the_app_exactly():
    source, bundle = tree_hashes(ROOT / "app"), tree_hashes(ADDON / "app")
    problems = (
        [f"missing from the bundle: {n}" for n in sorted(set(source) - set(bundle))]
        + [f"not in app/: {n}" for n in sorted(set(bundle) - set(source))]
        + [f"differs: {n}" for n in sorted(n for n in set(source) & set(bundle) if source[n] != bundle[n])]
    )
    assert not problems, "ha-addon/app is out of date; run scripts/sync_addon.sh\n" + "\n".join(problems)


def test_the_bundle_has_everything_the_ui_needs():
    for relative in (
        "main.py", "security.py", "templates/base.html", "templates/partials/_result_card.html",
        "static/vendor/bootstrap/bootstrap.min.css", "static/vendor/bootstrap/bootstrap.bundle.min.js",
        "static/js/app.js", "ui/ingress.py", "persistence/cache.py",
    ):
        assert (ADDON / "app" / relative).is_file(), relative


def test_the_bundle_runs_on_its_own(tmp_path):
    """Import and serve the bundled copy from the add-on folder, as the image will."""
    code = """
        from fastapi.testclient import TestClient
        import app.main as main
        assert main.__file__.replace("\\\\", "/").find("ha-addon/app/main.py") > 0, main.__file__
        c = TestClient(main.app)
        print(c.get("/").status_code, c.get("/ui/").status_code, c.get("/ui/static/css/app.css").status_code)
        # As Home Assistant forwards it: the ingress prefix already removed, the header added.
        h = {"X-Ingress-Path": "/api/hassio_ingress/AbC123xyz"}
        print(*[c.get(p, headers=h).status_code for p in (
            "/ui/", "/ui/static/css/app.css", "/ui/static/js/app.js",
            "/ui/static/vendor/bootstrap/bootstrap.min.css", "/ui/static/vendor/bootstrap/bootstrap.bundle.min.js")])
    """
    env = {**os.environ, "DATABASE_PATH": str(tmp_path / "t.db"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("ALLOWED_PEERS", None)
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ADDON, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-8:] == ["200"] * 8, result.stdout  # direct, then through ingress
    assert not any((ADDON / "app").rglob("__pycache__")), "running the bundle left cache files behind"


# --- scripts/sync_addon.sh -----------------------------------------------------------------------------------------

def sync(*args):
    return subprocess.run(["sh", str(SYNC), *args], capture_output=True, text=True, timeout=60)


def make_root(tmp_path, with_bundle=True):
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "app", tmp_path / "app", ignore=ignore)
    if with_bundle:
        shutil.copytree(ADDON / "app", tmp_path / "ha-addon" / "app", ignore=ignore)
    return tmp_path


def test_check_passes_for_the_real_repository():
    result = sync("--check")
    assert result.returncode == 0 and "matches" in result.stdout, result.stderr


def test_check_passes_for_a_synced_copy(tmp_path):
    assert sync("--check", str(make_root(tmp_path))).returncode == 0


def test_check_fails_when_the_app_changes(tmp_path):
    root = make_root(tmp_path)
    with open(root / "app" / "config.py", "a") as handle:
        handle.write("# a change that was not synced\n")
    result = sync("--check", str(root))
    assert result.returncode == 1
    assert "config.py" in result.stderr and "scripts/sync_addon.sh" in result.stderr


def test_check_fails_when_the_bundle_is_edited(tmp_path):
    root = make_root(tmp_path)
    (root / "ha-addon" / "app" / "main.py").write_text("# edited in the bundle\n")
    assert sync("--check", str(root)).returncode == 1


def test_check_fails_for_a_new_file_in_app(tmp_path):
    root = make_root(tmp_path)
    (root / "app" / "new_module.py").write_text("x = 1\n")
    result = sync("--check", str(root))
    assert result.returncode == 1 and "new_module.py" in result.stderr


def test_check_fails_for_a_stray_file_in_the_bundle(tmp_path):
    root = make_root(tmp_path)
    (root / "ha-addon" / "app" / "stray.txt").write_text("x")
    result = sync("--check", str(root))
    assert result.returncode == 1 and "stray.txt" in result.stderr


def test_check_fails_when_there_is_no_bundle(tmp_path):
    result = sync("--check", str(make_root(tmp_path, with_bundle=False)))
    assert result.returncode == 1 and "does not exist" in result.stderr


def test_check_ignores_python_cache_files(tmp_path):
    root = make_root(tmp_path)
    for folder in (root / "app", root / "ha-addon" / "app"):
        (folder / "__pycache__").mkdir()
        (folder / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"junk")
    assert sync("--check", str(root)).returncode == 0


def test_sync_copies_the_app_and_leaves_no_cache_files(tmp_path):
    root = make_root(tmp_path, with_bundle=False)
    (root / "app" / "__pycache__").mkdir()
    (root / "app" / "__pycache__" / "x.pyc").write_bytes(b"junk")
    result = sync(str(root))
    assert result.returncode == 0 and "copied" in result.stdout
    assert tree_hashes(root / "app") == tree_hashes(root / "ha-addon" / "app")
    assert not list((root / "ha-addon").rglob("__pycache__"))


def test_sync_replaces_a_stale_bundle(tmp_path):
    root = make_root(tmp_path)
    (root / "ha-addon" / "app" / "stray.txt").write_text("x")
    (root / "app" / "config.py").write_text("# new\n")
    assert sync(str(root)).returncode == 0
    assert not (root / "ha-addon" / "app" / "stray.txt").exists()
    assert sync("--check", str(root)).returncode == 0


def test_sync_refuses_a_folder_without_an_app(tmp_path):
    assert sync(str(tmp_path)).returncode == 2


# --- nothing secret in the package ---------------------------------------------------------------------------------

def package_text_files():
    for path in ADDON.rglob("*"):
        relative = path.relative_to(ADDON)
        if path.is_dir() or "vendor" in relative.parts or path.suffix in (".png", ".pyc") or "__pycache__" in relative.parts:
            continue
        yield path


def test_no_secrets_or_databases_in_the_package():
    for path in package_text_files():
        text = path.read_text(errors="ignore")
        assert not re.search(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", text), path
        assert not re.search(r"\b[0-9a-f]{32}\b", text), path
    assert not (ADDON / ".env").exists()
    assert not list(ADDON.rglob("*.db")) and not list(ADDON.rglob("*.sqlite*"))


def test_the_local_env_file_is_not_tracked_and_is_ignored():
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT)
    assert ignored.returncode == 0


# --- entrypoint: options to environment ---------------------------------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    return folder


def test_defaults_produce_a_safe_mock_setup(data_dir):
    env = entry.build_environment({}, data_dir)
    assert env["DATA_PROVIDER"] == "mock" and env["GEOCODER"] == "census"
    assert env["MIN_COMPARABLES_REQUIRED"] == "3"
    assert env["ALLOWED_PEERS"] == "172.30.32.2"
    assert env["DATABASE_PATH"] == str(data_dir / "rentpricingtool.db")
    assert "RENTCAST_API_KEY" not in env


def test_the_default_options_from_config_yaml_are_accepted(data_dir):
    env = entry.build_environment(CONFIG["options"], data_dir)
    assert env["DATA_PROVIDER"] == "mock" and env["MIN_COMPARABLES_REQUIRED"] == "3"


def test_the_database_is_a_fresh_file_in_data(data_dir):
    env = entry.build_environment({}, data_dir)
    assert env["DATABASE_PATH"] == str(data_dir / "rentpricingtool.db")
    assert not (data_dir / "rentpricingtool.db").exists()  # created by the app on first start, never copied in


def test_rentcast_with_a_key(data_dir):
    env = entry.build_environment({"data_provider": "rentcast", "rentcast_api_key": f"  {SECRET_KEY_VALUE} "}, data_dir)
    assert env["DATA_PROVIDER"] == "rentcast" and env["RENTCAST_API_KEY"] == SECRET_KEY_VALUE


@pytest.mark.parametrize("key", [None, "", "   "])
def test_rentcast_without_a_key_is_refused(data_dir, key):
    with pytest.raises(entry.ConfigurationError, match="rentcast_api_key"):
        entry.build_environment({"data_provider": "rentcast", "rentcast_api_key": key}, data_dir)


def test_a_key_given_with_mock_is_still_passed_through_for_later_switching(data_dir):
    assert entry.build_environment({"rentcast_api_key": SECRET_KEY_VALUE}, data_dir)["RENTCAST_API_KEY"] == SECRET_KEY_VALUE


@pytest.mark.parametrize("provider", ["csv", "zillow", "RENTCAST ", 5])
def test_unknown_providers_are_refused_or_normalised(data_dir, provider):
    options = {"data_provider": provider, "rentcast_api_key": SECRET_KEY_VALUE}
    if provider == "RENTCAST ":
        assert entry.build_environment(options, data_dir)["DATA_PROVIDER"] == "rentcast"
    else:
        with pytest.raises(entry.ConfigurationError, match="data_provider"):
            entry.build_environment(options, data_dir)


@pytest.mark.parametrize("option", ["data_provider", "geocoder", "rentcast_api_key", "ui_secret_key", "allowed_peers", "log_level"])
@pytest.mark.parametrize("value", [5, True, ["mock"], {"a": 1}])
def test_options_that_must_be_text_are_refused_not_defaulted(data_dir, option, value):
    with pytest.raises(entry.ConfigurationError, match=option):
        entry.build_environment({option: value}, data_dir) if option != "log_level" else entry._choice({option: value}, option, entry.LOG_LEVELS, "info")


@pytest.mark.parametrize("value", [0, 51, -1, "3", 3.5, True, None])
def test_invalid_minimums_are_refused(data_dir, value):
    with pytest.raises(entry.ConfigurationError, match="min_comparables_required"):
        entry.build_environment({"min_comparables_required": value}, data_dir)


@pytest.mark.parametrize("value", [1, 3, 50])
def test_valid_minimums(data_dir, value):
    assert entry.build_environment({"min_comparables_required": value}, data_dir)["MIN_COMPARABLES_REQUIRED"] == str(value)


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_a_blank_peer_list_never_disables_the_check(data_dir, raw):
    assert entry.build_environment({"allowed_peers": raw}, data_dir)["ALLOWED_PEERS"] == "172.30.32.2"


@pytest.mark.parametrize("raw", ["0.0.0.0/0", "10.0.0.0/8", "nonsense", "172.30.32.2/24"])
def test_unsafe_or_invalid_peer_lists_are_refused(data_dir, raw):
    with pytest.raises(entry.ConfigurationError, match="allowed_peers"):
        entry.build_environment({"allowed_peers": raw}, data_dir)


def test_a_custom_peer_list_is_accepted(data_dir):
    assert entry.build_environment({"allowed_peers": "172.30.32.2, 172.30.33.5"}, data_dir)["ALLOWED_PEERS"] == "172.30.32.2, 172.30.33.5"


def test_error_messages_never_contain_secret_values(data_dir):
    bad = {"data_provider": "zillow", "rentcast_api_key": SECRET_KEY_VALUE, "ui_secret_key": SECRET_KEY_VALUE}
    with pytest.raises(entry.ConfigurationError) as info:
        entry.build_environment(bad, data_dir)
    assert SECRET_KEY_VALUE not in str(info.value)


def test_building_the_environment_does_not_touch_the_process_environment(data_dir, monkeypatch):
    before = dict(os.environ)
    entry.build_environment({"data_provider": "rentcast", "rentcast_api_key": SECRET_KEY_VALUE}, data_dir)
    assert dict(os.environ) == before


# --- entrypoint: the form-security key -------------------------------------------------------------------------------------

def test_a_configured_ui_secret_is_used_and_nothing_is_written(data_dir):
    env = entry.build_environment({"ui_secret_key": " configured-secret "}, data_dir)
    assert env["UI_SECRET_KEY"] == "configured-secret"
    assert not (data_dir / "ui_secret").exists()


def test_a_ui_secret_is_generated_once_and_kept(data_dir):
    first = entry.build_environment({}, data_dir)["UI_SECRET_KEY"]
    assert len(first) >= 48
    assert entry.build_environment({}, data_dir)["UI_SECRET_KEY"] == first  # stable across restarts
    assert (data_dir / "ui_secret").read_text() == first


def test_the_generated_secret_file_is_private(data_dir):
    entry.ensure_ui_secret(data_dir)
    assert oct((data_dir / "ui_secret").stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("content", ["", "short"])
def test_a_damaged_secret_file_is_replaced(data_dir, content):
    (data_dir / "ui_secret").write_text(content)
    value = entry.ensure_ui_secret(data_dir)
    assert len(value) >= 48 and (data_dir / "ui_secret").read_text() == value


def test_different_installs_get_different_secrets(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    assert entry.ensure_ui_secret(a) != entry.ensure_ui_secret(b)


# --- entrypoint: loading options and logging -----------------------------------------------------------------------------------

def test_load_options(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"data_provider": "mock"}))
    assert entry.load_options(path) == {"data_provider": "mock"}


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '"text"', ""])
def test_unreadable_options_are_refused(tmp_path, content):
    path = tmp_path / "options.json"
    path.write_text(content)
    with pytest.raises(entry.ConfigurationError):
        entry.load_options(path)


def test_a_missing_options_file_is_refused(tmp_path):
    with pytest.raises(entry.ConfigurationError, match="does not exist"):
        entry.load_options(tmp_path / "nope.json")


def test_log_config_sets_the_app_level():
    assert entry.load_log_config("debug")["loggers"]["app"]["level"] == "DEBUG"
    assert entry.load_log_config("warning")["loggers"]["app"]["level"] == "WARNING"


def test_the_log_config_shows_the_apps_own_info_lines_and_hides_other_libraries():
    code = f"""
        import json, logging, logging.config
        logging.config.dictConfig(json.load(open({str(ADDON / 'log_config.json')!r})))
        logging.getLogger("app.services.valuation_engine").info("valuation_funnel status=ok")
        logging.getLogger("uvicorn.error").info("Uvicorn running")
        logging.getLogger("some.library").info("library chatter")
        logging.getLogger("some.library").warning("library warning")
    """
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "valuation_funnel status=ok" in result.stdout and "Uvicorn running" in result.stdout
    assert "library warning" in result.stdout and "library chatter" not in result.stdout


# --- entrypoint: main() --------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def launch(tmp_path, monkeypatch):
    """Run entry.main() against temporary options, with uvicorn replaced by a recorder."""
    calls = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(os, "environ", os.environ.copy())  # main() sets variables; keep them out of the suite
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(entry, "DATA_DIR", data)
    monkeypatch.setattr(entry, "OPTIONS_PATH", data / "options.json")

    def run(options):
        if options is not None:
            (data / "options.json").write_text(json.dumps(options))
        return entry.main(), calls

    return run


def test_main_starts_uvicorn_on_the_ingress_port_without_trusting_proxy_headers(launch):
    code, calls = launch({"data_provider": "mock", "min_comparables_required": 3, "log_level": "info"})
    assert code == 0 and len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("app.main:app",)
    assert kwargs["host"] == "0.0.0.0" and kwargs["port"] == 8099
    assert kwargs["proxy_headers"] is False  # the real peer address must reach the allow-list
    assert kwargs["log_level"] == "info" and kwargs["log_config"]["loggers"]["app"]["level"] == "INFO"


def test_main_puts_the_settings_in_the_environment(launch):
    launch({"data_provider": "rentcast", "rentcast_api_key": SECRET_KEY_VALUE, "min_comparables_required": 5})
    assert os.environ["DATA_PROVIDER"] == "rentcast" and os.environ["RENTCAST_API_KEY"] == SECRET_KEY_VALUE
    assert os.environ["GEOCODER"] == "census"
    assert os.environ["MIN_COMPARABLES_REQUIRED"] == "5" and os.environ["ALLOWED_PEERS"] == "172.30.32.2"
    assert os.environ["DATABASE_PATH"].endswith("/data/rentpricingtool.db")
    assert len(os.environ["UI_SECRET_KEY"]) >= 48


def test_main_never_prints_secrets(launch, capsys):
    launch({"data_provider": "rentcast", "rentcast_api_key": SECRET_KEY_VALUE, "ui_secret_key": SECRET_KEY_VALUE + "-ui"})
    out = capsys.readouterr()
    assert SECRET_KEY_VALUE not in out.out + out.err
    assert "data source rentcast, address lookup census" in out.out and "uses one request" in out.out
    assert "WARNING" not in out.out  # census is the default, so no mock-geocoder warning


def test_main_states_the_mock_setup_without_a_cost_warning(launch, capsys):
    launch({"data_provider": "mock"})
    out = capsys.readouterr().out
    assert "data source mock" in out and "172.30.32.2" in out and "uses one request" not in out


def test_main_refuses_to_start_on_bad_options_and_names_the_option(launch, capsys):
    code, calls = launch({"data_provider": "rentcast", "rentcast_api_key": ""})
    err = capsys.readouterr().err
    assert code == 1 and calls == []
    assert "ERROR" in err and "rentcast_api_key" in err and "was not started" in err


def test_main_refuses_a_bad_log_level(launch):
    code, calls = launch({"log_level": "chatty"})
    assert code == 1 and calls == []


def test_main_refuses_a_missing_options_file(launch, capsys):
    code, calls = launch(None)
    assert code == 1 and calls == [] and "does not exist" in capsys.readouterr().err


def test_main_makes_no_provider_calls(launch, monkeypatch):
    """Starting must not reach any data service; uvicorn is stubbed, so only setup code runs."""
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    code, _ = launch({"data_provider": "rentcast", "rentcast_api_key": SECRET_KEY_VALUE})
    assert code == 0


# --- documentation -------------------------------------------------------------------------------------------------------------------------------

def test_docs_cover_installation_first_run_and_the_optional_migration():
    docs = (ADDON / "DOCS.md").read_text()
    for needle in (
        "https://github.com/cskullerud/rentpricingtool", "Show in sidebar", "mock", "MOCK",
        "No ports", "no ports", "403 Forbidden", "encrypt your backups", "fresh", "Nothing is migrated",
        "Optional, advanced",
    ):
        assert needle in docs or needle.lower() in docs.lower(), needle


def test_docs_state_the_default_and_the_cost_of_live_data():
    docs = (ADDON / "DOCS.md").read_text()
    assert "default is `mock`" in docs and "one request" in docs and "cached" in docs


# --- address lookup (the geocoder option) ----------------------------------------------------------------

@pytest.mark.parametrize("value", ["census", "mock", " Census ", "MOCK"])
def test_valid_geocoder_choices(data_dir, value):
    assert entry.build_environment({"geocoder": value}, data_dir)["GEOCODER"] == value.strip().lower()


@pytest.mark.parametrize("value", ["google", "nominatim", "census,mock", 5, True])
def test_unknown_geocoders_are_refused(data_dir, value):
    with pytest.raises(entry.ConfigurationError, match="geocoder"):
        entry.build_environment({"geocoder": value}, data_dir)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_blank_geocoder_means_the_app_default_census(data_dir, value):
    assert entry.build_environment({"geocoder": value}, data_dir)["GEOCODER"] == "census"


def test_the_app_default_is_census_but_development_stays_mock():
    """The add-on turns the real geocoder on; the app itself defaults to the mock for development and tests."""
    from app.services.geocoding import GeocoderType, get_geocoder_type

    assert CONFIG["options"]["geocoder"] == "census"
    assert os.getenv("GEOCODER") is None and get_geocoder_type() is GeocoderType.MOCK


def test_the_environment_the_entrypoint_builds_selects_the_census_geocoder(data_dir, monkeypatch):
    from app.services.geocoding import CachingGeocoder, build_geocoder

    for name, value in entry.build_environment({}, data_dir).items():
        monkeypatch.setenv(name, value)
    assert isinstance(build_geocoder(), CachingGeocoder)


def test_main_announces_the_address_lookup(launch, capsys):
    launch({"data_provider": "mock", "geocoder": "census"})
    assert "address lookup census" in capsys.readouterr().out


def test_main_warns_when_rentcast_is_paired_with_the_mock_geocoder(launch, capsys):
    code, calls = launch({"data_provider": "rentcast", "rentcast_api_key": SECRET_KEY_VALUE, "geocoder": "mock"})
    out = capsys.readouterr().out
    assert code == 0 and len(calls) == 1  # a warning, not a refusal
    assert "WARNING: RentCast provider active with mock geocoder" in out
    assert "Address lookup to census" in out
    assert SECRET_KEY_VALUE not in out


def test_main_has_no_warning_for_the_mock_pairing_without_rentcast(launch, capsys):
    launch({"data_provider": "mock", "geocoder": "mock"})
    assert "WARNING" not in capsys.readouterr().out


def test_docs_describe_the_address_lookup():
    docs = (ADDON / "DOCS.md").read_text()
    for needle in ("Address lookup", "Census", "never uses a RentCast request", "Geocoder: census",
                   "geocode result=match cache=hit", "Apartment, unit, suite", "street-range interpolation"):
        assert needle in docs, needle


# --- search controls (0.3.0) ---------------------------------------------------------------------------------

def test_the_bundle_ships_the_search_control_files():
    for relative in ("services/search_options.py", "templates/partials/_search_summary.html", "ui/viewmodels.py"):
        assert (ADDON / "app" / relative).is_file(), relative


def test_the_search_choices_are_request_fields_not_app_options():
    """Radius, lookback and building type are chosen per valuation on the form, so the app has no
    option for them (and no RentCast radius setting)."""
    for name in ("radius", "lookback", "property", "building", "limit"):
        assert not any(name in key for key in CONFIG["schema"]), name
    assert "RENTCAST_RADIUS_MILES" not in (ADDON / "entrypoint.py").read_text()
    assert "RENTCAST_RADIUS_MILES" not in (ADDON / "DOCS.md").read_text()


def test_the_entrypoint_environment_has_no_radius_setting(data_dir):
    env = entry.build_environment({"data_provider": "mock"}, data_dir)
    assert not any("RADIUS" in key or "LOOKBACK" in key for key in env)


def test_the_changelog_describes_the_search_changes():
    newest = " ".join((ADDON / "CHANGELOG.md").read_text().split("## 0.2.0")[0].split())  # markdown wraps lines
    for needle in ("Search radius", "Lookback window", "Building type", "miles", "500", "Square feet are optional",
                   "Search criteria", "reaches the limit", "RENTCAST_RADIUS_MILES"):
        assert needle in newest, needle


def test_docs_describe_the_search_controls():
    docs = " ".join((ADDON / "DOCS.md").read_text().split())  # markdown wraps lines
    for needle in ("### Search controls", "Search radius (miles)", "Lookback window (days)", "Building type",
                   "0.5, 1, 2, 3, 5 miles", "never costs another RentCast request", "reached limit (500)",
                   "Square feet are optional", "Search criteria", "property-type filter", "Matching rules"):
        assert needle in docs, needle


def test_the_docs_say_a_new_radius_or_building_type_costs_a_request():
    assert "a new location, radius or building type" in " ".join((ADDON / "DOCS.md").read_text().split())


def test_the_bundle_still_serves_the_form_with_the_new_controls(tmp_path):
    code = """
        from fastapi.testclient import TestClient
        import app.main as main
        html = TestClient(main.app).get("/ui/").text
        print(all(x in html for x in ('id="search_radius_miles"', 'id="lookback_days"', 'id="property_type"', 'id="search-summary"')))
        print("Square feet (optional)" in html)
    """
    env = {**os.environ, "DATABASE_PATH": str(tmp_path / "t.db"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("ALLOWED_PEERS", None)
    result = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ADDON, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-2:] == ["True", "True"]
