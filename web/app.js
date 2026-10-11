const $ = (id) => document.getElementById(id);
const KEY = "unobio.searches";
const MAX_HISTORY = 30;
const chartInstances = {}; // declared early: syncChartTheme() (below) reads it at init time, before charts exist

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC[c]);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");
const link = (label, url) => {
  const href = safeUrl(url);
  return href ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label);
};

/* Theme toggle — persisted per-browser preference, defaulting to the OS
   setting on first visit. The button's sunrise/sunset artwork is driven
   entirely by CSS (data-theme selectors in style.css); this just flips
   the attribute and keeps its own a11y labels in sync. */
const THEME_KEY = "unobio.theme";

// Chart.js draws its legend/tick text on a <canvas> with its own default
// color (a mid gray, "#666"), not CSS — so it never picked up the theme and
// stayed muddy-gray on a dark card. Pull the real --text value straight from
// the page and hand it to Chart.js directly; existing charts need an
// explicit update() to repaint with it, since Chart.js resolves color only
// at creation unless re-rendered.
const SERIES_SLOTS = 6;

// Series colours live in style.css (--series-1..6, a validated row per theme).
// Reading them here keeps one source of truth: the pie slices, the ring between
// them and the table swatches all move together when the theme flips.
function themeColors() {
  const cs = getComputedStyle(document.documentElement);
  const read = (name) => cs.getPropertyValue(name).trim();
  return {
    text: read("--text"),
    card: read("--card"),
    series: Array.from({ length: SERIES_SLOTS }, (_, i) => read(`--series-${i + 1}`)),
  };
}

// Slice fill + the gap colour between slices. Only identity charts (pie/doughnut)
// get one colour per datum; anything else would be a rainbow for no reason.
function paintChartTheme(chart, colors) {
  const identity = chart.config.type === "pie" || chart.config.type === "doughnut";
  chart.data.datasets.forEach((ds) => {
    ds.borderColor = colors.card;
    if (identity && Array.isArray(ds.data)) {
      ds.backgroundColor = ds.data.map((_, i) => colors.series[i % colors.series.length]);
    }
  });
}

function syncChartTheme() {
  if (typeof Chart === "undefined") return;
  const colors = themeColors();
  Chart.defaults.color = colors.text;
  Object.values(chartInstances).forEach((chart) => {
    paintChartTheme(chart, colors);
    chart.update();
  });
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const btn = $("theme-toggle");
  const next = theme === "dark" ? "light" : "dark";
  btn.setAttribute("aria-pressed", String(theme === "dark"));
  btn.setAttribute("aria-label", `Switch to ${next} theme`);
  btn.title = `Switch to ${next} theme`;
  syncChartTheme();
}

