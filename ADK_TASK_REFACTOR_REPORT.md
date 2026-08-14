# Pokemon Red ADK Task Architecture Refactoring Report

> **Archived design:** The ADK runtime no longer uses this Task layer. The current
> contract is direct `action + repeat_until + max_repeats`; see
> [`docs/architecture.md`](docs/architecture.md). This report is retained only as
> historical implementation context.

> 완료일: 2026-08-14  
> 기준: 현재 작업 트리 및 실제 ROM smoke test  
> 설계 원칙: LLM은 Task를 정하고, Python은 실행하며, RAM/GameState가 성공을 판정한다.

## 1. 변경한 파일

핵심 구현:

- `src/pokemon_agent/adk_agent/tasking.py`: Goal, Task, StateDiff, verifier, executor policy, transition compressor 추가
- `src/pokemon_agent/adk_agent/loop.py`: action 단위 planning loop를 persistent Task loop로 변경
- `src/pokemon_agent/adk_agent/team.py`: Task planner, Python executor, event-driven interpreter, memory consolidator 역할 분리
- `src/pokemon_agent/adk_agent/planning.py`: planner context와 runtime state를 Goal/Task 중심으로 변경
- `src/pokemon_agent/adk_agent/adk_planner.py`: planner 역할을 action 선택에서 Task 생성으로 변경
- `src/pokemon_agent/adk_agent/adk_interpreter.py`: history compressor에서 Task boundary interpreter로 변경
- `src/pokemon_agent/adk_agent/prompts.py`: Task schema와 verifier 권위 명시
- `src/pokemon_agent/adk_agent/agent.py`: Dev UI execution LLM sub-agent 제거
- `src/pokemon_agent/adk_agent/runtime_state.py`: Goal/Task/StateDiff/call count 게시
- `src/pokemon_agent/adk_agent/web_tools.py`: Dev UI Python Task state를 step 사이에 유지
- `src/pokemon_agent/memory/file_memory.py`: `map:<map_name>` 단일 namespace와 맵 단위 원자적 저장
- `src/pokemon_agent/session.py`: warp/menu/item/Pokemon/flag/Pokedex state event 확장

문서 및 테스트:

- `docs/architecture.md`
- `prompts/planner.md`
- `README.md`
- `tests/test_adk_tasking.py`
- `tests/test_adk_loop.py`
- `tests/test_adk_web_tools.py`
- `tests/test_file_memory.py`

## 2. 새 schema

### Goal

```json
{
  "id": "obtain_pokeballs",
  "description": "Obtain at least one Poke Ball.",
  "status": "in_progress",
  "success_conditions": [
    {"path": "inventory.POKE_BALL", "min": 1}
  ],
  "verification": {
    "verified": false,
    "source": "deterministic_game_state"
  }
}
```

### TaskState

```json
{
  "task_id": "complete_oak_event",
  "description": "Complete the Professor Oak event and verify the reward",
  "status": "in_progress",
  "attempts": 1,
  "actions_taken": 3,
  "no_progress_actions": 0,
  "preconditions": [],
  "success_conditions": [
    {"path": "inventory.POKE_BALL", "min": 1}
  ],
  "failure_conditions": ["unexpected_map_change", "player_fainted"],
  "max_actions": 30
}
```

Task status는 `planned`, `in_progress`, `completed`, `failed`, `blocked`, `cancelled`, `unexpected`를 지원한다.

### ActionResult와 StateDiff

```json
{
  "task_id": "complete_oak_event",
  "action": {"type": "move", "target": [7, 5]},
  "before_state": {},
  "after_state": {},
  "state_events": [{"type": "position_changed"}],
  "result": "continue"
}
```

`StateDiff`는 map, position, inventory, party, badges, money, Pokedex, flags, dialog, battle, menu, warp 변화를 구조화한다.

### TaskResult

```json
{
  "action_result": "success",
  "task_result": "continue",
  "state_changes": ["position_changed"],
  "important_event": false,
  "reason": "task_in_progress",
  "goal_progress": 0.0,
  "goal_completed": false
}
```

## 3. Planner 호출 정책

이전에는 매 action마다 planner를 호출했다. 현재는 다음 경우에만 호출한다.

- 활성 Task가 없음
- Task가 `completed`, `failed`, `blocked`, `cancelled`, `unexpected`
- runtime이 명시적으로 `replan_required=true`

Task가 `in_progress`이면 동일 Task를 유지한 채 Python executor가 다음 action을 선택한다. `--task-no-progress-limit` 기본값은 10이며, 이 횟수 동안 의미 있는 상태 변화가 없으면 Task를 `blocked`로 만들고 다음 loop에서 재계획한다.

