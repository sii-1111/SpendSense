const NAMES = {
  food_delivery: "Food delivery", groceries: "Groceries", dining_out: "Eating out", shopping: "Shopping", fuel: "Fuel",
  travel_transport: "Travel and transport", rent: "Rent", emi_loan: "Loan EMIs", credit_card_payment: "Credit card bills",
  utilities: "Electricity, water, gas", mobile_internet: "Mobile and internet", subscriptions: "Subscriptions", insurance: "Insurance",
  investments: "Investments and SIPs", health: "Health", education: "Education", cash_withdrawal: "Cash withdrawals",
  transfer_to_person: "Sent to people", fees_charges: "Bank charges", other: "Other", salary_income: "Salary and income",
  refund_cashback: "Refunds and cashback", interest_dividend: "Interest", transfer_from_person: "Received from people", other_income: "Other income",
};
const PERIOD = { weekly: "week", monthly: "month", quarterly: "quarter", yearly: "year" };
const inr = (amount) => "₹" + Math.round(amount).toLocaleString("en-IN");
const esc = (value) => String(value ?? "").replace(/[&<>"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[character]);
const $ = (id) => document.getElementById(id);

const fileInput = $("file");
const results = $("out");
const resultsContent = $("resultsContent");
let current = null;

fileInput.addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const csv = await readStatement(file);
    if (!csv.trim()) throw new Error("The first worksheet is empty.");
    await analyse(csv);
  } catch (error) {
    showError("Couldn't read that statement: " + error.message);
  } finally {
    fileInput.value = "";
  }
});
$("cta").addEventListener("click", () => fileInput.click());
$("headerUpload").addEventListener("click", () => fileInput.click());
$("mobileUpload").addEventListener("click", () => { closeMenu(); fileInput.click(); });
$("sample").addEventListener("click", loadSample);
$("sampleAgain").addEventListener("click", loadSample);
$("resultsClose").addEventListener("click", closeResults);
$("reset").addEventListener("click", resetCorrections);

async function loadSample() {
  try {
    const response = await fetch("/sample.csv");
    if (!response.ok) throw new Error(`server returned ${response.status}`);
    await analyse(await response.text());
  } catch (error) {
    showError("Couldn't load the sample statement: " + error.message);
  }
}

async function resetCorrections() {
  const button = $("reset");
  const response = await fetch("/api/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  button.textContent = response.ok ? "Corrections forgotten" : "Couldn't reset";
  window.setTimeout(() => { button.textContent = "Forget my corrections"; }, 2500);
}

async function readStatement(file) {
  if (!file.name.toLowerCase().endsWith(".xlsx")) return file.text();
  if (!window.XLSX) throw new Error("Excel file support couldn't be loaded. Refresh the page and try again.");
  const workbook = window.XLSX.read(await file.arrayBuffer(), { type: "array" });
  const firstSheet = workbook.Sheets[workbook.SheetNames[0]];
  if (!firstSheet) throw new Error("This workbook has no worksheets.");
  const rows = window.XLSX.utils.sheet_to_json(firstSheet, { header: 1, raw: true, blankrows: false });
  if (!rows.length) return "";
  const dateHeaders = new Set(["date", "txn date", "transaction date", "value date"]);
  const dateColumns = rows[0].reduce((columns, value, index) => {
    if (dateHeaders.has(String(value).trim().toLowerCase())) columns.push(index);
    return columns;
  }, []);
  for (const row of rows.slice(1)) {
    for (const index of dateColumns) {
      if (typeof row[index] !== "number") continue;
      const date = window.XLSX.SSF.parse_date_code(row[index]);
      if (date) row[index] = `${date.y}-${String(date.m).padStart(2, "0")}-${String(date.d).padStart(2, "0")}`;
    }
  }
  return window.XLSX.utils.sheet_to_csv(window.XLSX.utils.aoa_to_sheet(rows), { blankrows: false });
}

async function analyse(csv) {
  $("err").hidden = true;
  resultsContent.innerHTML = '<p class="result-sub">Sorting transactions…</p>';
  results.hidden = false;
  const started = performance.now();
  try {
    const response = await fetch("/api/analyse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ csv }),
    });
    const body = await response.text();
    let data;
    try {
      data = JSON.parse(body);
    } catch {
      throw new Error(`a different program answered on this address (HTTP ${response.status}: "${body.slice(0, 60).trim()}"). Check that app.py is what's running on this port`);
    }
    if (!response.ok) throw new Error(data.error || `server returned ${response.status}`);
    current = data;
    render(data, performance.now() - started);
  } catch (error) {
    closeResults();
    showError("Couldn't read that statement: " + error.message);
  }
}

