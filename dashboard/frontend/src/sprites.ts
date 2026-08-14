const GEN1_RED_BLUE_BASE =
  "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/versions/generation-i/red-blue/transparent";

export function getGen1SpriteUrl(speciesId: number | null | undefined): string | null {
  if (!Number.isInteger(speciesId) || speciesId === null || speciesId === undefined) {
    return null;
  }
  if (speciesId < 1 || speciesId > 151) {
    return null;
  }
  return `${GEN1_RED_BLUE_BASE}/${speciesId}.png`;
}
