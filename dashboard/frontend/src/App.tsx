import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  Bug,
  Gamepad2,
  Layers3,
  ListFilter,
  MemoryStick,
  Search,
  Waypoints,
} from "lucide-react";
import { useLiveRuntime } from "./live";
import { getGen1SpriteUrl } from "./sprites";
import type { LiveEvent, LiveState, PartyMember, PipelineStatus, Point } from "./types";

const EVENT_FILTERS = ["ALL", "PLAN", "ACTION", "STATE", "VERIFY", "MEMORY", "ERROR"];

export default function App() {
  const { state, events, connection } = useLiveRuntime();
  const [showOverlay, setShowOverlay] = useState(false);
  const [debugMode, setDebugMode] = useState(false);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("ALL");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const filteredEvents = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return events.filter((event) => {
      const filterMatch = filter === "ALL" || eventGroup(event.type) === filter;
      const queryMatch = !needle || `${event.type} ${event.message} ${event.source}`.toLowerCase().includes(needle);
      return filterMatch && queryMatch;
    });
  }, [events, filter, query]);

  useEffect(() => {
    if (!filteredEvents.length) {
      if (selectedId !== null) setSelectedId(null);
      return;
    }
    if (selectedId === null || !filteredEvents.some((event) => event.id === selectedId)) {
      setSelectedId(filteredEvents[filteredEvents.length - 1].id);
    }
  }, [filteredEvents, selectedId]);

  const selectedEvent = events.find((event) => event.id === selectedId) ?? null;

  return (
    <div className="app-shell">
      <Header state={state} connection={connection} debugMode={debugMode} setDebugMode={setDebugMode} />
      <main className="workbench">
        <div className="primary-column">
          <GameViewport state={state} showOverlay={showOverlay} setShowOverlay={setShowOverlay} />
          <div className="lower-inspectors">
            <WorldMapPanel state={state} />
            <PartyPanel state={state} />
            <InventoryPanel state={state} />
          </div>
        </div>
        <aside className="inspector-column" aria-label="Runtime inspector">
          <StatePanel state={state} />
          <TaskPanel state={state} />
          <ActionPanel state={state} />
          <PipelinePanel state={state} />
          <MemoryPanel state={state} />
          {debugMode && <DebugPanel state={state} />}
        </aside>
      </main>
      <EventWorkbench
        events={filteredEvents}
        selectedEvent={selectedEvent}
        selectedId={selectedId}
        onSelect={setSelectedId}
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
          <h1>POKEMON RED</h1>
          <span>RUNTIME DEBUGGER</span>
        </div>
      </div>
      <div className="header-readouts">
        <Readout label="MAP" value={state?.game.map_name ?? "NO SIGNAL"} />
        <Readout label="EMULATOR" value={emulatorStatus.toUpperCase()} tone={emulatorStatus === "running" ? "ok" : "warn"} />
        <Readout label="RUNTIME" value={runtime} />
        <Readout label="LINK" value={connection.toUpperCase()} tone={connection === "connected" ? "ok" : "error"} />
      </div>
      <div className="mode-control" aria-label="Inspector mode">
        <button className={!debugMode ? "active" : ""} onClick={() => setDebugMode(false)}>LIVE</button>
        <button className={debugMode ? "active" : ""} onClick={() => setDebugMode(true)}>
          <Bug size={14} aria-hidden="true" /> DEBUG
        </button>
      </div>
    </header>
  );
}

