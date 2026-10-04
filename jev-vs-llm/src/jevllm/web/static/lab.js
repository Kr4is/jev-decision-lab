/* The lab. Three steps: connect your contenders, choose tickets (each with the user's ground truth), run.
   No framework, no build step. Strings that come from a model or the server go in with textContent, never innerHTML. */
const $ = (s, root = document) => root.querySelector(s);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
};

const TEAMS = ["billing", "infra", "account", "security"];
const QUEUE = { billing: "Billing", infra: "Infra", account: "Account", security: "Security", human_review: "Human review", page_oncall: "Page on-call" };
const MODEL_RE = /^[A-Za-z0-9._:/-]{1,80}$/;
const MAX_TICKETS = 40;
const SETTINGS = "decision-lab.settings", TICKETS = "decision-lab.tickets";
const pct = (x) => `${Math.round(x * 100)}%`;
const money = (x) => (x === 0 ? "$0" : `$${x < 0.01 ? x.toFixed(4) : x < 1 ? x.toFixed(3) : x.toFixed(2)}`);
const fmtMs = (ms) => (ms == null ? "–" : ms < 1 ? "<1 ms" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(2)} s`);
const kindOf = (id) => id.split(":")[0];
const MAX_MODELS = 4;
/* one colour per contender: teal is always JEV, then the models in the order they were added */
const COLORS = ["#006666", "#5B6770", "#5A526A", "#7A6034", "#8A3B3B"];

const state = {
  s: { jevKey: "", provider: "openai", openai: { key: "", models: [] }, litellm: { url: "", key: "", models: [] }, prices: {} },
  auto: {}, editing: null,        // public-list prices by lane id; the lane whose price is being edited
  tickets: [], cases: [], dataset: null, controller: null,
};

/* ---------------------------------------------------------------- what the user brings (kept in this browser only) */
function loadSettings() {
  for (const store of ["localStorage", "sessionStorage"]) {
    try {
      const raw = window[store].getItem(SETTINGS);
      if (raw) {
        const s = JSON.parse(raw);
        for (const p of ["openai", "litellm"]) if (s[p] && !s[p].models) s[p].models = s[p].model ? [s[p].model] : [];
        return { s: { ...state.s, ...s }, remembered: store === "localStorage" };
      }
    } catch {}
  }
  return { s: state.s, remembered: false };
}

function saveSettings() {
  try {
    localStorage.removeItem(SETTINGS); sessionStorage.removeItem(SETTINGS);
    (($("#remember").checked) ? localStorage : sessionStorage).setItem(SETTINGS, JSON.stringify(state.s));
  } catch {}
}

const llm = () => state.s[state.s.provider];

/* a contender is ready when it has what it needs to be called: JEV, then one lane per model you added */
function lanes() {
  const jev = { id: "jev:", kind: "jev", label: "JEV · jev-1.13.0", color: COLORS[0], ready: Boolean(state.s.jevKey), need: "Paste your TypeSafe key" };
  const p = state.s.provider, c = state.s[p], name = p === "openai" ? "OpenAI" : "LiteLLM";
  const need = !c.key ? `Paste your ${name} key` : p === "litellm" && !c.url ? "Enter your LiteLLM address" : "";
  const out = [jev, ...c.models.filter((m) => MODEL_RE.test(m)).map((m, i) => ({ id: `${p}:${m}`, kind: p, model: m, label: `${name} · ${m}`, color: COLORS[(i + 1) % COLORS.length], ready: !need, need }))];
  if ($("#baseline").checked) out.push({ id: "mock:", kind: "mock", label: "Keyword baseline", color: "#8d8d86", ready: true });
  return out;
}
const jevLane = () => lanes()[0];

function specOf(l) {
  const s = { kind: l.kind };
  if (l.kind === "openai" || l.kind === "litellm") s.model = l.model;
  if (l.kind === "litellm") { s.url = state.s.litellm.url.trim(); s.key = state.s.litellm.key; }
  const p = state.s.prices[l.id];
  if (p) { s.price_in = p.in; s.price_out = p.out; }
  return s;
}

function keysBody(ls) {
  const k = {};
  if (ls.some((l) => l.kind === "jev")) k.typesafe = state.s.jevKey;
  if (ls.some((l) => l.kind === "openai")) k.openai = state.s.openai.key;
  return k;
}

/* ---------------------------------------------------------------- prices: the public list, unless the user types one */
function ensurePrice(l) {
  if (!["jev", "openai", "litellm"].includes(l.kind) || (l.kind !== "jev" && !MODEL_RE.test(l.model || "")) || l.id in state.auto) return;
  state.auto[l.id] = undefined;
  const qs = new URLSearchParams({ kind: l.kind, model: l.model || "" });
  fetch(`/api/price?${qs}`).then((r) => r.json()).then((p) => { state.auto[l.id] = p.price_in == null ? null : { in: p.price_in, out: p.price_out }; })
    .catch(() => { state.auto[l.id] = null; }).finally(renderPrices);
}

const priceKey = (l) => l.id.replace(/[^A-Za-z0-9]/g, "_");

function priceBlock(l, box) {
  ensurePrice(l);
  const mine = state.s.prices[l.id], auto = state.auto[l.id];
  if (state.editing === l.id) {
    const f = el("form", {},
      el("label", { class: "sr-only", for: `pin-${priceKey(l)}`, text: "Input price per 1M tokens" }), "in $",
      el("input", { id: `pin-${priceKey(l)}`, type: "number", min: "0", step: "any", value: mine?.in ?? auto?.in ?? "" }),
      el("label", { class: "sr-only", for: `pout-${priceKey(l)}`, text: "Output price per 1M tokens" }), "out $",
      el("input", { id: `pout-${priceKey(l)}`, type: "number", min: "0", step: "any", value: mine?.out ?? auto?.out ?? "" }), "per 1M tokens",
      el("button", { class: "linkish", type: "submit", text: "Save" }),
      el("button", { class: "linkish", type: "button", text: "Cancel", onclick: () => { state.editing = null; renderPrices(); } }));
    f.addEventListener("submit", (e) => {
      e.preventDefault();
      const i = parseFloat($(`#pin-${priceKey(l)}`).value), o = parseFloat($(`#pout-${priceKey(l)}`).value);
      if (i >= 0 && o >= 0) state.s.prices[l.id] = { in: i, out: o }; else delete state.s.prices[l.id];
      state.editing = null; saveSettings(); renderPrices();
    });
    box.replaceChildren(f);
    return;
  }
  const text = mine ? `Your price: in ${money(mine.in)} · out ${money(mine.out)} per 1M tokens`
    : auto ? `In ${money(auto.in)} · out ${money(auto.out)} per 1M tokens (public price list)`
    : auto === undefined ? "Looking up the price…" : "No price known: the cost will show as unknown";
  box.replaceChildren(el("span", { text }),
    el("button", { class: "linkish", type: "button", text: mine || auto ? "Change" : "Set a price", onclick: () => { state.editing = l.id; renderPrices(); } }),
    ...(mine ? [el("button", { class: "linkish", type: "button", text: "Use the list", onclick: () => { delete state.s.prices[l.id]; saveSettings(); renderPrices(); } })] : []));
}

