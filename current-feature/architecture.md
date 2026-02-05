# Architecture: Action Parsing, Observation Handling & Reward Policy

## Overview

Redesign the rollout loop to use XML-based action parsing (aligned with Qwen3-VL reasoning traces), clean observation/error handling semantics, and a configurable reward policy. The changes span four capability groups: error semantics, user text formatting, XML action parsing, and reward computation.

## Capabilities

### 1. Observation Error Handling

**Problem:** Observation failures (screenshot capture, VM communication) are currently surfaced as model feedback. They should be treated as infrastructure errors since they are not related to model behavior.

**1. Data Structures**

No new structures. Use existing `EnvRuntimeError` from `environment/schemas.py`.

**2. Implementation Algorithm**

```
1. In collect_rollout, after calling _send_action("observe", {}):
   - If observe returns an error, raise EnvRuntimeError immediately
   - Do NOT pass observation failures into _build_user_text as feedback
2. Same for _step: if _send_action returns an error for a non-terminal action:
   - env_error is still passed as feedback to the model (this is correct — the action was attempted)
   - But if _send_action raises a connection/HTTP exception, let it propagate as EnvRuntimeError
```

**3. Files**

Modified files:

- `environment/iphone_env.py` — Change `observe` error handling to raise `EnvRuntimeError` instead of passing error as feedback

---

### 2. User Text Formatting

**Problem:** The first task prompt is wrapped in JSON via `_build_user_text`. It should be raw text. Subsequent environment responses should be prefixed with `ENVIRONMENT RESPONSE:` and use JSON. The explicit `request_retry` field should be removed.

**1. Data Structures**

No new structures.

**2. Implementation Algorithm**

```
1. First user turn (task prompt):
   - Pass task_prompt as raw string directly (no JSON wrapping)
   - Do not call _build_user_text for the initial task prompt

2. Rewrite _build_user_text for subsequent turns:
   - Remove task_prompt parameter (it's only used on the first turn)
   - Remove request_retry parameter and "request" field from feedback
   - Output format:
     "ENVIRONMENT RESPONSE:\n" + json.dumps({"feedback": {"type": ..., "message": ...}})
   - When no error:
     "ENVIRONMENT RESPONSE:\n" + json.dumps({})
     (or just return None if there's nothing to report — i.e., successful step with screenshot)

3. Parse error feedback:
   - Same format: "ENVIRONMENT RESPONSE:\n" + json.dumps({"feedback": {"type": "parse_error", "message": "..."}})
   - Model infers it needs to retry from the error message itself
```

**3. Files**

Modified files:

- `environment/iphone_env.py` — Rewrite `_build_user_text`, update first user turn in `collect_rollout`

---

### 3. XML Action Parsing

**Problem:** Need a standalone parser that extracts self-closing XML action tags from model output, separates reasoning from action, and returns structured results. Must work with Qwen3-VL's chat template where `add_generation_prompt=True` opens `<think>` in the prompt, and the model closes it in its output.

**Key invariant:** `add_generation_prompt` opens `<think>` in the PROMPT. The model closes it in the OUTPUT. Everything after `</think>` is the action.

**Generation context:** When `apply_chat_template(..., add_generation_prompt=True)` is called, the template appends:

```
<|im_start|>assistant
<think>
```

So the model begins generating _inside_ the think block. It does NOT output an opening `<think>` tag itself. A typical raw model completion looks like:

```
(reasoning text here...)
</think>

<tap x="0.42" y="0.77" />
<|im_end|>
```

**1. Data Structures**

In `environment/schemas.py`:

```python
@dataclass
class ParsedOutput:
    reasoning: str | None      # Text before </think> (model's CoT reasoning)
    action_text: str | None    # Raw XML action tag string (e.g., '<tap x="0.42" y="0.77" />')
    action: dict | None        # Parsed {"action": str, "params": dict} or None on failure
    error: str | None          # Parse error message if action extraction failed
```

Update `Turn`:

```python
@dataclass
class Turn:
    t: int
    role: str
    screenshot: str | None
    raw_output: str | None         # Store raw model completion verbatim
    parsed_output: ParsedOutput | None  # NEW: breakdown of LLM response
    action: dict | None            # Keep for backward compat; same as parsed_output.action
    reward: float | None
```

**2. Implementation Algorithm**

Allowed actions and their parameter schemas:

```
tap(x: float, y: float)
long_press(x: float, y: float)
swipe(start_x: float, start_y: float, end_x: float, end_y: float)
type_text(text: str)
go_home()
wait()
fail()
finished()
```

XML format: `<action_name attr1="val1" attr2="val2" />`

Parsing algorithm (input is the raw model completion string):

