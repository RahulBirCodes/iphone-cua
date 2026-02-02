# Distributed iPhone RL Environment — Architecture (Concise)

```
Ray Driver / Trainer
  |
  | ray.remote actor per rollout (iphone_slot=1)
  v
  +--------------------+             +--------------------+
  | Host A             |             | Host B             |
  | iphone_slot=K      |             | iphone_slot=K      |
  |                    |             |                    |
  |  +--------------+  |             |  +--------------+  |
  |  | iPhoneEnv 1  |  |             |  | iPhoneEnv 3  |  |
  |  +--------------+  |             |  +--------------+  |
  |     |              |             |     |              |
  |     v              |             |     v              |
  |  +--------------+  |             |  +--------------+  |
  |  | VM 1 (tart)  |  |             |  | VM 1 (tart)  |  |
  |  +--------------+  |             |  +--------------+  |
  |                    |             |                    |
  |  +--------------+  |             |                    |
  |  | iPhoneEnv 2  |  |
  |  +--------------+  |             |                    |
  |     |              |             |                    |
  |     v              |             |                    |
  |  +--------------+  |             |                    |
  |  | VM 2 (tart)  |  |             |                    |
  |  +--------------+  |             |                    |
  +--------------------+             +--------------------+
           |                                   |
           | (each iphone env calls actor pool)|
           +-------------------+---------------------------+
                               |
                               v
        +----------------------------------------+
        | Inference Tier (GPU nodes)             |
        |                                        |
        |  +----------------------------------+  |
        |  | VLLMActor pool                   |  |
        |  | num_gpus=2 each, async batching   |  |
        |  +----------------------------------+  |
        +----------------------------------------+
```

## Key points

- One rollout == one `iPhoneEnv` Ray actor == one VM. No sharing until rollout completes.
- Ray custom resources cap per-host concurrency (e.g., `iphone_slot: K`).
- Inference runs on GPU-tagged Ray nodes; each `VLLMActor` reserves `num_gpus=2`.
- `VLLMActor.generate` returns final completion only (async, continuous batching in vLLM).
- `iPhoneEnv` talks to VM via HTTP on port 8000 (`/action`), gets base64 screenshots.
- VMController controls the macOS Simulator (xcrun + AppleScript + pynput).

## Files (entry points)

- `iphone_env.py` — Ray actor + rollout loop + VM lifecycle
- `vllm_inference.py` — Ray vLLM actor for async inference (TP=2)
- `vm_controller.py` — HTTP server inside VM that drives Simulator
- `ray_cluster.template.yaml` — host inventory + resource limits
- `start_ray_cluster.sh` — starts Ray head/workers with resource caps
