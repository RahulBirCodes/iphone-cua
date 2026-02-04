import os
import sys
from pathlib import Path

# Avoid Ray's uv runtime env hook in restricted environments where psutil
# can't query process parents.
os.environ.setdefault("RAY_ENABLE_UV_RUN_RUNTIME_ENV", "0")

import ray

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inference.vllm_inference import VLLMActor


def test_vllm_actor_generate_final_completion() -> None:
    num_gpus = int(os.environ.get("VLLM_TEST_NUM_GPUS", "0"))
    ray.init(
        address="local",
        num_cpus=1,
        num_gpus=num_gpus,
        resources={"inference_node": 1},
    )
    try:
        model_id = os.environ.get("VLLM_TEST_MODEL", "distilgpt2")
        actor = VLLMActor.options(num_gpus=num_gpus).remote(
            model=model_id, max_model_len=256
        )
        messages = [
            {"role": "system", "content": "You are a funny assistant."},
            {"role": "user", "content": "tell me a joke. DO NOT USE ANY WORD I USED"},
        ]
        output = ray.get(
            actor.generate.remote(
                None,
                {"temperature": 1.0, "max_tokens": 20},
                messages,
            )
        )
        print("\n\n VLLM ACTOR OUTPUT:", output, "\n\n")
        assert isinstance(output, str)
        assert output.strip()
    finally:
        ray.shutdown()
