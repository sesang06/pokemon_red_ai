import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Bug,
  BrainCircuit,
  ChevronDown,
  Gamepad2,
  ListFilter,
  MemoryStick,
  Search,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useLiveRuntime } from "./live";
import { getGen1SpriteUrl } from "./sprites";
import type { LiveEvent, LiveState, PartyMember, Point } from "./types";

const EVENT_FILTERS = [
  { value: "ALL", label: "전체" },
  { value: "PLAN", label: "계획" },
  { value: "THINKING", label: "생각" },
  { value: "ACTION", label: "행동" },
  { value: "STATE", label: "상태" },
  { value: "VERIFY", label: "검증" },
  { value: "MEMORY", label: "기억" },
  { value: "ERROR", label: "오류" },
];

export default function App() {
  const { state, events, connection } = useLiveRuntime();
  const [debugMode, setDebugMode] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("ALL");

  const filteredEvents = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return events.filter((event) => {
      const filterMatch = filter === "ALL" || eventGroup(event.type) === filter;
      const queryMatch = !needle || `${event.type} ${event.message} ${event.source}`.toLowerCase().includes(needle);
      return filterMatch && queryMatch;
    });
  }, [events, filter, query]);

  return (
    <div className="app-shell">
      <Header state={state} connection={connection} debugMode={debugMode} setDebugMode={setDebugMode} />
      <main className="workbench">
        <div className="primary-column">
          <GameViewport state={state} />
        </div>
        <aside className="inspector-column" aria-label="실행 상태 점검">
          <Card className="inspector-card">
            <ScrollArea className="inspector-scroll">
              <CardContent className="inspector-content">
                <StatePanel state={state} />
                <ActionPanel state={state} />
                <ThinkingPanel state={state} />
                <MemoryPanel state={state} />
                {debugMode && <DebugPanel state={state} />}
              </CardContent>
            </ScrollArea>
          </Card>
        </aside>
      </main>
      <EventWorkbench
        events={filteredEvents}
        query={query}
        setQuery={setQuery}
        filter={filter}
        setFilter={setFilter}
      />
    </div>
  );
}

function Header({
  state,
  connection,
  debugMode,
  setDebugMode,
}: {
  state: LiveState | null;
  connection: string;
  debugMode: boolean;
  setDebugMode: (value: boolean) => void;
}) {
  const emulatorStatus = state?.emulator.status ?? "waiting";
  const runtime = formatDuration(state?.emulator.frame_index ?? 0, state?.emulator.fps ?? 60);
  return (
    <header className="topbar">
      <div className="brand-block">
        <span className="brand-mark" aria-hidden="true">R</span>
        <div>
          <h1>포켓몬 레드</h1>
          <span>실시간 디버거</span>
        </div>
      </div>
      <div className="header-readouts">
        <Readout label="지도" value={state?.game.map_name ?? "신호 없음"} />
        <Readout label="에뮬레이터" value={statusText(emulatorStatus)} tone={emulatorStatus === "running" ? "ok" : "warn"} />
        <Readout label="실행 시간" value={runtime} />
        <Readout label="연결" value={statusText(connection)} tone={connection === "connected" ? "ok" : "error"} />
      </div>
      <Tabs
        value={debugMode ? "debug" : "live"}
        onValueChange={(value) => setDebugMode(value === "debug")}
        className="mode-tabs"
      >
        <TabsList aria-label="점검 모드">
          <TabsTrigger value="live">실시간</TabsTrigger>
          <TabsTrigger value="debug"><Bug aria-hidden="true" /> 디버그</TabsTrigger>
        </TabsList>
      </Tabs>
    </header>
  );
}

function Readout({ label, value, tone = "neutral" }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`readout ${tone}`}>
      <span>{label}</span>
      <Badge variant="outline" className="readout-value">{value}</Badge>
    </div>
  );
}

