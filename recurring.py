"""Find recurring debits and compute the facts about them. No model here.

A group is recurring when the same merchant charges at a regular interval
(weekly / monthly / quarterly / yearly) with similar amounts.
"""
from collections import defaultdict
from datetime import date
from statistics import median

PERIODS = {"weekly": (6, 8, 52), "monthly": (26, 35, 12), "quarterly": (85, 97, 4), "yearly": (355, 375, 1)}


def detect(txns):
    groups = defaultdict(list)
    for t in txns:
        if t["direction"] == "debit" and t["channel"] not in ("atm", "bank_charge"):
            groups[t["merchant_key"]].append(t)

    recurring = []
    for key, g in groups.items():
        if len(g) < 2:
            continue
        g.sort(key=lambda t: t["date"])
        days = [(date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days for a, b in zip(g, g[1:])]
        gap = median(days)
        period = next((p for p, (lo, hi, _) in PERIODS.items() if lo <= gap <= hi), None)
        if not period or any(not (PERIODS[period][0] - 3 <= d <= PERIODS[period][1] + 3) for d in days):
            continue
        amounts = [t["amount"] for t in g]
        paid = [a for a in amounts if a > 5]  # ignore ₹1-₹5 trial/verification charges
        if not paid or max(paid) > 1.35 * min(paid):
            continue  # amounts too varied (e.g. groceries every week) -> habit, not a subscription
        latest = amounts[-1]
        recurring.append({
            "merchant_key": key,
            "payee": g[-1]["payee"] or g[-1]["narration"][:40],
            "channel": g[-1]["channel"],
            "period": period,
            "count": len(g),
            "first_date": g[0]["date"], "last_date": g[-1]["date"],
            "latest_amount": latest,
            "annual_cost": round(latest * PERIODS[period][2], 2),
            "price_hike_pct": round((latest / paid[0] - 1) * 100, 1) if paid[0] and latest > paid[0] * 1.05 else 0,
            "trial_converted": amounts[0] <= 5 < latest,
            "txn_ids": [t["id"] for t in g],
            "typical_day": int(median(date.fromisoformat(t["date"]).day for t in g)),
        })
    return recurring


def leak_flags(rec, service_type, overlap_count, rubric):
    """Plain rules. Essential spend (rent, EMI, insurance, SIP, utilities) is never flagged."""
    if service_type == "essential":
        return []
    flags = []
    if rec["trial_converted"]:
        flags.append("A free or ₹1 trial turned into a paid plan.")
    if rec["price_hike_pct"]:
        flags.append(f"Price went up {rec['price_hike_pct']:.0f}% since the first charge.")
    if service_type in rubric["overlap_groups"] and overlap_count > 1:
        flags.append(f"You pay for {overlap_count} {service_type.replace('_', ' ')} services.")
    if service_type == "unknown":
        flags.append("Recurring charge from a merchant we couldn't identify.")
    return flags
