from pathlib import Path
from typing import Iterable

import yaml

from entigram.governance.warden import Warden


def declare_schema_paths(
    workspace: Path,
    paths: Iterable[Path],
    *,
    lock: bool = True,
) -> None:
    manifest_path = workspace / ".etg" / "entigram.yaml"
    manifest = yaml.safe_load(manifest_path.read_text()) or {}
    manifest["schema_paths"] = [
        path.resolve().relative_to(workspace.resolve()).as_posix()
        for path in paths
    ]
    manifest_path.write_text(yaml.dump(manifest, default_flow_style=False))
    if lock:
        Warden(str(workspace)).lock_fingerprint()