function renderPrices() {
  priceBlock(jevLane(), $("#jev-price"));
  const ls = lanes().filter((l) => l.kind === state.s.provider);
  $("#llm-models").replaceChildren(...ls.map((l) => {
    const box = el("div", { class: "price" });
    priceBlock(l, box);
    return el("li", { style: `--c:${l.color}` },
      el("div", { class: "top" }, el("span", { text: l.model }),
        el("button", { class: "linkish", type: "button", text: "Remove", "aria-label": `Remove ${l.model}`, onclick: () => removeModel(l.model) })),
      box);
  }));
}

function removeModel(m) {
  const c = state.s[state.s.provider];
  c.models = c.models.filter((x) => x !== m);
  delete state.s.prices[`${state.s.provider}:${m}`];
  saveSettings(); renderConnect();
}

function addModel() {
  const v = $("#llm-model").value.trim(), hint = $("#models-hint"), c = state.s[state.s.provider];
  if (!MODEL_RE.test(v)) { hint.textContent = "Model ids use letters, digits and . _ : / - only."; return; }
  if (c.models.includes(v)) { hint.textContent = `${v} is already in the list.`; return; }
  if (c.models.length >= MAX_MODELS) { hint.textContent = `Compare up to ${MAX_MODELS} models at once.`; return; }
  c.models.push(v); $("#llm-model").value = ""; hint.textContent = "";
  saveSettings(); renderConnect();
}

