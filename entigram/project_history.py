import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
HISTORY_FILE = PROJECT_ROOT / "projects.json"

def add_project_to_history(path: str):
    """Adds a project path to the local history file."""
    history = get_project_history()
    path = str(Path(path).absolute())
    
    if path in history:
        history.remove(path)
    
    history.insert(0, path)
    # Keep last 10 projects
    history = history[:10]
    
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)

def get_project_history():
    """Returns the list of recent project paths."""
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except:
        return []


def remove_project_from_history(path: str):
    """Removes a project path from the local history file."""
    normalized = str(Path(path).absolute())
    history = [item for item in get_project_history() if item != normalized]
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f)
