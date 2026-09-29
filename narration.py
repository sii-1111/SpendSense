"""Load a bank statement CSV and parse Indian bank narrations.

Accepts common export layouts:
  date, narration|description|particulars|remarks, debit|withdrawal, credit|deposit
  or date, narration, amount, type (DR/CR)
Parsing is deterministic: Jev never has to read raw reference numbers.
"""
import csv
import io
import re
from datetime import datetime

DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y", "%d-%m-%y")
HANDLE_NOISE = re.compile(r"^(paytmqr|q\d+|bharatpe|gpay|pay|upi|mab|yespay)[\w.]*$", re.I)


def _num(v):
    v = (v or "").replace(",", "").replace("₹", "").strip()
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def _date(v):
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(v.strip(), f).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognised date: {v!r}")


def load_csv(text):
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    if not rows:
        raise ValueError("The CSV has no rows.")
    cols = {c.lower().strip(): c for c in rows[0].keys()}
    pick = lambda *names: next((cols[n] for n in names if n in cols), None)
    c_date = pick("date", "txn date", "transaction date", "value date")
    c_narr = pick("narration", "description", "particulars", "remarks", "details")
    c_dr, c_cr = pick("debit", "withdrawal", "withdrawal amt.", "dr"), pick("credit", "deposit", "deposit amt.", "cr")
    c_amt, c_type = pick("amount"), pick("type", "dr/cr")
    if not (c_date and c_narr and ((c_dr and c_cr) or c_amt)):
        raise ValueError("Need columns: date, narration, and debit+credit (or amount+type).")
    out = []
    for i, r in enumerate(rows):
        if c_dr and c_cr:
            dr, cr = _num(r[c_dr]), _num(r[c_cr])
            amount, direction = (dr, "debit") if dr else (cr, "credit")
        else:
            amount = abs(_num(r[c_amt]))
            t = (r.get(c_type) or "").lower()
            direction = "credit" if t.startswith("c") or _num(r[c_amt]) > 0 and not t else "debit"
        if not amount:
            continue
        txn = {"id": i, "date": _date(r[c_date]).isoformat(), "narration": r[c_narr].strip(),
               "amount": round(amount, 2), "direction": direction}
        txn.update(parse_narration(txn["narration"]))
        for k in ("label", "recurring_label"):  # optional eval columns
            if r.get(k):
                txn[k] = r[k].strip()
        out.append(txn)
    return out


def _clean(s):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 &.'-]", " ", s)).strip()


def parse_narration(n):
    u = n.upper()
    channel, payee, vpa, note = "other", "", "", ""
    if u.startswith("UPI"):
        channel = "upi"
        parts = [p.strip() for p in n.split("/")]
        # Typical: UPI/DR/<ref>/<NAME>/<BANK>/<vpa>/<note>
        vpa = next((p for p in parts if "@" in p), "")
        texty = [p for p in parts[1:] if p and not p.isdigit() and p.upper() not in ("DR", "CR", "P2A", "P2M")
                 and "@" not in p and len(p) > 3]
        payee = texty[0] if texty else ""
        note = texty[-1] if len(texty) > 2 else ""
    elif re.match(r"^(POS|PCD|ECOM|VPS|IPS|MPS)\b", u) or " CARD " in u:
        channel = "card"
        payee = re.sub(r"^(POS|PCD|ECOM|VPS|IPS|MPS)\s*(\d+X*\d*\s*)?", "", n, flags=re.I)
    elif re.match(r"^(ACH|NACH|ECS|SI)\b", u) or "AUTOPAY" in u or "MANDATE" in u:
        channel = "mandate"
        payee = re.sub(r"^(ACH|NACH|ECS|SI)\s*(D|DR)?\s*-?\s*", "", n, flags=re.I)
    elif re.match(r"^(NEFT|IMPS|RTGS)", u):
        channel = "bank_transfer"
        parts = re.split(r"[-/]", n)
        payee = next((p for p in parts[1:] if re.search(r"[A-Za-z]{3}", p) and not re.fullmatch(r"[A-Z]{4}0\w{6}", p.strip())), "")
    elif "ATM" in u or "NFS" in u or "CASH WDL" in u:
        channel = "atm"
    elif re.search(r"CHG|CHRG|CHARGES|FEE|GST", u):
        channel = "bank_charge"
    elif re.search(r"INT\.?PD|INTEREST", u):
        channel = "interest"
    payee = _clean(payee)[:60]
    return {"channel": channel, "payee": payee, "vpa": vpa.lower(), "note": _clean(note)[:60],
            "merchant_key": merchant_key(payee, vpa, n)}


def merchant_key(payee, vpa, narration):
    """Stable key so the same merchant maps to the same cached answer.
    'SWIGGY INSTAMART' and 'SWIGGY' stay distinct; ref numbers and card masks drop out."""
    handle = vpa.split("@")[0] if vpa else ""
    if handle and not HANDLE_NOISE.match(handle) and not handle.isdigit():
        base = handle
    else:
        base = payee or narration
    words = re.sub(r"[^a-z ]", " ", base.lower().replace(".", " ")).split()
    words = [w for w in words if w not in ("pvt", "ltd", "private", "limited", "india", "com", "in", "www", "the", "payment", "payments")]
    return "_".join(words[:3]) or "unknown"