/* ---------------------------------------------------------------- step 1: connect */
function renderConnect() {
  const p = state.s.provider;
  $("#llm-openai").hidden = p !== "openai"; $("#llm-litellm").hidden = p !== "litellm";
  const [jev, ...llms] = lanes().filter((l) => l.kind !== "mock");
  $("#jev-status").className = `status${jev.ready ? " ready" : ""}`;
  $("#jev-status").textContent = jev.ready ? "Ready" : jev.need;
  const ready = llms.filter((l) => l.ready).length;
  $("#llm-status").className = `status${ready ? " ready" : ""}`;
  $("#llm-status").textContent = !llms.length ? "Add at least one model" : ready ? `Ready · ${ready} model${ready === 1 ? "" : "s"}` : llms[0].need;
  renderPrices(); renderRunHint();
}

function bindConnect() {
  const { s, remembered } = loadSettings();
  state.s = { ...state.s, ...s, openai: { ...state.s.openai, ...s.openai }, litellm: { ...state.s.litellm, ...s.litellm }, prices: s.prices || {} };
  $("#remember").checked = remembered;
  $("#jev-key").value = state.s.jevKey; $("#openai-key").value = state.s.openai.key;
  $("#litellm-url").value = state.s.litellm.url; $("#litellm-key").value = state.s.litellm.key;
  document.querySelector(`input[name="provider"][value="${state.s.provider}"]`).checked = true;

  const on = (id, fn) => $(id).addEventListener("input", () => { fn($(id).value.trim()); saveSettings(); renderConnect(); });
  on("#jev-key", (v) => { state.s.jevKey = v; });
  on("#openai-key", (v) => { state.s.openai.key = v; });
  on("#litellm-url", (v) => { state.s.litellm.url = v.replace(/\/+$/, ""); });
  on("#litellm-key", (v) => { state.s.litellm.key = v; });
  $("#add-model").addEventListener("click", addModel);
  $("#llm-model").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addModel(); } });
  document.querySelectorAll('input[name="provider"]').forEach((r) => r.addEventListener("change", () => {
    state.s.provider = r.value; $("#llm-model").value = ""; $("#model-suggestions").replaceChildren(); $("#models-hint").textContent = "";
    saveSettings(); renderConnect();
  }));
  $("#connect").addEventListener("submit", (e) => e.preventDefault());
  $("#remember").addEventListener("change", saveSettings);
  $("#baseline").addEventListener("change", renderConnect);
  $("#forget").addEventListener("click", () => {
    state.s = { jevKey: "", provider: "openai", openai: { key: "", models: [] }, litellm: { url: "", key: "", models: [] }, prices: {} };
    for (const id of ["#jev-key", "#openai-key", "#litellm-url", "#litellm-key", "#llm-model"]) $(id).value = "";
    document.querySelector('input[name="provider"][value="openai"]').checked = true;
    try { localStorage.removeItem(SETTINGS); sessionStorage.removeItem(SETTINGS); } catch {}
    renderConnect();
  });
  $("#load-models").addEventListener("click", loadModels);
  const local = ["localhost", "127.0.0.1", "[::1]"].includes(location.hostname);
  $("#insecure").hidden = location.protocol !== "http:" || local;
}

async function loadModels() {
  const hint = $("#models-hint"), p = state.s.provider, c = state.s[p];
  if (!c.key || (p === "litellm" && !c.url)) { hint.textContent = p === "openai" ? "Paste your OpenAI key first." : "Enter the address and key first."; return; }
  hint.textContent = "Loading…";
  try {
    const r = await fetch("/api/models", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(p === "openai" ? { provider: "openai", key: c.key } : { provider: "litellm", key: c.key, url: c.url }) });
    const j = await r.json();
    if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "request refused");
    $("#model-suggestions").replaceChildren(...j.models.map((m) => el("option", { value: m })));
    hint.textContent = `${j.models.length} models. Click the box and pick one.`;
  } catch (e) { hint.textContent = `Could not load them: ${e.message}`; }
}

