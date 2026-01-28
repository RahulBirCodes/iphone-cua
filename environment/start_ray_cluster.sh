#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <cluster_config.yaml>" >&2
  exit 1
fi

config_path="$1"

mapfile -t lines < <(
  python - "$config_path" <<'PY'
import json
import sys

try:
    import yaml
except Exception:
    print("PyYAML is required to parse the cluster config.", file=sys.stderr)
    sys.exit(2)

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    cfg = yaml.safe_load(handle) or {}

hosts = cfg.get("hosts", [])
if not hosts:
    print("No hosts found in cluster config.", file=sys.stderr)
    sys.exit(3)

head_port = int(cfg.get("head_port", 6379))
ray_temp_dir = cfg.get("ray_temp_dir", "")

head = hosts[0]
print(head.get("host", ""))
print(head.get("ssh_user", ""))
print(head_port)
print(ray_temp_dir)

for host in hosts:
    host_ip = host.get("host", "")
    if not host_ip:
        continue
    user = host.get("ssh_user", "")
    resources = host.get("resources", {})
    print(f"{host_ip}\t{user}\t{json.dumps(resources, separators=(',', ':'))}")
PY
)

head_host="${lines[0]}"
head_user="${lines[1]}"
head_port="${lines[2]}"
ray_temp_dir="${lines[3]}"

if [[ -z "$head_host" ]]; then
  echo "Head host is missing in config." >&2
  exit 4
fi

head_target="$head_host"
if [[ -n "$head_user" ]]; then
  head_target="${head_user}@${head_host}"
fi

head_resources='{}'
if [[ ${#lines[@]} -ge 5 ]]; then
  IFS=$'\t' read -r _ _ head_resources <<< "${lines[4]}"
fi

head_cmd=(ray start --head --port="$head_port" --resources="$head_resources")
if [[ -n "$ray_temp_dir" ]]; then
  head_cmd+=(--temp-dir="$ray_temp_dir")
fi

echo "Starting Ray head on $head_target ..."
ssh "$head_target" "${head_cmd[*]}"

for ((i=5; i<${#lines[@]}; i++)); do
  IFS=$'\t' read -r host user resources <<< "${lines[$i]}"
  if [[ -z "$host" ]]; then
    continue
  fi
  target="$host"
  if [[ -n "$user" ]]; then
    target="${user}@${host}"
  fi
  worker_cmd=(ray start --address="${head_host}:${head_port}" --resources="$resources")
  if [[ -n "$ray_temp_dir" ]]; then
    worker_cmd+=(--temp-dir="$ray_temp_dir")
  fi
  echo "Starting Ray worker on $target ..."
  ssh "$target" "${worker_cmd[*]}"
done
