from pathlib import Path
import yaml

BASE_DIRECTORY = Path(__file__).resolve().parent.parent


def load_version():
    path = BASE_DIRECTORY / "version.yaml"
    with path.open(encoding="utf-8") as file:
        versions = yaml.safe_load(file)
    if not isinstance(versions, dict) or not isinstance(versions.get("version"), str) or not versions["version"].strip():
        raise ValueError("version.yaml must contain a nonempty version string")
    return versions["version"]
