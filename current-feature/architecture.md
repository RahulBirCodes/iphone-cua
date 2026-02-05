# Architecture: Inference Actor ↔ iPhoneEnv Integration

## Overview

Wire the inference actors (`MLXActor`/`VLLMActor`) into `iPhoneEnv._get_llm_resp` using Ray object store refs to avoid re-serializing the growing turns history on every `.remote()` call. Update the inference actor `generate()` API to return a parsed dict via HuggingFace `tokenizer.parse_response()`.

## Capabilities

### 1. Inference Actor API Update

**Problem:** `generate()` returns raw `str`. Caller has to deal with raw model output including special tokens. Should return a structured dict using `tokenizer.parse_response()`.

**1. Data Structures**

`InferenceActor.generate()` return type changes from `str` → `dict[str, Any]`

The dict comes from `tokenizer.parse_response(raw_text)` — typically contains keys like `role`, `content`, `reasoning_content` (model-dependent).

**2. Implementation Algorithm**

```
For both MLXActor and VLLMActor:

1. Resolve turn_refs via ray.get(turn_refs) → list[Turn]
2. Convert turns to messages via _turns_to_messages(turns) - included in InferenceActor subclass
3. Apply chat template with add_generation_prompt=True → prompt string
4. Generate raw text from model engine (same as today)
5. Call self._tokenizer.parse_response(raw_text) → dict
6. Return the dict

generate() signature changes:
  async def generate(self, turn_refs: list[ray.ObjectRef], sampling: dict[str, Any]) -> dict[str, Any]
```

**3. Files**

Modified files:

- `environment/inference/inference_actor.py` — Update base `generate()` signature: accept `turn_refs: list[ray.ObjectRef]`, return `dict[str, Any]`. Add `_turns_to_messages()` method.
- `environment/inference/mlx_inference.py` — Update `generate()` to resolve turn refs, format messages, generate, call `parse_response()`, return dict
- `environment/inference/vllm_inference.py` — Same changes as MLX

---

### 2. Turn → Message Conversion (in Inference Actor)

**Problem:** Inference actor receives `list[ray.ObjectRef]` pointing to `Turn` objects. Needs to resolve them and convert to chat messages for the model.

**1. Data Structures**

Uses existing `Turn` dataclass from `environment/schemas.py`. No new structures.

**2. Implementation Algorithm**

```
_turns_to_messages(turns: list[Turn]) -> list[dict]:

For each turn:
  - role="system":
      {"role": "system", "content": turn.raw_output}

  - role="user" WITH screenshot:
      {"role": "user", "content": [
          {"type": "text", "text": turn.raw_output or ""},
          {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{turn.screenshot}"}}
      ]}

  - role="user" WITHOUT screenshot:
      {"role": "user", "content": turn.raw_output}

  - role="assistant":
      {"role": "assistant", "content": turn.raw_output}
      ** FULL raw_output — reasoning traces ARE kept in context **
```

This lives on the base `InferenceActor` class so both MLX and vLLM inherit it.

**3. Files**

Modified files:

- `environment/inference/inference_actor.py` — Add `_turns_to_messages()` method

---

### 3. iPhoneEnv ↔ Inference Actor Wiring + Object Store

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

_get_llm_resp(turn_refs: list[ray.ObjectRef]) -> str:
  1. result_dict = ray.get(
         self._inference_actor.generate.remote(turn_refs, self._sampling)
     )
  2. Return result_dict["content"]
     (raw output string — fed into parse_fn as before)
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
  - `_get_llm_resp`: signature changes to accept `turn_refs`, calls inference actor, extracts string from returned dict

---

### 4. Simplify XML Action Parser (Model-Independent)

**Problem:** The current `parser/action_parser.py` is coupled to Qwen3-VL's output format. It strips special tokens (`<|im_end|>`, etc.), splits `</think>` reasoning from action content, and threads `reasoning` through `ParsedOutput`. Now that `tokenizer.parse_response()` handles all of that upstream and returns a clean `content` string, the parser should be a pure XML action extractor — no model knowledge at all.

**What to remove:**
- Special token stripping (lines 66-69) — `parse_response()` handles this
- `</think>` splitting (lines 71-83) — `parse_response()` returns `content` (post-think) and `reasoning_content` separately
- `reasoning` field on `ParsedOutput` — no longer the parser's job; reasoning lives in the dict from `parse_response()`

**1. Data Structures**

`ParsedOutput` in `environment/schemas.py` simplifies:

```python
@dataclass
class ParsedOutput:
    content: str        # The input string (or matched tag string)
    action: dict | None # {"action": str, "params": dict} or None on failure
```

`reasoning` field removed — the raw reasoning is available from `parse_response()["reasoning_content"]` and is stored on `Turn.raw_output` (which is the full model output including reasoning).

**2. Implementation Algorithm**

```
parse(content: str) -> ParsedOutput:

Input: clean content string (already stripped of special tokens and thinking tags
       by tokenizer.parse_response() — this is just the action region)

1. text = content.strip()

2. Extract exactly one self-closing XML action tag
   - Regex: <(action_name) attrs />
   - Only match ALLOWED_ACTIONS
   - 0 matches → ParsedOutput(content=text, action=None)
   - >1 matches → ParsedOutput(content=text, action=None)

3. Parse and validate attributes against action schema
   - Same validation as today (float ranges, required params, etc.)
   - Failure → ParsedOutput(content=matched_tag, action=None)

4. Return ParsedOutput(content=matched_tag, action={"action": name, "params": validated})
```

**3. Files**

Modified files:
- `environment/schemas.py` — Remove `reasoning` field from `ParsedOutput`
- `environment/parser/action_parser.py` — Remove special token stripping, remove `</think>` splitting, remove all `reasoning=` args from ParsedOutput constructors. `parse()` takes a clean content string, not raw model output.

---

## Key Architectural Decisions

1. **Turn refs, not message refs.** We store `Turn` objects in the object store (not pre-formatted messages). The inference actor handles Turn→message conversion because it knows the model's expected format (multimodal content blocks, chat template).

2. **Inference actor resolves refs itself.** `generate()` receives `list[ObjectRef]` and calls `ray.get()` internally. Only small ref pointers cross the `.remote()` boundary.

3. **`_get_llm_resp` returns the `content` string from `parse_response()`.** The rollout loop feeds this clean string to `parse_fn` (the XML action parser). The parser never sees special tokens or thinking tags — `parse_response()` already separated those. `Turn.raw_output` stores the full model output (reasoning + action) for logging and for feeding back as assistant context.

4. **Full reasoning in assistant turns.** Assistant `raw_output` (including CoT reasoning) is preserved in the Turn and included verbatim in messages sent to the model. The model sees its own prior reasoning traces.

5. **XML parser is model-independent.** It knows nothing about `<think>`, `<|im_end|>`, or any tokenizer format. It takes a clean string and extracts one self-closing XML action tag. All model-specific parsing is handled by `tokenizer.parse_response()` in the inference layer.

6. **Sampling params on iPhoneEnv.** Passed once at init, used for every `generate` call in the rollout. Keeps the rollout loop clean.

7. **`add_generation_prompt=True`** is already set in both `MLXActor.format_messages()` and `VLLMActor.format_messages()`. This stays as-is.
