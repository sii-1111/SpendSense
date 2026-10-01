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
The command-line tools (`categorise.py`, `eval.py`) keep merchant memory in `memory.json`; delete it to reset.
The web app keeps a separate memory per visitor in server RAM ("Forget my corrections" clears yours).
Using a gateway? Set `JEV_BASE_URL` and `JEV_MODEL`.

## Deploy (Render)
1. Push this folder to a GitHub repo (`.gitignore` keeps out `memory.json`, keys and real statements).
2. On render.com: **New → Blueprint**, pick the repo. `render.yaml` sets the build/start commands and a health check.
3. It starts in **demo mode** (`JEV_MOCK=1`, simulated answers, no key, no cost).
   For live Jev: in the service's Environment tab, delete `JEV_MOCK` and set `TYPESAFE_API_KEY`.
4. Share the `https://…onrender.com` link. Free instances sleep when idle, so the first visit can be slow.

Railway, Fly.io and Hugging Face Spaces (Docker) work too: same start command, set the same environment variables.

### Public-demo safeguards (all configurable by environment variable)
| Setting | Default | What it does |
|---|---|---|
| `RATE_LIMIT_PER_HOUR` | 10 | Statements per visitor per hour (counted per IP and per browser session) |
| `DAILY_JEV_CALL_CAP` | 3000 | Live mode only: stops new analyses once this many Jev calls are made in a day |
| `MAX_UPLOAD_BYTES` | 2000000 | Largest accepted upload |
| `ACCESS_LOG` | off | Set to `1` to log method, path and status (statement contents are never logged) |

Other built-ins: a per-visitor session cookie (HttpOnly, SameSite, Secure over HTTPS); statements processed in memory
and never written to disk; a "demo data only" notice on the page; `/healthz` for the platform's health check.
People in one office may share an IP address, so raise `RATE_LIMIT_PER_HOUR` for a live presentation.
Sessions live in RAM, so a restart or redeploy clears everyone's corrections. A real product would use a database
and a production server (e.g. FastAPI behind uvicorn).

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
