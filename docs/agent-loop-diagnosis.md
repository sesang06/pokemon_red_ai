# 포켓몬 에이전트 루프 진단 리포트

**작성일**: 2026-08-14
**대상**: `src/pokemon_agent/adk_agent/` 루프 (Planning → Execution → Result Interpretation)
**증상**: 에이전트가 같은 자리를 왕복만 하고, LLM 토큰을 과도하게 소모함
**근거 자료**: `logs/actions/20260814/actions.jsonl` (53턴 실측), 기록된 상태를 재생한 Gemini API 실측 호출 2회

---

## 요약

| 항목 | 실측값 |
|---|---|
| LLM 계획이 실제 채택된 비율 | **0 / 53턴 (0%)** |
| 플래너 호출당 입력 토큰 | **54,008** |
| 그중 `recent_actions`가 차지하는 비중 | **87%** |
| 플래너 응답 종료 사유 | **`MAX_TOKENS` (JSON 중간 절단)** |
| 스키마 완주에 실제로 필요한 출력 토큰 | **1,092** (설정값 1,000) |
| 53턴 1회 실행 추정 입력 토큰 | **약 2.86M (전량 폐기)** |

**한 줄 결론**: LLM 플래너는 매 턴 54k 토큰을 쓰고 호출되지만, 출력 예산(1,000 토큰) 부족으로 JSON이 잘려 파싱에 실패하고 **매번 조용히 룰 기반 플래너로 폴백**된다. 실제로 게임을 조종하는 것은 `step_count % 4` 라운드로빈 방향 선택기이며, 이것이 왕복 현상의 직접 원인이다.

---

## 1. LLM 계획이 100% 버려지고 있다

액션 로그 53턴 전부 `source=rule`, `current_goal=safe_loop`이다. `safe_loop`는 `team.py`의 `_normalize_plan_decision`이 채우는 **기본값**으로, LLM 응답이 단 한 번도 반영되지 않았음을 뜻한다.

```
 19 move [7,2] (1,2)->(4,2)   src=rule  goal=safe_loop
 20 move [9,2] (4,2)->(4,2)   src=rule  goal=safe_loop
 21 move [4,6] (4,2)->(4,2)   src=rule  goal=safe_loop
 22 move [0,2] (4,2)->(2,2)   src=rule  goal=safe_loop
 23 move [6,2] (1,2)->(4,2)   src=rule  goal=safe_loop   ← 무한 왕복
```

---

## 2. 원인: 출력 토큰 예산 부족으로 JSON이 절단됨

기록된 상태를 그대로 재생한 실측 결과:

```
prompt_tokens   : 54008
thoughts_tokens : 764
output_tokens   : 221
finish_reason   : MAX_TOKENS
response text   : '{ "objective": "safe_loop", "current_goal": "Obtain a starter
                    Pokemon from Professor Oak in Oak's Lab.", ...   ← 문자열 도중 절단
```

원인 사슬:

1. `adk_agent/adk_planner.py:29` — `max_output_tokens: int = 1000`
2. `adk_agent/adk_planner.py:31` — `thinking_budget: int | None = -1` (자동). Gemini 2.5 Flash는 **thinking 토큰이 `maxOutputTokens`에 함께 계상**되므로 thinking 764 토큰이 예산을 먼저 잠식한다.
3. `adk_agent/prompts.py`의 `PLANNING_AGENT_PROMPT`가 요구하는 스키마 — 문단형 필드 3개(`decision_rationale`, `session_dialog`, `future_objective`) + `decision_trace` 12개 필드 — 는 **정상 완료 시 1,092 토큰**을 소비한다.
4. → 1,000 예산으로는 thinking을 0으로 만들어도 구조적으로 완주가 불가능하다.
5. 절단된 JSON → `adk_planner.py:261`의 `_parse_json_object`가 `JSONDecodeError`를 잡고 `None` 반환
6. → `team.py`의 `raw_decision = None` → `rule_based_plan` 폴백

---

## 3. 왕복의 정체: 룰 플래너의 방향 라운드로빈

실제 조종자인 `planning.py:204-210`:

```python
candidates = [("right", 1, 0), ("down", 0, 1), ("left", -1, 0), ("up", 0, -1)]
offset = step_count % len(candidates)
```

