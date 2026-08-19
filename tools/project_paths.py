"""Shared project paths. Default Tosca import folder: imports/tsu."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_FILE = ROOT / "tosca.config.json"


def load_config() -> dict:
    defaults = {
        "tsuImportDir": "imports/tsu",
        "excelDataDir": "data",
        "generatedTestsDir": "tests/generated",
        "allureResultsDir": "allure-results",
        "reportsDir": "reports",
    }
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update({k: v for k, v in data.items() if v})
        except Exception:
            pass
    return defaults


def _as_dir(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def tsu_import_dir() -> Path:
    env = os.environ.get("TOSCA_TSU_DIR")
    if env:
        path = Path(env)
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path
    return _as_dir(load_config()["tsuImportDir"])


def excel_data_dir() -> Path:
    return _as_dir(load_config()["excelDataDir"])


def generated_tests_dir() -> Path:
    return _as_dir(load_config()["generatedTestsDir"])


def collect_tsu_files(target: Path | None = None) -> list[Path]:
    """Find .tsu files. Directories are searched recursively."""
    if target is None:
        target = tsu_import_dir()
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.rglob("*.tsu") if p.is_file())
    return []


def resolve_tsu_target(raw: str | None) -> Path:
    """
    Resolve a CLI path against the common import folder.
    - no arg            → imports/tsu
    - filename only     → imports/tsu/<name>
    - existing path     → that path
    """
    inbox = tsu_import_dir()
    if not raw:
        return inbox
    given = Path(raw)
    if given.exists():
        return given
    nested = inbox / raw
    if nested.exists():
        return nested
    matches = list(inbox.rglob(given.name))
    if matches:
        return matches[0]
    return given
