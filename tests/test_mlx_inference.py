import os

# Avoid Ray's uv runtime env hook in restricted environments where psutil
# can't query process parents.
os.environ.setdefault("RAY_ENABLE_UV_RUN_RUNTIME_ENV", "0")

import ray
import pytest
from huggingface_hub import snapshot_download
from huggingface_hub.utils import LocalEntryNotFoundError

from environment.inference.mlx_inference import MLXActor, MLX_VLM_MODEL_MAP
from environment.parsers.qwen3_response_parser import qwen3_response_parser
from environment.schemas import Turn


def _ensure_model_cached(model_id: str) -> None:
    try:
        snapshot_download(model_id, local_files_only=True)
    except LocalEntryNotFoundError:
        pytest.skip(
            f"Model '{model_id}' not cached. Set MLX_TEST_ALLOW_DOWNLOAD=1 "
            "to allow downloads."
        )


def test_mlx_actor_generate_final_completion() -> None:
    ray.init(
        address="local",
        num_cpus=1,
        num_gpus=0,
        resources={"inference_node": 1},
    )
    try:
        model_id = os.environ.get(
            "MLX_TEST_MODEL", "Qwen/Qwen3-VL-2B-Thinking"
        )
        allow_download = os.environ.get("MLX_TEST_ALLOW_DOWNLOAD", "0") == "1"
        resolved_model_id = MLX_VLM_MODEL_MAP.get(model_id, model_id)
        if not allow_download:
            _ensure_model_cached(resolved_model_id)
        actor = MLXActor.options(num_gpus=0).remote(
            model=model_id,
            parse_model_output=qwen3_response_parser,
            max_kv_size=4096,
            trust_remote_code=True,
        )
        turns = [
            Turn(t=0, role="system", screenshot=None, reasoning=None, content="You are a helpful assistant.", action=None, reward=None),
            Turn(t=1, role="user", screenshot=None, reasoning=None, content="Say hello in one short sentence.", action=None, reward=None),
        ]
        turn_refs = [ray.put(t) for t in turns]
        output = ray.get(
            actor.generate.remote(
                turn_refs,
                {"temperature": 0.2, "max_tokens": 20},
            )
        )
        print("\n\n MLX ACTOR OUTPUT:", output, "\n\n")
        assert hasattr(output, "reasoning")
        assert hasattr(output, "content")
        assert isinstance(output.content, str)
    finally:
        ray.shutdown()
