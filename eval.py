"""Evaluate SpendSense on a labeled statement (columns: label, recurring_label).

    python eval.py [data/sample_statement.csv]

Runs twice: a cold run (empty merchant memory) and a warm run (memory filled by
the cold run) to show how cost falls as the app learns a user's merchants.
Uses a temporary memory file so your real memory.json is untouched.
"""
import sys
import tempfile
from collections import Counter
from pathlib import Path

import categorise

ROOT = Path(__file__).parent


def main(path):
    text = Path(path).read_text()
    categorise.MEMORY_PATH = Path(tempfile.mkdtemp()) / "memory.json"
    cold = categorise.analyse(text)
    warm = categorise.analyse(text)
    txns = [t for t in cold["transactions"] if t.get("label")]
    n = len(txns)
    s = cold["stats"]

    print(f"\n=== SpendSense eval: {n} labeled transactions, model={s['model']} ===")
    if s["model"] == "MOCK":
        print("!! MOCK MODE: this tests the pipeline, not Jev. Unset JEV_MOCK for real numbers.")

    ok = [t["category"] == t["label"] for t in txns]
    print(f"\nCategory accuracy: {sum(ok)}/{n} = {sum(ok) / n:.0%}")
    by_src = Counter(t["source"] for t in txns)
    for src in ("rule", "memory", "jev"):
        sub = [c for t, c in zip(txns, ok) if t["source"] == src]
        if sub:
            print(f"  via {src:<6}: {sum(sub)}/{len(sub)} = {sum(sub) / len(sub):.0%}")

    auto = [(t, c) for t, c in zip(txns, ok) if not t.get("needs_review")]
    print(f"\nAuto-accepted (no review needed): {len(auto)}/{n} = {len(auto) / n:.0%}, "
          f"accuracy {sum(c for _, c in auto) / max(len(auto), 1):.0%}")
    rev = [(t, c) for t, c in zip(txns, ok) if t.get("needs_review")]
    print(f"Sent to review: {len(rev)}; of those, {sum(not c for _, c in rev)} were actually wrong "
          f"(review is catching real errors if this is most of them)")

    print("\nMost common mistakes (fix with not_for lines in categories.json):")
    conf = Counter((t["label"], t["category"]) for t, c in zip(txns, ok) if not c)
    for (want, got), k in conf.most_common(8):
        ex = next(t for t in txns if t["label"] == want and t["category"] == got)
        print(f"  {want:<20} -> {got:<20} x{k}   e.g. {ex['payee'] or ex['narration'][:30]}")

    # Recurring detection vs labels (merchant level)
    labeled_rec = {t["merchant_key"] for t in cold["transactions"] if t.get("recurring_label") == "yes"}
    found = {r["merchant_key"] for r in cold["recurring"]}
    tp = labeled_rec & found
    print(f"\nRecurring charges: found {len(found)}, labeled {len(labeled_rec)}, correct {len(tp)}")
    for k in sorted(labeled_rec - found):
        print(f"  missed:       {k}")
    for k in sorted(found - labeled_rec):
        print(f"  false alarm:  {k}")
    print(f"Money leaks flagged: {cold['leaks']['count']} worth ₹{cold['leaks']['annual_cost']:,.0f}/year")
    for r in cold["recurring"]:
        if r["flags"]:
            print(f"  {r['payee'][:26]:<26} ₹{r['latest_amount']:>7,.0f}/{r['period']:<8} {' '.join(r['flags'])}")

    # Cost
    unique = len({(t["merchant_key"], t["direction"]) for t in cold["transactions"]})
    ws = warm["stats"]
    per_txn = s["cost_usd"] / max(s["transactions"], 1)
    print(f"\nCold run: {s['transactions']} transactions -> {s['jev_calls']} Jev calls "
          f"({unique} unique merchants, {s['by_rule']} handled by rules), cost ${s['cost_usd']:.6f}")
    print(f"Warm run: {ws['jev_calls']} Jev calls, {ws['by_memory']} answered from merchant memory, cost ${ws['cost_usd']:.6f}")
    print(f"Cold-start cost per 1M transactions: ${per_txn * 1e6:,.2f} (falls further as memory warms up)")
    print(f"Latency p50 per call: {s['latency_p50_ms']} ms\n")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "sample_statement.csv")
