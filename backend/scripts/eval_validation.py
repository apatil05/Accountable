"""Measure claim validation against hand-labeled claims (docs/VALIDATION_PLAN.md, step 9).

Runs the real validation pipeline (fetch + Gemini judge) on scripts/eval_claims.json several
times and compares `validated` with each claim's label. Needs GEMINI_API_KEY; each run makes
~20 judge calls (~1 minute).

    python -m scripts.eval_validation              # from backend/; 3 runs
    python -m scripts.eval_validation --runs 1 -v  # one run, print every claim

Exits 1 if mean precision is below 90%. Recall is reported but not gated.
Run it before changing judge prompts, thresholds, or tiers.

Labels (judged by a person reading the page at `url`):
  supported      the page supports the claim as written
  partial        the page supports the core fact but not some detail; correct outcomes are
                 "rejected" or "validated with the text rewritten" (validation trims partial claims)
  not_supported  the page does not support it; must be rejected
Each claim also has the judge labels used by scoring (services/scoring.py):
  direction      positive / negative / neutral. Neutral claims are dropped by validation, so a
                 claim labeled neutral counts as correctly rejected whatever its `label`.
  severity       minor / notable / major
Direction and severity agreement are reported over validated claims, not gated.
To add claims, copy them (with URL) from a research run's validation logs and label them.
"""

import argparse
import json
import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from app import config
from app.models import CategoryScore, Claim
from app.services import validation

PRECISION_BAR = 0.90
CLAIMS_FILE = Path(__file__).with_name("eval_claims.json")


def run_once(items: list[dict]) -> dict[str, Claim]:
    """Validate every claim once. One report per merchant, so claims are grouped by URL as in production."""
    claims = {it["id"]: Claim(text=it["claim"], source_url=it["url"]) for it in items}
    by_merchant: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_merchant[it["merchant"]].append(it)
    for merchant, rows in by_merchant.items():
        cats: dict[str, CategoryScore] = {}
        for it in rows:
            cats.setdefault(it["category"], CategoryScore(category=it["category"], summary="")).claims.append(claims[it["id"]])
        _, complete = validation.validate_claims(merchant, list(cats.values()))
        if not complete:
            print(f"  WARNING: some judge calls failed for {merchant}; those claims count as not validated.")
    return claims


def outcome(it: dict, claim: Claim) -> str:
    """TP / FP / FN / TN. Recall only counts 'supported' claims."""
    ok, rewritten = claim.validated, claim.text != it["claim"]
    label = "not_supported" if it.get("direction") == "neutral" else it["label"]
    if label == "supported":
        return "TP" if ok else "FN"
    if label == "partial":
        if not ok:
            return "TN"
        return "TP" if rewritten else "FP"
    return "FP" if ok else "TN"


SEVERITY_ORDER = {"minor": 0, "notable": 1, "major": 2}


def label_agreement(items: list[dict], claims: dict[str, Claim]) -> tuple[list[bool], list[bool], list[bool]]:
    """(direction matches, severity matches exactly, severity within one level), over validated
    claims that have the labels. Mismatches are printed."""
    direction, sev_exact, sev_near = [], [], []
    for it in items:
        c = claims[it["id"]]
        if not c.validated or "direction" not in it:
            continue
        direction.append(c._direction == it["direction"])
        if not direction[-1]:
            print(f"  direction  {it['id']}  label={it['direction']}  judge={c._direction or '-'}")
        if "severity" in it and c._severity in SEVERITY_ORDER:
            gap = abs(SEVERITY_ORDER[c._severity] - SEVERITY_ORDER[it["severity"]])
            sev_exact.append(gap == 0)
            sev_near.append(gap <= 1)
            if gap:
                print(f"  severity   {it['id']}  label={it['severity']}  judge={c._severity}")
    return direction, sev_exact, sev_near


def _pct(xs: list[bool]) -> str:
    return f"{sum(xs) / len(xs):.0%} of {len(xs)}" if xs else "n/a"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    if not config.GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set (backend/.env); the eval needs the live judge.")
        return 2

    items = json.loads(CLAIMS_FILE.read_text())
    precisions, recalls = [], []
    history: dict[str, list[str]] = defaultdict(list)
    all_dir: list[bool] = []
    all_sev_exact: list[bool] = []
    all_sev_near: list[bool] = []
    for r in range(args.runs):
        print(f"run {r + 1}/{args.runs}")
        claims = run_once(items)
        counts = defaultdict(int)
        for it in items:
            o = outcome(it, claims[it["id"]])
            counts[o] += 1
            history[it["id"]].append(o)
            if args.verbose or o in ("FP", "FN"):
                print(f"  {o}  {it['id']}  label={it['label']:13}  {it['claim'][:90]}")
                if claims[it["id"]].text != it["claim"]:
                    print(f"        rewritten to: {claims[it['id']].text[:160]}")
        tp, fp, fn = counts["TP"], counts["FP"], counts["FN"]
        precisions.append(tp / (tp + fp) if tp + fp else 1.0)
        recalls.append(tp / (tp + fn) if tp + fn else 1.0)
        print(f"  TP={tp} FP={fp} FN={fn} TN={counts['TN']}  precision {precisions[-1]:.0%}  recall {recalls[-1]:.0%}")
        d, se, sn = label_agreement(items, claims)
        all_dir += d
        all_sev_exact += se
        all_sev_near += sn
        print(f"  direction agreement {_pct(d)}  severity exact {_pct(se)}, within one level {_pct(sn)}")

    flips = {cid: h for cid, h in history.items() if len(set(h)) > 1}
    if flips:
        print("\nunstable across runs:")
        for cid, h in flips.items():
            print(f"  {cid}: {' '.join(h)}")
    mean_p = statistics.mean(precisions)
    print(
        f"\n{len(items)} claims x {args.runs} runs  "
        f"precision mean {mean_p:.0%} worst {min(precisions):.0%} (bar {PRECISION_BAR:.0%})  "
        f"recall mean {statistics.mean(recalls):.0%} worst {min(recalls):.0%}"
    )
    print(
        f"direction agreement {_pct(all_dir)}  severity exact {_pct(all_sev_exact)}, "
        f"within one level {_pct(all_sev_near)}  (all runs, validated claims)"
    )
    if mean_p < PRECISION_BAR:
        print("FAIL: mean precision below bar")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