/* ---------------------------------------------------------------- step 2: tickets with ground truth */
let uid = 0;
const newId = () => { let id; do { id = `u${++uid}`; } while (state.tickets.some((t) => t.id === id)); return id; };
const blank = () => ({ id: newId(), text: "", team: "", page: "" });
const fromCase = (t) => ({ id: t.id, text: t.text, team: t.team ?? "none", page: t.page == null ? "" : t.page ? "yes" : "no" });

function saveTickets() { try { sessionStorage.setItem(TICKETS, JSON.stringify(state.tickets)); } catch {} }

function rowOf(t) {
  const team = el("select", { class: "team", "aria-label": "Correct team" },
    el("option", { value: "", text: "Choose…" }), ...TEAMS.map((x) => el("option", { value: x, text: QUEUE[x] })), el("option", { value: "none", text: "No single answer" }));
  team.value = t.team;
  const page = el("select", { "aria-label": "Should it page on-call?" },
    el("option", { value: "", text: "Not asked" }), el("option", { value: "yes", text: "Yes" }), el("option", { value: "no", text: "No" }));
  page.value = t.page;
  const text = el("textarea", { rows: "2", maxlength: "4000", "aria-label": "Ticket text", placeholder: "Paste the ticket text" });
  text.value = t.text;
  const note = el("span", { class: "row-note" });
  const tr = el("tr", {}, el("td", {}, text, note), el("td", {}, team), el("td", {}, page),
    el("td", {}, el("button", { class: "linkish", type: "button", text: "Remove", "aria-label": "Remove this ticket", onclick: () => { state.tickets = state.tickets.filter((x) => x !== t); saveTickets(); renderTickets(); } })));
  const check = () => { const bad = !t.team; tr.classList.toggle("invalid", bad); note.textContent = bad ? "Choose the correct team so the results can be checked." : ""; };
  text.addEventListener("input", () => { t.text = text.value; saveTickets(); renderRunHint(); });
  team.addEventListener("change", () => { t.team = team.value; saveTickets(); check(); renderTicketCount(); });
  page.addEventListener("change", () => { t.page = page.value; saveTickets(); renderTicketCount(); });
  if (!t.team && t.text) check();
  return tr;
}

function renderTickets() {
  $("#tickets tbody").replaceChildren(...state.tickets.map(rowOf));
  $("#tickets").hidden = !state.tickets.length; $("#no-tickets").hidden = state.tickets.length > 0;
  renderTicketCount(); renderRunHint();
}

function renderTicketCount() {
  const n = state.tickets.length, scored = state.tickets.filter((t) => TEAMS.includes(t.team)).length, paged = state.tickets.filter((t) => t.page).length;
  $("#ticket-count").textContent = n ? `${n} ticket${n === 1 ? "" : "s"} · ${scored} with a correct team to check · ${paged} with a paging answer` : "";
}

function addTickets(list) {
  const room = MAX_TICKETS - state.tickets.length;
  state.tickets.push(...list.slice(0, Math.max(0, room)));
  saveTickets(); renderTickets();
  return list.length > room ? list.length - room : 0;
}

async function loadSource() {
  const v = $("#source").value;
  if (!v) return;
  if (v === "dataset") {
    state.dataset ??= (await (await fetch("/api/dataset")).json()).tickets.map(fromCase);
    state.tickets = state.dataset.map((t) => ({ ...t }));
  } else {
    const c = state.cases.find((x) => x.id === v);
    state.tickets = c.tickets.map(fromCase);
  }
  saveTickets(); renderTickets();
}

/* a small CSV reader: quoted fields may hold commas, quotes ("") and line breaks */
function parseCsv(text) {
  const rows = []; let row = [], cur = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) { if (c === '"') { if (text[i + 1] === '"') { cur += '"'; i++; } else q = false; } else cur += c; }
    else if (c === '"') q = true;
    else if (c === ",") { row.push(cur); cur = ""; }
    else if (c === "\n" || c === "\r") { if (c === "\r" && text[i + 1] === "\n") i++; row.push(cur); rows.push(row); row = []; cur = ""; }
    else cur += c;
  }
  if (cur || row.length) { row.push(cur); rows.push(row); }
  return rows.filter((r) => r.some((x) => x.trim()));
}

