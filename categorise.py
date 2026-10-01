"""SpendSense core.

    python categorise.py data/sample_statement.csv

Pipeline per statement:
  1. Parse narrations (narration.py) and detect recurring charges (recurring.py), both pure code.
  2. Rules handle the obvious (ATM, bank charges, interest).
  3. Merchant memory: user corrections and high-confidence past answers skip Jev.
  4. One Jev call per unique merchant, not per transaction. The state includes facts
     code already knows (direction, how often, typical amount, recurring pattern), and
     the category options are restricted by direction (credits never see 'rent').
  5. Low confidence goes to a review queue; corrections are saved back to memory.
"""
import json
import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median

import recurring as rec_mod
import jev_client
from jev_client import JevClient, JevError

jev_client._mock_decide.fields = ["payee", "upi_id", "note", "sample_narration"]  # mock only
from narration import load_csv

ROOT = Path(__file__).parent
RUBRIC = json.loads((ROOT / "categories.json").read_text())
MEMORY_PATH = ROOT / "memory.json"
_lock = threading.Lock()
ESSENTIAL = {"rent", "emi_loan", "insurance", "investments", "utilities", "mobile_internet", "credit_card_payment",
             "transfer_to_person", "education"}  # recurring transfers to a person = household help, family support


class FileMemory:
    """Merchant memory in one JSON file. Used by the CLI and eval.py (single user)."""

    def __init__(self, path=None):
        self.path = path

    def _p(self):
        return Path(self.path) if self.path else MEMORY_PATH  # read at call time so eval.py can redirect it

    def load(self):
        try:
            return json.loads(self._p().read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def merge(self, entries, overwrite=False):
        with _lock:
            mem = self.load()
            mem.update(entries if overwrite else {k: v for k, v in entries.items() if k not in mem})
            self._p().write_text(json.dumps(mem, indent=2))


def correction_entry(merchant_key, direction, category):
    valid = RUBRIC["debit_categories"] if direction == "debit" else RUBRIC["credit_categories"]
    if direction not in ("debit", "credit") or category not in valid:
        raise ValueError(f"Unknown category {category!r} for a {direction}")
    return {f"{merchant_key}|{direction}": {"category": category, "source": "user"}}


def save_correction(merchant_key, direction, category, memory=None):
    (memory or FileMemory()).merge(correction_entry(merchant_key, direction, category), overwrite=True)


def rule_category(t):
    if t["channel"] == "atm" and t["direction"] == "debit":
        return "cash_withdrawal"
    if t["channel"] == "bank_charge" and t["direction"] == "debit":
        return "fees_charges"
    if t["channel"] == "interest" and t["direction"] == "credit":
        return "interest_dividend"
    return None


def build_request(group, rec):
    t = group[-1]
    state = {
        "payee": t["payee"], "upi_id": t["vpa"] or None, "channel": t["channel"],
        "note": t["note"] or None, "sample_narration": t["narration"][:120],
        "direction": t["direction"], "typical_amount_inr": median(x["amount"] for x in group),
        "times_seen_in_statement": len(group),
    }
    if rec:
        state["recurring"] = f"{rec['period']}, usually around day {rec['typical_day']} of the month, {rec['count']} times"
    cats = RUBRIC["debit_categories"] if t["direction"] == "debit" else RUBRIC["credit_categories"]
    questions = {
        "category": {"type": "choice",
                     "instructions": "Which spending category does this bank transaction belong to? Use the payee, UPI ID, note, amount and pattern.",
                     "criteria": cats},
        "is_person": {"type": "noul", "instructions": "Is the payee an individual person rather than a business or organisation?"},
    }
    if rec:
        questions["service_type"] = {"type": "choice",
                                     "instructions": "What kind of recurring charge is this?",
                                     "criteria": RUBRIC["service_types"]}
    return state, questions


def analyse(csv_text, client=None, memory=None):
    """memory: any object with load() -> dict and merge(dict). Defaults to memory.json."""
    store = memory or FileMemory()
    txns = load_csv(csv_text)
    recs = {r["merchant_key"]: r for r in rec_mod.detect(txns)}
    memory = store.load()
    th = RUBRIC["thresholds"]
    stats = {"transactions": len(txns), "by_rule": 0, "by_memory": 0, "jev_calls": 0,
             "cost_usd": 0.0, "latency_ms": [], "model": None}

    # Group the rest by merchant+direction so each merchant costs one call.
    groups = defaultdict(list)
    for t in txns:
        cat = rule_category(t)
        if cat:
            t.update(category=cat, confidence=1.0, source="rule"); stats["by_rule"] += 1
            continue
        m = memory.get(f"{t['merchant_key']}|{t['direction']}")
        if m:
            t.update(category=m["category"], confidence=1.0, source="memory"); stats["by_memory"] += 1
            continue
        groups[(t["merchant_key"], t["direction"])].append(t)

    client = client or JevClient()
    client.mock_hints = MOCK_HINTS

    def ask(item):
        (key, direction), group = item
        rec = recs.get(key) if direction == "debit" else None
        state, qs = build_request(group, rec)
        try:
            answers, meta = client.decide(state, qs)
        except JevError as e:
            return item, None, {"error": str(e)}
        return item, answers, meta

    service_types = {}
    new_memory = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for ((key, direction), group), answers, meta in pool.map(ask, groups.items()):
            if answers is None:
                for t in group:
                    t.update(category="other", confidence=0.0, source="error", needs_review=True)
                continue
            stats["jev_calls"] += 1
            stats["cost_usd"] += meta["cost_usd"]; stats["latency_ms"].append(meta["latency_ms"]); stats["model"] = meta["model"]
            c = answers["category"]
            for t in group:
                t.update(category=c["choice"], confidence=round(c["confidence"], 3), source="jev",
                         p_person=round(answers["is_person"]["noul"], 3),
                         needs_review=c["confidence"] < th["auto_accept_confidence"] or c["choice"] in ("other", "other_income"))
            if "service_type" in answers:
                service_types[key] = answers["service_type"]["choice"]
            if c["confidence"] >= th["remember_confidence"] and c["choice"] not in ("other", "other_income"):
                new_memory[f"{key}|{direction}"] = {"category": c["choice"], "source": "jev"}

    if new_memory:
        store.merge(new_memory)

    # Recurring charges and leaks
    by_id = {t["id"]: t for t in txns}
    HABITS = {"fuel", "groceries", "food_delivery", "dining_out", "travel_transport", "shopping", "health"}
    recs = {k: r for k, r in recs.items() if by_id[r["txn_ids"][-1]]["category"] not in HABITS}  # regular habit, not a charge
    for key, r in recs.items():
        r["category"] = by_id[r["txn_ids"][-1]]["category"]
        st = service_types.get(key)
        if not st:  # merchant came from memory/rules: infer essential from category
            st = "essential" if r["category"] in ESSENTIAL else "unknown"
        if r["category"] in ESSENTIAL:
            st = "essential"  # category decision wins: never tell someone to cancel their EMI
        r["service_type"] = st
    overlap = defaultdict(int)
    for r in recs.values():
        overlap[r["service_type"]] += 1
    for r in recs.values():
        r["flags"] = rec_mod.leak_flags(r, r["service_type"], overlap[r["service_type"]], RUBRIC)

    totals = defaultdict(float)
    for t in txns:
        if t["direction"] == "debit":
            totals[t["category"]] += t["amount"]
    lat = sorted(stats.pop("latency_ms")) or [0]
    stats.update(latency_p50_ms=round(lat[len(lat) // 2]), cost_usd=round(stats["cost_usd"], 7),
                 needs_review=sum(bool(t.get("needs_review")) for t in txns))
    flagged = [r for r in recs.values() if r["flags"]]
    return {
        "transactions": txns,
        "totals": dict(sorted(((k, round(v, 2)) for k, v in totals.items()), key=lambda kv: -kv[1])),
        "income": round(sum(t["amount"] for t in txns if t["direction"] == "credit"), 2),
        "spend": round(sum(t["amount"] for t in txns if t["direction"] == "debit"), 2),
        "recurring": sorted(recs.values(), key=lambda r: (not r["flags"], -r["annual_cost"])),
        "leaks": {"count": len(flagged), "annual_cost": round(sum(r["annual_cost"] for r in flagged), 2)},
        "subscriptions_annual": round(sum(r["annual_cost"] for r in recs.values() if r["service_type"] != "essential"), 2),
        "stats": stats,
        "categories": {"debit": list(RUBRIC["debit_categories"]), "credit": list(RUBRIC["credit_categories"])},
    }


MOCK_HINTS = {  # offline mock only, never sent to the API
    "category": {
        "food_delivery": ["swiggy", "zomato", "eatsure"], "groceries": ["instamart", "blinkit", "zepto", "dmart", "bigbasket", "grocer"],
        "dining_out": ["tea", "cafe", "starbucks", "hotel", "bakery", "darshini"], "shopping": ["amazon", "flipkart", "myntra", "croma"],
        "fuel": ["hpcl", "indian oil", "bpcl", "petrol", "fuel"], "travel_transport": ["uber", "ola", "irctc", "rapido", "metro", "fastag"],
        "rent": ["/rent", "rent"], "emi_loan": ["-emi", "loan", "bajaj fin"], "credit_card_payment": ["cred", "cc payment", "credit card"],
        "utilities": ["bescom", "cesc", "bwssb", "indane", "gas", "maintenance"], "mobile_internet": ["jio", "airtel", "act fibernet", "tata play"],
        "subscriptions": ["netflix", "spotify", "sonyliv", "cultfit", "coursera plus", "hotstar", "jiohotstar", "subscription", "youtube", "google", "prime", "cult", "swiggy one", "linkedin", "apple"],
        "insurance": ["lic", "ergo", "insurance", "star health"], "investments": ["groww", "zerodha", "sip", "mf", "nps"],
        "health": ["apollo", "pharma", "1mg", "hospital", "clinic"], "education": ["school", "coursera", "fees"],
        "transfer_to_person": ["kumar", "priya", "amma", "appa", "sharma", "reddy", "lakshmi", "naik", "manjula", "appa"],
        "salary_income": ["salary", "sal"], "refund_cashback": ["refund", "cashback", "rev"], "transfer_from_person": ["kumar", "priya", "rahul"],
    },
    "is_person": ["kumar", "priya", "amma", "sharma", "reddy", "rahul", "lakshmi", "naik", "manjula", "appa"],
    "service_type": {
        "video_streaming": ["netflix", "hotstar", "prime", "sonyliv", "zee5"], "music_streaming": ["spotify", "youtube"],
        "cloud_storage": ["google", "icloud"], "software_app": ["linkedin", "microsoft", "canva"], "delivery_membership": ["swiggy one"],
        "fitness": ["cult"], "essential": ["-emi", "loan", "lic of", "sip", "rent", "jio", "bescom", "insurance", "cred"],
    },
}


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "sample_statement.csv"
    res = analyse(Path(path).read_text())
    print(json.dumps({k: res[k] for k in ("totals", "leaks", "subscriptions_annual", "stats")}, indent=2))
    for r in res["recurring"]:
        print(f"  {r['payee'][:28]:<28} {r['period']:<9} ₹{r['latest_amount']:>9,.0f}  {r['service_type']:<16} {'; '.join(r['flags'])}")
