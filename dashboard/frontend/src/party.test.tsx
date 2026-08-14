import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { PartyRow } from "./App";
import type { PartyMember } from "./types";

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
    expect(html).toContain("HP 20 / 20");
    expect(html).toContain("HP 23 / 23");
  });

  it("renders a neutral fallback for an invalid species id", () => {
    const html = renderToStaticMarkup(<PartyRow member={member("UNKNOWN", 999, 0, 0)} />);

    expect(html).not.toContain("<img");
    expect(html).toContain(">?</span>");
    expect(html).toContain("UNKNOWN");
  });
});