function initTheme() {
  let theme;
  try {
    theme = localStorage.getItem(THEME_KEY);
  } catch (e) {
    theme = null;
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  applyTheme(theme);
}

$("theme-toggle").onclick = () => {
  const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  applyTheme(theme);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (e) {
    /* storage unavailable (private mode): the choice just won't persist */
  }
};

initTheme();

const SPINNER_PATHS = [
  "M108.581,64.968V14.124l44.007,25.422L108.581,64.968",
  "M153.591,92.101V41.258L109.582,66.68L153.591,92.101",
  "M155.586,92.062V41.221l44.009,25.42L155.586,92.062",
  "M200.299,119.14V68.297l-44.004,25.421L200.299,119.14",
  "M155.586,146.255V95.412l44.009,25.422L155.586,146.255",
  "M200.299,173.35v-50.844l-44.004,25.422L200.299,173.35",
  "M155.586,200.482v-50.84l44.009,25.417L155.586,200.482",
  "M153.591,200.521v-50.84l-44.009,25.418L153.591,200.521",
  "M108.581,227.696v-50.844l44.007,25.421L108.581,227.696",
  "M106.62,227.696v-50.844l-44.005,25.421L106.62,227.696",
  "M61.562,200.553V149.71l44.007,25.423L61.562,200.553",
  "M59.709,200.56v-50.843l-44.008,25.422L59.709,200.56",
  "M14.699,173.467v-50.843l44.01,25.42L14.699,173.467",
  "M59.709,146.235V95.392l-44.008,25.42L59.709,146.235",
  "M14.699,119.141V68.297l44.01,25.421L14.699,119.141",
  "M59.709,92.101V41.258L15.701,66.68L59.709,92.101",
  "M61.562,92.092V41.249l44.007,25.419L61.562,92.092",
  "M106.62,64.968V14.124L62.614,39.546L106.62,64.968",
];

function spinnerMarkup(message) {
  const paths = SPINNER_PATHS.map((d) => `<path d="${d}"/>`).join("");
  return `<div class="loading">
    <div class="spinner-wrap">
      <svg class="spinner-svg" viewBox="0 0 213.235 241.176" aria-hidden="true">${paths}</svg>
    </div>
    <p>${esc(message)}</p>
  </div>`;
}

let searches = loadHistory(); // [{indication, mechanism, at, report}]
let current = null; // the report on screen, so the expand buttons can find their card

function loadHistory() {
  try {
    const list = JSON.parse(localStorage.getItem(KEY));
    // Entries saved by an older build of this page have a different shape and
    // would break rendering — drop anything that isn't the current shape.
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
  return s.mechanism === s.indication ? s.mechanism : `${s.mechanism} — ${s.indication}`;
}

let lastActiveHistoryKey = null;
let historyFilter = "";

function verdictDotClass(verdict) {
  if (verdict === "Invest") return "invest";
  if (verdict === "Do Not Invest") return "no";
  if (verdict === "Conditional") return "cond";
  return "unknown";
}

function renderHistory(activeKey) {
  if (typeof activeKey !== "undefined") lastActiveHistoryKey = activeKey;
  const ul = $("history");
  ul.innerHTML = "";

  const filtered = historyFilter
    ? searches.filter((s) => historyLabel(s).toLowerCase().includes(historyFilter))
    : searches;

  if (!filtered.length) {
    ul.innerHTML = `<li class="empty-item">${searches.length ? "No matches" : "Nothing yet"}</li>`;
    return;
  }
  filtered.forEach((s) => {
    const key = `${s.indication}\u0000${s.mechanism}`;
    const li = document.createElement("li");
    li.className = key === lastActiveHistoryKey ? "active" : "";
    const dotClass = verdictDotClass(s.report.verdict);
    li.innerHTML = `<span class="q"><span class="history-dot ${dotClass}" title="${esc(s.report.verdict || "No verdict")}"></span>${esc(historyLabel(s))}</span>
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

$("history-search").oninput = () => {
  historyFilter = $("history-search").value.trim().toLowerCase();
  renderHistory();
};

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

// Mirrors schema.SECTION_TITLES. A builder can override the label with
// item.group (e.g. "Financials" for the rNPV claim, whose schema section is
// "recommendation").
const SECTION_LABELS = {
  recommendation: "Recommendation",
  scientific_thesis: "Scientific Thesis",
  human_translation_thesis: "Human Translation",
  clinical_development_plan: "Clinical Development Plan",
  competitive_landscape: "Competitive Landscape",
  commercial_opportunity: "Commercial Opportunity",
  capital_required: "Capital Required",
  key_risks: "Key Risks",
  critical_unknowns: "Critical Unknowns",
  diligence_questions: "Diligence Questions",
};

// Raw records first (under "Data"), then claims grouped by category, each
// category split into its Evidence and Inference claims — so a reader never
// has to scan badges to tell a sourced fact from an analyst judgement.
function renderClaimGroups(items, parentTitle) {
  const data = items.filter((i) => i.kind !== "claim");
  const groups = new Map();
  items
    .filter((i) => i.kind === "claim")
    .forEach((i) => {
      const label = i.group || SECTION_LABELS[i.section] || "Other";
      if (!groups.has(label)) groups.set(label, { evidence: [], inference: [] });
      groups.get(label)[i.type === "evidence" ? "evidence" : "inference"].push(i);
    });
  const sub = (title, list, cls) =>
    list.length
      ? `<div class="claim-subgroup"><h5 class="claim-subhead ${cls}">${esc(title)} <span class="count">${list.length}</span></h5>${list
          .map(renderItem)
          .join("")}</div>`
      : "";
  let html = data.length ? sub("Data", data, "data") : "";
  groups.forEach((g, label) => {
    const showTitle = !(groups.size === 1 && parentTitle && parentTitle.toLowerCase() === label.toLowerCase());
    html += `<div class="claim-group">${showTitle ? `<h4 class="claim-group-title">${esc(label)}</h4>` : ""}${sub(
      "Evidence",
      g.evidence,
      "evidence"
    )}${sub("Inference", g.inference, "inference")}</div>`;
  });
  return html;
}

function renderItems(card) {
  if (card.items.length) return renderClaimGroups(card.items);
  return `<p class="placeholder">${
    card.wired ? "No results yet" : "No connected source yet"
  }</p>`;
}

function renderClaimsOnly(card) {
  const claimItems = (card.items || []).filter((i) => i.kind === "claim");
  if (!claimItems.length) return "";
  return `<div class="items-below-table">${renderClaimGroups(claimItems)}</div>`;
}

function renderTableCell(value) {
  if (value && typeof value === "object" && "text" in value) {
    return value.url ? link(value.text, value.url) : esc(value.text);
  }
  const display = value === undefined || value === null || value === "" ? "Not available" : value;
  const isNA = display === "Not available";
  return `<span${isNA ? ' class="na"' : ""}>${esc(display)}</span>`;
}

// Every table gets a leading row number (card 2/Market sentiment never calls
// this — it's a chart, rendered through renderGraphDataTable instead — so the
// one card the user asked to leave alone is excluded automatically).
function renderTable(card) {
  if (!card.rows || !card.rows.length) {
    return `<p class="placeholder">${card.wired ? "No results yet" : "No connected source yet"}</p>`;
  }
  const head = `<tr><th class="row-num">#</th>${card.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const body = card.rows
    .map(
      (row, i) =>
        `<tr><td class="row-num">${i + 1}</td>${card.columns.map((c) => `<td>${renderTableCell(row[c])}</td>`).join("")}</tr>`
    )
    .join("");
  return `<table class="card-table"><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

// Maps a source URL to the row number(s) that cite it, so the Sources list in
// the expanded view can say which row each source backs — scanning every
// cell of every row for a {text,url} link, not just a specific column name,
// since which column carries the link differs per card. Numbering restarts
// at 1 per table; a composite card (more than one table, e.g. Pipeline's
// Drug/Trial, Scientific evidence's 8 sections) prefixes the table's own
// title so "#2" in two different tables is never ambiguous.
function sourceRowTags(card) {
  const tables = [];
  if (card.view === "table" && card.rows) tables.push({ label: null, rows: card.rows });
  else if (card.view === "composite" && card.sections) {
    card.sections.filter((s) => s.kind === "table" && s.rows).forEach((s) => tables.push({ label: s.title, rows: s.rows }));
  }
  const multiTable = tables.length > 1;
  // url -> table label -> sorted row numbers, rendered as compact ranges
  // ("#1–6, 9") — several rows often share one source page (e.g. every
  // Orange Book patent of a drug links to that drug's single listing).
  const numsByUrl = {};
  tables.forEach((t) => {
    t.rows.forEach((row, i) => {
      const label = multiTable && t.label ? t.label : "";
      new Set(Object.values(row).filter((v) => v && typeof v === "object" && v.url).map((v) => v.url)).forEach((url) => {
        const byLabel = (numsByUrl[url] = numsByUrl[url] || new Map());
        if (!byLabel.has(label)) byLabel.set(label, []);
        byLabel.get(label).push(i + 1);
      });
    });
  });
  const ranges = (nums) => {
    const out = [];
    nums.forEach((n) => {
      const last = out[out.length - 1];
      if (last && n === last[1] + 1) last[1] = n;
      else out.push([n, n]);
    });
    return out.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ");
  };
  const tagsByUrl = {};
  Object.entries(numsByUrl).forEach(([url, byLabel]) => {
    tagsByUrl[url] = [...byLabel].map(([label, nums]) => `${label ? `${label} ` : ""}#${ranges(nums)}`);
  });
  return tagsByUrl;
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
          ? renderClaimGroups(sec.items, sec.title)
          : '<p class="placeholder">No results yet</p>';
      }
      return `<div class="composite-section"><h3>${esc(sec.title)}</h3>${body}</div>`;
    })
    .join("");
}