function showError(message) {
  const error = $("err");
  error.textContent = message;
  error.hidden = false;
}

function closeResults() {
  results.hidden = true;
}

function render(data, elapsedMs) {
  const transactions = data.transactions;
  const dates = transactions.map((transaction) => transaction.date).sort();
  const formatDate = (value) => new Date(value).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  const flaggedRecurring = data.recurring.filter((recurring) => recurring.flags.length);
  const headline = flaggedRecurring.length
    ? `You pay ${inr(data.leaks.annual_cost)} a year for ${flaggedRecurring.length} recurring ${flaggedRecurring.length === 1 ? "charge" : "charges"} worth a second look.`
    : `No recurring charges stood out. Your subscriptions come to ${inr(data.subscriptions_annual)} a year.`;
  const maxTotal = Math.max(...Object.values(data.totals), 1);
  const review = transactions.filter((transaction) => transaction.needs_review);
  const seen = new Set();
  const reviewUnique = review.filter((transaction) => {
    const key = transaction.merchant_key + transaction.direction;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  $("err").hidden = true;
  resultsContent.innerHTML = `
    <h2 class="result-headline" id="resultsTitle">${headline}</h2>
    <p class="result-total num">Total spent: <strong>${inr(data.spend)}</strong></p>
    <p class="result-period num">${formatDate(dates[0])} to ${formatDate(dates.at(-1))}. Money received: ${inr(data.income)}.</p>
    ${flaggedRecurring.length ? `<ul class="leaks">${flaggedRecurring.map((recurring) => `
      <li class="leak"><div class="leak-top num"><span>${esc(recurring.payee)}</span>
        <span>${inr(recurring.latest_amount)} a ${PERIOD[recurring.period]} <span class="yr">(${inr(recurring.annual_cost)} a year)</span></span></div>
        <ul>${recurring.flags.map((flag) => `<li>${esc(flag)}</li>`).join("")}</ul></li>`).join("")}</ul>` : ""}
    <h2>Spending by category</h2>
    <div class="bars">${Object.entries(data.totals).map(([key, value]) => `
      <div class="row num"><span class="label">${NAMES[key] || key}</span>
        <div class="track" aria-hidden="true"><div class="fill" style="width:${(value / maxTotal * 100).toFixed(1)}%"></div></div>
        <span class="amt">${inr(value)}</span></div>`).join("")}</div>
    ${reviewUnique.length ? `<h2 id="reviewSection">Check these payees</h2>
      <p class="result-sub">We weren't sure about these. Pick the right category once, and future payments to the same payee will use it.</p>
      <div class="scroll"><table><thead><tr><th>Payee</th><th class="r">Amount</th><th>Category</th><th></th></tr></thead><tbody>
      ${reviewUnique.map((transaction) => `<tr data-key="${esc(transaction.merchant_key)}" data-dir="${transaction.direction}">
        <td>${esc(transaction.payee || transaction.narration.slice(0, 40))}</td><td class="r num">${inr(transaction.amount)}</td>
        <td><select aria-label="Category for ${esc(transaction.payee)}">${data.categories[transaction.direction].map((category) =>
          `<option value="${category}" ${category === transaction.category ? "selected" : ""}>${NAMES[category] || category}</option>`).join("")}</select></td>
        <td><button class="save" type="button">Save</button></td></tr>`).join("")}</tbody></table></div>` : ""}
    <details id="recurringDetails"><summary>All recurring payments (${data.recurring.length})</summary>
      <div class="scroll"><table><thead><tr><th>Payee</th><th>Type</th><th class="r">Per charge</th><th class="r">Per year</th></tr></thead><tbody>
      ${data.recurring.map((recurring) => `<tr><td>${esc(recurring.payee)}</td><td>${recurring.service_type === "essential" ? "Essential" : esc(recurring.service_type.replace(/_/g, " "))}</td>
        <td class="r num">${inr(recurring.latest_amount)}/${PERIOD[recurring.period]}</td><td class="r num">${inr(recurring.annual_cost)}</td></tr>`).join("")}
      </tbody></table></div></details>
    <p class="result-meta num">${data.stats.transactions} transactions sorted in ${elapsedMs < 1000 ? Math.round(elapsedMs) + " ms" : (elapsedMs / 1000).toFixed(1) + " s"} with ${data.stats.jev_calls} calls to ${esc(data.stats.model)}${data.stats.model === "MOCK" ? " (demo answers, not the real model)" : ""}.
      ${data.stats.by_rule} were handled by rules and ${data.stats.by_memory} by payees it already knew. Model cost for this statement: $${data.stats.cost_usd.toFixed(5)}.</p>`;

  resultsContent.querySelectorAll(".save").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("tr");
      const select = row.querySelector("select");
      button.disabled = true;
      const response = await fetch("/api/correct", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ merchant_key: row.dataset.key, direction: row.dataset.dir, category: select.value }),
      });
      button.outerHTML = response.ok ? '<span class="saved">Saved</span>' : '<span class="error">Not saved</span>';
    });
  });
}

