/* Landing page. Cases come from /api/cases; stamps appear only for results that were really recorded
   (/api/showcase, the static file web/showcase.json). Strings from the server go in with textContent. */
const $ = (s, root = document) => root.querySelector(s);
const el = (tag, props = {}, ...kids) => {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
};

/* one colour per contender, by position in the recording: JEV first, then the models in the order they were added */
const COLORS = ["#006666", "#5B6770", "#5A526A", "#7A6034", "#8A3B3B"];
/* teal is always JEV; the other contenders take the next colours in the order they were recorded */
function paintBackends(rec) {
  let n = 0;
  for (const b of rec.backends) b.color = b.kind === "jev" ? COLORS[0] : COLORS[1 + (n++ % (COLORS.length - 1))];
}

const QUEUE = { billing: "Billing", infra: "Infra", account: "Account", security: "Security", human_review: "Human review", page_oncall: "Page on-call" };
const fmtMs = (ms) => (ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
const pct = (x) => `${Math.round(x * 100)}%`;

/* team and page are the labels written in cases.json; null means "no single right answer" */
function verdict(t, r) {
  if (t.team == null && t.page == null) return null;
  const teamOk = t.team == null || r.team === t.team;
  const pageOk = t.page == null || (r.queue === "page_oncall") === t.page;
  return teamOk && pageOk ? "matches" : "missed";
}

function stamp(be, r, t, i) {
  if (!r) return null;
  if (!r.ok) {
    return el("div", { class: "stamp", style: `--i:${i};--ink-c:${be.color}` },
      el("b", { text: "No answer" }), el("span", { text: be.label }), el("span", { text: `Error: ${r.error}` }));
  }
  const v = verdict(t, r);
  const cost = r.cost_known ? ` · $${r.cost_usd < 0.001 ? r.cost_usd.toFixed(5) : r.cost_usd.toFixed(4)}` : "";
  return el("div", { class: "stamp", "data-verdict": v, style: `--i:${i};--ink-c:${be.color}` },
    el("b", { text: QUEUE[r.queue] || r.queue }),
    el("span", { text: be.label }),
    el("span", { text: `${pct(r.confidence)} sure · ${fmtMs(r.latency_ms)}${cost}` }),
    v ? el("span", { class: "verdict", text: v === "matches" ? "As expected" : "Not as expected" }) : null);
}

function slip(c, t, rec) {
  const row = rec?.results?.[c.id]?.[t.id];
  const stamps = row ? rec.backends.map((be, i) => stamp(be, row[be.id], t, i)).filter(Boolean) : [];
  const key = [];
  if (t.team) key.push(el("div", {}, el("dt", { text: "Correct team" }), el("dd", { text: QUEUE[t.team] })));
  if (t.page != null) key.push(el("div", {}, el("dt", { text: "Page on-call" }), el("dd", { text: t.page ? "Yes" : "No" })));
  return el("article", { class: `slip${stamps.length ? " armed" : ""}` },
    el("blockquote", { class: "slip-text", text: t.text }),
    key.length ? el("dl", { class: "slip-key" }, key) : null,
    el("p", { class: "trap", text: t.trap }),
    stamps.length ? el("div", { class: "stamps" }, stamps) : null,
    el("div", { class: "slip-foot" },
      el("a", { class: "try", href: `/lab?case=${encodeURIComponent(c.id)}&ticket=${encodeURIComponent(t.id)}`, text: "Try this ticket in the lab" })));
}

function caseBlock(c, rec) {
  return el("article", { class: "case", "aria-labelledby": `case-${c.id}` },
    el("div", { class: "case-copy" },
      el("p", { class: "over", text: c.tag }),
      el("h3", { id: `case-${c.id}`, text: c.title }),
      el("p", { text: c.situation }),
      el("p", { class: "watch" }, el("b", { text: "Watch for" }), c.watch),
      el("a", { class: "btn ghost", href: `/lab?case=${encodeURIComponent(c.id)}&run=1`, text: "Open this case in the lab" })),
    el("div", { class: "slips" }, c.tickets.map((t) => slip(c, t, rec))));
}

const joinList = (xs) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);

