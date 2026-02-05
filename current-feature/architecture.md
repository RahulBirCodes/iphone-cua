# Architecture: Inference Actor ↔ iPhoneEnv Integration

## Overview

Wire the inference actors (`MLXActor`/`VLLMActor`) into `iPhoneEnv._get_llm_resp` using Ray object store refs to avoid re-serializing the growing turns history on every `.remote()` call. Inference actors take a `parse_model_output` callable that splits raw model output into `reasoning` and `content`. The XML action parser becomes model-independent, only parsing clean content strings.

## Capabilities

### 1. Qwen3 Response Parser

**Problem:** Need a model-specific parser that splits raw model output into reasoning and content. vLLM's built-in `Qwen3ReasoningParser` requires both `<think>` and `</think>` in the output, but with `add_generation_prompt=True` the opening `<think>` is in the prompt — the model only outputs the closing `</think>`. vLLM also strips special tokens (`<|im_end|>`) via `skip_special_tokens=True` by default, so that's already handled.

**1. Data Structures**

Type: `Callable[[str], tuple[str | None, str | None]]`

Returns: `(reasoning, content)` where:
- `reasoning` = text before `</think>` (the model's CoT), or `None` if no thinking
- `content` = text after `</think>` (the action region), or the full text if no thinking

**2. Implementation Algorithm**

```
qwen3_response_parser(raw_output: str) -> tuple[str | None, str | None]:

1. If "</think>" in raw_output:
     reasoning, _, content = raw_output.partition("</think>")
     return (reasoning.strip() or None, content.strip() or None)
2. Else:
     return (None, raw_output)

Note: Does NOT require opening <think> — handles the closing-only case
      from add_generation_prompt=True.
```

**3. Files**

New files:
- `environment/parser/qwen3_response_parser.py` — Standalone `qwen3_response_parser()` function

Modified files:
- `environment/parser/__init__.py` — Export `qwen3_response_parser`

---

### 2. Inference Actor API Update

**Problem:** `generate()` returns raw `str`. Should return a structured dict with `reasoning` and `content` separated by a `parse_model_output` callable passed at init.

**1. Data Structures**

New init parameter on inference actors:
- `parse_model_output: Callable[[str], tuple[str | None, str | None]]` — passed into `__init__`

`generate()` return type changes from `str` → `dict[str, str | None]`:
```python
{"reasoning": str | None, "content": str | None}
```

**2. Implementation Algorithm**

```
For both MLXActor and VLLMActor:

__init__ changes:
  - Add parameter: parse_model_output callable
  - Store as self._parse_model_output

generate(turn_refs, sampling) -> dict[str, str | None]:
  1. Resolve turn_refs via ray.get(turn_refs) → list[Turn]
  2. Convert turns to messages via _turns_to_messages(turns)
  3. Apply chat template with add_generation_prompt=True → prompt string
  4. Generate raw text from model engine (same as today)
  5. reasoning, content = self._parse_model_output(raw_text)
  6. Return {"reasoning": reasoning, "content": content}

generate() signature changes:
  async def generate(self, turn_refs: list[ray.ObjectRef], sampling: dict[str, Any]) -> dict[str, str | None]
```

**3. Files**

Modified files:
- `environment/inference/inference_actor.py` — Update base `generate()` signature: accept `turn_refs: list[ray.ObjectRef]`, return `dict[str, str | None]`. Add `parse_model_output` to base init. Add `_turns_to_messages()` method.
- `environment/inference/mlx_inference.py` — Update `__init__` and `generate()` to use new signature
- `environment/inference/vllm_inference.py` — Same changes as MLX

---

### 3. Turn → Message Conversion (in Inference Actor)

**Problem:** Inference actor receives `list[ray.ObjectRef]` pointing to `Turn` objects. Needs to resolve them and convert to chat messages for the model. Assistant turns must reconstruct the full output (including `<think>...</think>`) so the model sees its own prior reasoning.

**1. Data Structures**

Uses updated `Turn` dataclass (see Capability 5). No new structures.

**2. Implementation Algorithm**

```
_turns_to_messages(turns: list[Turn]) -> list[dict]:

For each turn:
  - role="system":
      {"role": "system", "content": turn.content}

  - role="user" WITH screenshot:
      {"role": "user", "content": [
          {"type": "text", "text": turn.content or ""},
          {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{turn.screenshot}"}}
      ]}

  - role="user" WITHOUT screenshot:
      {"role": "user", "content": turn.content}

  - role="assistant":
      Reconstruct full model output for chat template:
      if turn.reasoning:
          content = f"<think>\n{turn.reasoning}\n</think>\n\n{turn.content or ''}"
      else:
          content = turn.content or ""
      {"role": "assistant", "content": content}

      The opening <think> is prepended because the chat template expects
      the full assistant message. add_generation_prompt=True will add
      <think> for the NEW generation, but prior turns need it explicitly.
```

This lives on the base `InferenceActor` class so both MLX and vLLM inherit it.

**3. Files**

Modified files:
- `environment/inference/inference_actor.py` — Add `_turns_to_messages()` method

---

### 4. iPhoneEnv ↔ Inference Actor Wiring + Object Store

**Problem:** `_get_llm_resp` is a stub returning `""`. Need to wire it to the inference actor. Passing the full `list[Turn]` via `.remote()` re-serializes everything each call (quadratic with screenshots). Use `ray.put()` per turn and pass lightweight ObjectRef list instead.

**1. Data Structures**

New instance state on `iPhoneEnv`:
- `self._inference_actor` — Ray actor handle (passed in `__init__`)
- `self._sampling` — Sampling params dict (passed in `__init__`)

New local state in `collect_rollout`:
- `turn_refs: list[ray.ObjectRef]` — Grows alongside `turns`, one ref per turn

**2. Implementation Algorithm**

```
__init__ changes:
  - Add parameter: inference_actor (ray actor handle)
  - Add parameter: sampling (dict — temperature, max_tokens, etc.)
  - Store as self._inference_actor, self._sampling

collect_rollout changes:
  - Initialize turn_refs: list[ray.ObjectRef] = []
  - Every place a Turn is appended to turns:
      ref = ray.put(turn)
      turn_refs.append(ref)
  - Call _get_llm_resp(turn_refs) instead of _get_llm_resp(turns)

_get_llm_resp(turn_refs: list[ray.ObjectRef]) -> dict[str, str | None]:
  1. result_dict = ray.get(
         self._inference_actor.generate.remote(turn_refs, self._sampling)
     )
  2. Return result_dict  ({"reasoning": ..., "content": ...})

collect_rollout then uses:
  result_dict = self._get_llm_resp(turn_refs)
  content = result_dict["content"] or ""
  reasoning = result_dict["reasoning"]
  parsed_output = self._parse_fn(content)         # XML parser gets clean content string
  action = parsed_output.action                    # parsed action dict or None

  # Build assistant Turn with separated fields
  Turn(role="assistant", reasoning=reasoning, content=content, action=action, ...)
```

**Object store efficiency:**
- Each Turn is `ray.put()` once → immutable, never re-serialized
- `turn_refs` list is just small ObjectRef pointers (~bytes each)
- Inference actor calls `ray.get(turn_refs)` to resolve → zero-copy on same node
- Old Turn objects persist in store for rollout duration, GC'd when refs dropped

**3. Files**

Modified files:
- `environment/iphone_env.py`:
  - `__init__`: add `inference_actor` and `sampling` params
  - `collect_rollout`: maintain `turn_refs` alongside `turns`, `ray.put()` each new turn
  - `_get_llm_resp`: signature changes to accept `turn_refs`, returns dict from inference actor

---

### 5. Turn Schema Update

**Problem:** `Turn.raw_output` mixes reasoning and content into one string. With the inference actor now returning them separately, Turn should store them as distinct fields.

**1. Data Structures**

```python
@dataclass
class Turn:
    t: int
    role: str
    screenshot: str | None
    reasoning: str | None      # Model's CoT reasoning (from parse_model_output), None for user/system
    content: str | None        # The content: action text for assistant, task text for user, system prompt for system
    action: dict | None        # Parsed action dict from XML parser, None for user/system or parse failure
    reward: float | None

# Removed fields:
#   raw_output      → replaced by reasoning + content
#   parsed_output   → action is directly on Turn
```

**2. Impact**

- `RolloutResult.turns` contains Turns with the new schema
- `_save_rollout_json` serializes reasoning and content as separate fields
- `_judge_fn` receives turns with new schema
- Turn→message conversion in inference actor uses `reasoning` + `content` to reconstruct full assistant output

**3. Files**

Modified files:
- `environment/schemas.py` — Update `Turn` dataclass, remove `ParsedOutput` (no longer needed as a separate type — reasoning is on Turn, content is on Turn, action is on Turn)
- `environment/iphone_env.py` — Update all Turn construction sites

---

### 6. Simplify XML Action Parser (Model-Independent)

**Problem:** The current `parser/action_parser.py` is coupled to Qwen3-VL's output format. It strips special tokens (`<|im_end|>`, etc.) and splits `</think>` reasoning from action content. Now that `parse_model_output` handles that upstream and the rollout loop passes only the clean `content` string, the parser should be a pure XML action extractor.

**What to remove:**
- Special token stripping (lines 66-69) — vLLM `skip_special_tokens=True` handles this; MLX response parser can handle it
- `</think>` splitting (lines 71-83) — `parse_model_output` already separated reasoning from content
- `reasoning` field from return — no longer the parser's concern

**1. Data Structures**

Parser return type simplifies. Since `ParsedOutput` is removed (see Capability 5), the parser returns `dict | None` — the parsed action dict, or None on failure.

Type: `Callable[[str], dict | None]`

**2. Implementation Algorithm**

```
parse(content: str) -> dict | None:

Input: clean content string (just the action region)

1. text = content.strip()

2. Extract exactly one self-closing XML action tag
   - Regex: <(action_name) attrs />
   - Only match ALLOWED_ACTIONS
   - 0 matches → None
   - >1 matches → None

3. Parse and validate attributes against action schema
   - Same validation as today (float ranges, required params, etc.)
   - Failure → None

4. Return {"action": name, "params": validated}
```

**3. Files**

Modified files:
- `environment/parser/action_parser.py` — Remove special token stripping, remove `</think>` splitting, return `dict | None` instead of `ParsedOutput`
- `environment/parser/__init__.py` — Update exports

---

## Key Architectural Decisions

1. **`parse_model_output` is a callable on the inference actor.** Model-specific output parsing (e.g., `<think>` splitting for Qwen3) is injected via a callable, not baked into the actor. The actor stays model-engine-specific (MLX vs vLLM) but not model-family-specific (Qwen3 vs Llama).

2. **Qwen3 response parser handles closing-only `</think>`.** Since `add_generation_prompt=True` puts the opening `<think>` in the prompt, the model only outputs the closing tag. vLLM's built-in `Qwen3ReasoningParser` requires both tags, so we use our own parser that splits on `</think>` only.

3. **Turn stores `reasoning` and `content` separately.** No more `raw_output` — the model's reasoning and action content are distinct fields. For assistant turns fed back to the model, `_turns_to_messages` reconstructs the full `<think>...</think>` block.

4. **`ParsedOutput` is removed.** Its fields (`reasoning`, `content`, `action`) now live directly on `Turn`. The XML action parser returns `dict | None` (just the action).

5. **Turn refs, not message refs.** We store `Turn` objects in the object store (not pre-formatted messages). The inference actor handles Turn→message conversion because it knows the model's expected format.

6. **Inference actor resolves refs itself.** `generate()` receives `list[ObjectRef]` and calls `ray.get()` internally. Only small ref pointers cross the `.remote()` boundary.

7. **XML parser is model-independent.** It knows nothing about `<think>`, `<|im_end|>`, or any tokenizer format. It takes a clean string and extracts one self-closing XML action tag.

8. **Full reasoning in assistant turns.** Reasoning traces are preserved on Turn and reconstructed into `<think>...</think>` blocks in messages sent to the model. The model sees its own prior reasoning.

9. **`add_generation_prompt=True`** is already set in both `MLXActor.format_messages()` and `VLLMActor.format_messages()`. This stays as-is.
