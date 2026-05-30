"""open_app — launch a whitelisted desktop application by name."""

import asyncio
import subprocess
from pathlib import Path

import yaml
from loguru import logger

from backend.tools import registry

# Resolve apps.yaml relative to this file, not the CWD — the CWD depends on
# how uvicorn is started and would break if the backend is launched from any
# directory other than the repo root.
_APPS_YAML = Path(__file__).resolve().parent / "apps.yaml"


def _load_whitelist() -> dict:
    """Load and parse apps.yaml. Returns empty dict on any file/parse error."""
    try:
        with open(_APPS_YAML, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("apps", {})
    except FileNotFoundError:
        logger.error("apps.yaml not found at {}", _APPS_YAML)
        return {}
    except yaml.YAMLError as e:
        logger.error("apps.yaml parse error: {}", e)
        return {}


def _popen_args(entry: dict) -> list[str]:
    """Build the subprocess arg list for a whitelist entry by its type.

    All three modes produce a list — no shell=True anywhere. This avoids
    quoting bugs and removes any injection surface even within trusted entries.
    The empty string after 'start' is the window-title placeholder that cmd
    requires when the target begins with a quote or special character.
    """
    kind = entry["type"]
    if kind == "exe":
        return [entry["path"]]
    if kind == "shell":
        # apps.yaml stores shell commands as a pre-built list — no quoting needed.
        return entry["command"]
    if kind == "store":
        # Microsoft Store / MSIX apps are launched via cmd's shell:AppsFolder verb.
        return ["cmd", "/c", "start", "", f"shell:AppsFolder\\{entry['app_id']}"]
    raise ValueError(f"unknown app type: {kind!r}")


@registry.register(
    name="open_app",
    description=(
        "Open or launch a desktop application by name. Use this when the user asks to "
        "open, launch, or start an app (e.g. 'open VS Code', 'launch Chrome', 'start Word'). "
        "Only apps in the whitelist can be launched. If the app is not available, tell the "
        "user to add it to apps.yaml. Do not guess a path or invent an app key."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Whitelist key of the app to launch (e.g. 'vscode', 'chrome', 'spotify'). "
                    "Keys are lowercase; match the user's intent to the closest key."
                ),
            },
        },
        "required": ["name"],
    },
)
async def open_app(name: str) -> str | dict:
    """Look up the app in apps.yaml and launch it via subprocess.Popen.

    Returns a confirmation string on success, or a soft-error dict if the app
    is unknown, the path is broken, or the YAML file can't be read. Soft errors
    let the LLM phrase the failure gracefully rather than crashing the turn.
    """
    whitelist = _load_whitelist()
    if not whitelist:
        return {"error": "App whitelist could not be loaded.", "type": "ConfigError"}

    # Case-insensitive lookup so the LLM can send 'VS Code', 'vscode', or 'VSCode'.
    key = name.strip().lower()
    entry = whitelist.get(key)
    if entry is None:
        available = ", ".join(sorted(whitelist.keys()))
        logger.warning("open_app: unknown key '{}' (available: {})", key, available)
        return {
            "error": (
                f"'{name}' is not in the app whitelist. "
                f"Available apps: {available}. "
                "Ask the user to add it to backend/tools/apps.yaml."
            ),
            "type": "UnknownAppError",
        }

    try:
        args = _popen_args(entry)
    except (KeyError, ValueError) as e:
        logger.error("open_app: bad whitelist entry for '{}': {}", key, e)
        return {"error": f"Whitelist entry for '{name}' is malformed: {e}", "type": "ConfigError"}

    # run_in_executor keeps the brief spawn syscall off the asyncio event loop.
    # We never .wait() — the launched app runs independently as its own OS process.
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: subprocess.Popen(args))
        logger.info("open_app: launched '{}' via {}", key, args[0])
        return f"Opening {name}, sir."
    except FileNotFoundError:
        logger.error("open_app: binary not found for '{}': {}", key, args)
        return {
            "error": f"Could not find the executable for '{name}'. The path in apps.yaml may be wrong.",
            "type": "LaunchError",
        }
    except OSError as e:
        logger.error("open_app: OS error launching '{}': {}", key, e)
        return {"error": f"Failed to launch '{name}': {e}", "type": "LaunchError"}