const normTeam = (v) => { const t = String(v ?? "").trim().toLowerCase(); return TEAMS.includes(t) ? t : ["none", "n/a", "-"].includes(t) ? "none" : ""; };
const normPage = (v) => { const t = String(v ?? "").trim().toLowerCase(); return ["yes", "true", "1", "y"].includes(t) ? "yes" : ["no", "false", "0", "n"].includes(t) ? "no" : ""; };

function parseBatch(raw) {
  const text = raw.trim();
  if (!text) return { rows: [], error: "Paste some tickets first." };
  const out = [];
  if (text.startsWith("{")) {
    for (const [i, line] of text.split(/\n+/).entries()) {
      try { const o = JSON.parse(line); out.push({ text: String(o.text ?? ""), team: normTeam(o.team), page: o.page === true ? "yes" : o.page === false ? "no" : normPage(o.page) }); }
      catch { return { rows: [], error: `Line ${i + 1} is not valid JSON.` }; }
    }
  } else {
    const rows = parseCsv(text), head = rows[0].map((h) => h.trim().toLowerCase());
    const col = (names) => head.findIndex((h) => names.includes(h));
    const [ti, tm, pg] = [col(["text", "ticket"]), col(["team", "correct_team", "label"]), col(["page", "page_oncall", "oncall"])];
    if (ti < 0) return { rows: [], error: "The first row must name the columns, with at least text and team." };
    for (const r of rows.slice(1)) out.push({ text: r[ti] ?? "", team: tm < 0 ? "" : normTeam(r[tm]), page: pg < 0 ? "" : normPage(r[pg]) });
  }
  return { rows: out.filter((r) => r.text.trim()).map((r) => ({ id: newId(), ...r })), error: "" };
}

function bindTickets() {
  $("#add-ticket").addEventListener("click", () => { addTickets([blank()]); const rows = $("#tickets tbody textarea"); rows[rows.length - 1]?.focus(); });
  $("#load-source").addEventListener("click", loadSource);
  $("#clear-tickets").addEventListener("click", () => { state.tickets = []; saveTickets(); renderTickets(); });
  $("#open-paste").addEventListener("click", () => { $("#paste").hidden = false; $("#paste-text").focus(); });
  $("#paste-cancel").addEventListener("click", () => { $("#paste").hidden = true; $("#paste-hint").textContent = ""; });
  $("#paste-add").addEventListener("click", () => {
    const { rows, error } = parseBatch($("#paste-text").value);
    if (error) { $("#paste-hint").textContent = error; return; }
    const cut = addTickets(rows);
    const missing = rows.filter((r) => !r.team).length;
    $("#paste-hint").textContent = `${rows.length - cut} added.${cut ? ` ${cut} left out: the lab takes ${MAX_TICKETS} at most.` : ""}${missing ? ` ${missing} still need a correct team.` : ""}`;
    if (!cut) { $("#paste-text").value = ""; if (!missing) $("#paste").hidden = true; }
  });
}

/* ---------------------------------------------------------------- step 3: run and read */
function renderRunHint() {
  const ls = lanes().filter((l) => l.ready), n = state.tickets.length;
  const calls = n * ls.length;
  $("#run-hint").textContent = !ls.length ? "Connect at least one contender in step 1, or tick the keyword baseline to try the lab without a key."
    : !n ? "Add tickets in step 2." : `${n} ticket${n === 1 ? "" : "s"} × ${ls.length} contender${ls.length === 1 ? "" : "s"} = ${calls} call${calls === 1 ? "" : "s"}.`;
}

async function stream(body, onEvent, signal) {
  const r = await fetch("/api/batch", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { const j = await r.json(); msg = j.detail ? (Array.isArray(j.detail) ? j.detail.map((d) => `${(d.loc || []).slice(-1)}: ${d.msg}`).join("; ") : j.detail) : msg; } catch {}
    throw new Error(msg);
  }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = /^event: (.*)$/m.exec(chunk), da = /^data: (.*)$/m.exec(chunk);
      if (ev && da) onEvent(ev[1], JSON.parse(da[1]));
    }
  }
}