function renderGraphDataTable(card) {
  if (!card.table_rows || !card.table_rows.length) return "";
  const head = `<tr><th></th>${card.table_columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const body = card.table_rows
    .map((row) => {
      const swatch = row._slot
        ? `<span class="color-dot series-${Number(row._slot)}"></span>`
        : `<span class="color-dot color-dot-muted" title="Not among the chart's top slices"></span>`;
      const cells = card.table_columns.map((c) => `<td>${renderTableCell(row[c])}</td>`).join("");
      return `<tr><td>${swatch}</td>${cells}</tr>`;
    })
    .join("");
  return `<div class="graph-data-table-wrap">
    <h4>All records (${card.table_rows.length}) — dot color matches the chart slice above</h4>
    <table class="card-table"><thead>${head}</thead><tbody>${body}</tbody></table>
  </div>`;
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
    const isModal = chartIdPrefix === "chart-modal";
    inner = card.chart
      ? `<div class="chart-wrap"><canvas id="${chartIdPrefix}-${esc(card.id)}"></canvas></div>` +
        (isModal ? renderGraphDataTable(card) : "")
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

let chartPopup = null;

function closeChartPopup() {
  if (chartPopup) {
    chartPopup.remove();
    chartPopup = null;
  }
}

// Positioned below (or, if there's no room, above) the chart's own canvas —
// never over it — and clamped horizontally into the viewport. Scrolling
// inside it is just ordinary overflow, no extra JS needed.
function openChartPopup(canvas, clickX, title, summary, sources) {
  closeChartPopup();
  const div = document.createElement("div");
  div.className = "chart-popup";
  div.innerHTML = `
    <button type="button" class="chart-popup-close" aria-label="Close">&times;</button>
    <h4>${esc(title)}</h4>
    <p>${esc(summary)}</p>
    ${
      sources.length
        ? `<ul>${sources
            .map((s) => `<li>${link(s.source, s.url)}${s.date_accessed ? ` <span class="meta">${esc(s.date_accessed)}</span>` : ""}</li>`)
            .join("")}</ul>`
        : ""
    }`;
  document.body.appendChild(div);

  const canvasRect = canvas.getBoundingClientRect();
  const popupRect = div.getBoundingClientRect();
  let left = Math.min(Math.max(clickX - popupRect.width / 2, 10), window.innerWidth - popupRect.width - 10);
  let top = canvasRect.bottom + 8;
  if (top + popupRect.height > window.innerHeight - 10) {
    top = Math.max(10, canvasRect.top - popupRect.height - 8);
  }
  div.style.left = `${left}px`;
  div.style.top = `${top}px`;

  div.querySelector(".chart-popup-close").onclick = closeChartPopup;
  chartPopup = div;
}

document.addEventListener("click", (e) => {
  if (chartPopup && !chartPopup.contains(e.target) && e.target.tagName !== "CANVAS") closeChartPopup();
});

function mountChart(canvasId, chartConfig, details) {
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
  // Cloned so each mount gets its own options object — mutating it below to
  // add onClick must never leak back into the shared report data (the same
  // chart config is reused for the grid canvas and, separately, the modal one).
  const config = JSON.parse(JSON.stringify(chartConfig));
  if (details && details.length) {
    config.options = config.options || {};
    config.options.onClick = (evt, elements) => {
      if (!elements.length) return;
      const d = details[elements[0].index];
      if (!d) return;
      openChartPopup(canvas, evt.native.clientX, d.label, d.summary, d.sources || []);
    };
  }
  const chart = new Chart(canvas, config);
  paintChartTheme(chart, themeColors());
  chart.update();
  chartInstances[canvasId] = chart;
}

function renderCard(card) {
  return `<article class="card" data-card-id="${esc(card.id)}">
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
  const rowTags = sourceRowTags(card);
  const sources = card.sources.length
    ? `<div class="sources-list"><h3>Sources</h3><ul>${card.sources
        .map((s) => {
          const tags = s.url ? rowTags[s.url] : null;
          const tagHtml = tags && tags.length ? `<span class="source-row-tag">${tags.map(esc).join(", ")}</span> ` : "";
          return `<li>${tagHtml}${link(s.source, s.url)}${s.date_accessed ? ` <span class="meta">accessed ${esc(s.date_accessed)}</span>` : ""}</li>`;
        })
        .join("")}</ul></div>`
    : "";
  $("modal-body").innerHTML = `${cardHeading(card)}
    ${gapNote(card)}
    ${renderCardBody(card, "chart-modal")}
    ${sources}
    <div class="card-foot">${sourcesLabel(card)}</div>`;
  $("modal-body").querySelector("h2").id = "modal-title";
  $("modal").classList.remove("hidden");
  openCardId = card.id;
  $("modal-close").focus();
  if (card.view === "graph" && card.chart) {
    mountChart(`chart-modal-${card.id}`, card.chart, card.chart_details);
  }
}

function closeModal() {
  $("modal").classList.add("hidden");
  $("modal-body").innerHTML = "";
  openCardId = null;
}

/* What the client is looking at right now — sent with every assistant
   message, so "this card", "here" or "why this verdict?" resolve to what's
   actually on screen. Structured state, not a screenshot. */
let openCardId = null;
let pendingQuery = null;

function visibleCards() {
  const box = $("content").getBoundingClientRect();
  return [...$("content").querySelectorAll(".grid .card")]
    .filter((el) => {
      const r = el.getBoundingClientRect();
      const shown = Math.min(r.bottom, box.bottom) - Math.max(r.top, box.top);
      return shown > Math.min(r.height, box.height) * 0.35;
    })
    .map((el) => cardRef(el.dataset.cardId));
}

function cardRef(id) {
  const card = current && current.cards.find((c) => c.id === id);
  return card ? { id: card.id, number: card.number, title: card.title } : { id };
}

function screenState() {
  const content = $("content");
  const state = {
    page: !$("welcome").classList.contains("hidden")
      ? "welcome"
      : content.querySelector(".loading")
        ? "loading"
        : content.querySelector(".error")
          ? "error"
          : current && content.querySelector(".grid")
            ? "report"
            : "empty",
    search_mode: searchMode,
    search_inputs:
      searchMode === "detailed"
        ? { indication: $("indication").value.trim(), mechanism: $("mechanism").value.trim() }
        : { query: $("query").value.trim() },
  };
  if (state.page === "welcome") state.welcome_input = $("welcome-query").value.trim();
  if (state.page === "loading") state.evaluating = pendingQuery;
  if (state.page === "error") state.error = content.querySelector(".error").textContent;
  if (current) {
    state.loaded_report = { mechanism: current.mechanism, indication: current.indication, verdict: current.verdict };
    if (state.page === "report") {
      state.visible_cards = visibleCards();
      state.open_card = openCardId ? cardRef(openCardId) : null;
      state.verdict_reasoning_open = !!content.querySelector(".verdict-wrap.pinned");
      state.chart_popup = chartPopup ? chartPopup.querySelector("h4").textContent : null;
    }
  }
  return state;
}