export function GameViewport({ state }: { state: LiveState | null }) {
  const screenshotUrl = imageUrl(state?.game.screenshot);
  const overlayUrl = imageUrl(state?.game.overlay);
  return (
    <Card className="panel viewport-panel">
      <PanelHeader icon={<Gamepad2 size={15} />} title="게임 화면" meta={state ? `프레임 ${pad(state.emulator.frame_index, 8)}` : "대기 중"} />
      <div className="viewport-stage viewport-stage-dual">
        <ViewportFeed label="현재 화면" imageUrl={screenshotUrl} alt="실시간 포켓몬 레드 게임 화면" />
        <div className="viewport-side-column">
          <ViewportFeed compact label="충돌 영역 + 월드 좌표" imageUrl={overlayUrl} alt="실시간 충돌 영역과 월드 좌표 오버레이" />
          <PartyPanel state={state} />
        </div>
      </div>
    </Card>
  );
}

function ViewportFeed({
  label,
  imageUrl,
  alt,
  compact = false,
}: {
  label: string;
  imageUrl: string | null;
  alt: string;
  compact?: boolean;
}) {
  return (
    <div className={`viewport-feed${compact ? " viewport-feed-compact" : ""}`}>
      <div className="viewport-feed-label"><span>{label}</span><i aria-hidden="true" /></div>
      <div className="game-frame">
        {imageUrl ? (
          <img src={imageUrl} alt={alt} width={160} height={144} />
        ) : (
          <div className="no-frame"><span>게임 화면 신호 없음</span><small>PokemonSession을 기다리는 중</small></div>
        )}
      </div>
    </div>
  );
}

export function StatePanel({ state }: { state: LiveState | null }) {
  const game = state?.game;
  const cells: Array<[string, unknown]> = [
    ["지도", game?.map_name],
    ["지도 ID", game?.map_id],
    ["현재 좌표", pointText(game?.position)],
    ["바라보는 방향", directionText(game?.facing)],
    ["게임 모드", modeText(game?.mode)],
    ["대화", game?.dialog_open ? "열림" : "닫힘"],
    ["전투", game?.in_battle ? "진행 중" : "없음"],
    ["파티", `${game?.party.length ?? 0} / 6`],
    ["배지", `${game?.badges?.length ?? 0} / 8`],
  ];
  return (
    <InspectorSection title="현재 상태">
      <dl className="state-grid">
        {cells.map(([label, value]) => (
          <div className="state-cell" key={label}>
            <dt>{label}</dt>
            <dd>{displayValue(value)}</dd>
          </div>
        ))}
      </dl>
      {game?.dialog_open && game.dialog_text && (
        <div className="dialog-readout"><Badge variant="outline">대화</Badge>{game.dialog_text}</div>
      )}
    </InspectorSection>
  );
}

function ActionPanel({ state }: { state: LiveState | null }) {
  const action = state?.agent.action;
  const result = state?.agent.result;
  const actionType = String(action?.type ?? "IDLE").toUpperCase();
  const actionValue = actionType === "MOVE" ? pointText(action?.target) : listText(action?.buttons);
  return (
    <InspectorSection title="현재 행동">
      <div className="action-command">
        <Badge variant="secondary">{actionTypeText(actionType)}</Badge>
        <strong>{actionValue}</strong>
      </div>
      <DataRows rows={[
        ["결과", statusText(String(result?.status ?? result?.stop_reason ?? "waiting"))],
        ["이유", action?.reason],
      ]} />
    </InspectorSection>
  );
}

export function ThinkingPanel({ state }: { state: LiveState | null }) {
  const thinking = state?.agent.thinking;
  const hasSummary = Boolean(thinking?.summary);
  return (
    <InspectorSection title="생각 요약" icon={<BrainCircuit size={14} />}>
      {hasSummary ? (
        <div className="thinking-summary">
          <div className="thinking-summary-meta">
            <Badge variant="outline">{agentText(thinking?.agent)}</Badge>
            <Badge variant={thinking?.status === "streaming" ? "secondary" : "outline"}>
              {statusText(thinking?.status ?? "idle")}
            </Badge>
          </div>
          <ScrollArea className="thinking-summary-scroll">
            <p>{thinking?.summary}</p>
          </ScrollArea>
        </div>
      ) : <EmptyLine value="아직 Gemini 생각 요약이 없습니다" />}
    </InspectorSection>
  );
}

