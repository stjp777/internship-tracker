"""Config loading shared by every entrypoint (CLI, cloud poll, gmail push)."""
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent.parent


def load_config(base=BASE):
    """config.yaml, overlaid with the gitignored config.local.yaml.

    Local overrides are merged one level deep, so config.local.yaml can set
    just `notifications.discord_webhook` without restating the whole block.
    Relative paths are resolved against the project directory.
    """
    base = Path(base)
    with open(base / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    local = base / "config.local.yaml"
    if local.exists():
        with open(local, encoding="utf-8") as f:
            overlay = yaml.safe_load(f) or {}
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    cfg["database"] = str(base / cfg.get("database", "internships.db"))
    g = cfg.get("gmail", {})
    for key in ("credentials", "token"):
        if key in g:
            g[key] = str(base / g[key])
    return cfg