function show(report) {
  current = report;
  const v = report.verdict;
  const vClass = v === "Do Not Invest" ? "no" : v === "Conditional" ? "cond" : "";
  const hasReasoning = !!report.verdict_reasoning;
  const c = report.cost || {};
  $("content").innerHTML = `
    <div class="report-head">
      <h1>${
        report.mechanism === report.indication
          ? esc(report.mechanism)
          : `${esc(report.mechanism)} <span class="label">in</span> ${esc(report.indication)}`
      }</h1>
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
    .forEach((card) => mountChart(`chart-grid-${card.id}`, card.chart, card.chart_details));
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
  pendingQuery = body.query || `${body.mechanism} | ${body.indication}`;
  closeModal();
  $("content").innerHTML = spinnerMarkup("Collecting sources and running the committee… this can take a minute.");
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
    pendingQuery = null;
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

/* The two sidebar panels (Searches, Assistant) each collapse independently
   via their own header button, and share one draggable resizer that only
   does anything while both are expanded (nothing to resize otherwise). */
const MIN_PANEL_HEIGHT = 60;

function updateSidebarResizer() {
  const disabled =
    $("history-panel").classList.contains("collapsed") || $("assistant-panel").classList.contains("collapsed");
  $("sidebar-resizer").classList.toggle("disabled", disabled);
}

document.querySelectorAll(".panel-toggle").forEach((btn) => {
  btn.onclick = () => {
    const panel = btn.closest(".sidebar-panel");
    const collapsed = panel.classList.toggle("collapsed");
    btn.setAttribute("aria-expanded", String(!collapsed));
    updateSidebarResizer();
  };
});

// A horizontal drag handle that sets `target`'s height, clamped to
// [min, maxFn()]. Used for both dividers: Searches / Assistant, and the chat
// list / open chat inside the Assistant panel.
function makeResizer(resizer, target, min, maxFn) {
  let startY = 0;
  let startHeight = 0;
  function onMove(e) {
    const height = Math.min(Math.max(startHeight + (e.clientY - startY), min), Math.max(min, maxFn()));
    target.style.height = `${height}px`;
    target.style.maxHeight = "none";
  }
  function onUp() {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    resizer.classList.remove("dragging");
  }
  resizer.addEventListener("mousedown", (e) => {
    if (resizer.classList.contains("disabled")) return;
    e.preventDefault();
    startY = e.clientY;
    startHeight = target.getBoundingClientRect().height;
    resizer.classList.add("dragging");
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  });
}

makeResizer($("sidebar-resizer"), $("history-panel"), MIN_PANEL_HEIGHT, () =>
  $("sidebar").getBoundingClientRect().height - MIN_PANEL_HEIGHT - $("sidebar-resizer").getBoundingClientRect().height - 40
);

// The chat list can grow until only room for the message box and a few lines
// of the open chat is left below it.
const MIN_CHAT_LOG_HEIGHT = 90;
makeResizer($("chat-resizer"), $("assistant-chats"), 28, () => {
  const body = $("assistant-panel").querySelector(".panel-body").getBoundingClientRect();
  const list = $("assistant-chats").getBoundingClientRect();
  const form = $("assistant-form").getBoundingClientRect();
  return body.bottom - list.top - form.height - $("chat-resizer").getBoundingClientRect().height - MIN_CHAT_LOG_HEIGHT - 24;
});

$("chat-history-toggle").onclick = () => {
  const collapsed = $("chat-history").classList.toggle("collapsed");
  $("chat-history-toggle").setAttribute("aria-expanded", String(!collapsed));
  $("chat-resizer").classList.toggle("disabled", collapsed);
};

$("modal-close").onclick = closeModal;
$("modal").onclick = (e) => { if (e.target === $("modal")) closeModal(); };
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

/* Assistant panel — explains the UI/cards, answers questions about the
   report currently on screen (grounded server-side in that exact report
   JSON — see assistant.py — never free-invented), and can propose a new
   search, which the user runs with one click rather than it firing silently. */
let assistantHistory = []; // [{role: "user"|"assistant", text}], capped server-side too

// The assistant answers in a short structured format (one sentence, then "- "
// bullet lines, **bold** for key terms). Rendered as that small subset only,
// and always escaped FIRST — model text never reaches innerHTML unescaped.
function formatAssistantText(text) {
  const inline = (s) => esc(s).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const out = [];
  let bullets = [];
  const flush = () => {
    if (bullets.length) out.push(`<ul class="assistant-bullets">${bullets.map((b) => `<li>${b}</li>`).join("")}</ul>`);
    bullets = [];
  };
  String(text || "").split(/\r?\n/).forEach((line) => {
    const m = line.match(/^\s*[-•*]\s+(.*)$/);
    if (m) bullets.push(inline(m[1]));
    else if (line.trim()) { flush(); out.push(`<p class="assistant-p">${inline(line.trim())}</p>`); }
  });
  flush();
  return out.join("");
}

// Which of our databases the assistant queried for a reply — shown under it,
// so it's visible when an answer came from a live lookup vs. the screen.
const LOOKUP_LABELS = {
  drugs_for_indication: "drug candidates (Open Targets · FDA · CT.gov)",
  clinical_trials: "ClinicalTrials.gov",
  literature: "PubMed",
  target_disease_evidence: "Open Targets",
  drug_profile: "Open Targets drug profile",
  fda_approvals: "FDA approvals",
  us_patents_exclusivity: "Orange Book",
  drug_safety: "FAERS & FDA labels",
  drug_labels: "FDA labels",
  eu_approvals: "EMA",
  medicare_spending: "CMS Medicare spending",
  biologic_exclusivity: "Purple Book",
  company_filings: "SEC EDGAR",
  worldwide_patents: "Lens.org",
};

function appendAssistantMsg(who, text, cls, lookups) {
  const log = $("assistant-log");
  log.classList.remove("hidden");
  const div = document.createElement("div");
  div.className = cls ? `assistant-msg ${cls}` : "assistant-msg";
  const checked = (lookups || []).map((l) => LOOKUP_LABELS[l] || l);
  div.innerHTML = `<span class="who">${esc(who)}</span>${formatAssistantText(text)}${
    checked.length ? `<span class="assistant-lookups">Checked: ${esc(checked.join(" · "))}</span>` : ""
  }`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function appendAssistantSearch(search) {
  const log = $("assistant-log");
  const div = document.createElement("div");
  div.className = "assistant-msg assistant-search";
  div.innerHTML = `<span class="who">Suggested search</span>
    <span class="assistant-search-terms">${esc(search.mechanism)} <span class="label">in</span> ${esc(search.indication)}</span>
    <button type="button" class="assistant-search-run">Run this search</button>`;
  div.querySelector(".assistant-search-run").onclick = () => {
    setSearchMode("detailed");
    $("indication").value = search.indication;
    $("mechanism").value = search.mechanism;
    updateEvaluateEnabled();
    evaluate({ indication: search.indication, mechanism: search.mechanism });
  };
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function updateAssistantSendEnabled() {
  $("assistant-send").disabled = !$("assistant-input").value.trim();
}

/* Chat history — every conversation with the assistant is kept (per browser,
   localStorage) and listed above the chat, titled by its first question.
   Pending "…" placeholders and error notes are never saved. */
const CHATS_KEY = "unobio.chats";
const MAX_CHATS = 30;
let chats = loadChats(); // [{id, title, at, messages: [{who, text} | {search}], history}]
let activeChatId = null;

function loadChats() {
  try {
    const list = JSON.parse(localStorage.getItem(CHATS_KEY));
    return Array.isArray(list) ? list.filter((c) => c && c.id && Array.isArray(c.messages)) : [];
  } catch (e) {
    return [];
  }
}

function saveChats() {
  chats = chats.slice(0, MAX_CHATS);
  try {
    localStorage.setItem(CHATS_KEY, JSON.stringify(chats));
  } catch (e) {
    /* storage full or unavailable: chats just won't persist */
  }
}

function activeChat() {
  return chats.find((c) => c.id === activeChatId) || null;
}

function renderChatList() {
  const ul = $("assistant-chats");
  if (!chats.length) {
    ul.innerHTML = '<li class="empty-item">No chats yet</li>';
    return;
  }
  ul.innerHTML = "";
  chats.forEach((c) => {
    const li = document.createElement("li");
    li.className = c.id === activeChatId ? "active" : "";
    li.title = c.title;
    li.innerHTML = `<span class="chat-title">${esc(c.title)}</span>
      <span class="chat-when">${esc(whenLabel(c.at))}</span>
      <button type="button" class="chat-delete" aria-label="Delete chat" title="Delete chat">&times;</button>`;
    li.onclick = () => openChat(c.id);
    li.querySelector(".chat-delete").onclick = (e) => {
      e.stopPropagation();
      chats = chats.filter((x) => x.id !== c.id);
      saveChats();
      if (c.id === activeChatId) newChat();
      else renderChatList();
    };
    ul.appendChild(li);
  });
}

function openChat(id) {
  activeChatId = id;
  const chat = activeChat();
  const log = $("assistant-log");
  log.innerHTML = "";
  log.classList.toggle("hidden", !chat || !chat.messages.length);
  (chat ? chat.messages : []).forEach((m) => (m.search ? appendAssistantSearch(m.search) : appendAssistantMsg(m.who, m.text, null, m.lookups)));
  assistantHistory = chat ? chat.history || [] : [];
  renderChatList();
}

function newChat() {
  activeChatId = null;
  assistantHistory = [];
  $("assistant-log").innerHTML = "";
  $("assistant-log").classList.add("hidden");
  renderChatList();
  $("assistant-input").focus();
}

function recordChatMessage(message) {
  let chat = activeChat();
  if (!chat) {
    const first = message.text || "New chat";
    chat = {
      id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`,
      title: first.length > 60 ? `${first.slice(0, 57)}…` : first,
      at: Date.now(),
      messages: [],
      history: [],
    };
    activeChatId = chat.id;
  }
  chat.messages.push(message);
  chat.history = assistantHistory;
  chat.at = Date.now();
  chats = [chat, ...chats.filter((c) => c.id !== chat.id)];
  saveChats();
  renderChatList();
}

