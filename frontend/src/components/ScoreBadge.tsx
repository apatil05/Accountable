export function scoreTone(score: number | null | undefined) {
  if (score == null) return "none";
  if (score >= 65) return "good";
  if (score >= 45) return "mid";
  return "bad";
}

export default function ScoreBadge({ score, size = "md" }: { score: number | null | undefined; size?: "md" | "lg" }) {
  return (
    <span className={`score score-${scoreTone(score)} score-${size}`} title="Your personalized score (0-100)">
      {score == null ? "—" : Math.round(score)}
    </span>
  );
}