const requestTickets = (list) => list.map((t) => ({ id: t.id, text: t.text.trim(), team: TEAMS.includes(t.team) ? t.team : null, page: t.page === "yes" ? true : t.page === "no" ? false : null }));
const thresholdValue = () => { const v = parseFloat($("#threshold").value); return v >= 0 && v <= 1 ? v : 0.6; };

function problem() {
  const ls = lanes().filter((l) => l.ready);
  if (!ls.length) return "Connect at least one contender in step 1.";
  if (!state.tickets.length) return "Add at least one ticket in step 2.";
  const empty = state.tickets.findIndex((t) => !t.text.trim());
  if (empty >= 0) return `Ticket ${empty + 1} has no text.`;
  const noTeam = state.tickets.findIndex((t) => !t.team);
  if (noTeam >= 0) { document.querySelectorAll("#tickets tbody tr")[noTeam]?.classList.add("invalid"); return `Ticket ${noTeam + 1} needs its correct team. Pick "No single answer" if there is none.`; }
  return "";
}

function chip(d, ticket) {
  if (!d) return el("div", { class: "chip waiting" }, el("span", { text: "Waiting…" }));
  if (!d.ok) return el("div", { class: "chip wrong" }, el("span", { class: "queue", text: "No answer" }), el("span", { class: "facts", text: d.error }));
  const scored = d.correct !== null || d.page_correct !== null;
  const bad = d.correct === false || d.page_correct === false;
  const why = d.correct === false && d.page_correct === false ? "Wrong team and wrong paging" : d.correct === false ? "Wrong team" : d.page_correct === false ? "Wrong paging" : "Correct";
  const top = Object.entries(d.team_probs).sort((a, b) => b[1] - a[1]);
  return el("div", { class: `chip${scored ? (bad ? " wrong" : " right") : ""}` },
    el("span", { class: "queue", text: QUEUE[d.team] || d.team }),   // what it decided; the verdict below is about this
    d.queue !== d.team ? el("span", { class: "facts", text: `Routed to ${QUEUE[d.queue] || d.queue}${d.queue === "human_review" ? " (below your confidence threshold)" : ""}` }) : null,
    el("span", { text: `${pct(d.confidence)} sure · ${fmtMs(d.latency_ms)}` }),
    el("span", { class: "verdict", text: scored ? why : "Not scored" }),
    el("details", {}, el("summary", { text: "Details" }),
      el("div", { class: "probs" }, top.map(([k, p], i) => el("div", { class: `prob${i === 0 ? " top" : ""}` }, el("span", { text: k }), el("i", {}, el("b", { style: `width:${p * 100}%` })), el("span", { text: pct(p) })))),
      el("p", { class: "facts", text: `Urgent ${pct(d.urgent_prob)} · severity ${d.severity.toFixed(1)} / 2 · ${d.input_tokens} in / ${d.output_tokens} out tokens${d.tokens_estimated ? " (est.)" : ""}${d.cost_known ? ` · ${money(d.cost_usd)}` : ""}` })));
}

function scoreCard(l, s, firstError) {
  const rows = [];
  const acc = s?.accuracy;
  rows.push(el("div", {}, el("dt", { text: "Correct team" }), el("dd", { text: s && s.scored ? `${s.correct} of ${s.scored} (${pct(acc)})` : "Nothing to check" })));
  rows.push(el("div", {}, el("dt", { text: "Paging on-call" }), el("dd", { text: s && s.paging[1] ? `${s.paging[0]} of ${s.paging[1]} right` : "Not asked" })));
  rows.push(el("div", {}, el("dt", { text: "Median latency" }), el("dd", { text: s ? fmtMs(s.p50_ms) : "–" })));
  const per1k = s && s.cost_known && s.done > s.errors ? (s.cost_usd / (s.done - s.errors)) * 1000 : null;
  rows.push(el("div", {}, el("dt", { text: "Cost" }), el("dd", { text: !s ? "–" : s.cost_known ? `${money(s.cost_usd)} · ${money(per1k ?? 0)} per 1,000` : "No price set" })));
  return el("article", { class: "card", "data-kind": l.kind, style: `--c:${l.color}` },
    el("h3", { text: l.label }),
    el("div", { class: "bar", "aria-hidden": "true" }, el("i", { style: `width:${acc == null ? 0 : acc * 100}%` })),
    el("dl", {}, rows),
    s && s.errors ? el("p", { class: "fail", text: `${s.errors} call${s.errors === 1 ? "" : "s"} failed${firstError ? `: ${firstError}` : ""}` }) : null);
}