$("assistant-new-chat").onclick = newChat;

$("assistant-form").onsubmit = async (e) => {
  e.preventDefault();
  const text = $("assistant-input").value.trim();
  if (!text) return;
  appendAssistantMsg("You", text);
  recordChatMessage({ who: "You", text });
  const chatId = activeChatId;
  $("assistant-input").value = "";
  updateAssistantSendEnabled();
  $("assistant-send").disabled = true;
  const pending = appendAssistantMsg("Assistant", "…", "note");
  try {
    const res = await fetch("/api/assistant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: assistantHistory, report: current, screen: screenState() }),
    });
    const data = await res.json().catch(() => ({ error: `Server error (${res.status})` }));
    pending.remove();
    if (!res.ok || data.error) throw new Error(data.error || `Server error (${res.status})`);
    // The user may have switched chats while waiting: only paint the reply
    // into the log if its own chat is still the one on screen.
    if (activeChatId === chatId) {
      appendAssistantMsg("Assistant", data.reply, null, data.lookups);
      assistantHistory = [...assistantHistory, { role: "user", text }, { role: "assistant", text: data.reply }].slice(-12);
      recordChatMessage({ who: "Assistant", text: data.reply, lookups: data.lookups || [] });
      if (data.search) {
        appendAssistantSearch(data.search);
        recordChatMessage({ search: data.search });
      }
    }
  } catch (err) {
    pending.remove();
    appendAssistantMsg("Assistant", err.message, "note");
  } finally {
    updateAssistantSendEnabled();
  }
};
$("assistant-input").oninput = updateAssistantSendEnabled;
renderChatList();

/* Welcome page <-> app. The overlay stays in the DOM and is moved in and out
   with one class, so both directions run the same transition. Its box matches
   quick-search ("mechanism | indication"), which the server already parses. */
const WELCOME_MS = 420;

function leaveWelcome(then) {
  const w = $("welcome");
  if (w.classList.contains("hidden")) {
    if (then) then();
    return;
  }
  w.classList.add("leaving");
  document.body.classList.add("app-enter");
  setTimeout(() => {
    w.classList.add("hidden");
    document.body.classList.remove("app-enter");
    if (then) then();
  }, WELCOME_MS);
}

function enterWelcome() {
  const w = $("welcome");
  w.classList.remove("hidden");
  // Start from the "out" state and release it two frames later, so the browser
  // has a computed style to animate from (the element was display:none).
  w.classList.add("leaving");
  requestAnimationFrame(() => requestAnimationFrame(() => w.classList.remove("leaving")));
  updateWelcomeGoEnabled();
  $("welcome-query").focus();
}

function updateWelcomeGoEnabled() {
  $("welcome-go").disabled = !$("welcome-query").value.trim();
}

$("welcome-form").onsubmit = (e) => {
  e.preventDefault();
  const query = $("welcome-query").value.trim();
  if (!query) return;
  $("welcome-go").disabled = true;
  leaveWelcome(() => {
    setSearchMode("quick");
    $("query").value = query;
    updateEvaluateEnabled();
    evaluate({ query });
  });
};
$("welcome-query").oninput = updateWelcomeGoEnabled;
$("home-btn").onclick = enterWelcome;

/* Welcome-page helix: a solid-glass double helix, rendered with WebGL and spun
   about its own axis.

   Why the base-pair rungs matter: a perfect helix turning about its axis looks
   exactly like the same helix sliding along it (the barber-pole effect), so a
   bare pair of ribbons can only ever read as "a picture moving upwards". The
   rungs sit at fixed heights and visibly sweep round like a spiral staircase,
   which is what makes the rotation legible.

   Why the rungs are part of the body, not sticks laid over it. Three things:
   - They are solid prisms swept along the radial direction, shaded by the same
     shader as the ribbons, and they stop exactly on the ribbon's surface.
   - The depth buffer is on, so a rung is occluded PER PIXEL where a ribbon is in
     front of it. Sorting whole quads back to front (what this used to do) made a
     rung jump in front of or behind a whole ribbon at once, which is precisely
     what reads as a stick moving on its own.
   - They cast real shadows onto the ribbons, so the junction has contact.

   Shadows are a genuine shadow map: the model is rendered once from the light's
   point of view into an off-screen depth texture, and the main pass compares
   each fragment against it (3x3 PCF for a soft edge). The light is fixed in page
   space while the model turns, so the shadows sweep across the form — the
   strongest rotation cue available, and it also ties the rungs to the ribbons.

   The glass look is all per-pixel lighting: a bevel along each ribbon edge, a
   studio light strip reflected in the surface, Fresnel at grazing angles, sharp
   specular glints, and a faint accent glow inside. Depth also comes from aerial
   perspective: the far side recedes toward the page colour. Every colour comes
   from theme tokens (--helix-* and --bg), re-read on a theme flip. */
function readTokenRgb(probe, token) {
  probe.style.color = `var(${token})`;
  const m = getComputedStyle(probe).color.match(/[\d.]+/g);
  return m ? m.slice(0, 3).map(Number) : [128, 128, 128];
}

const v3 = {
  add: (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]],
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  scale: (a, k) => [a[0] * k, a[1] * k, a[2] * k],
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  norm: (a) => {
    const l = Math.hypot(a[0], a[1], a[2]) || 1;
    return [a[0] / l, a[1] / l, a[2] / l];
  },
};

const HELIX_PERIOD_MS = 24000; // one full revolution
const HELIX_FRAME_MS = 33;     // ~30 fps: plenty for a slow spin, half the GPU work of 60
const HELIX_LIGHT = v3.norm([-0.45, -0.6, 0.66]); // upper left, in front of the page (y points down)
const HELIX_RUNGS = 16;        // base pairs along the visible length (~10.5 per turn, as in DNA)
const HELIX_SHADOW_SIZE = 1024;
const HELIX_VERTEX_FLOATS = 11; // pos(2) depth(1) normal(3) near(1) edge(1) lightPos(3)
const helixReducedMotion = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
const helix = {
  gl: null, main: null, shadow: null, buf: null,
  fbo: null, shadowTex: null, shadowDepth: null,
  angle: 0, last: 0, frame: 0, running: false,
};

/* Depth packed into RGBA8: WebGL1 cannot read a depth texture without an
   extension, and this is supported everywhere. */
const HELIX_DEPTH_GLSL = `
vec4 packDepth(float d) {
  vec4 enc = fract(vec4(1.0, 255.0, 65025.0, 16581375.0) * d);
  enc -= enc.yzww * vec4(1.0 / 255.0, 1.0 / 255.0, 1.0 / 255.0, 0.0);
  return enc;
}
float unpackDepth(vec4 c) {
  return dot(c, vec4(1.0, 1.0 / 255.0, 1.0 / 65025.0, 1.0 / 16581375.0));
}`;

