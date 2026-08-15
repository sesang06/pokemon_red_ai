import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GameViewport, Header, StatePanel, ThinkingPanel } from "./App";
import type { LiveState } from "./types";


describe("GameViewport", () => {
  it("uses shadcn header primitives and shows current and maximum steps", () => {
    const state = {
      emulator: { status: "running", frame_index: 600, fps: 60 },
      game: { map_name: "Oak's Lab" },
      agent: { current_step: 12, max_steps: 100 },
    } as unknown as LiveState;

    const markup = renderToStaticMarkup(
      <Header state={state} connection="connected" debugMode={false} setDebugMode={() => undefined} />,
    );

    expect(markup).toContain('data-slot="card"');
    expect(markup).toContain('data-slot="card-header"');
    expect(markup).toContain('data-slot="card-title"');
    expect(markup).toContain('data-slot="card-description"');
    expect(markup).toContain('data-slot="card-content"');
    expect(markup).toContain('data-slot="card-footer"');
    expect(markup).toContain("12 / 100");
  });

  it("renders the live frame and collision overlay at the same time", () => {
    const state = {
      emulator: { frame_index: 42 },
      game: {
        screenshot: { format: "png", base64: "live-frame" },
        overlay: { format: "png", base64: "overlay-frame" },
      },
    } as unknown as LiveState;

    const markup = renderToStaticMarkup(<GameViewport state={state} />);

    expect(markup).toContain("data:image/png;base64,live-frame");
    expect(markup).toContain("data:image/png;base64,overlay-frame");
    expect(markup).toContain("현재 화면");
    expect(markup).toContain("충돌 영역 + 월드 좌표");
    expect(markup).toContain("viewport-feed-compact");
    expect(markup).toContain("viewport-side-column");
    expect(markup).toContain("파티 정보");
    expect(markup.indexOf("충돌 영역 + 월드 좌표")).toBeLessThan(markup.indexOf("파티 정보"));
    expect(markup).not.toContain("게임 160 × 144");
    expect(markup).not.toContain("오버레이 640 × 576");
    expect(markup).not.toContain("동시 화면");
    expect(markup.match(/<img/g)).toHaveLength(2);
  });

  it("renders a compact current state summary", () => {
    const state = {
      game: {
        map_name: "Oak's Lab",
        map_id: 40,
        position: { x: 9, y: 5 },
        facing: "up",
        mode: "explore",
        dialog_open: false,
        in_battle: false,
        party: [],
        items: [],
      },
    } as unknown as LiveState;

    const markup = renderToStaticMarkup(<StatePanel state={state} />);

    expect(markup.match(/class="state-cell(?: state-cell-wide)?"/g)).toHaveLength(5);
    expect(markup).toContain("현재 상태");
    expect(markup).toContain("방향");
    expect(markup).toContain("배지");
    expect(markup).toContain("탐색");
    expect(markup).not.toContain("파티");
    expect(markup).not.toContain(">ID<");
    expect(markup).not.toContain("가방");
  });

  it("renders the latest Gemini thinking summary", () => {
    const state = {
      agent: {
        thinking: {
          agent: "planner",
          status: "streaming",
          summary: "The current target is reachable within the bounded path.",
          updated_at: "2026-08-15T00:00:00Z",
        },
      },
    } as LiveState;

    const markup = renderToStaticMarkup(<ThinkingPanel state={state} />);

    expect(markup).toContain("생각 요약");
    expect(markup).toContain("플래너");
    expect(markup).toContain("생성 중");
    expect(markup).toContain("The current target is reachable within the bounded path.");
  });
});