방문 좌표 기록도, 목표도, 누적 상태도 없이 **우 → 하 → 좌 → 상을 기계적으로 순회**한다. 좁은 실내(Oak's Lab)에서는 이것이 곧바로 `(4,2) → (2,2) → (4,2)` 핑퐁으로 나타난다.

즉 **왕복은 LLM의 판단 실패가 아니라, LLM이 애초에 조종석에 없어서 생긴 결과**다.

---

## 4. 실패가 드러나지 않은 이유 (버그)

`adk_agent/team.py:92-93`:

```python
if plan_error is None:
    plan_error = None if raw_decision is None else "invalid_plan_decision"
```

LLM 응답이 파싱 불가여서 `raw_decision`이 `None`이 되면 **`plan_error`도 `None`으로 남는다.** 완전 실패가 정상 상태로 보고된다. `_parse_json_object` 역시 `JSONDecodeError`를 로그 없이 삼키므로, 런타임 상태·트레이스·액션 로그 어디에도 경고가 남지 않는다.

---

## 5. 토큰 폭식: 스텝당 54k, 그중 87%가 중복 관측 데이터

`compact_state_for_prompt` 출력 실측 분해 (총 187,909자):

| 필드 | 크기(자) | 비중 |
|---|---:|---:|
| **recent_actions** | **162,689** | **87%** |
| visible_world_cells | 5,897 | 3% |
| state | 1,052 | <1% |
| world_map | 1,006 | <1% |
| instruction | 545 | <1% |
| long_term_memory | 719 | <1% |
| 기타 | ~100 | — |

원인 두 가지:

**(a) 히스토리 엔트리에 관측 blob이 통째로 박혀 있다.** 액션 로그 엔트리 평균 크기는 15KB이며 내역은 다음과 같다:

```
result                12,157 bytes  (그중 before_observation 5,978 + after_observation 5,997)
  └ after_observation 내부: visible_world_cells 3,321 / state 1,794 / world_map 640
plan_decision          3,036 bytes
action                    84 bytes
```

`team.py`의 `compact_result`는 `before_observation`/`after_observation`을 남기고, `_compact_observation_for_summary`는 그 안의 `visible_world_cells`를 그대로 둔다. `compact_state_for_prompt`가 `action_history[-5:]`를 프롬프트에 넣으므로 **매 요청에 관측 그리드 10개가 실린다.**

**(b) `indent=2` 직렬화.** `adk_planner.py:147`의 `json.dumps(..., indent=2)`가 80,205자를 187,909자로 **2.3배** 부풀린다.

### 개선 효과 검증

`recent_actions`에서 관측 blob을 제거하고(action / stop_reason / steps_taken / position_after만 유지) `indent`를 없앤 뒤 동일 턴을 재호출한 결과:

```
prompt_tokens : 5601      ← 54,008 대비 90% 감소
thoughts      : 1338
output        : 1092
finish_reason : STOP       ← 정상 완료
current_goal  : "Obtain a starter Pokemon from Professor Oak."
action_request: {'type': 'move', 'target': [4, 3]}
```

토큰이 90% 줄고, 응답이 완주하며, 목표 지향적 행동이 나온다.

> 참고: 프롬프트를 줄여도 출력이 1,092 토큰이므로, **5번(프롬프트 축소)만으로는 부족하고 2번(출력 예산 상향)이 반드시 함께 필요하다.**

---

## 6. 부수적 낭비 및 무효 코드

| 위치 | 문제 |
|---|---|
| `team.py:221`, `history.py:6` | `RAW_HISTORY_TURNS=20`. 히스토리는 매 스텝 1씩 늘고 압축 후 다시 20으로 잘리므로, **21스텝부터 "1턴 압축"을 위해 매 스텝 interpreter LLM이 호출**된다. |
| `prompts.py` `RESULT_INTERPRETER_PROMPT` | 매 호출마다 `history_summary` 전체를 새로 쓰게 지시. 1턴 정보로 누적 요약이 계속 덮어써지며 정보가 유실된다. |
| `loop.py:109-129` | `stuck_score`를 계산하지만 **아무도 소비하지 않는다.** 프롬프트에 숫자로만 실리고, 실제 조종자인 룰 플래너는 무시한다. |
| `history.py:21`, `adk_planner.py:189` | `trim_session_to_recent_turns` / `_strip_prior_media_from_session_service`가 `session_service.sessions` dict를 찾는데 `SqliteSessionService`에는 해당 속성이 없어 **항상 no-op**. |
| `data/adk_sessions.db` | `events` 테이블 0건, `sessions`에 `adk_agent`와 `src.pokemon_agent.adk_agent` 두 app_name이 공존 (CLI/Dev UI 세션 분리). |

---

## 권장 조치 (우선순위순)

| # | 조치 | 위치 | 기대 효과 |
|---|---|---|---|
| 1 | `max_output_tokens` 1,000 → 4,000. 또는 `thinking_budget`을 512로 고정하고 `decision_trace` 스키마를 축소 | `adk_planner.py:29,31` / `prompts.py` | **LLM이 실제로 조종석에 앉는다.** 단독으로 왕복 현상 해소 |
| 2 | `raw_decision is None`일 때도 `plan_error`를 채우고, 폴백 발생 시 경고 로그. `_parse_json_object`에 원문 일부 로깅 | `team.py:92`, `adk_planner.py:261` | 동일 장애의 재발을 즉시 감지 |
| 3 | `recent_actions`용 슬림 엔트리 도입(관측 blob 제거) + `indent` 제거 | `planning.py:306`, `team.py` `compact_result`, `adk_planner.py:147` | 54k → 5.6k 토큰 (90% 절감) |
| 4 | 룰 폴백을 방문 좌표 기록 기반으로 교체하고 `stuck_score`를 실제로 소비 (임계 초과 시 직전 방향 반전 금지, 미방문 프론티어 강제) | `planning.py:187-233`, `loop.py:109` | LLM 실패 시에도 왕복하지 않는 안전망 |
| 5 | interpreter를 매 스텝이 아니라 누적 시에만 호출 (예: 히스토리 30 초과 시 10턴 일괄 압축) | `team.py:221`, `history.py:6` | interpreter LLM 호출 약 1/10로 감소, 요약 품질 개선 |

---

## 재현 방법

```bash
# 1) 프롬프트 크기 분해
.venv/Scripts/python.exe -c "
import json,sys; sys.path.insert(0,'src')
from pokemon_agent.adk_agent.agents.planner.schema import compact_state_for_prompt
rows=[json.loads(l) for l in open('logs/actions/20260814/actions.jsonl',encoding='utf-8')]
state={'objective':'safe_loop','observation':rows[-1]['result']['after_observation'],
       'mode':'overworld','step_count':53,'action_history':rows[-5:]}
p=compact_state_for_prompt(state)
for k,v in p.items(): print(f'{k:28}', len(json.dumps(v,ensure_ascii=False,indent=2)))
"

# 2) 절단 재현 — 기록된 상태를 실제 모델에 재생하여 finish_reason 확인
#    (system_instruction=PLANNING_AGENT_PROMPT, maxOutputTokens=1000, thinkingBudget=-1)
#    → finish_reason: MAX_TOKENS
```