100 action을 수행하는 하나의 Task 테스트에서 planner 호출은 정확히 1회였다.

## 4. Task lifecycle

```text
planned
  -> in_progress
       -> continue -> 같은 Task로 다음 action
       -> completed -> result interpretation/memory -> planner
       -> failed -> failure memory -> planner
       -> blocked -> failure memory -> planner
       -> unexpected -> event interpretation -> planner
```

Execution은 Google ADK agent가 아니라 Python이다. 현재 GameState와 Task를 보고 기존 계약 중 하나를 선택한다.

```json
{"type":"buttons","buttons":["a","wait"]}
{"type":"move","target":[9,3]}
```

`move.target`은 현재 map의 world coordinate다. collision, walk cell, Dijkstra 변환은 기존 `PokemonSession`과 navigation 계층에 남아 있다.

## 5. Goal verification

Goal과 Task 성공은 LLM 문장이 아니라 `tasking.py`의 deterministic condition evaluator가 판정한다.

지원 증거:

- inventory item count
- party와 HP
- Pokedex count/flag
- badges, money
- map과 position
- event flags
- dialog, battle, menu
- warp와 state event
- MCP action result
- Task action/no-progress counter

`obtain_pokeballs`의 성공 조건은 다음 하나다.

```json
{"path":"inventory.POKE_BALL","min":1}
```

Oak dialog가 열리거나 닫혀도 inventory가 0이면 Goal은 완료되지 않는다.

## 6. Memory 구조

저장 파일과 원자적 쓰기 방식은 `data/long_term_memory.json` 그대로 유지한다. 현재 지원 key는 맵 단위 하나뿐이다.

```text
map:<map_name>
```

- Planner는 `search_memory(map_name)` 도구로 현재 맵의 단일 항목을 읽는다.
- Result Interpreter는 먼저 `search_memory(map_name)`를 호출하고, 갱신할 맵 지식이 있을 때만 `save_memory(map_name, value)`를 호출한다.
- 두 도구 모두 임의 key 인자를 받지 않으며 저장 key는 항상 `map:<map_name>`이다.

별도 Memory Consolidator와 namespace 후보 후처리는 제거했다. ADK 도구 호출이 실제 파일 읽기와 쓰기를 담당한다.

Raw action은 날짜별 JSONL과 ADK SQLite에 남는다. 모델용 short-term context는 raw turn 나열 대신 최근 `transition_history` 20개와 deterministic overflow summary를 사용한다. 숫자 20에 도달했다는 이유만으로 LLM을 호출하지 않는다.

## 7. LLM 호출 지점과 정책

### CLI Task Planner

실제 네트워크 호출 지점:

```text
src/pokemon_agent/adk_agent/adk_planner.py
GoogleAdkPlanner.plan_async() -> Runner.run_async()
```

호출 조건은 Task planning이 필요한 경우다. 입력은 Goal, current/last Task, compact GameState, state events, recent transitions, relevant memory, failure memory, story dependencies, 최신 screenshot/overlay다.

### CLI Result Interpreter

실제 네트워크 호출 지점:

```text
src/pokemon_agent/adk_agent/adk_interpreter.py
GoogleAdkResultInterpreter.summarize_async() -> Runner.run_async()
```

호출 조건:

- Task completed/failed/blocked/unexpected
- Goal completed/failed
- major map transition
- important item/Pokemon/flag 변화
- 반복 실패

`task_result=continue`이고 durable event가 없으면 호출하지 않는다.

### LLM을 호출하지 않는 부분

- Python Task Executor
- observe와 RAM parsing
- StateDiff 생성
- Task/Goal verifier
- Dijkstra navigation
- MCP action execution
- transition history compression
- map-scoped `search_memory`/`save_memory` 도구

### ADK Dev UI

root `pokemon_red_team`, planning sub-agent, result interpreter sub-agent는 Dev UI 메시지/위임 시 ADK runtime을 통해 LLM을 호출할 수 있다. execution sub-agent는 제거했다. Dev UI의 `run_team_step` 내부 실행은 Python Task loop이며 별도 Gemini 호출을 만들지 않는다.

## 8. 제거 또는 정리한 코드

- planner의 `buttons`/`move` 직접 출력 계약 제거
- planner의 `action_request`/직접 action을 1회성 Task로 변환하던 legacy compatibility 제거
- 사용되지 않던 `planning.plan_next_action()` action-planning adapter 제거
- action 중심 `_normalize_plan_decision`과 관련 fallback helper 제거
- 21번째 이후 매 step 호출되던 history-based interpreter 경로 제거
- `agent.py`의 중복 prompt literal 제거, `prompts.py`를 단일 source of truth로 변경
- Dev UI의 Gemini execution sub-agent 제거
- 오래된 `docs/architecture.md`와 `prompts/planner.md` 갱신