```
parse(raw_output: str) -> ParsedOutput:

1. Strip special tokens + whitespace
   - Remove trailing/embedded <|im_end|> (and any similar end markers)
   - text = text.strip()

2. Split reasoning vs action region
   - Look for the literal substring "</think>"
   - If "</think>" exists:
     - reasoning_text = everything BEFORE "</think>" (trimmed)
     - action_region  = everything AFTER "</think>" (trimmed)
   - If "</think>" does NOT exist:
     - reasoning_text = "" (empty string)
     - action_region  = entire output (trimmed)
   - NOTE: The model does NOT output an opening <think> — that's in the prompt.
     If a leading <think> somehow appears in the output, strip it from reasoning_text.

3. Extract exactly one action tag from action_region
   - Use regex to find self-closing XML tags: <(tag_name) ... />
   - Only match tags in ALLOWED_ACTIONS = {tap, long_press, swipe, type_text, go_home, wait, fail, finished}
   - If 0 matching tags found: return ParsedOutput(reasoning_text, None, None, error="no_action_tag_found")
   - If >1 matching tags found: return ParsedOutput(reasoning_text, None, None, error="multiple_action_tags")
   - If exactly 1: proceed to attribute parsing

4. Parse attributes from the matched tag
   - Use regex or xml.etree to extract attributes
   - Validate against action schema:
     - tap: x (float 0-1), y (float 0-1)
     - long_press: x (float 0-1), y (float 0-1)
     - swipe: start_x (float 0-1), start_y (float 0-1), end_x (float 0-1), end_y (float 0-1)
     - type_text: text (str, required)
     - go_home, wait, fail, finished: no params
   - If validation fails: return ParsedOutput(reasoning_text, tag_text, None, error="invalid_params: ...")

5. Return ParsedOutput(
     reasoning=reasoning_text,
     action_text=matched_tag_string,
     action={"action": tag_name, "params": {validated params}},
     error=None
   )
```

**What gets stored vs reused:**

- `Turn.raw_output` = exact model completion verbatim (for logging/debugging)
- `Turn.parsed_output` = structured breakdown (reasoning, action, error)
- Reasoning text does NOT get fed back into the next prompt (avoids context bloat)
- Next prompt includes: system instructions, current observation (screenshot + JSON), optional short action history
- Full reasoning traces stay in logs, not in the prompt

**3. Files**

New files:

- `environment/action_parser.py` — Standalone XML action parser with `parse(raw_output: str) -> ParsedOutput`
  - Contains: `ALLOWED_ACTIONS` dict mapping action name -> param schema
  - Contains: `parse()` function implementing the algorithm above
  - Contains: param validation helpers (float range check, required field check)

Modified files:

- `environment/schemas.py` — Add `ParsedOutput` dataclass
- `environment/iphone_env.py` — Replace `_default_parse` and `_extract_action` with the new parser:
  - `_parse_fn` signature becomes `Callable[[str], ParsedOutput]`
  - `_extract_action` is removed (action is on `ParsedOutput.action`)
  - `ParseResult` type alias is removed
  - Rollout loop uses `parsed_output.action` for stepping
  - Rollout loop stores `parsed_output` on `Turn`
  - Parse error path uses `parsed_output.error` message in feedback

---

### 4. Reward Policy

**Problem:** Need a configurable reward structure. Parse failures should optionally incur a penalty. Final reward comes from judge function, gated by success/failure.

**1. Data Structures**

In `environment/schemas.py`:

```python
@dataclass
class RewardPolicy:
    parse_penalty: float = 0.0      # Penalty applied each time parser fails
    success_reward: float = 1.0     # Reward when judge determines success
    failure_penalty: float = 0.0    # Penalty when judge determines failure
```

**2. Implementation Algorithm**

```
Reward accumulation during rollout:

1. Initialize cumulative_reward = 0.0

2. On each turn where parsed_output.error is not None (parse failure):
   - cumulative_reward += reward_policy.parse_penalty
   - Store parse_penalty on that assistant Turn.reward

3. On termination:
   - If DONE: call judge_fn → if judge says success, final_reward = reward_policy.success_reward
                              → if judge says failure, final_reward = reward_policy.failure_penalty
   - If FAIL (model called fail()): final_reward = reward_policy.failure_penalty
   - If TRUNCATED: final_reward = reward_policy.failure_penalty
   - total_reward = cumulative_reward + final_reward

4. Store total_reward on RolloutResult.final_reward
```

**3. Files**

Modified files:

- `environment/schemas.py` — Add `RewardPolicy` dataclass
- `environment/iphone_env.py`:
  - `iPhoneEnv.__init__` accepts `reward_policy: RewardPolicy` (default `RewardPolicy()`)
  - `collect_rollout` tracks cumulative parse penalties
  - Terminal reward computation uses `reward_policy.success_reward` / `failure_penalty`
  - `_judge_fn` signature turns into `Callable[[list[Turn], str, str], bool]` where it's just success or not

---

## Key Architectural Decisions

1. **Parser is a standalone module** (`environment/action_parser.py`), not embedded in iphone_env.py. This allows unit testing the parser independently and swapping parser implementations.

2. **ParsedOutput is the single return type from parsing.** It replaces the old `ParseResult = dict | tuple[dict, float]` union. All information (reasoning, action, error) lives on one object. The reward-from-parse concept is removed — rewards come exclusively from `RewardPolicy`.

3. **No explicit retry request in feedback.** The model receives the error message and must infer the need to retry. This simplifies the protocol and avoids prompt engineering in the environment layer.

4. **RewardPolicy defaults are neutral** (parse_penalty=0.0, failure_penalty=0.0, success_reward=1.0). This means the system behaves like before until explicitly configured for RL training.

5. **Observation failures are fatal.** If the environment cannot take a screenshot, that's infrastructure, not model behavior. Raising `EnvRuntimeError` lets the orchestrator handle retries at the VM level rather than polluting the model's context.

6. **Turn.action is kept alongside Turn.parsed_output** for backward compatibility with existing code that reads `turn.action`. Both fields contain the same action dict (or None).