function Readout({ label, value, tone = "neutral" }: { label: string; value: string; tone?: string }) {
  return (
    <div className={`readout ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function GameViewport({
  state,
  showOverlay,
  setShowOverlay,
}: {
  state: LiveState | null;
  showOverlay: boolean;
  setShowOverlay: (value: boolean) => void;
}) {
  const frame = showOverlay ? state?.game.overlay : state?.game.screenshot;
  const imageUrl = frame ? `data:image/${frame.format};base64,${frame.base64}` : null;
  return (
    <section className="panel viewport-panel">
      <PanelHeader icon={<Gamepad2 size={15} />} title="GAME" meta={state ? `FRAME ${pad(state.emulator.frame_index, 8)}` : "WAITING"}>
        <button
          className={`icon-command ${showOverlay ? "selected" : ""}`}
          onClick={() => setShowOverlay(!showOverlay)}
          aria-pressed={showOverlay}
          title="Toggle collision and coordinate overlay"
        >
          <Layers3 size={16} aria-hidden="true" />
          <span>OVERLAY</span>
        </button>
      </PanelHeader>
      <div className="viewport-stage">
        <div className="viewport-ruler ruler-x"><span>00</span><span>08</span><span>16</span><span>20</span></div>
        <div className="viewport-ruler ruler-y"><span>00</span><span>06</span><span>12</span><span>18</span></div>
        <div className="game-frame">
          {imageUrl ? (
            <img src={imageUrl} alt="Live Pokemon Red PyBoy frame" width={160} height={144} />
          ) : (
            <div className="no-frame"><span>NO VIDEO SIGNAL</span><small>Waiting for PokemonSession</small></div>
          )}
        </div>
      </div>
      <div className="viewport-footer">
        <span>160 x 144</span>
        <span>NEAREST-NEIGHBOR</span>
        <span>{showOverlay ? "COLLISION OVERLAY" : "RAW FRAME"}</span>
      </div>
    </section>
  );
}

function StatePanel({ state }: { state: LiveState | null }) {
  const game = state?.game;
  return (
    <InspectorSection title="STATE">
      <DataRows rows={[
        ["MAP", game?.map_name],
        ["MAP ID", game?.map_id],
        ["POSITION", pointText(game?.position)],
        ["FACING", game?.facing],
        ["MODE", game?.mode],
        ["DIALOG", game?.dialog_open ? "OPEN" : "CLOSED"],
        ["BATTLE", game?.in_battle ? "ACTIVE" : "NONE"],
      ]} />
      {game?.dialog_open && game.dialog_text && <div className="dialog-readout">{game.dialog_text}</div>}
    </InspectorSection>
  );
}

function TaskPanel({ state }: { state: LiveState | null }) {
  const task = state?.agent.task;
  return (
    <InspectorSection title="TASK" accent>
      {task ? (
        <>
          <div className="task-name">{task.description || task.id}</div>
          <DataRows rows={[
            ["STATUS", task.status],
            ["ATTEMPT", task.attempt],
            ["STEP", task.max_steps ? `${task.step} / ${task.max_steps}` : task.step],
          ]} />
        </>
      ) : <EmptyLine value="No active task" />}
    </InspectorSection>
  );
}

function ActionPanel({ state }: { state: LiveState | null }) {
  const action = state?.agent.action;
  const result = state?.agent.result;
  const actionType = String(action?.type ?? "IDLE").toUpperCase();
  const actionValue = actionType === "MOVE" ? pointText(action?.target) : listText(action?.buttons);
  return (
    <InspectorSection title="ACTION">
      <div className="action-command"><span>{actionType}</span><strong>{actionValue}</strong></div>
      <DataRows rows={[
        ["RESULT", result?.status ?? result?.stop_reason ?? "WAITING"],
        ["REASON", action?.reason],
      ]} />
    </InspectorSection>
  );
}

function PipelinePanel({ state }: { state: LiveState | null }) {
  const pipeline = state?.agent.pipeline ?? {};
  const stages = ["planner", "executor", "verifier", "interpreter", "memory"];
  return (
    <InspectorSection title="PIPELINE">
      <div className="pipeline">
        {stages.map((stage) => <PipelineStage key={stage} name={stage} status={pipeline[stage] ?? "idle"} />)}
      </div>
      <div className="metric-strip">
        <span>P <b>{state?.agent.planner_calls ?? 0}</b></span>
        <span>E <b>{state?.agent.executor_actions ?? 0}</b></span>
        <span>I <b>{state?.agent.interpreter_calls ?? 0}</b></span>
      </div>
    </InspectorSection>
  );
}

function PipelineStage({ name, status }: { name: string; status: PipelineStatus }) {
  return (
    <div className={`pipeline-stage ${status}`}>
      <span className="pipeline-symbol">{status === "complete" ? "✓" : status === "active" ? "●" : "○"}</span>
      <span>{name.toUpperCase()}</span>
    </div>
  );
}

function WorldMapPanel({ state }: { state: LiveState | null }) {
  const rows = state?.navigation.visible_cells ?? [];
  const player = state?.navigation.player;
  const target = state?.navigation.target;
  const path = new Set((state?.navigation.path ?? []).map(pointKey));
  const hasCells = rows.some((row) => row.length > 0);
  return (
    <section className="panel compact-panel map-panel">
      <PanelHeader icon={<Waypoints size={15} />} title="WORLD MAP" meta={state?.game.map_name ?? "NO MAP"} />
      {hasCells ? (
        <div className="world-grid" style={{ gridTemplateColumns: `repeat(${Math.max(...rows.map((row) => row.length))}, 1fr)` }}>
          {rows.flat().map((cell) => {
            const isPlayer = samePoint(cell, player);
            const isTarget = samePoint(cell, target);
            const onPath = path.has(pointKey(cell));
            return (
              <div
                key={`${cell.x}:${cell.y}`}
                className={`world-cell ${cell.walkable ? "walkable" : "blocked"} ${onPath ? "path" : ""} ${isPlayer ? "player" : ""} ${isTarget ? "target" : ""}`}
                title={`World (${cell.x}, ${cell.y}) ${cell.walkable ? "walkable" : "blocked"}`}
              >
                {isPlayer ? "P" : isTarget ? "T" : onPath ? "·" : ""}
              </div>
            );
          })}
        </div>
      ) : <EmptyLine value="Map data unavailable" />}
      <div className="map-legend"><span><i className="player-key" /> PLAYER</span><span><i className="target-key" /> TARGET</span><span><i className="path-key" /> PATH</span></div>
    </section>
  );
}

function PartyPanel({ state }: { state: LiveState | null }) {
  const party = state?.game.party ?? [];
  return (
    <section className="panel compact-panel party-panel">
      <PanelHeader title="PARTY" meta={`${party.length} / 6`} />
      <div className="party-list">
        {party.length ? party.map((member, index) => <PartyRow member={member} key={`${member.species_id}:${index}`} />) : <EmptyLine value="Party data unavailable" />}
      </div>
      <div className="sprite-credit">SPRITES: POKEAPI GEN I RED/BLUE</div>
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
        {sprite ? <img src={sprite} alt={`${member.species} Generation I sprite`} onError={() => setFailed(true)} /> : <span>?</span>}
      </div>
      <div className="party-data">
        <div><strong>{member.nickname || member.species}</strong><span>Lv {member.level ?? "?"}</span></div>
        <div className="hp-line"><span>HP {member.hp ?? "?"} / {member.max_hp ?? "?"}</span><span>{member.status || "OK"}</span></div>
        <div className="hp-track"><i style={{ width: `${hpPercent}%` }} /></div>
      </div>
    </div>
  );
}

function InventoryPanel({ state }: { state: LiveState | null }) {
  const items = state?.game.items ?? [];
  return (
    <section className="panel compact-panel inventory-panel">
      <PanelHeader title="INVENTORY" meta={`${items.length} SLOTS`} />
      <div className="inventory-list">
        {items.length ? items.map((item, index) => (
          <div className="inventory-row" key={`${item.item_id}:${index}`}><span>{item.name}</span><strong>x{item.quantity}</strong></div>
        )) : <EmptyLine value="No items read" />}
      </div>
    </section>
  );
}

function MemoryPanel({ state }: { state: LiveState | null }) {
  const recent = state?.memory.recent ?? [];
  const activity = state?.memory.last_activity;
  return (
    <InspectorSection title="MEMORY" icon={<MemoryStick size={14} />}>
      {activity && <div className="memory-activity"><span>{activity.type.toUpperCase()}</span>{activity.keys.join(", ")}</div>}
      <div className="memory-list">
        {recent.length ? recent.slice(0, 3).map((item) => (
          <div key={item.key}><strong>{item.key}</strong><span>{formatMemoryValue(item.value)}</span></div>
        )) : <EmptyLine value="No memory activity" />}
      </div>
    </InspectorSection>
  );
}

function DebugPanel({ state }: { state: LiveState | null }) {
  return (
    <InspectorSection title="DEBUG" icon={<Bug size={14} />}>
      <details open><summary>STATE DIFF</summary><JsonBlock value={state?.debug.state_diff} /></details>
      <details><summary>ACTION OUTCOME</summary><JsonBlock value={state?.debug.action_outcome} /></details>
      <details><summary>RAM</summary><JsonBlock value={state?.debug.ram} /></details>
      <details><summary>SCREEN</summary><JsonBlock value={state?.debug.screenshot_metadata} /></details>
    </InspectorSection>
  );
}

function EventWorkbench({
  events,
  selectedEvent,
  selectedId,
  onSelect,
  query,
  setQuery,
  filter,
  setFilter,
}: {
  events: LiveEvent[];
  selectedEvent: LiveEvent | null;
  selectedId: number | null;
  onSelect: (id: number) => void;
  query: string;
  setQuery: (value: string) => void;
  filter: string;
  setFilter: (value: string) => void;
}) {
  return (
    <section className="event-workbench">
      <div className="event-console">
        <div className="event-toolbar">
          <div className="event-title"><ListFilter size={15} /> EVENT LOG <span>{events.length}</span></div>
          <label className="search-control"><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter events" /></label>
          <select value={filter} onChange={(event) => setFilter(event.target.value)} aria-label="Event type filter">
            {EVENT_FILTERS.map((value) => <option key={value}>{value}</option>)}
          </select>
        </div>
        <div className="event-columns event-columns-header"><span>TIME</span><span>TYPE</span><span>MESSAGE</span><span>SOURCE</span></div>
        <div className="event-scroll" role="log" aria-live="polite">
          {events.length ? events.map((event) => (
            <button key={event.id} className={`event-columns event-row ${selectedId === event.id ? "selected" : ""}`} onClick={() => onSelect(event.id)}>
              <time>{formatTime(event.timestamp)}</time>
              <span className={`event-type ${eventGroup(event.type).toLowerCase()}`}>{event.type}</span>
              <span>{event.message}</span>
              <span>{event.source}</span>
            </button>
          )) : <div className="event-empty">Waiting for structured runtime events.</div>}
        </div>
      </div>
      <div className="event-detail">
        <div className="detail-header"><span>EVENT DETAIL</span>{selectedEvent && <strong>#{pad(selectedEvent.id, 4)}</strong>}</div>
        {selectedEvent ? (
          <>
            <DataRows rows={[
              ["TIMESTAMP", formatTime(selectedEvent.timestamp, true)],
              ["TYPE", selectedEvent.type],
              ["SOURCE", selectedEvent.source],
              ["MESSAGE", selectedEvent.message],
            ]} />
            <JsonBlock value={selectedEvent.payload} />
          </>
        ) : <EmptyLine value="Select an event" />}
      </div>
    </section>
  );
}

function PanelHeader({ icon, title, meta, children }: { icon?: ReactNode; title: string; meta?: string; children?: ReactNode }) {
  return (
    <div className="panel-header">
      <div>{icon}{title}</div>
      <div className="panel-header-actions">{meta && <span>{meta}</span>}{children}</div>
    </div>
  );
}

function InspectorSection({ title, icon, accent = false, children }: { title: string; icon?: ReactNode; accent?: boolean; children: ReactNode }) {
  return (
    <section className={`inspector-section ${accent ? "accent" : ""}`}>
      <h2>{icon}{title}</h2>
      {children}
    </section>
  );
}

function DataRows({ rows }: { rows: Array<[string, unknown]> }) {
  return <dl className="data-rows">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{displayValue(value)}</dd></div>)}</dl>;
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{value ? JSON.stringify(value, null, 2) : "NO DATA"}</pre>;
}

function EmptyLine({ value }: { value: string }) {
  return <div className="empty-line">{value}</div>;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "--";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value).toUpperCase();
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
  return Array.isArray(value) ? value.map(String).join(" ").toUpperCase() : "--";
}

function formatMemoryValue(value: unknown): string {
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

function pointKey(value: Point): string {
  return `${value.x}:${value.y}`;
}

function samePoint(a: Point | null | undefined, b: Point | null | undefined): boolean {
  return Boolean(a && b && a.x === b.x && a.y === b.y);
}

function eventGroup(type: string): string {
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