const HELIX_SHADOW_VS = `
attribute vec3 a_light;   // light clip space: xy in -1..1, z as depth 0..1
varying float v_depth;
void main() {
  v_depth = a_light.z;
  gl_Position = vec4(a_light.xy, a_light.z * 2.0 - 1.0, 1.0);
}`;

const HELIX_SHADOW_FS = `
precision mediump float;
varying float v_depth;
${HELIX_DEPTH_GLSL}
void main() {
  gl_FragColor = packDepth(clamp(v_depth, 0.0, 1.0));
}`;

const HELIX_VS = `
attribute vec2 a_pos;     // clip space, projected on the CPU
attribute float a_depth;  // NDC depth, so the depth buffer can sort per pixel
attribute vec3 a_normal;  // surface normal; z points at the viewer
attribute float a_near;   // 0 = far side of the helix, 1 = nearest
attribute float a_edge;   // -1 .. 1 across the ribbon (or rung) width
attribute vec3 a_light;   // this vertex in the shadow map's space
varying vec3 v_normal;
varying float v_near;
varying float v_edge;
varying vec3 v_light;
void main() {
  v_normal = a_normal;
  v_near = a_near;
  v_edge = a_edge;
  v_light = a_light;
  gl_Position = vec4(a_pos, a_depth, 1.0);
}`;

const HELIX_FS = `
precision mediump float;
varying vec3 v_normal;
varying float v_near;
varying float v_edge;
varying vec3 v_light;
uniform vec3 u_face;   // glass body
uniform vec3 u_rim;    // polished edges
uniform vec3 u_glint;  // highlights and the reflected studio light
uniform vec3 u_tint;   // a breath of accent, glowing inside
uniform vec3 u_bg;     // page colour, for aerial depth
uniform vec3 u_light;
uniform sampler2D u_shadowMap;
uniform float u_texel;
${HELIX_DEPTH_GLSL}

/* 1.0 = fully lit, 0.0 = fully shadowed. 3x3 PCF so the edge is soft rather
   than a stair-stepped outline of the shadow map's pixels. */
float shadowAt(vec3 lightPos, float ndl) {
  vec2 uv = lightPos.xy * 0.5 + 0.5;
  if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) return 1.0;
  // Surfaces nearly edge-on to the light need a bigger bias or they shadow
  // themselves in stripes (acne).
  float bias = mix(0.014, 0.0025, clamp(ndl, 0.0, 1.0));
  float lit = 0.0;
  for (int y = -1; y <= 1; y++) {
    for (int x = -1; x <= 1; x++) {
      vec2 o = vec2(float(x), float(y)) * u_texel;
      float d = unpackDepth(texture2D(u_shadowMap, uv + o));
      lit += step(lightPos.z - bias, d);
    }
  }
  return lit / 9.0;
}

void main() {
  vec3 V = vec3(0.0, 0.0, 1.0);
  vec3 N = normalize(v_normal);
  float facing = dot(N, V);
  float inside = step(facing, 0.0);          // looking at the ribbon's inner face
  N = mix(N, -N, inside);
  facing = abs(facing);
  float fresnel = pow(1.0 - facing, 2.0);
  float ndl = dot(N, u_light);
  float diffuse = max(ndl, 0.0);
  float nh = max(dot(N, normalize(u_light + V)), 0.0);
  float spec = pow(nh, 90.0);
  float sheen = pow(nh, 10.0);

  // Direct light only: ambient and the environment are not occluded, so the
  // shadowed side stays glassy instead of going flat black.
  float shade = shadowAt(v_light, ndl);
  float lit = mix(0.3, 1.0, shade);
  diffuse *= lit;
  sheen *= lit;
  spec *= shade;

  // Fake studio environment: the reflected ray's vertical direction picks
  // between a light "sky" and a dark "floor", plus one soft light strip. As the
  // helix turns, that strip glides across the surface: glossy, and a strong
  // rotation cue.
  vec3 R = reflect(-V, N);
  float up = -R.y;                           // page y points down
  vec3 env = mix(u_face * 0.22, u_glint, smoothstep(-0.5, 0.95, up));
  float strip = smoothstep(0.2, 0.0, abs(up - 0.3));

  vec3 body = u_face * (0.36 + 0.5 * diffuse);
  body = mix(body, u_tint, 0.3 * pow(facing, 2.0));    // light caught inside the glass: tells it from metal
  vec3 col = mix(body, env, 0.22 + 0.5 * fresnel);
  col += u_glint * 0.3 * strip * (0.45 + 0.55 * fresnel);

  // Bevel: a bright polished line at each edge, a thin dark band just inside it.
  float e = abs(v_edge);
  float bevel = smoothstep(0.78, 0.97, e);
  float band = smoothstep(0.55, 0.74, e) * (1.0 - bevel);
  col *= 1.0 - 0.28 * band;
  col = mix(col, mix(u_rim, u_tint, 0.15), bevel * (0.55 + 0.35 * v_near));

  col += u_glint * (0.9 * spec + 0.07 * sheen);
  col *= mix(1.0, 0.62, inside);
  // A little shade on the reflected light too. Direct light alone leaves the
  // shadow visible but weak, because most of a glass surface's brightness here
  // is environment rather than diffuse.
  col *= mix(0.7, 1.0, shade);
  col = mix(u_bg, col, mix(0.5, 1.0, v_near));         // the far side recedes
  gl_FragColor = vec4(clamp(col, 0.0, 1.0), 1.0);
}`;

function compileHelixProgram(gl, vsSrc, fsSrc) {
  const compile = (type, src) => {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
    return sh;
  };
  const prog = gl.createProgram();
  gl.attachShader(prog, compile(gl.VERTEX_SHADER, vsSrc));
  gl.attachShader(prog, compile(gl.FRAGMENT_SHADER, fsSrc));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
  return prog;
}

/* Both passes read the same interleaved buffer but use different slices of it.
   Attribute enable state is global here (no VAOs in plain WebGL1), so every
   slot is cleared before the ones this pass needs are switched on. */
const HELIX_STRIDE = HELIX_VERTEX_FLOATS * 4;
const HELIX_MAIN_LAYOUT = [
  ["a_pos", 2, 0], ["a_depth", 1, 8], ["a_normal", 3, 12],
  ["a_near", 1, 24], ["a_edge", 1, 28], ["a_light", 3, 32],
];
const HELIX_SHADOW_LAYOUT = [["a_light", 3, 32]];

function useHelixProgram(gl, prog, layout) {
  gl.useProgram(prog);
  gl.bindBuffer(gl.ARRAY_BUFFER, helix.buf);
  for (let i = 0; i < 8; i++) gl.disableVertexAttribArray(i);
  layout.forEach(([name, size, offset]) => {
    const loc = gl.getAttribLocation(prog, name);
    if (loc < 0) return;
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, size, gl.FLOAT, false, HELIX_STRIDE, offset);
  });
}

