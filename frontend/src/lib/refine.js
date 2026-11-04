/**
 * Splits a natural-language request like "make it warmer, keep it under 30k"
 * into a style instruction (-> 3D edit job) and a budget cap (-> planner).
 * The 3D editor can't act on money, and the planner shouldn't have to guess
 * a number out of free text, so we separate them on the client.
 */
const BUDGET =
  /(?:under|below|within|less than|max(?:imum)?(?: of)?|budget(?: of)?|up to|upto)\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|lakhs?|lacs?|l)?\b/i;

const FILLER = /\b(keep|it|this|the|please|and|stay|make|my|total|cost|costs|budget|but|also|room)\b/gi;

export function parseRefinement(text) {
  const t = text.trim();
  const m = t.match(BUDGET);
  let budgetCap = null;
  let rest = t;
  if (m) {
    const n = parseFloat(m[1].replace(/,/g, ""));
    const unit = (m[2] || "").toLowerCase();
    budgetCap = Math.round(unit === "k" ? n * 1e3 : unit ? n * 1e5 : n);
    rest = t.replace(m[0], " ");
  }
  const letters = rest.replace(FILLER, "").replace(/[^a-z]/gi, "");
  const stylePrompt = letters.length >= 3 ? rest.replace(/\s+/g, " ").replace(/^[\s,.;]+|[\s,.;]+$/g, "") : null;
  return { stylePrompt, budgetCap };
}
