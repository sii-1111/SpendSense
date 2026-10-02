"""Load a bank statement CSV and parse Indian bank narrations.

Accepts common export layouts:
  date, narration|description|particulars|remarks, debit|withdrawal, credit|deposit
  or date, narration, amount, type (DR/CR)
Parsing is deterministic: Jev never has to read raw reference numbers.
"""
import csv
import io
import re
from datetime import datetime, timedelta

DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%d %b %Y", "%d-%m-%y")
HANDLE_NOISE = re.compile(r"^(paytmqr|q\d+|bharatpe|gpay|pay|upi|mab|yespay)[\w.]*$", re.I)
DATE_HEADERS = ("date", "txn date", "transaction date", "value date", "posting date", "trans date")
NARRATION_HEADERS = ("narration", "description", "particulars", "remarks", "details", "transaction details",
                     "transaction description", "transaction narration", "transaction remarks", "narration remarks",
                     "payment details")
DEBIT_HEADERS = ("debit", "debits", "debit amount", "debit amt", "debit dr", "dr", "dr amount", "dr amt",
                 "withdrawal", "withdrawals", "withdrawal amount", "withdrawal amt", "withdrawal dr", "money out")
CREDIT_HEADERS = ("credit", "credits", "credit amount", "credit amt", "credit cr", "cr", "cr amount", "cr amt",
                  "deposit", "deposits", "deposit amount", "deposit amt", "deposit cr", "money in")
AMOUNT_HEADERS = ("amount", "transaction amount", "transaction amt", "amt")
TYPE_HEADERS = ("type", "dr cr", "cr dr", "transaction type", "debit credit")


def _column_key(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def _find_columns(headers):
    columns = {_column_key(header): header for header in headers if _column_key(header)}

    def pick(names):
        return next((columns[name] for name in names if name in columns), None)

    date = pick(DATE_HEADERS)
    narration = pick(NARRATION_HEADERS)
    debit, credit = pick(DEBIT_HEADERS), pick(CREDIT_HEADERS)
    amount, kind = pick(AMOUNT_HEADERS), pick(TYPE_HEADERS)
    if date and narration and ((debit and credit) or amount):
        return date, narration, debit, credit, amount, kind
    return None


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
    try:
        serial = float(v.strip())
        if serial >= 1:
            return (datetime(1899, 12, 30) + timedelta(days=serial)).date()
    except (OverflowError, ValueError):
        pass
    raise ValueError(f"Unrecognised date: {v!r}")


def load_csv(text):
    source_rows = list(csv.reader(io.StringIO(text.strip())))
    if not source_rows:
        raise ValueError("The CSV has no rows.")
    header_index = next((index for index, row in enumerate(source_rows[:30]) if _find_columns(row)), None)
    if header_index is None:
        raise ValueError("Need columns: date, narration, and debit+credit (or amount+type).")
    headers = [header.strip() for header in source_rows[header_index]]
    c_date, c_narr, c_dr, c_cr, c_amt, c_type = _find_columns(headers)
    rows = [
        {header: row[index].strip() if index < len(row) else "" for index, header in enumerate(headers) if header}
        for row in source_rows[header_index + 1:]
        if any(value.strip() for value in row)
    ]
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
    if re.search(r"\b(CHG|CHGS|CHRG|CHARGES|ANNUAL FEE|MIN BAL)\b", u) and not u.startswith("UPI"):
        channel = "bank_charge"  # checked first: "DEBIT CARD ANNUAL FEE" must not be read as a card purchase
    elif u.startswith("UPI"):
        channel = "upi"
        # Two common layouts:
        #   UPI/DR/<ref>/<NAME>/<BANK>/<vpa>/<note>        (slash style)
        #   UPI-<NAME>-<vpa>-<IFSC>-<ref>-<note>           (HDFC-style dash export)
        sep = "/" if "/" in n[:8] else "-"
        parts = [p.strip() for p in n.split(sep)]
        vpa = next((p for p in parts if "@" in p), "")
        texty = [p for p in parts[1:] if p and not p.isdigit() and p.upper() not in ("DR", "CR", "P2A", "P2M")
                 and "@" not in p and len(p) > 3 and not re.fullmatch(r"[A-Z]{4}0\w{6}", p)]
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