const menuToggle = $("menuToggle");
const menu = $("mobileMenu");
const menuOverlay = $("menuOverlay");
function setMenuOpen(open) {
  menuToggle.setAttribute("aria-expanded", String(open));
  menuToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  menu.hidden = !open;
  menuOverlay.hidden = !open;
  document.body.classList.toggle("menu-open", open);
}
function closeMenu() { setMenuOpen(false); }
menuToggle.addEventListener("click", () => setMenuOpen(menu.hidden));
menuOverlay.addEventListener("click", closeMenu);
menu.querySelectorAll("a").forEach((link) => link.addEventListener("click", closeMenu));
function handleNavigation(link) {
  const destination = link.textContent.trim().toLowerCase();
  closeMenu();
  if (destination === "home") { closeResults(); return; }
  if (destination === "analysis" || !current) {
    closeResults();
    fileInput.click();
    return;
  }
  results.hidden = false;
  const target = destination === "recurring" ? $("recurringDetails") : $("reviewSection");
  if (target) {
    if (destination === "recurring") target.open = true;
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
document.querySelectorAll(".nav-link, .mobile-link").forEach((link) => link.addEventListener("click", (event) => {
  event.preventDefault();
  const destination = link.textContent.trim();
  document.querySelectorAll(".nav-link, .mobile-link").forEach((item) => {
    const active = item.textContent.trim() === destination;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  handleNavigation(link);
}));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { closeMenu(); closeResults(); }
});
window.addEventListener("resize", () => { if (window.innerWidth > 720) closeMenu(); });

function countUp(element, index) {
  const target = Number(element.dataset.target);
  const decimals = Number(element.dataset.decimals);
  const suffix = element.dataset.suffix;
  const duration = 1500 + index * 80;
  const startAt = performance.now() + 480 + index * 90;
  const tick = (now) => {
    if (now < startAt) { requestAnimationFrame(tick); return; }
    const progress = Math.min((now - startAt) / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    element.textContent = (target * eased).toFixed(decimals) + suffix;
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
const statsObserver = new IntersectionObserver((entries, observer) => {
  if (entries.some((entry) => entry.isIntersecting)) {
    document.querySelectorAll(".stat-value[data-target]").forEach(countUp);
    observer.disconnect();
  }
}, { threshold: 0.25 });
statsObserver.observe(document.querySelector(".stats"));
