from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InstanceConfig:
    instance_id: str
    host_controller_url: str
    max_concurrent: int


@dataclass(frozen=True)
class PoolConfig:
    instances: list[InstanceConfig]


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "YAML config requested but PyYAML is not installed."
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data or {}


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_config(path: str | Path) -> PoolConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")
    if config_path.suffix.lower() in {".yml", ".yaml"}:
        raw = _load_yaml(config_path)
    else:
        raw = _load_json(config_path)

    instances_raw = raw.get("instances", [])
    instances: list[InstanceConfig] = []
    for entry in instances_raw:
        instances.append(
            InstanceConfig(
                instance_id=str(entry["id"]),
                host_controller_url=str(entry["host_controller_url"]).rstrip("/"),
                max_concurrent=int(entry.get("max_concurrent", 1)),
            )
        )
    if not instances:
        raise ValueError("Config must define at least one instance.")
    return PoolConfig(instances=instances)
