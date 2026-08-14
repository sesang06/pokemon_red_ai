import { describe, expect, it } from "vitest";
import { getGen1SpriteUrl } from "./sprites";

describe("getGen1SpriteUrl", () => {
  it("resolves a Generation I Red/Blue sprite directly from species id", () => {
    expect(getGen1SpriteUrl(4)).toBe(
      "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-i/red-blue/transparent/4.png",
    );
  });

  it("returns null for missing or invalid species ids", () => {
    expect(getGen1SpriteUrl(null)).toBeNull();
    expect(getGen1SpriteUrl(0)).toBeNull();
    expect(getGen1SpriteUrl(152)).toBeNull();
  });
});