export function PartyPanel({ state }: { state: LiveState | null }) {
  const party = state?.game.party ?? [];
  return (
    <section className="party-panel" aria-label="파티 정보">
      <div className="party-panel-header">
        <strong>파티</strong>
        <Badge variant="outline">{party.length} / 6</Badge>
      </div>
      <div className="party-list">
        {party.length ? party.map((member, index) => <PartyRow member={member} key={`${member.species_id}:${index}`} />) : <EmptyLine value="파티 데이터가 없습니다" />}
      </div>
    </section>
  );
}

export function PartyRow({ member }: { member: PartyMember }) {
  const [failed, setFailed] = useState(false);
  const sprite = failed ? null : getGen1SpriteUrl(member.species_id);
  const hpPercent = member.hp !== null && member.max_hp ? Math.max(0, Math.min(100, member.hp / member.max_hp * 100)) : 0;
  return (
    <div className="party-row">
      <div className="sprite-box">
        {sprite ? <img src={sprite} alt={`${member.species} 1세대 스프라이트`} onError={() => setFailed(true)} /> : <span>?</span>}
      </div>
      <div className="party-data">
        <div><strong>{member.nickname || member.species}</strong><span>레벨 {member.level ?? "?"}</span></div>
        <div className="hp-line"><span>체력 {member.hp ?? "?"} / {member.max_hp ?? "?"}</span><span>{member.status || "정상"}</span></div>
        <Progress className="hp-track" value={hpPercent} aria-label={`${member.nickname || member.species} 체력`} />
      </div>
    </div>
  );
}

function MemoryPanel({ state }: { state: LiveState | null }) {
  const recent = state?.memory.recent ?? [];
  const activity = state?.memory.last_activity;
  return (
    <InspectorSection title="장기 기억" icon={<MemoryStick size={14} />}>
      {activity && (
        <div className="memory-activity"><Badge variant="secondary">{memoryActivityText(activity.type)}</Badge>{activity.keys.join(", ")}</div>
      )}
      <div className="memory-list">
        {recent.length ? recent.slice(0, 3).map((item) => (
          <div key={item.key}><strong>{item.key}</strong><span>{formatMemoryValue(item.value)}</span></div>
        )) : <EmptyLine value="기억 활동이 없습니다" />}
      </div>
    </InspectorSection>
  );
}

function DebugPanel({ state }: { state: LiveState | null }) {
  return (
    <InspectorSection title="디버그" icon={<Bug size={14} />}>
      <DebugDisclosure label="상태 변화" value={state?.debug.state_diff} defaultOpen />
      <DebugDisclosure label="행동 결과" value={state?.debug.action_outcome} />
      <DebugDisclosure label="RAM" value={state?.debug.ram} />
      <DebugDisclosure label="화면 정보" value={state?.debug.screenshot_metadata} />
    </InspectorSection>
  );
}

function DebugDisclosure({ label, value, defaultOpen = false }: { label: string; value: unknown; defaultOpen?: boolean }) {
  return (
    <Collapsible defaultOpen={defaultOpen} className="debug-disclosure">
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="debug-trigger">
          {label}<ChevronDown aria-hidden="true" />
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent><JsonBlock value={value} /></CollapsibleContent>
    </Collapsible>
  );
}

