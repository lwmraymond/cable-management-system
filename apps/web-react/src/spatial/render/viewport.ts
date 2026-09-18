/** Large layout changes may crop the focused object; small resizes preserve user zoom. */
export function shouldRefitViewport(previous: readonly [number, number], next: readonly [number, number]): boolean {
  if (![...previous, ...next].every(value => Number.isFinite(value) && value > 0)) return false;
  const aspectChange = Math.abs((next[0] / next[1]) / (previous[0] / previous[1]) - 1);
  return aspectChange >= 0.1 && Math.max(Math.abs(next[0] - previous[0]), Math.abs(next[1] - previous[1])) >= 80;
}
