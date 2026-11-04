/** Display-only totals. The planner (server) is the authority on budget compliance. */
export function totals(items = []) {
  let catalog = 0;
  let estMin = 0;
  let estMax = 0;
  for (const it of items) {
    const q = it.qty || 1;
    if (it.price?.kind === "catalog") catalog += it.price.amount * q;
    else if (it.price) {
      estMin += it.price.min * q;
      estMax += it.price.max * q;
    }
  }
  return { catalog, estMin, estMax, low: catalog + estMin, high: catalog + estMax };
}

export function budgetStatus(t, cap) {
  if (!cap) return null;
  if (t.high <= cap) return { tone: "ok", text: "Within budget, even at the high estimates." };
  if (t.low > cap) return { tone: "bad", text: "Over budget even at the low estimates. Try a cheaper alternative or drop an item." };
  return { tone: "warn", text: "Could go over budget depending on the estimated items." };
}
