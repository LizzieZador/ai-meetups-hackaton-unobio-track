const $ = (id) => document.getElementById(id);
const KEY = "unobio.searches";
const MAX_HISTORY = 30;

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC[c]);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
const link = (label, url) => {
  const href = safeUrl(url);
  return href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label);
};

let searches = loadHistory(); // [{query, at, report}]
let current = null; // the report on screen, so the expand buttons can find their card

function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY));
    // Entries saved by an older build of this page have a different report shape
    // and would break rendering — drop anything that isn't a card report.
    return Array.isArray(list) ? list.filter((s) => s && Array.isArray(s.report?.cards)) : [];
  } catch (e) {
    return [];
  }
}

function saveHistory() {
  // A report with every card populated is sizeable; if the browser refuses the
  // write, drop the oldest entries and retry rather than silently losing history.
  for (let keep = Math.min(searches.length, MAX_HISTORY); keep > 0; keep--) {
    try {
      localStorage.setItem(KEY, JSON.stringify(searches.slice(0, keep)));
      searches = searches.slice(0, keep);
      return;
    } catch (e) {
      /* quota exceeded — try again with fewer entries */
    }
  }
  try {
    localStorage.removeItem(KEY);
  } catch (e) {
    /* storage unavailable (private mode): history just won't persist */
  }
}

function whenLabel(at) {
  const d = new Date(at || Date.now());
  if (Number.isNaN(d.getTime())) return "";
  return d.toDateString() === new Date().toDateString() ? "Today" : d.toLocaleDateString();
}

function filledCount(report) {
  return (report.cards || []).filter((c) => c.items.length).length;
}

function renderHistory(active) {
  const ul = $("history");
  ul.innerHTML = "";
  if (!searches.length) {
    ul.innerHTML = '<li class="empty-item">Nothing yet</li>';
    return;
  }
  searches.forEach((s) => {
    const li = document.createElement("li");
    li.className = s.query === active ? "active" : "";
    li.innerHTML = `<span class="q">${esc(s.query)}</span>
      <span class="when">${esc(whenLabel(s.at))} · ${filledCount(s.report)}/${s.report.cards.length}</span>`;
    li.title = s.query;
    li.onclick = () => {
      $("query").value = s.query;
      $("evaluate").disabled = false;
      show(s.report);
      renderHistory(s.query);
    };
    ul.appendChild(li);
  });
}

function renderItem(item) {
  if (item.kind === "analog") {
    return `<div class="item"><span class="badge data">DATA</span>${esc(item.name)}
      <span class="meta">${esc(item.stage)}</span></div>`;
  }
  if (item.kind === "record") {
    return `<div class="item"><span class="badge data">DATA</span>${link(item.source, item.url)}
      <span class="meta">${esc(item.raw_snippet || "")}</span></div>`;
  }
  const isEvidence = item.type === "evidence";
  const meta = isEvidence
    ? `${link(item.source, item.url)}${item.exact_snippet ? " — " + esc(item.exact_snippet) : ""}`
    : `${esc(item.reasoning || "")}${item.confidence ? ` (confidence: ${esc(item.confidence)})` : ""}`;
  return `<div class="item"><span class="badge ${isEvidence ? "evidence" : "inference"}">${
    isEvidence ? "EVIDENCE" : "INFERENCE"
  }</span>${esc(item.claim)}<span class="meta">${meta}</span></div>`;
}

function renderItems(card) {
  if (card.items.length) return card.items.map(renderItem).join("");
  return `<p class="placeholder">${
    card.wired ? "No results yet" : "No connected source yet"
  }</p>`;
}

function cardHeading(card) {
  return `<h2><span class="n">${card.number}</span>${esc(card.title)}</h2>
    ${card.subtitle ? `<div class="sub">${esc(card.subtitle)}</div>` : ""}`;
}

function sourcesLabel(card) {
  return `${card.sources.length} source${card.sources.length === 1 ? "" : "s"}`;
}