function initHelixGL() {
  const canvas = $("helix");
  try {
    const gl = canvas.getContext("webgl", {
      alpha: true, premultipliedAlpha: true, antialias: true, depth: true,
    });
    if (!gl) throw new Error("WebGL unavailable");

    helix.main = compileHelixProgram(gl, HELIX_VS, HELIX_FS);
    helix.shadow = compileHelixProgram(gl, HELIX_SHADOW_VS, HELIX_SHADOW_FS);
    helix.buf = gl.createBuffer();

    // Off-screen target for the light's-eye pass.
    helix.shadowTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, helix.shadowTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, HELIX_SHADOW_SIZE, HELIX_SHADOW_SIZE, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

    helix.shadowDepth = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, helix.shadowDepth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT16, HELIX_SHADOW_SIZE, HELIX_SHADOW_SIZE);

    helix.fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, helix.fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, helix.shadowTex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, helix.shadowDepth);
    if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) {
      throw new Error("shadow framebuffer incomplete");
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);

    gl.enable(gl.DEPTH_TEST);   // opaque model: per-pixel occlusion, not per-quad sorting
    gl.depthFunc(gl.LEQUAL);
    gl.disable(gl.BLEND);

    helix.u = {};
    gl.useProgram(helix.main);
    ["u_face", "u_rim", "u_glint", "u_tint", "u_bg", "u_light", "u_shadowMap", "u_texel"]
      .forEach((n) => { helix.u[n] = gl.getUniformLocation(helix.main, n); });
    gl.uniform3fv(helix.u.u_light, HELIX_LIGHT);
    gl.uniform1i(helix.u.u_shadowMap, 0);
    gl.uniform1f(helix.u.u_texel, 1 / HELIX_SHADOW_SIZE);

    helix.gl = gl;
    return true;
  } catch (e) {
    // No WebGL (or a driver refused the shader): the welcome page simply has no
    // illustration. Nothing else depends on it.
    console.warn("Helix disabled:", e.message);
    canvas.classList.add("hidden");
    helix.gl = null;
    return false;
  }
}

function applyHelixColors() {
  const gl = helix.gl;
  if (!gl) return;
  const probe = document.createElement("span");
  probe.style.display = "none";
  document.body.appendChild(probe);
  gl.useProgram(helix.main);
  const set = (uniform, token) => gl.uniform3fv(helix.u[uniform], readTokenRgb(probe, token).map((c) => c / 255));
  set("u_face", "--helix-face");
  set("u_rim", "--helix-rim");
  set("u_glint", "--helix-glint");
  set("u_tint", "--helix-tint");
  set("u_bg", "--bg");
  probe.remove();
}

function sizeHelixCanvas() {
  const gl = helix.gl;
  if (!gl) return;
  const canvas = $("helix");
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(window.innerWidth * dpr);
  canvas.height = Math.round(window.innerHeight * dpr);
  gl.viewport(0, 0, canvas.width, canvas.height);
}

/* One frame of geometry. Each vertex carries its screen position, its world
   position (the shadow pass needs it), the surface normal, depth cues and the
   across-the-width coordinate the bevel reads. */
function buildHelixQuads(w, h) {
  const narrow = w < 800;
  const S = Math.min(w, h * 1.35);
  const A = narrow ? [w * 0.2, -h * 0.12] : [w * 0.58, -h * 0.16];
  const B = narrow ? [w * 1.1, h * 1.12] : [w * 1.08, h * 1.16];
  const R = S * 0.19;        // cylinder radius
  const W = S * 0.12;        // ribbon width
  const TURNS = 1.5;
  const N = 240;             // segments per ribbon
  const CAM = S * 1.8;       // camera distance: strong enough perspective to read as depth
  const dx = B[0] - A[0];
  const dy = B[1] - A[1];
  const len = Math.hypot(dx, dy);
  const axis = [dx / len, dy / len, 0];
  const side = [-axis[1], axis[0], 0];   // in the page plane, across the axis
  const toViewer = [0, 0, 1];
  const dTheta = TURNS * 2 * Math.PI;

  // Perspective about the axis, so the axis itself stays a straight line.
  const project = (pt, base) => {
    const k = CAM / (CAM - pt[2]);
    return [base[0] + (pt[0] - base[0]) * k, base[1] + (pt[1] - base[1]) * k, k];
  };
  const nearOf = (z) => Math.min(1, Math.max(0, (z + R) / (2 * R)));
  const quads = [];

  // A point on the axis, and the cylinder frame there, for strand phase `phase`.
  const frameAt = (u, phase) => {
    const theta = u * dTheta + phase + helix.angle;
    const sin = Math.sin(theta);
    const cos = Math.cos(theta);
    const base = [A[0] + dx * u, A[1] + dy * u, 0];
    const radial = v3.add(v3.scale(side, sin), v3.scale(toViewer, cos));  // surface normal
    const around = v3.add(v3.scale(side, cos), v3.scale(toViewer, -sin));
    return { base, radial, around, centre: v3.add(base, v3.scale(radial, R)) };
  };

  [0, Math.PI].forEach((phase) => {
    let prev = null;
    for (let i = 0; i <= N; i++) {
      const u = i / N;
      const f = frameAt(u, phase);
      const tangent = v3.add(v3.scale(axis, len), v3.scale(f.around, R * dTheta));
      const across = v3.norm(v3.cross(f.radial, tangent));
      // Edge normals lean well outward across the width: a rounded, thick
      // ribbon, so its edges catch light even face-on.
      const wl = v3.add(f.centre, v3.scale(across, W / 2));
      const wr = v3.add(f.centre, v3.scale(across, -W / 2));
      const cur = {
        wl, wr,
        l: project(wl, f.base),
        r: project(wr, f.base),
        nl: v3.norm(v3.add(f.radial, v3.scale(across, 0.9))),
        nr: v3.norm(v3.add(f.radial, v3.scale(across, -0.9))),
        z: f.centre[2],
      };
      if (prev) {
        const near0 = nearOf(prev.z);
        const near1 = nearOf(cur.z);
        quads.push({
          z: (prev.z + cur.z) / 2,
          v: [
            [prev.l, prev.nl, near0, 1, prev.wl], [cur.l, cur.nl, near1, 1, cur.wl], [cur.r, cur.nr, near1, -1, cur.wr],
            [prev.l, prev.nl, near0, 1, prev.wl], [cur.r, cur.nr, near1, -1, cur.wr], [prev.r, prev.nr, near0, -1, prev.wr],
          ],
        });
      }
      prev = cur;
    }
  });

  /* Base-pair rungs: solid prisms swept along the radial direction, spanning one
     strand to the other straight through the axis. They stop a hair short of the
     ribbon surface (RUNG_REACH) so the two do not fight for the same depth at the
     joint — a fraction of a pixel, invisible, and the rung's own shadow on the
     ribbon is what makes the junction read as attached. */
  const RUNG_SIDES = 8;
  const RUNG_REACH = 0.998;
  const rodR = W * 0.042;
  for (let k = 0; k < HELIX_RUNGS; k++) {
    const u = (k + 0.5) / HELIX_RUNGS;
    const f = frameAt(u, 0);
    const dir = f.radial;                                  // always perpendicular to the axis
    const p1 = axis;                                       // one cross-section axis
    const p2 = v3.norm(v3.cross(dir, p1));                 // the other
    const endA = v3.add(f.base, v3.scale(dir, R * RUNG_REACH));
    const endB = v3.sub(f.base, v3.scale(dir, R * RUNG_REACH));

    const ring = (centreWorld) => {
      const pts = [];
      for (let s = 0; s < RUNG_SIDES; s++) {
        const a = (s / RUNG_SIDES) * 2 * Math.PI;
        const offset = v3.add(v3.scale(p1, Math.cos(a) * rodR), v3.scale(p2, Math.sin(a) * rodR));
        const world = v3.add(centreWorld, offset);
        pts.push({
          world,
          screen: project(world, f.base),
          normal: v3.norm(offset),
          near: nearOf(world[2]),
          edge: Math.cos(a),                               // polished line along the rod's length
        });
      }
      return pts;
    };
    const ra = ring(endA);
    const rb = ring(endB);

    for (let s = 0; s < RUNG_SIDES; s++) {
      const s2 = (s + 1) % RUNG_SIDES;
      const a = ra[s];
      const b = ra[s2];
      const c = rb[s2];
      const d = rb[s];
      quads.push({
        z: (a.world[2] + c.world[2]) / 2,
        v: [
          [a.screen, a.normal, a.near, a.edge, a.world],
          [b.screen, b.normal, b.near, b.edge, b.world],
          [c.screen, c.normal, c.near, c.edge, c.world],
          [a.screen, a.normal, a.near, a.edge, a.world],
          [c.screen, c.normal, c.near, c.edge, c.world],
          [d.screen, d.normal, d.near, d.edge, d.world],
        ],
      });
    }
  }

  quads.sort((q1, q2) => q1.z - q2.z); // back to front, so equal depths stay stable
  return { quads, A, B, R, W };
}

