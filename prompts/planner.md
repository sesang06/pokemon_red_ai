# Planner Contract

The runtime source of truth is
`src/pokemon_agent/adk_agent/agents/planner/prompt.py`.

The planner emits one bounded `buttons` or current-map world-coordinate `move`
action. Each action is executed exactly once and cannot mark a Goal complete.

```json
{
  "action": {
    "type": "buttons",
    "buttons": ["a", "wait", "a", "wait", "a"],
    "reason": "advance_dialog_three_times"
  },
  "reason": "Send the complete ordered input sequence once, then observe fresh state"
}
```

Repeated button presses and timing pauses must be explicit tokens in the same
ordered `buttons` array. After that array or one `move` completes, the runtime
observes RAM/GameState and asks the Planner for a new action. Separate repetition
control fields, Planner-generated preconditions, and Task objects are not accepted.

Input priority is RAM/GameState, verifier state, previous action outcome,
the current map memory returned by `search_memory(map_name)`, and finally model
inference. Memory is never injected into the Planner JSON and the Planner cannot
save it. The latest screenshot and overlay support interpretation but are not
Goal-success evidence.
