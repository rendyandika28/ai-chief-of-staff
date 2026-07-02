"""Plugin system — decorator-based tool registration and auto-discovery."""

import importlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_registry = None


def init(registry):
    global _registry
    _registry = registry


def tool(name: str, description: str):
    """Decorator: register a Tool class into the ToolRegistry."""
    def decorator(cls):
        cls.name = name
        cls.description = description
        if _registry is not None:
            _registry.register(name, cls())
        return cls
    return decorator


def discover(plugins_dir: str = "app/plugins/", config_path: str = "config/plugins.json"):
    config = {}
    cp = Path(config_path)
    if cp.exists():
        try:
            config = json.loads(cp.read_text())
        except json.JSONDecodeError:
            pass

    enabled = set(config.get("enabled", []))
    disabled = set(config.get("disabled", []))

    pp = Path(plugins_dir)
    if not pp.exists():
        return

    for entry in sorted(pp.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        if entry.name in disabled:
            continue
        if enabled and entry.name not in enabled:
            continue
        try:
            importlib.import_module(f"app.plugins.{entry.name}")
            logger.info(f"Plugin loaded: {entry.name}")
        except Exception as e:
            logger.error(f"Plugin load failed: {entry.name} — {e}")
