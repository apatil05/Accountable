// Helpers for the "What we found" topics on the company page.

// Stored summaries can run 2+ sentences; show only the first. Skips periods in abbreviations like "Inc." or "U.S.".
export function firstSentence(text: string) {
  const m = text.match(/^.*?(?<!\b(?:Inc|Co|Corp|Ltd|St|Mr|Ms|Dr|vs|U\.S|[A-Z]))[.!?](?=\s+["A-Z]|$)/s);
  return m ? m[0] : text;
}