function recordedLine(rec) {
  if (!rec?.recorded) return "No recorded results yet.";
  const day = (iso) => new Date(iso).toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" });
  const byDay = new Map();      // a contender may have been recorded on another day than the others
  for (const b of rec.backends) {
    const d = day(b.recorded_at || rec.recorded_at);
    byDay.set(d, [...(byDay.get(d) || []), b.label]);
  }
  const when = byDay.size === 1 ? `on ${[...byDay.keys()][0]} with ${joinList(rec.backends.map((b) => b.label))}`
    : [...byDay].map(([d, ls]) => `${joinList(ls)} on ${d}`).join("; ");
  return `Recorded ${when}. A ticket goes to a person below ${pct(rec.threshold)} confidence. Run the same tickets with your own keys to compare.`;
}

/* the headline numbers: the labeled set, then the use cases, per contender */
function ledger(cases, rec) {
  if (!rec?.recorded) return el("p", { class: "empty-note", text: "No results to show yet." });
  const out = [];
  if (rec.dataset) {
    const rows = rec.backends.map((b, i) => {
      const s = rec.dataset.summary[b.id];
      if (!s) return null;
      const per1k = s.cost_known && s.done > s.errors ? (s.cost_usd / (s.done - s.errors)) * 1000 : null;
      return el("tr", {},
        el("th", { scope: "row" }, el("span", { class: "swatch", style: `background:${b.color}` }), b.label),
        el("td", {}, s.scored ? el("div", { class: "meter" }, el("i", {}, el("b", { style: `width:${s.accuracy * 100}%;background:${b.color}` })), el("span", { text: `${pct(s.accuracy)} · ${s.correct} of ${s.scored}` })) : "–"),
        el("td", { text: s.p50_ms == null ? "–" : fmtMs(s.p50_ms) }),
        el("td", { text: per1k == null ? "No price set" : per1k === 0 ? "$0" : `$${per1k < 1 ? per1k.toFixed(3) : per1k.toFixed(2)}` }));
    }).filter(Boolean);
    out.push(el("table", { class: "ledger" },
      el("caption", { text: `Labeled dataset: ${rec.dataset.n} support tickets with a known correct team` }),
      el("thead", {}, el("tr", {}, el("th", { scope: "col", text: "Contender" }), el("th", { scope: "col", text: "Correct team" }), el("th", { scope: "col", text: "Median latency" }), el("th", { scope: "col", text: "Cost per 1,000 tickets" }))),
      el("tbody", {}, rows)));
  }
  const per = rec.backends.map((b, i) => {
    let ok = 0, n = 0;
    for (const c of cases) for (const t of c.tickets) {
      const r = rec.results?.[c.id]?.[t.id]?.[b.id];
      if (!r) continue;
      const v = r.ok ? verdict(t, r) : (t.team == null && t.page == null ? null : "missed");
      if (v) { n++; ok += v === "matches" ? 1 : 0; }
    }
    return n ? el("li", {}, el("span", { class: "swatch", style: `background:${b.color}` }), `${b.label}: ${ok} of ${n} as expected`) : null;
  }).filter(Boolean);
  if (per.length) out.push(el("div", { class: "cases-tally" }, el("p", { class: "slip-label", text: "On the use cases below" }), el("ul", {}, per)));
  return el("div", {}, out);
}

async function init() {
  const list = $("#case-list");
  try {
    const [cases, rec] = await Promise.all([fetch("/api/cases").then((r) => r.json()), fetch("/api/showcase").then((r) => r.json())]);
    if (rec.recorded) paintBackends(rec);
    $("#recorded").textContent = recordedLine(rec);
    $("#ledger").replaceChildren(ledger(cases.cases, rec));
    list.replaceChildren(...cases.cases.map((c) => caseBlock(c, rec)));
  } catch (e) {
    $("#recorded").textContent = `Could not load the cases: ${e.message}`;
    return;
  }
  const armed = document.querySelectorAll(".slip.armed");
  if (!("IntersectionObserver" in window)) { armed.forEach((s) => s.classList.add("in")); return; }
  const io = new IntersectionObserver((entries) => entries.forEach((en) => {
    if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
  }), { threshold: 0.4 });
  armed.forEach((s) => io.observe(s));
}

init();
