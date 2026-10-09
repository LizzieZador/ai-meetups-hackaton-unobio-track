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

let searches = loadHistory(); // [{indication, mechanism, at, report}]
let current = null; // the report on screen, so the expand buttons can find their card

function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY));
    // Entries saved by an older build of this page (single combined "query",
    // or quick_summary/verdict_claims-shaped reports) have a different shape
    // and would break rendering — drop anything that isn't the current shape.
    return Array.isArray(list)
      ? list.filter((s) => s && s.indication && s.mechanism && Array.isArray(s.report?.cards))
      : [];
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

function historyLabel(s) {
  return `${s.mechanism} — ${s.indication}`;
}

function renderHistory(activeKey) {
  const ul = $("history");
  ul.innerHTML = "";
  if (!searches.length) {
    ul.innerHTML = '<li class="empty-item">Nothing yet</li>';
    return;
  }
  searches.forEach((s) => {
    const key = `${s.indication}\u0000${s.mechanism}`;
    const li = document.createElement("li");
    li.className = key === activeKey ? "active" : "";
    li.innerHTML = `<span class="q">${esc(historyLabel(s))}</span>
      <span class="when">${esc(whenLabel(s.at))} · ${filledCount(s.report)}/${s.report.cards.length}</span>`;
    li.title = historyLabel(s);
    li.onclick = () => {
      setSearchMode("detailed");
      $("indication").value = s.indication;
      $("mechanism").value = s.mechanism;
      $("evaluate").disabled = false;
      show(s.report);
      renderHistory(key);
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

function renderClaimsOnly(card) {
  const claimItems = (card.items || []).filter((i) => i.kind === "claim");
  if (!claimItems.length) return "";
  return `<div class="items-below-table">${claimItems.map(renderItem).join("")}</div>`;
}

function renderTableCell(value) {
  if (value && typeof value === "object" && "text" in value) {
    return value.url ? link(value.text, value.url) : esc(value.text);
  }
  const display = value === undefined || value === null || value === "" ? "Not available" : value;
  const isNA = display === "Not available";
  return `<span${isNA ? ' class="na"' : ""}>${esc(display)}</span>`;
}

function renderTable(card) {
  if (!card.rows || !card.rows.length) {
    return `<p class="placeholder">${card.wired ? "No results yet" : "No connected source yet"}</p>`;
  }
  const head = `<tr>${card.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const body = card.rows
    .map((row) => `<tr>${card.columns.map((c) => `<td>${renderTableCell(row[c])}</td>`).join("")}</tr>`)
    .join("");
  return `<table class="card-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function renderComposite(card) {
  return card.sections
    .map((sec) => {
      let body;
      if (sec.kind === "text") {
        body = `<p class="composite-text">${esc(sec.text)}</p>`;
      } else if (sec.kind === "table") {
        body = renderTable({ columns: sec.columns, rows: sec.rows, wired: true });
      } else {
        body = sec.items.length
          ? sec.items.map(renderItem).join("")
          : '<p class="placeholder">No results yet</p>';
      }
      return `<div class="composite-section"><h3>${esc(sec.title)}</h3>${body}</div>`;
    })
    .join("");
}

function renderTimeline(card) {
  if (!card.rows || !card.rows.length) {
    return `<p class="placeholder">${card.wired ? "No results yet" : "No connected source yet"}</p>`;
  }
  return `<div class="timeline-list">${card.rows
    .map((r) => {
      const track = r.stages
        .map((s, i) => {
          const state = i > r.reached_index ? "pending" : i === r.reached_index ? "current" : "done";
          return `<div class="timeline-stage ${state}"><span class="dot"></span><span class="label">${esc(s)}</span></div>`;
        })
        .join("");
      return `<div class="timeline-row">
        <div class="timeline-drug">${esc(r.drug)}</div>
        <div class="timeline-track">${track}</div>
      </div>`;
    })
    .join("")}</div>`;
}

function renderCardBody(card, chartIdPrefix) {
  let inner;
  if (card.view === "table") {
    inner = renderTable(card) + (card.show_claims_below ? renderClaimsOnly(card) : "");
  } else if (card.view === "graph") {
    inner = card.chart
      ? `<div class="chart-wrap"><canvas id="${chartIdPrefix}-${esc(card.id)}"></canvas></div>`
      : `<p class="placeholder">${card.wired ? "No results yet" : "No connected source yet"}</p>`;
  } else if (card.view === "composite") {
    inner = renderComposite(card);
  } else if (card.view === "timeline") {
    inner = renderTimeline(card);
  } else {
    inner = renderItems(card);
  }
  return `<div class="items">${inner}</div>`;
}

function gapNote(card) {
  return card.data_gap_note ? `<p class="data-gap-note">${esc(card.data_gap_note)}</p>` : "";
}

function cardHeading(card) {
  return `<h2><span class="n">${card.number}</span>${esc(card.title)}</h2>
    ${card.subtitle ? `<div class="sub">${esc(card.subtitle)}</div>` : ""}`;
}

function sourcesLabel(card) {
  return `${card.sources.length} source${card.sources.length === 1 ? "" : "s"}`;
}

const chartInstances = {};

function mountChart(canvasId, chartConfig) {
  if (chartInstances[canvasId]) {
    chartInstances[canvasId].destroy();
    delete chartInstances[canvasId];
  }
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  if (typeof Chart === "undefined") {
    // Fails silently otherwise (an empty canvas looks identical to "no data") —
    // this is almost always the Chart.js CDN script failing to load (network
    // block, ad blocker, offline), not a backend data problem.
    canvas.closest(".chart-wrap").innerHTML =
      '<p class="placeholder">Chart library failed to load (check network/ad blocker) — raw data is still listed under Sources below.</p>';
    return;
  }
  chartInstances[canvasId] = new Chart(canvas, chartConfig);
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
    ${gapNote(card)}
    ${renderCardBody(card, "chart-grid")}
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
    ${gapNote(card)}
    ${renderCardBody(card, "chart-modal")}
    ${sources}
    <div class="card-foot">${sourcesLabel(card)}</div>`;
  $("modal-body").querySelector("h2").id = "modal-title";
  $("modal").classList.remove("hidden");
  $("modal-close").focus();
  if (card.view === "graph" && card.chart) {
    mountChart(`chart-modal-${card.id}`, card.chart);
  }
}

function closeModal() {
  $("modal").classList.add("hidden");
  $("modal-body").innerHTML = "";
}

function show(report) {
  current = report;
  const v = report.verdict;
  const vClass = v === "Do Not Invest" ? "no" : v === "Conditional" ? "cond" : "";
  const hasReasoning = !!report.verdict_reasoning;
  const c = report.cost || {};
  $("content").innerHTML = `
    <div class="report-head">
      <h1>${esc(report.mechanism)} <span class="label">in</span> ${esc(report.indication)}</h1>
      ${
        v
          ? `<span class="verdict-wrap">
               <button type="button" class="verdict ${vClass}"${hasReasoning ? "" : " disabled"}>${esc(v)}</button>
               ${hasReasoning ? `<span class="verdict-tip" role="tooltip">${esc(report.verdict_reasoning)}</span>` : ""}
             </span>`
          : ""
      }
    </div>
    <div class="grid">${report.cards.map(renderCard).join("")}</div>
    <p class="cost">~$${esc(c.approx_cost_usd)} · ${esc(c.calls)} calls · ${esc(c.model)}</p>`;
  $("content").querySelectorAll(".expand").forEach((b) => {
    b.onclick = () => openCard(current.cards.find((x) => x.id === b.dataset.card));
  });
  report.cards
    .filter((card) => card.view === "graph" && card.chart)
    .forEach((card) => mountChart(`chart-grid-${card.id}`, card.chart));
  if (hasReasoning) {
    const wrap = $("content").querySelector(".verdict-wrap");
    wrap.querySelector(".verdict").onclick = () => wrap.classList.toggle("pinned");
  }
}

// One global listener (not re-added per search) closes a pinned verdict
// tooltip when clicking anywhere outside it.
document.addEventListener("click", (e) => {
  const wrap = $("content").querySelector(".verdict-wrap.pinned");
  if (wrap && !wrap.contains(e.target)) wrap.classList.remove("pinned");
});

async function evaluate(body) {
  $("evaluate").disabled = true;
  $("content").innerHTML = '<p class="loading">Collecting sources and running the committee… this can take a minute.</p>';
  try {
    const res = await fetch("/api/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({ error: `Server error (${res.status})` }));
    if (!res.ok || data.error) throw new Error(data.error || `Server error (${res.status})`);
    // The server resolves "query" into indication/mechanism either way, so
    // history always keys off its response, not the raw form input.
    const { indication, mechanism } = data;
    searches = [
      { indication, mechanism, at: Date.now(), report: data },
      ...searches.filter((s) => !(s.indication === indication && s.mechanism === mechanism)),
    ];
    saveHistory();
    renderHistory(`${indication}\u0000${mechanism}`);
    show(data);
  } catch (e) {
    $("content").innerHTML = `<p class="error">${esc(e.message)}</p>`;
  } finally {
    updateEvaluateEnabled();
  }
}

// "detailed" = separate Indication/Mechanism fields (default); "quick" = one
// combined box, parsed server-side the same way the old single-field search
// used to work ("mechanism | indication" / "... for ..." / "... in ...").
let searchMode = "detailed";

function setSearchMode(mode) {
  searchMode = mode;
  $("search-detailed").classList.toggle("hidden", mode !== "detailed");
  $("search-quick").classList.toggle("hidden", mode !== "quick");
  $("search-mode-toggle").textContent = mode === "detailed" ? "Quick search" : "Detailed search";
  updateEvaluateEnabled();
}

function updateEvaluateEnabled() {
  $("evaluate").disabled =
    searchMode === "detailed"
      ? !($("indication").value.trim() && $("mechanism").value.trim())
      : !$("query").value.trim();
}

$("search-mode-toggle").onclick = () => setSearchMode(searchMode === "detailed" ? "quick" : "detailed");

$("search").onsubmit = (e) => {
  e.preventDefault();
  if ($("evaluate").disabled) return;
  if (searchMode === "detailed") {
    evaluate({ indication: $("indication").value.trim(), mechanism: $("mechanism").value.trim() });
  } else {
    evaluate({ query: $("query").value.trim() });
  }
};
$("indication").oninput = updateEvaluateEnabled;
$("mechanism").oninput = updateEvaluateEnabled;
$("query").oninput = updateEvaluateEnabled;
$("toggle-sidebar").onclick = () => { $("sidebar").classList.add("collapsed"); $("open-sidebar").classList.remove("hidden"); };
$("open-sidebar").onclick = () => { $("sidebar").classList.remove("collapsed"); $("open-sidebar").classList.add("hidden"); };
$("modal-close").onclick = closeModal;
$("modal").onclick = (e) => { if (e.target === $("modal")) closeModal(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* Assistant panel — input + send shell for the helper model.
   The model is not wired up yet: a sent message is logged with a note saying so,
   so the control is never silently dead. Wiring it means replacing the note
   below with a call to an /api/assistant endpoint. */
function appendAssistantMsg(who, text, cls) {
  const log = $("assistant-log");
  log.classList.remove("hidden");
  const div = document.createElement("div");
  div.className = cls ? `assistant-msg ${cls}` : "assistant-msg";
  div.innerHTML = `<span class="who">${esc(who)}</span>${esc(text)}`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function updateAssistantSendEnabled() {
  $("assistant-send").disabled = !$("assistant-input").value.trim();
}

$("assistant-form").onsubmit = (e) => {
  e.preventDefault();
  const text = $("assistant-input").value.trim();
  if (!text) return;
  appendAssistantMsg("You", text);
  $("assistant-input").value = "";
  updateAssistantSendEnabled();
  appendAssistantMsg("Assistant", "Not connected to the model yet — this window only takes the message for now.", "note");
};
$("assistant-input").oninput = updateAssistantSendEnabled;

setSearchMode("detailed");
renderHistory();