function renderCard(card) {
  return `<article class="card">
    <div class="card-head">
      <div>${cardHeading(card)}</div>
      <button type="button" class="expand" data-card="${esc(card.id)}" title="Expand" aria-label="Expand ${esc(card.title)}">
        <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true">
          <path d="M11.5 3.5h5v5M16.5 3.5l-5.5 5.5"/><path d="M8.5 16.5h-5v-5M3.5 16.5l5.5-5.5"/>
        </svg>
      </button>
    </div>
    <div class="items">${renderItems(card)}</div>
    <div class="card-foot">${sourcesLabel(card)}</div>
  </article>`;
}

function openCard(card) {
  if (!card) return;
  const sources = card.sources.length
    ? `<div class="sources-list"><h3>Sources</h3><ul>${card.sources
        .map((s) => `<li>${link(s.source, s.url)}${s.date_accessed ? ` <span class="meta">accessed ${esc(s.date_accessed)}</span>` : ""}</li>`)
        .join("")}</ul></div>`
    : "";
  $("modal-body").innerHTML = `${cardHeading(card)}
    <div class="items">${renderItems(card)}</div>
    ${sources}
    <div class="card-foot">${sourcesLabel(card)}</div>`;
  $("modal-body").querySelector("h2").id = "modal-title";
  $("modal").classList.remove("hidden");
  $("modal-close").focus();
}

function closeModal() {
  $("modal").classList.add("hidden");
  $("modal-body").innerHTML = "";
}

function show(report) {
  current = report;
  const v = report.verdict;
  const vClass = v === "Do Not Invest" ? "no" : v === "Conditional" ? "cond" : "";
  const verdictBlock = report.verdict_claims.length
    ? `<section class="verdict-block"><h2>Committee verdict &amp; diligence questions</h2>
         <div class="items">${report.verdict_claims.map(renderItem).join("")}</div></section>`
    : "";
  const c = report.cost || {};
  $("content").innerHTML = `
    <div class="report-head">
      <h1>${esc(report.query)}</h1><span class="label">Evaluation</span>
      ${v ? `<span class="verdict ${vClass}">${esc(v)}</span>` : ""}
    </div>
    ${verdictBlock}
    <div class="grid">${report.cards.map(renderCard).join("")}</div>
    <p class="cost">~$${esc(c.approx_cost_usd)} · ${esc(c.calls)} calls · ${esc(c.model)}</p>`;
  $("content").querySelectorAll(".expand").forEach((b) => {
    b.onclick = () => openCard(current.cards.find((x) => x.id === b.dataset.card));
  });
}

async function evaluate(query) {
  $("evaluate").disabled = true;
  $("content").innerHTML = '<p class="loading">Collecting sources and running the committee… this can take a minute.</p>';
  try {
    const res = await fetch("/api/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });
    const data = await res.json().catch(() => ({ error: `Server error (${res.status})` }));
    if (!res.ok || data.error) throw new Error(data.error || `Server error (${res.status})`);
    searches = [{ query, at: Date.now(), report: data }, ...searches.filter((s) => s.query !== query)];
    saveHistory();
    renderHistory(query);
    show(data);
  } catch (e) {
    $("content").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  } finally {
    $("evaluate").disabled = !$("query").value.trim();
  }
}

$("search").onsubmit = (e) => {
  e.preventDefault();
  const q = $("query").value.trim();
  if (q && !$("evaluate").disabled) evaluate(q);
};
$("query").oninput = () => { $("evaluate").disabled = !$("query").value.trim(); };
$("toggle-sidebar").onclick = () => { $("sidebar").classList.add("collapsed"); $("open-sidebar").classList.remove("hidden"); };
$("open-sidebar").onclick = () => { $("sidebar").classList.remove("collapsed"); $("open-sidebar").classList.add("hidden"); };
$("modal-close").onclick = closeModal;
$("modal").onclick = (e) => { if (e.target === $("modal")) closeModal(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
renderHistory();
