"""Generate a fresh, editable PBIP under the ignored dashboard/local directory."""
import argparse
import json
from pathlib import Path

from dashboard.model import write_model
from dashboard.report import write_report
from scripts.db import Config, carregar_env

ROOT = Path(__file__).resolve().parent


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build(destination: Path, server: str, database: str):
    destination.mkdir(parents=True, exist_ok=False)
    write_json(destination / "DataBuild.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": "DataBuild.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    write_model(destination / "DataBuild.SemanticModel", server, database, write_json)
    write_report(destination / "DataBuild.Report", write_json)
    return destination / "DataBuild.pbip"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="DataBuild", help="New folder under dashboard/local")
    args = parser.parse_args()
    if not args.name.isascii() or not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("Use an ASCII name with letters, digits, hyphens or underscores.")
    carregar_env()
    config = Config.do_ambiente()
    local = ROOT / "local"
    if local.resolve() != local:
        parser.error("dashboard/local must be a normal directory inside the repository.")
    target = local / args.name
    if target.exists():
        parser.error("Project already exists. Use a new --name to preserve your edits.")
    project = build(target, config.servidor, config.banco)
    print(project.relative_to(ROOT.parent))


if __name__ == "__main__":
    main()
