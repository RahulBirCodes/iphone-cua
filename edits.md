1. Failures for observations should NOT be passed to the model (should instead raise EnvRuntimeError since that's not tied to model state)
2. The first task prompt from the user should not be in json from \_build_user_text, just add it raw. Then in \_build_user_text, prefix at the top ENVIRONMENT RESPONSE: and then put the json. Remove the retry thing, just respond with the error and the model should know that it needs to retry rather than us explicitly putting it in.

3. Action space and parsing stuff:

- These are all the valid actions: tap(x, y) # takes in x: float between (0,1) and y: float between (0,1), normalized coords from top left of screen
  swipe(start_x, end_x, start_y, end_y) # normalized coords of start and end loc of swipe
  type_text(text) # text is just a string to type into the focused field
  go_home()
  wait() # ui is updating, do not do anything
  fail() # task is infeasible
  finished() # task is done
  long_press(x, y) # takes in x: float between (0,1) and y: float between (0,1), normalized coords from top left of screen
- I want the model to output it in this format: <long_press x="0.42" y="0.77" />
- Create a default parser in a separate file which parses xml within the model response and returns a dict (has each action from send_action format). It throws a parse error if not able to which it extracts and passes back to the model.
- Qwen model already outputs reasoning traces in its chat template (probably going to use <https://huggingface.co/Qwen/Qwen3-VL-2B-Thinking>). But I'm going to ignore it's tool use format and instead ensure that it outputs after it's reasoning trace just the xml action (reasoning is that I want the model to reason in it's cot and then return just the xml answer).

**Parsing**

- update Turn to include parsed_output which is optional but breaks down llm response into reasoning and content. Then parse fn should extract reasoning and action and also parse the action out instead of \_extract_action too. For qwen model specifically this is how it works:

"Qwen Action Parsing & Reasoning Contract

PROMPT CONSTRUCTION (Qwen chat template)
• Build messages as normal (system + user).
• Apply Qwen’s chat template with add_generation_prompt=True.
• With THIS template, add_generation_prompt=True appends:
<|im_start|>assistant

so generation begins inside the think block.

Rendered prompt (schematic)
<|im_start|>system
You are an iPhone agent. Output format rules:
• Put all reasoning inside …
• After , output exactly ONE self-closing XML action tag and nothing else.
Allowed actions: <tap … />, <long_press … />, <swipe … />, <type_text … />, <go_home />, , ,
<|im_end|>

<|im_start|>user
OBSERVATION (JSON):
{“task”:“Open Settings and toggle Wi-Fi”,“feedback”:{“type”:“env_error”,“message”:””}}
<|im_end|>

<|im_start|>assistant

MODEL OUTPUT (expected shape)
• The model writes free-form reasoning first (it is already inside because the template opened it).
• The model then outputs a closing .
• After , it outputs exactly one self-closing XML action tag.
• It may end with <|im_end|> (strip it).

Example completion
I should tap the Settings icon on the home screen, then navigate to Wi-Fi and toggle it.

<tap x="0.42" y="0.77" />
<|im_end|>

PARSING STRATEGY (deterministic)
Input: raw model completion text (store it as-is in Turn.raw_output) 1. Strip special tokens + whitespace

    • Remove trailing/embedded <|im_end|> (and any similar end markers you see).
    • text = text.strip()

    2. Split reasoning vs action region

    • If “” appears:
    • reasoning_text = everything before “” (trimmed)
    • action_region  = everything after “” (trimmed)
    • Else:
    • reasoning_text = “” (or keep None)
    • action_region  = full text (trimmed)

    3. Extract exactly one action tag from action_region

    • Allowed self-closing tags: tap, long_press, swipe, type_text, go_home, wait, finished, fail
    • Require exactly ONE action tag (recommended).
    • If 0 tags: parse error
    • If >1 tags: parse error (or “take last tag” if you want a tolerant mode early on)
    • Parse attributes into params dict.
    • Return env wire dict:

{“action”: “”, “params”: {…}}

    4. Control flow on failures

    • If parsing fails: do NOT step the env.
    • Append assistant turn with action=None.
    • Next user turn includes FEEDBACK (JSON) requesting retry, same screenshot.
    • Model tries again.

TURN STORAGE (what to keep)
• Keep Turn.raw_output = exact model completion (so you can read reasoning later).
• Keep Turn.action = parsed {“action”,“params”} (or None on parse error).
• Optional: if you want easy access to reasoning without re-splitting, you can store reasoning_text separately, but you don’t have to. Re-splitting on is cheap.

IMPORTANT NOTE ABOUT “reparsing to put in chat template”
• You do NOT need to feed full prior raw_output (reasoning) back to the model each step.
• For the next model call, include:
• system instructions
• current observation user message (screenshot + JSON)
• optionally a short action history (last 1–3 actions)
• Keep full reasoning traces in logs, not necessarily in the next prompt, to avoid context bloat."

1. Reward fn design:

- Create a new schema called "@dataclass
  class RewardPolicy:
  parse_penalty: float = 0.0 # set later, start 0.0
  success_reward: float = 1.0 # or use judge output
  failure_penalty: float = 0.0

Then use the RewardPolicy's parse_penalty for when the parser fails to parse and at the end of the turns when you call judge fn, you use the success_reward if passed or failure_penalty if not. Also remove any defaults for now for those 2.