/* Orthographic frame for the light's-eye pass: an axis along the light, plus the
   model's extent in that frame so the shadow map covers it tightly. */
function helixLightFrame(model) {
  const f = v3.scale(HELIX_LIGHT, -1);                        // the direction light travels
  const up = Math.abs(f[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0];
  const right = v3.norm(v3.cross(up, f));
  const upv = v3.cross(f, right);

  const pad = model.R + model.W;
  const xs = [Math.min(model.A[0], model.B[0]) - pad, Math.max(model.A[0], model.B[0]) + pad];
  const ys = [Math.min(model.A[1], model.B[1]) - pad, Math.max(model.A[1], model.B[1]) + pad];
  const zs = [-pad, pad];

  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity, minD = Infinity, maxD = -Infinity;
  for (const x of xs) for (const y of ys) for (const z of zs) {
    const p = [x, y, z];
    const px = v3.dot(p, right);
    const py = v3.dot(p, upv);
    const pd = v3.dot(p, f);
    minX = Math.min(minX, px); maxX = Math.max(maxX, px);
    minY = Math.min(minY, py); maxY = Math.max(maxY, py);
    minD = Math.min(minD, pd); maxD = Math.max(maxD, pd);
  }
  return {
    right, upv, f,
    cx: (minX + maxX) / 2, hx: (maxX - minX) / 2 || 1,
    cy: (minY + maxY) / 2, hy: (maxY - minY) / 2 || 1,
    minD, spanD: (maxD - minD) || 1,
  };
}

function renderHelix() {
  const gl = helix.gl;
  if (!gl) return;
  const w = window.innerWidth;
  const h = window.innerHeight;
  const model = buildHelixQuads(w, h);
  const quads = model.quads;
  const lf = helixLightFrame(model);
  const zHalf = model.R + model.W;   // the model's own depth range, for the depth buffer

  const data = new Float32Array(quads.length * 6 * HELIX_VERTEX_FLOATS);
  let o = 0;
  for (const q of quads) {
    for (const [p, n, near, edge, world] of q.v) {
      data[o++] = (p[0] / w) * 2 - 1;  // CSS px -> clip space
      data[o++] = 1 - (p[1] / h) * 2;
      // Nearer means a larger world z, and in NDC nearer must be smaller.
      data[o++] = Math.max(-1, Math.min(1, -world[2] / zHalf));
      data[o++] = n[0];
      data[o++] = n[1];                // page coords (y down), same frame as HELIX_LIGHT
      data[o++] = n[2];
      data[o++] = near;
      data[o++] = edge;
      data[o++] = (v3.dot(world, lf.right) - lf.cx) / lf.hx;
      data[o++] = (v3.dot(world, lf.upv) - lf.cy) / lf.hy;
      data[o++] = (v3.dot(world, lf.f) - lf.minD) / lf.spanD;
    }
  }

  gl.bindBuffer(gl.ARRAY_BUFFER, helix.buf);
  gl.bufferData(gl.ARRAY_BUFFER, data, gl.DYNAMIC_DRAW);
  const count = quads.length * 6;

  // Pass 1 — depth from the light's point of view.
  gl.bindFramebuffer(gl.FRAMEBUFFER, helix.fbo);
  gl.viewport(0, 0, HELIX_SHADOW_SIZE, HELIX_SHADOW_SIZE);
  gl.clearColor(1, 1, 1, 1);          // white = farthest, so empty space never shadows
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  useHelixProgram(gl, helix.shadow, HELIX_SHADOW_LAYOUT);
  gl.drawArrays(gl.TRIANGLES, 0, count);

  // Pass 2 — the visible model, reading that depth back as shadow.
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.viewport(0, 0, $("helix").width, $("helix").height);
  gl.clearColor(0, 0, 0, 0);
  gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
  useHelixProgram(gl, helix.main, HELIX_MAIN_LAYOUT);
  gl.activeTexture(gl.TEXTURE0);
  gl.bindTexture(gl.TEXTURE_2D, helix.shadowTex);
  gl.drawArrays(gl.TRIANGLES, 0, count);
}

function helixTick(now) {
  if (!helix.running) return;
  helix.frame = requestAnimationFrame(helixTick);
  if (helix.last && now - helix.last < HELIX_FRAME_MS) return;
  // Capped, so returning to a backgrounded tab doesn't jump the model.
  const dt = helix.last ? Math.min(now - helix.last, 100) : 0;
  helix.last = now;
  helix.angle = (helix.angle + (dt / HELIX_PERIOD_MS) * 2 * Math.PI) % (2 * Math.PI);
  renderHelix();
}

function startHelix() {
  if (!helix.gl || helix.running || helixReducedMotion) return; // reduced motion: the still frame stays
  helix.running = true;
  helix.last = 0;
  helix.frame = requestAnimationFrame(helixTick);
}

function stopHelix() {
  helix.running = false;
  cancelAnimationFrame(helix.frame);
}

// Spin only while the welcome page is on screen; the app underneath never pays for it.
function syncHelixRunning() {
  if ($("welcome").classList.contains("hidden")) stopHelix();
  else startHelix();
}

let helixResizeFrame = 0;
window.addEventListener("resize", () => {
  cancelAnimationFrame(helixResizeFrame);
  helixResizeFrame = requestAnimationFrame(() => {
    sizeHelixCanvas();
    renderHelix();
  });
});
// The theme toggle only flips data-theme; re-read the palette and repaint.
new MutationObserver(() => {
  applyHelixColors();
  renderHelix();
}).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
new MutationObserver(syncHelixRunning).observe($("welcome"), { attributes: true, attributeFilter: ["class"] });
// A GPU reset drops the context; stop cleanly, and rebuild if the browser restores it.
$("helix").addEventListener("webglcontextlost", (e) => {
  e.preventDefault();
  stopHelix();
  helix.gl = null;
});
$("helix").addEventListener("webglcontextrestored", () => {
  if (!initHelixGL()) return;
  sizeHelixCanvas();
  applyHelixColors();
  renderHelix();
  syncHelixRunning();
});

setSearchMode("detailed");
renderHistory();
if (initHelixGL()) {
  sizeHelixCanvas();
  applyHelixColors();
  renderHelix();
  syncHelixRunning();
}
$("welcome-query").focus();
