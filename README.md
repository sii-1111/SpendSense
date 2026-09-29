# SpendSense: bank transaction categoriser built on Jev

Upload a bank statement CSV. Every UPI, card, NACH/ACH, NEFT/IMPS and ATM transaction is sorted into a
category, and recurring charges worth a second look are pointed out: trials that turned into paid plans,
price increases, overlapping streaming services, and auto-debits from unknown merchants.

## Why Jev fits
- **Volume:** a finance app processes millions of transactions a day. `eval.py` prints the cost per 1M.
- **A fixed set of answers:** 20 debit categories, 5 credit categories and 9 service types. No text generation.
- **Speed:** results appear while the user watches the upload finish.

## Design choices worth showing in the demo
1. **Code parses, Jev judges.** `narration.py` turns `UPI/DR/4521.../SWIGGY INSTAMART/YESB/instamart.swiggy@icici/order`
   into payee, UPI ID, channel and note. Jev never reads reference numbers.
2. **Facts go into the state.** Recurrence is detected in code (`recurring.py`) and passed to Jev as
   "monthly, around day 1, 3 times". That's how ₹18,000 to *SURESH NAIK* on the 1st becomes **Rent**,
   while ₹450 to *RAHUL KUMAR* stays **Sent to people**.
3. **Options are restricted by facts.** A credit only sees income categories, a debit only spending ones.
   Fewer options means fewer mistakes.
4. **One call per merchant, not per transaction.** In the sample, 110 transactions become about 30 calls.
5. **Merchant memory.** High-confidence answers and user corrections are saved in `memory.json`, so the
   next statement needs even fewer calls. The warm run in `eval.py` shows this.
6. **Rules for the obvious.** ATM withdrawals, bank charges and interest never reach the model.
7. **Policy lives in code.** Rent, EMIs, insurance, SIPs, utilities and household transfers are never
   flagged as leaks. Regular habits (food, fuel, groceries) aren't treated as subscriptions.
   Low-confidence answers go to a "Check these payees" review list.

## Files
| File | What it does |
|---|---|
| `categories.json` | Categories with `what` / `not_for` / `examples`, service types, thresholds |
| `narration.py` | CSV loader (common bank export layouts) and narration parser |
| `recurring.py` | Recurring detection and leak rules (pure code) |
| `categorise.py` | Pipeline: rules → memory → Jev per merchant → review queue → leaks |
| `app.py` + `static/index.html` | Web app (standard library only) |
| `eval.py` | Accuracy, review quality, top mistakes, recurring detection, cold vs warm cost |
| `data/sample_statement.csv` | 3 months, 110 labeled transactions (synthetic) |

## Run
```bash
pip install -r requirements.txt

JEV_MOCK=1 python app.py          # offline demo with fake answers → http://localhost:8000
JEV_MOCK=1 python eval.py

export TYPESAFE_API_KEY=...       # real Jev
python eval.py
python app.py
```
Delete `memory.json` to reset what the app has learned. Using a gateway? Set `JEV_BASE_URL` and `JEV_MODEL`.

## Hard cases in the sample data
- Swiggy (food delivery) vs Swiggy Instamart (groceries)
- JioHotstar (subscription) vs Jio recharge (mobile)
- Tea-stall QR payments whose UPI IDs look like random handles
- Rent and domestic-help salary paid by UPI to individuals
- LinkedIn Premium: ₹2 trial, then ₹1,600 a month
- Netflix price increase from ₹649 to ₹799

## Success criteria (suggested)
- At least 90% category accuracy on auto-accepted transactions, with at least 80% auto-accepted
- Most of the review list is made up of genuinely wrong or ambiguous items
- Every labeled recurring charge found, with essential payments never flagged
- Cost per 1M transactions reported for both cold and warm memory

## Privacy
Bank statements are sensitive. For a real product, mask account numbers and names before sending state to
any API, check TypeSafe's data-retention terms, and keep `memory.json` per user.
