# Distributed iPhone RL Environment

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
