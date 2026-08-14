# Planner Contract

The runtime source of truth is
`src/pokemon_agent/adk_agent/agents/planner/prompt.py`.

The planner emits one bounded `buttons` or current-map world-coordinate `move`
action. It may request bounded repetition but cannot mark a Goal complete.

```json
{
  "action": {
    "type": "buttons",
    "buttons": ["a", "wait"],
    "reason": "advance_dialog"
  },
  "repeat_until": {"path": "dialog_open", "equals": false},
  "max_repeats": 8,
  "reason": "Advance the current dialog until RAM reports that it closed"
}
```

`repeat_until` supports exactly one of `equals`, `min`, `max`, or `contains`.
Without it, `max_repeats` is forced to 1. Planner-generated preconditions and
Task objects are not accepted.

Input priority is RAM/GameState, verifier state, previous action outcome,
relevant memory, and finally model inference. The latest screenshot and overlay
support interpretation but are not Goal-success evidence.
