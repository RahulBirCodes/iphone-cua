# Distributed iPhone RL Environment — Architecture (Concise)

```
Ray Driver / Trainer
  |
  | ray.remote actor per rollout (iphone_slot=1)
  v
  Ray Cluster (N macOS EC2 hosts, resources: iphone_slot=K per host)
   |
   +-- Host A (iphone_slot=K)
   |     |
   |     +-- VM 1 (tart) <---- iPhoneEnv Actor 1
   |     |      |
   |     |      +-- VMController (HTTP :8000) -> Simulator
   |     |
   |     +-- VM 2 (tart) <---- iPhoneEnv Actor 2
   |            |
   |            +-- VMController (HTTP :8000) -> Simulator
   |
   +-- Host B (iphone_slot=K)
         |
         +-- VM 1 (tart) <---- iPhoneEnv Actor 3
                |
                +-- VMController (HTTP :8000) -> Simulator
```

## Key points
- One rollout == one `iPhoneEnv` Ray actor == one VM. No sharing until rollout completes.
- Ray custom resources cap per-host concurrency (e.g., `iphone_slot: K`).
- `iPhoneEnv` talks to VM via HTTP on port 8000 (`/action`), gets base64 screenshots.
- VMController controls the macOS Simulator (xcrun + AppleScript + pynput).

## Files (entry points)
- `iphone_env.py` — Ray actor + rollout loop + VM lifecycle
- `vm_controller.py` — HTTP server inside VM that drives Simulator
- `ray_cluster.template.yaml` — host inventory + resource limits
- `start_ray_cluster.sh` — starts Ray head/workers with resource caps