function EventWorkbench({
  events,
  query,
  setQuery,
  filter,
  setFilter,
}: {
  events: LiveEvent[];
  query: string;
  setQuery: (value: string) => void;
  filter: string;
  setFilter: (value: string) => void;
}) {
  return (
    <Card className="event-workbench">
      <div className="event-console">
        <div className="event-toolbar">
          <div className="event-title">
            <ListFilter size={15} />
            이벤트 기록
            <Badge variant="secondary">{events.length}</Badge>
          </div>
          <label className="search-control"><Search size={14} /><Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="이벤트 검색" /></label>
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger size="sm" aria-label="이벤트 유형 필터"><SelectValue /></SelectTrigger>
            <SelectContent>{EVENT_FILTERS.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <div className="event-columns event-columns-header"><span>시간</span><span>유형</span><span>메시지</span><span>출처</span></div>
        <ScrollArea className="event-scroll" role="log" aria-live="polite">
          {events.length ? events.map((event) => (
            <div key={event.id} className="event-columns event-row">
              <time>{formatTime(event.timestamp)}</time>
              <Badge variant="outline" className={`event-type ${eventGroup(event.type).toLowerCase()}`} title={event.type}>{eventTypeText(event.type)}</Badge>
              <span title={event.message}>{eventMessageText(event.message)}</span>
              <span title={event.source}>{sourceText(event.source)}</span>
            </div>
          )) : <div className="event-empty">구조화된 실행 이벤트를 기다리는 중입니다.</div>}
        </ScrollArea>
      </div>
    </Card>
  );
}

function PanelHeader({ icon, title, meta, children }: { icon?: ReactNode; title: string; meta?: string; children?: ReactNode }) {
  return (
    <CardHeader className="panel-header">
      <CardTitle>{icon}{title}</CardTitle>
      <CardAction className="panel-header-actions">{meta && <Badge variant="outline">{meta}</Badge>}{children}</CardAction>
    </CardHeader>
  );
}

function InspectorSection({ title, icon, children }: { title: string; icon?: ReactNode; children: ReactNode }) {
  return (
    <section className="inspector-section">
      <h2>{icon}{title}</h2>
      {children}
      <Separator className="inspector-separator" />
    </section>
  );
}

function DataRows({ rows }: { rows: Array<[string, unknown]> }) {
  return <dl className="data-rows">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{displayValue(value)}</dd></div>)}</dl>;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{value ? JSON.stringify(value, null, 2) : "데이터 없음"}</pre>;
}

function EmptyLine({ value }: { value: string }) {
  return <div className="empty-line">{value}</div>;
}

