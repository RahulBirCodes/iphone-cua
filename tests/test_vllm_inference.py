import os

# Avoid Ray's uv runtime env hook in restricted environments where psutil
# can't query process parents.
os.environ.setdefault("RAY_ENABLE_UV_RUN_RUNTIME_ENV", "0")

import ray

from environment.inference.vllm_inference import VLLMActor
from environment.parsers.qwen3_response_parser import qwen3_response_parser
from environment.schemas import Turn


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
            model=model_id,
            parse_model_output=qwen3_response_parser,
            max_model_len=256,
        )
        turns = [
            Turn(t=0, role="system", screenshot=None, reasoning=None, content="You are a funny assistant.", action=None, reward=None),
            Turn(t=1, role="user", screenshot=None, reasoning=None, content="tell me a joke. DO NOT USE ANY WORD I USED", action=None, reward=None),
        ]
        turn_refs = [ray.put(t) for t in turns]
        output = ray.get(
            actor.generate.remote(
                turn_refs,
                {"temperature": 1.0, "max_tokens": 20},
            )
        )
        print("\n\n VLLM ACTOR OUTPUT:", output, "\n\n")
        assert hasattr(output, "reasoning")
        assert hasattr(output, "content")
        assert isinstance(output.content, str)
    finally:
        ray.shutdown()