`app.py`와 초기 `agent/` 계층은 수동/초기 실행 테스트에서 여전히 사용되므로 삭제하지 않았다. 기존 `PokemonSession`, MCP, RAM reader, Qt UI, pathfinding도 보존했다.

## 9. 테스트 결과

전체 테스트:

```text
109 passed, 4 warnings
```

warning 4건은 Google ADK `BaseAgentConfig` deprecation이다.

새 테스트는 다음을 검증한다.

- Goal -> Task 생성
- Task가 여러 action 동안 유지되고 planner가 중간에 호출되지 않음
- 100 action Task에서 planner 1회
- RAM inventory Poke Ball 0 -> 5로 바뀔 때만 Goal/Task 완료
- Oak dialog open + inventory 0인 false success 방지
- structured StateDiff의 dialog/item event
- map-scoped memory tool read/write
- task boundary에서만 interpreter 호출
- `map:<map_name>` key round trip
- 방향성 장애물 학습과 재계획
- 화면 밖/도달 불가 목표가 현재 칸을 `target_reached`로 오인하지 않음
- Route 22 왕복 waypoint와 Pallet Town 연구소 입구 판정
- Parcel/Pokedex flag 조합이 완료된 Oak 대화를 반복하지 않음

## 10. 실제 ROM smoke test

`states/fixed_start.state`를 덮어쓰지 않고 disposable smoke checkpoint를 사용해 실제 ROM을 구간별로 끝까지 실행했다. 전체 smoke는 `--no-adk-model`과 동일한 구성으로 Python Task executor, RAM observer, deterministic verifier만 사용했다.

실제로 통과한 단계:

```text
fixed Oak's Lab state
-> Bulbasaur 선택
-> Oak's Lab 첫 라이벌 전투 완료
-> Viridian Mart에서 Oak's Parcel 획득
-> Oak에게 Parcel 전달 및 Pokedex 수령
-> Route 22 첫 라이벌 전투 완료
-> Oak's Lab 귀환
-> Poke Ball 5개 수령
```

최종 관찰과 verifier 결과:

```text
termination_reason=goal_completed
Goal status=completed
Goal verification source=deterministic_game_state
inventory.POKE_BALL=5
map=Oak's Lab
position=(5,3)
got_pokedex=true
beat_route22_rival_1st_battle=true
got_pokeballs_from_oak=true
received_pokeballs=true
llm_planner_call_count=0
interpreter_call_count=0
```

마지막 귀환/수령 구간은 17 action과 planner 2회로 끝났다. 대화가 열리거나 닫힌 사실만으로 성공 처리하지 않았고, RAM inventory가 실제로 0에서 5로 바뀐 뒤에만 Goal을 완료했다.

Smoke 중 발견해 수정한 실제 결함:

- 영구 유지되는 `got_oaks_parcel` flag가 `got_pokedex` 이후에도 Parcel 전달 Task를 반복하던 조건 순서
- 이동 뒤 12프레임에 좌표를 읽어 보행 완료 전 `movement_blocked`로 오인하던 timing
- Route 22 장벽의 진입/귀환 우회 waypoint 누락
- collision nearest 보정이 현재 칸을 가짜 `target_reached`로 반환하던 경우
- Pallet Town `x=12` 전체를 연구소 입구로 간주하던 과도한 범위

Smoke는 디버깅 중 발견한 결함을 고치며 checkpoint를 이어 실행한 검증이다. 최종 코드 경로는 모든 단계에서 실제 PyBoy/RAM 상태를 사용했으며 fixed state 원본은 변경하지 않았다.

## 11. 남은 문제

1. ADK CLI와 ADK Dev UI는 같은 live PyBoy 인스턴스를 공유하지 않는다. 단일 원격 MCP/PyBoy 서비스화는 별도 작업이다.
2. Google ADK `BaseAgentConfig` deprecation warning을 제거하려면 향후 ADK API 변경에 맞춘 migration이 필요하다.
3. Gemini 429에 대한 RetryInfo 기반 backoff와 호출 token 측정은 아직 없다. 이번 Task 단위 호출 정책으로 호출량 자체는 크게 줄었다.
4. Route 22 왕복은 현재 알려진 맵 구조에 맞춘 world waypoint를 사용한다. 더 일반적인 장거리 map graph/path planner는 후속 개선 대상이다.
5. 전투 resolver는 안전한 `A` 중심 규칙이라 Sand-Attack 같은 장기전에서 느릴 수 있다. 전투 RAM cursor와 기술 효과를 이용한 별도 deterministic battle policy가 필요하다.