function imageUrl(frame: { format: string; base64: string } | null | undefined): string | null {
  return frame ? `data:image/${frame.format};base64,${frame.base64}` : null;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "--";
  if (typeof value === "boolean") return value ? "예" : "아니요";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function pointText(value: unknown): string {
  if (Array.isArray(value) && value.length >= 2) return `${value[0]},${value[1]}`;
  if (value && typeof value === "object" && "x" in value && "y" in value) {
    const point = value as Point;
    return `${point.x},${point.y}`;
  }
  return "--";
}

function listText(value: unknown): string {
  return Array.isArray(value) ? value.map((button) => buttonText(String(button))).join(" · ") : "--";
}

function formatMemoryValue(value: unknown): string {
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

function buttonText(value: string): string {
  const labels: Record<string, string> = {
    a: "A",
    b: "B",
    start: "START",
    select: "SELECT",
    up: "위",
    down: "아래",
    left: "왼쪽",
    right: "오른쪽",
    wait: "기다리기",
  };
  return labels[value.toLowerCase()] ?? value;
}

function actionTypeText(value: string): string {
  const labels: Record<string, string> = {
    MOVE: "이동",
    BUTTONS: "버튼",
    WAIT: "기다리기",
    IDLE: "대기",
  };
  return labels[value] ?? value;
}

function directionText(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    up: "위",
    down: "아래",
    left: "왼쪽",
    right: "오른쪽",
  };
  return value ? labels[value.toLowerCase()] ?? value : "--";
}

function modeText(value: string | null | undefined): string {
  const labels: Record<string, string> = {
    explore: "탐색",
    overworld: "필드",
    dialog: "대화",
    battle: "전투",
    menu: "메뉴",
    unknown: "알 수 없음",
  };
  return value ? labels[value.toLowerCase()] ?? value : "--";
}

function statusText(value: string): string {
  const labels: Record<string, string> = {
    active: "진행 중",
    complete: "완료",
    connected: "연결됨",
    connecting: "연결 중",
    disconnected: "연결 끊김",
    failed: "실패",
    idle: "대기",
    interrupted_battle: "전투로 중단",
    interrupted_dialog: "대화로 중단",
    interrupted_menu: "메뉴로 중단",
    movement_blocked: "이동 불가",
    movement_progress: "이동 중",
    no_path: "경로 없음",
    planned_path_exhausted: "경로 실행 완료",
    realtime_ticker_stopped: "실시간 틱 중단",
    running: "실행 중",
    single_action_complete: "행동 완료",
    stopped: "중지됨",
    streaming: "생성 중",
    success: "성공",
    target_reached: "목표 도착",
    waiting: "대기 중",
  };
  return labels[value.toLowerCase()] ?? value.replaceAll("_", " ");
}

function agentText(value: "planner" | "interpreter" | null | undefined): string {
  if (value === "planner") return "플래너";
  if (value === "interpreter") return "결과 해석기";
  return "모델";
}

function memoryActivityText(value: string): string {
  const labels: Record<string, string> = {
    read: "읽기",
    search: "검색",
    search_memory: "검색",
    save: "저장",
    save_memory: "저장",
    write: "저장",
  };
  return labels[value.toLowerCase()] ?? value;
}

function eventTypeText(type: string): string {
  const exact: Record<string, string> = {
    THINKING_SUMMARY: "생각 요약",
    PLANNING_ERROR: "계획 오류",
    EXECUTION_ERROR: "실행 오류",
    INTERPRETATION_ERROR: "해석 오류",
  };
  if (exact[type]) return exact[type];
  const labels: Record<string, string> = {
    PLAN: "계획",
    THINKING: "생각",
    ACTION: "행동",
    STATE: "상태",
    VERIFY: "검증",
    MEMORY: "기억",
    ERROR: "오류",
  };
  return labels[eventGroup(type)] ?? type;
}

function eventMessageText(message: string): string {
  if (message.startsWith("Verifier: ")) {
    return `검증: ${statusText(message.slice("Verifier: ".length).replaceAll(" ", "_"))}`;
  }
  const replacements: Array<[RegExp, string]> = [
    [/^Plan MOVE to /, "이동 계획: "],
    [/^Plan BUTTONS /, "버튼 계획: "],
    [/^Executed MOVE to /, "이동 실행: "],
    [/^Executed BUTTONS /, "버튼 실행: "],
    [/^Goal completed: /, "목표 완료: "],
    [/^Action result interpreted$/, "행동 결과 해석 완료"],
    [/^Planner thinking summary$/, "플래너 생각 요약"],
    [/^Interpreter thinking summary$/, "결과 해석기 생각 요약"],
    [/^Dashboard listening at /, "대시보드 실행 중: "],
  ];
  for (const [pattern, replacement] of replacements) {
    if (pattern.test(message)) return message.replace(pattern, replacement);
  }
  const memoryRead = message.match(/^Read (\d+) relevant memory entr(?:y|ies)$/);
  if (memoryRead) return `관련 기억 ${memoryRead[1]}개 읽음`;
  const memoryUpdated = message.match(/^Updated (\d+) memory entr(?:y|ies)$/);
  if (memoryUpdated) return `기억 ${memoryUpdated[1]}개 갱신`;
  return message;
}

function sourceText(source: string): string {
  const labels: Record<string, string> = {
    agent: "에이전트",
    dashboard: "대시보드",
    executor: "실행기",
    game: "게임",
    interpreter: "결과 해석기",
    memory: "기억",
    planner: "플래너",
    verifier: "검증기",
  };
  return labels[source.toLowerCase()] ?? source;
}

function eventGroup(type: string): string {
  if (type.includes("THINKING")) return "THINKING";
  if (type.includes("PLAN") || type.includes("TASK") || type.includes("GOAL")) return "PLAN";
  if (type.includes("ACTION") || type.includes("BUTTON") || type.includes("MOVE")) return "ACTION";
  if (type.includes("VERIFICATION")) return "VERIFY";
  if (type.includes("MEMORY")) return "MEMORY";
  if (type.includes("ERROR") || type.includes("FAILED")) return "ERROR";
  return "STATE";
}

function formatTime(value: string, includeMs = false): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "--:--:--";
  return date.toLocaleTimeString([], {
    hour12: false,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: includeMs ? 3 : undefined,
  });
}

function formatDuration(frames: number, fps: number): string {
  const seconds = Math.floor(frames / Math.max(1, fps));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  const remaining = seconds % 60;
  return `${pad(hours, 2)}:${pad(minutes, 2)}:${pad(remaining, 2)}`;
}

function pad(value: number, width: number): string {
  return String(value).padStart(width, "0");
}
