# Architecture: iPhone CUA Parsing, Prompting, and Reward Updates

## Overview

This design updates the rollout environment to use XML action parsing with Qwen-style reasoning separation, new reward policy handling, and revised user feedback formatting. Requirements are sourced from `edits.md` per request; no other planning docs are referenced.

## Capabilities

### Prompt Construction and Environment Feedback

**1. Data Structures**

```python
from dataclasses import dataclass
from typing import Protocol

class UserMessageBuilderProtocol(Protocol):
    def build_task_message(self, task_prompt: str) -> str: ...
    def build_env_feedback(self, payload: "EnvFeedback") -> str: ...

@dataclass(frozen=True)
class EnvFeedback:
    type: str
    message: str
```

**2. Implementation Algorithm**

```
1. When starting a rollout, send the first user message as the raw task prompt text.
2. For all environment feedback messages:
   - Build a JSON payload containing only feedback fields (no task in JSON).
   - Prefix the message with "ENVIRONMENT RESPONSE:\n" followed by the JSON.
3. Remove any explicit retry request field from feedback payloads.
4. If an observation error occurs during reset/observe, raise EnvRuntimeError and stop.
```

**3. Files**

New files to create:

- `environment/prompting/protocols.py` - `UserMessageBuilderProtocol` and feedback data structures.
- `environment/prompting/builder.py` - prompt/feedback formatting logic.

Modified files:

- `environment/iphone_env.py` - use raw task prompt for the first user message and new feedback formatting; raise on observation failures.
- `environment/schemas.py` - add feedback data structures if centralized there instead of a new prompting module.

---

### XML Action Parsing and Qwen Reasoning Split

**1. Data Structures**

```python
from dataclasses import dataclass
from typing import Protocol

class ActionParserProtocol(Protocol):
    def parse(self, raw_output: str) -> "ParsedOutput": ...

@dataclass(frozen=True)
class ParsedOutput:
    reasoning: str | None
    content: str
    action: dict | None
    error: str | None

@dataclass(frozen=True)
class ActionSpec:
    name: str
    params: dict
```

**2. Implementation Algorithm**

```
1. Strip special tokens (e.g., <|im_end|>) and surrounding whitespace.
2. Split reasoning vs action:
   - If "</think>" exists, reasoning = text before it, content = text after it.
   - Otherwise, reasoning = None, content = full text.
3. Extract exactly one self-closing XML action tag from content.
   - Allowed tags: tap, long_press, swipe, type_text, go_home, wait, finished, fail.
   - If zero tags or more than one tag, return ParsedOutput with error.
4. Parse attributes into params and normalize to environment action schema.
5. Return ParsedOutput with `action` populated as {"action": name, "params": params}.
```

**3. Files**

New files to create:

- `environment/parsing/protocols.py` - `ActionParserProtocol`, parse errors.
- `environment/parsing/schemas.py` - `ParsedOutput`, `ActionSpec`, parse error types.
- `environment/parsing/xml_action_parser.py` - default XML parser implementation.

Modified files:

- `environment/iphone_env.py` - use `ParsedOutput` directly instead of `_extract_action` and store parsed output on turns.
- `environment/schemas.py` - extend `Turn` with `parsed_output: ParsedOutput | None`.

---

### Action Schema and VM Mapping

**1. Data Structures**

```python
from dataclasses import dataclass
from typing import Protocol

class ActionMapperProtocol(Protocol):
    def to_env_action(self, parsed_action: dict) -> dict: ...

@dataclass(frozen=True)
class ActionSpace:
    names: set[str]
```

**2. Implementation Algorithm**

```
1. Define a canonical action schema used by parsing:
   - tap(x, y)
   - long_press(x, y)
   - swipe(start_x, end_x, start_y, end_y)
   - type_text(text)
   - go_home()
   - wait()
   - finished()
   - fail()
2. Map canonical swipe params to VM controller params:
   - start_x -> x1
   - start_y -> y1
   - end_x -> x2
   - end_y -> y2
3. Validate that all normalized coordinates are in [0, 1].
4. Pass mapped actions to `_send_action` without further mutation.
```

**3. Files**

New files to create:

- `environment/actions/protocols.py` - `ActionMapperProtocol`.
- `environment/actions/mapper.py` - canonical-to-VM action mapping.
- `environment/actions/schemas.py` - action definitions and constants.

Modified files:

- `environment/iphone_env.py` - apply action mapping before sending to VM.
- `environment/vm_controller.py` - update docstring to match canonical schema mapping.

---

### Reward Policy and Turn Recording

**1. Data Structures**

```python
from dataclasses import dataclass
from typing import Protocol

class JudgeProtocol(Protocol):
    def judge(self, turns: list["Turn"], task_id: str, task_prompt: str) -> float: ...

@dataclass(frozen=True)
class RewardPolicy:
    parse_penalty: float = 0.0
    success_reward: float
    failure_penalty: float
```

**2. Implementation Algorithm**

```
1. On parse failure:
   - Append an assistant turn with action=None and parsed_output containing error.
   - Apply RewardPolicy.parse_penalty to the last turn reward.
   - Send feedback as ENVIRONMENT RESPONSE with parse error message.
2. On successful termination:
   - If termination is Done, set final_reward to success_reward or judge output (if configured).
   - If termination is Fail, set final_reward to failure_penalty.
3. Store the model raw output and parsed output in every assistant turn.
```

**3. Files**

New files to create:

- `environment/rewards/schemas.py` - `RewardPolicy`.
- `environment/rewards/protocols.py` - `JudgeProtocol` if not placed in a shared location.

Modified files:

- `environment/iphone_env.py` - apply reward policy for parse errors and termination.
- `environment/schemas.py` - add reward policy references if RolloutResult should include it.

## Distributed Computing Changes (if applicable)

None. Ray usage remains unchanged, with parsing and reward handled within the actor.

## Testability Strategy

### Protocols for Testing

- `ActionParserProtocol`: parse raw model output into `ParsedOutput`.
- `UserMessageBuilderProtocol`: build task and feedback messages deterministically.
- `ActionMapperProtocol`: map canonical action parameters to VM wire format.
- `JudgeProtocol`: evaluate terminal trajectories.

### Dependency Injection Points

- `iPhoneEnv.__init__`: accepts parser, judge, message builder, action mapper via protocols.
- `iPhoneEnv.collect_rollout`: uses injected parser/mapper rather than internal helpers.

### Mockable Components

- `ActionParserProtocol`: mock to inject parse failures or specific actions.
- `ActionMapperProtocol`: mock to assert mapping behavior without touching VM.
- `JudgeProtocol`: mock to return fixed rewards.

### Test Cases

- Parse XML action with single tag and correct attribute mapping.
- Parse output containing multiple tags returns parse error.
- Qwen reasoning split using `</think>` yields correct reasoning and content.
- Initial user message equals raw task prompt, not JSON.
- Environment feedback messages start with `ENVIRONMENT RESPONSE:` and have no retry field.
- Observation error during reset/observe raises `EnvRuntimeError` and does not prompt the model.
- Parse failure applies `parse_penalty` and does not step the VM.
- `finished` and `fail` actions set terminal rewards based on `RewardPolicy`.

## Key Architectural Decisions

- Use XML-only action parsing to simplify model output validation and enforce a single-action contract.
- Separate canonical action schema from VM wire format via an action mapper to decouple parser output from controller implementation.
- Store reasoning and parsed output on `Turn` to preserve traceability without polluting prompts.

