import argparse
import re
from pathlib import Path
from typing import Dict, List


_PLUGIN_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _validated_plugin_name(plugin_name: str) -> str:
    if not isinstance(plugin_name, str) or not _PLUGIN_NAME_RE.fullmatch(plugin_name):
        raise ValueError(
            "plugin name must start with a lowercase letter and contain only "
            "lowercase letters, numbers, and underscores"
        )
    return plugin_name


def generate_plugin_boilerplate(plugin_name: str, target_dir: str):
    """Scaffold an explicitly enabled Entigram CLI plugin."""
    plugin_name = _validated_plugin_name(plugin_name)
    target_path = Path(target_dir).expanduser().resolve()
    plugins_dir = target_path / ".etg" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_file = plugins_dir / f"{plugin_name}.py"

    content = f'''def {plugin_name}_handler(args):
    """Handle the {plugin_name} command."""
    print("Executing custom plugin '{plugin_name}'...")


def register_command(subparsers):
    """Register the {plugin_name} command with the Entigram CLI."""
    parser = subparsers.add_parser("{plugin_name}", help="Custom plugin for {plugin_name}")
    parser.set_defaults(func={plugin_name}_handler)
'''
    plugin_file.write_text(content)
    print(f"✅ Successfully bootstrapped plugin '{plugin_name}' at {plugin_file}")
    print("Run it only with the global --enable-workspace-plugins acknowledgement.")


def get_plugins(target_dir: str) -> List[Dict[str, str]]:
    """List plugin source files without importing or executing them."""
    plugins_dir = Path(target_dir).expanduser().resolve() / ".etg" / "plugins"
    plugins = []
    if not plugins_dir.exists():
        return []

    for plugin_file in plugins_dir.glob("*.py"):
        if plugin_file.name == "__init__.py":
            continue
        plugin_info = {
            "name": plugin_file.stem,
            "path": str(plugin_file),
            "valid": False,
            "description": "No register_command found.",
        }
        try:
            content = plugin_file.read_text()
            if "def register_command" in content:
                plugin_info["valid"] = True
                plugin_info["description"] = f"Entigram CLI Extension: {plugin_file.stem}"
        except OSError:
            pass
        plugins.append(plugin_info)
    return plugins


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Entigram Plugin Scaffolder")
    parser.add_argument("name", help="Name of the custom plugin")
    parser.add_argument("--dir", default=".", help="Target directory (workspace root)")
    args = parser.parse_args()
    generate_plugin_boilerplate(args.name, args.dir)