async function run() {
  const msg = problem();
  $("#problem").textContent = msg;
  if (msg) return;
  const ls = lanes().filter((l) => l.ready), list = state.tickets.slice();
  state.controller = new AbortController();
  $("#run").disabled = true; $("#stop").hidden = false;
  $("#results").hidden = false;
  const cells = new Map(), summaries = new Map(), firstError = new Map(); let done = 0, total = list.length * ls.length;
  const renderScore = () => $("#score").replaceChildren(...ls.map((l) => scoreCard(l, summaries.get(l.id), firstError.get(l.id))));
  $("#compare thead").replaceChildren(el("tr", {}, el("th", { scope: "col", text: "Ticket" }), el("th", { scope: "col", text: "You expect" }), ...ls.map((l) => el("th", { scope: "col", text: l.label }))));
  $("#compare tbody").replaceChildren(...list.map((t) => {
    const tr = el("tr", {}, el("td", { class: "q" }, el("p", { text: t.text })),
      el("td", { class: "truth" }, el("b", { text: TEAMS.includes(t.team) ? QUEUE[t.team] : "No single answer" }), t.page ? el("div", { text: `Page on-call: ${t.page === "yes" ? "yes" : "no"}` }) : null));
    for (const l of ls) { const td = el("td", {}, chip(null)); cells.set(`${t.id}|${l.id}`, td); tr.append(td); }
    return tr;
  }));
  renderScore();
  $("#progress").textContent = "Starting…";
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    await stream({ lanes: ls.map(specOf), tickets: requestTickets(list), keys: keysBody(ls), threshold: thresholdValue() }, (ev, d) => {
      if (ev === "item") {
        done++;
        cells.get(`${d.ticket}|${d.id}`)?.replaceChildren(chip(d));
        summaries.set(d.id, d.summary);
        if (!d.ok && !firstError.has(d.id)) firstError.set(d.id, d.error);
        renderScore();
        $("#progress").textContent = `${done} of ${total} answers in`;
      }
      if (ev === "done") $("#progress").textContent = `Finished: ${done} answers.`;
    }, state.controller.signal);
  } catch (e) {
    $("#progress").textContent = e.name === "AbortError" ? `Stopped after ${done} of ${total} answers.` : `Could not run the comparison: ${e.message}`;
  } finally { $("#run").disabled = false; $("#stop").hidden = true; }
}

/* ---------------------------------------------------------------- start */
async function init() {
  state.cases = (await (await fetch("/api/cases")).json()).cases;
  $("#source").replaceChildren(el("option", { value: "", text: "A use case…" }),
    ...state.cases.map((c) => el("option", { value: c.id, text: `${c.title} (${c.tickets.length} tickets)` })),
    el("option", { value: "dataset", text: "Labeled dataset (40 tickets)" }));
  bindConnect(); bindTickets();
  $("#run").addEventListener("click", run);
  $("#stop").addEventListener("click", () => state.controller?.abort());
  const qs = new URLSearchParams(location.search), c = state.cases.find((x) => x.id === qs.get("case"));
  try { state.tickets = JSON.parse(sessionStorage.getItem(TICKETS) || "[]"); } catch { state.tickets = []; }
  if (c) {          // a link from the landing page: that case, or just one of its tickets
    const one = c.tickets.find((t) => t.id === qs.get("ticket"));
    state.tickets = (one ? [one] : c.tickets).map(fromCase);
    $("#source").value = c.id;
  }
  uid = state.tickets.length;
  renderConnect(); renderTickets();
  if (c) $("#step-tickets").scrollIntoView();
}

init().catch((e) => { $("#problem").textContent = `Could not reach the server: ${e.message}`; });
