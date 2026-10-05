const $ = (id) => document.getElementById(id);
const KEY = "unobio.searches";
const load = () => { try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; } };
const save = (list) => { try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, 30))); } catch (e) {} };

let searches = load(); // [{query, report}]

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC[c]);
const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "");

function renderHistory(active) {
  const ul = $("history");
  ul.innerHTML = searches.length ? "" : '<li style="cursor:default">Nothing yet</li>';
  searches.forEach((s) => {
    const li = document.createElement("li");
    li.textContent = s.query;
    li.title = s.query;
    if (s.query === active) li.className = "active";
    li.onclick = () => {
      $("query").value = s.query;
      $("evaluate").disabled = false;
      renderReport(s.report);
      renderHistory(s.query);
    };
    ul.appendChild(li);
  });
}

function renderClaim(c) {
  const badge = `<span class="badge ${esc(c.type)}">${c.type === "evidence" ? "EVIDENCE" : "INFERENCE"}</span>`;
  let meta = "";
  if (c.type === "evidence") {
    const url = safeUrl(c.url);
    const src = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(c.source)}</a>` : esc(c.source);
    meta = `<span class="meta">${src}${c.exact_snippet ? " — " + esc(c.exact_snippet) : ""}</span>`;
  } else if (c.reasoning) {
    meta = `<span class="meta">${esc(c.reasoning)} (confidence: ${esc(c.confidence)})</span>`;
  }
  return `<div class="claim">${badge}${esc(c.claim)}${meta}</div>`;
}

function renderRawRecord(rec) {
  const url = safeUrl(rec.url);
  const src = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(rec.source)}</a>` : esc(rec.source);
  return `<div class="claim">${src}<span class="meta">${esc(rec.raw_snippet || "")}</span></div>`;
}

function renderRawData(rawData, analogs) {
  const cards = (rawData || []).map((s) => `
    <article class="card raw">
      <h2>${esc(s.title)}</h2>
      <div class="sub">${s.records.length} record(s)</div>
      <div class="claims">${s.records.length ? s.records.map(renderRawRecord).join("") : '<p class="sub">No data returned by this source for this query.</p>'}</div>
    </article>`).join("");
  const analogItems = (analogs || []).length
    ? analogs.map((d) => `<div class="claim">${esc(d.name)}<span class="meta">${esc(d.stage || "stage unknown")}</span></div>`).join("")
    : '<p class="sub">No known competing drugs on this target were found in Open Targets.</p>';
  return `
    <h2 class="block-title">Raw source data <span class="sub">(no LLM — pulled directly from the connected databases)</span></h2>
    <div class="grid">${cards}</div>
    <h2 class="block-title">Analogs / competing drugs</h2>
    <article class="card raw"><div class="claims">${analogItems}</div></article>`;
}

function renderReport(r) {
  const vClass = r.verdict === "Do Not Invest" ? "no" : r.verdict === "Conditional" ? "cond" : "";
  const cards = r.sections.map((s) => `
    <article class="card">
      <h2><span class="n">${s.number}</span>${esc(s.title)}</h2>
      <div class="sub">${esc(s.subtitle)}</div>
      <div class="claims">${s.claims.length ? s.claims.map(renderClaim).join("") : '<p class="sub">No claims generated for this section.</p>'}</div>
      <footer><span>${s.claims.length} claims</span>
        <span>${s.sources.length} sources${s.sources.length ? `<button type="button" data-sec="${s.id}">View sources</button>` : ""}</span></footer>
    </article>`).join("");
  const c = r.cost || {};
  $("content").innerHTML = `
    <div class="report-head"><h1>${esc(r.query)}</h1><span>Evaluation</span>
      ${r.verdict ? `<span class="verdict ${vClass}">${esc(r.verdict)}</span>` : ""}</div>
    ${renderRawData(r.raw_data, r.analogs)}
    <h2 class="block-title">Committee verdict <span class="sub">(LLM analysis of the data above)</span></h2>
    <div class="grid">${cards}</div>
    <p class="cost">~$${esc(c.approx_cost_usd)} · ${esc(c.calls)} calls · ${esc(c.model)}</p>`;
  $("content").querySelectorAll("footer button").forEach((b) => {
    b.onclick = () => openSources(r.sections.find((s) => s.id === b.dataset.sec));
  });
}

function openSources(s) {
  $("modal-title").textContent = `${s.title} — sources`;
  $("modal-list").innerHTML = s.sources.map((x) => {
    const url = safeUrl(x.url);
    const name = url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(x.source)}</a>` : esc(x.source);
    return `<li>${name} <span class="sub">(accessed ${esc(x.date_accessed)})</span></li>`;
  }).join("");
  $("modal").classList.remove("hidden");
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
    searches = [{ query, report: data }, ...searches.filter((s) => s.query !== query)];
    save(searches);
    renderHistory(query);
    renderReport(data);
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
$("modal-close").onclick = () => $("modal").classList.add("hidden");
$("modal").onclick = (e) => { if (e.target === $("modal")) $("modal").classList.add("hidden"); };
renderHistory();
