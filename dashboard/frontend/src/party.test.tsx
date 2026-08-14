import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PartyPanel, PartyRow } from "./App";
import type { LiveState, PartyMember } from "./types";

function member(species: string, speciesId: number, level: number, hp: number): PartyMember {
  return {
    species,
    species_id: speciesId,
    internal_species_id: null,
    nickname: null,
    level,
    hp,
    max_hp: hp,
    status: "OK",
    types: [],
  };
}

describe("PartyRow", () => {
  it("does not render the external sprite credit", () => {
    const html = renderToStaticMarkup(<PartyPanel state={null} />);

    expect(html).not.toContain("PokeAPI");
    expect(html).not.toContain("스프라이트:");
  });

  it("renders each party member from its own synchronized state", () => {
    const html = renderToStaticMarkup(
      <>
        <PartyRow member={member("Bulbasaur", 1, 5, 20)} />
        <PartyRow member={member("Charmander", 4, 6, 23)} />
      </>,
    );

    expect(html).toContain("transparent/1.png");
    expect(html).toContain("transparent/4.png");
    expect(html).toContain("Bulbasaur");
    expect(html).toContain("Charmander");
    expect(html).toContain("체력 20 / 20");
    expect(html).toContain("체력 23 / 23");
  });

  it("renders all six party members without an internal scroll viewport", () => {
    const party = [
      member("Bulbasaur", 1, 5, 20),
      member("Ivysaur", 2, 16, 45),
      member("Venusaur", 3, 32, 80),
      member("Charmander", 4, 6, 23),
      member("Charmeleon", 5, 18, 48),
      member("Charizard", 6, 36, 95),
    ];
    const state = { game: { party } } as unknown as LiveState;

    const html = renderToStaticMarkup(<PartyPanel state={state} />);

    expect(html.match(/class="party-row"/g)).toHaveLength(6);
    expect(html).not.toContain("data-radix-scroll-area-viewport");
    expect(html).toContain("Charizard");
  });

  it("renders a neutral fallback for an invalid species id", () => {
    const html = renderToStaticMarkup(<PartyRow member={member("UNKNOWN", 999, 0, 0)} />);

    expect(html).not.toContain("<img");
    expect(html).toContain(">?</span>");
    expect(html).toContain("UNKNOWN");
  });
});
