import os
import sys
from pathlib import Path

# Avoid Ray's uv runtime env hook in restricted environments where psutil
# can't query process parents.
os.environ.setdefault("RAY_ENABLE_UV_RUN_RUNTIME_ENV", "0")

import ray
import pytest
from huggingface_hub import snapshot_download
from huggingface_hub.utils import LocalEntryNotFoundError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from inference.mlx_inference import MLXActor, MLX_VLM_MODEL_MAP


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
        runtime_env={"env_vars": {"PYTHONPATH": str(ROOT)}},
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
            max_kv_size=4096,
            trust_remote_code=True,
        )
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in one short sentence."},
        ]
        output = ray.get(
            actor.generate.remote(
                None,
                {"temperature": 0.2, "max_tokens": 20},
                messages,
            )
        )
        print("\n\n MLX ACTOR OUTPUT:", output, "\n\n")
        assert isinstance(output, str)
        assert output.strip()
    finally:
        ray.shutdown()
