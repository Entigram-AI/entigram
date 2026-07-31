import json
import os
import tempfile
from pathlib import Path


LEGACY_HISTORY_FILE = Path(__file__).resolve().parent.parent / "projects.json"
CONFIG_DIR = Path(os.environ.get("ENTIGRAM_CONFIG_DIR", Path.home() / ".etg")).expanduser()
HISTORY_FILE = CONFIG_DIR / "projects.json"


def _write_history(history):
    temporary_name = None
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(HISTORY_FILE.parent, 0o700)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=HISTORY_FILE.parent,
            prefix="projects.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(history, temporary)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, HISTORY_FILE)
        return True
    except OSError:
        # Project history is a convenience feature and must never prevent a
        # workspace operation in a read-only or sandboxed environment.
        return False
    finally:
        if temporary_name and Path(temporary_name).exists():
            Path(temporary_name).unlink()


def add_project_to_history(path: str):
    """Add a project path to user-local history."""
    history = get_project_history()
    normalized = str(Path(path).expanduser().resolve())
    if normalized in history:
        history.remove(normalized)
    history.insert(0, normalized)
    _write_history(history[:10])


def get_project_history():
    """Return validated recent project paths without writing into site-packages."""
    source = HISTORY_FILE if HISTORY_FILE.exists() else LEGACY_HISTORY_FILE
    if not source.exists():
        return []
    try:
        history = json.loads(source.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(history, list):
        return []
    return [item for item in history if isinstance(item, str)][:10]


def remove_project_from_history(path: str):
    """Remove a project path from user-local history."""
    normalized = str(Path(path).expanduser().resolve())
    history = [item for item in get_project_history() if item != normalized]
    _write_history(history)
