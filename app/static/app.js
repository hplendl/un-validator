/* UN Validator front end: dataset picker, live pipeline (SSE), dashboard, findings, map.
 * Security: every value that comes from data (layer names, messages, metadata, paths) goes through esc()
 * before it is placed in HTML, and there are no inline event handlers (the server sends a CSP that forbids them).
 * The only raw HTML is the footer, which is configured on the server (config.FOOTER_HTML). */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const JOB_ID = /^[A-Za-z0-9]{1,40}$/;
const fmt = (n) => (n === null || n === undefined || n === "") ? "" : (typeof n === "number" ? n.toLocaleString("en-US") : n);
const scoreColor = (v) => v == null ? "#8a94a6" : v >= 90 ? "#1f9d63" : v >= 80 ? "#5fa82e" : v >= 70 ? "#d9a21a" : v >= 60 ? "#dd6b20" : "#d23f3f";
const SEVCOL = { error: "#d23f3f", warning: "#d9921a", info: "#2f7fd8" };

const S = {
  cfg: null, mode: "dataset", job: null, es: null, result: null, dsIdx: 0, t0: 0, timer: null,
  stages: [], stageState: {}, counts: { error: 0, warning: 0, info: 0 }, checksRun: 0,
  dsTotal: 1, dsIndex: 0, stagesDone: 0, curFrac: 0, map: null, mapLayers: null,
};

/* ---------------- init ---------------- */
async function init() {
  S.cfg = await (await fetch("/api/config")).json();
  document.title = S.cfg.title;
  $("#appTitle").textContent = S.cfg.title;
  $("#hdrVersion").textContent = "v" + S.cfg.version;
  $("#footer").innerHTML = S.cfg.footer_html;
  S.stages = S.cfg.stages;
  buildPipeline();
  buildCheckList();
  $$("#modeSeg button").forEach((b) => b.onclick = () => setMode(b.dataset.mode));
  $$("#tabs button[data-tab]").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
  $("#runBtn").onclick = run;
  $("#cancelBtn").onclick = () => S.job && fetch(`/api/jobs/${S.job}/cancel`, { method: "POST" });
  $("#resultDs").onchange = (e) => { S.dsIdx = +e.target.value; renderResult(); };
  $$(".filters input, .filters select").forEach((el) => el.addEventListener("input", renderFindings));
  ["dsSelect", "folderSelect"].forEach((id) => $("#" + id).addEventListener("change", showSelInfo));
  $("#dash").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-ds]"); if (tr) selectDs(+tr.dataset.ds); });
  $("#findTable tbody").addEventListener("click", (e) => {
    const det = e.target.closest(".det"); if (det) { det.classList.toggle("open"); return; }
    const b = e.target.closest("button[data-zoom]");
    if (b) { const f = S.shownFindings[+b.dataset.zoom]; if (f) zoomTo(f.layer, f.check); }
  });
  await loadDatasets();
  const q = new URLSearchParams(location.search);
  if (q.get("job") && JOB_ID.test(q.get("job"))) attachJob(q.get("job"));
}

async function loadDatasets() {
  const d = await (await fetch("/api/datasets")).json();
  S.datasets = d;
  const groups = {};
  d.datasets.forEach((x) => (groups[x.group] = groups[x.group] || []).push(x));
  $("#dsSelect").innerHTML = Object.entries(groups).map(([g, xs]) =>
    `<optgroup label="${esc(g)}">` + xs.map((x) => `<option value="${esc(x.path)}" data-rel="${esc(x.rel)}" data-mb="${esc(x.size_mb)}">${esc(x.name)}${x.kind === "gpkg" ? " (gpkg)" : ""}</option>`).join("") + "</optgroup>").join("");
  $("#folderSelect").innerHTML = d.folders.map((f) => `<option value="${esc(f.path)}" data-rel="${esc(f.rel)}">${esc(f.rel)}  (${esc(f.count)})</option>`).join("");
  const nap = [...$("#dsSelect").options].find((o) => /NapervilleElectric/.test(o.text));
  if (nap) nap.selected = true;
  showSelInfo();
}

function showSelInfo() {
  let txt = "";
  if (S.mode === "dataset") { const o = $("#dsSelect").selectedOptions[0]; if (o) txt = `${o.dataset.rel} · ${o.dataset.mb} MB`; }
  if (S.mode === "folder") { const o = $("#folderSelect").selectedOptions[0]; if (o) txt = `${o.text.trim()} datasets will run in sequence`; }
  $("#dsInfo").textContent = txt || `Data folder: ${S.datasets ? S.datasets.root : ""}`;
}

function setMode(m) {
  S.mode = m;
  $$("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === m));
  $("#modeDataset").classList.toggle("hidden", m !== "dataset");
  $("#modeFolder").classList.toggle("hidden", m !== "folder");
  $("#modePath").classList.toggle("hidden", m !== "path");
  showSelInfo();
}

function showTab(t) {
  $$("#tabs button[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
  $$(".tabpane").forEach((p) => p.classList.toggle("on", p.id === "pane-" + t));
  if (t === "map") setTimeout(renderMap, 30);
}

function buildCheckList() {
  $("#checkList").innerHTML = S.stages.map((s, i) => {
    const cs = S.cfg.checks[s.id] || [];
    return `<div class="cg">${i + 1}. ${esc(s.name)}</div>` + cs.map((c) =>
      `<div class="ci" title="${esc(c.description)}">${esc(c.name)}${c.plugin ? '<span class="tag">plugin</span>' : ""}${c.requires.length ? `<span class="tag req">needs ${esc(c.requires.join(","))}</span>` : ""}</div>`).join("");
  }).join("");
}

/* ---------------- pipeline ---------------- */
function buildPipeline() {
  $("#pipeline").innerHTML = S.stages.map((s, i) => `
    <div class="stage" id="stg-${s.id}">
      <div><span class="num">${i + 1}</span><span class="nm">${esc(s.name)}</span></div>
      <div class="st">Waiting</div>
      <div class="bar"><div></div></div>
      <div class="foot"><span class="score"></span><span class="chips"></span></div>
    </div>`).join("");
  S.stageState = {};
  S.stages.forEach((s) => S.stageState[s.id] = { total: 0, idx: 0, frac: 0, counts: { error: 0, warning: 0, info: 0 } });
}
function stageEl(id) { return $("#stg-" + id); }
function setStageBar(id) {
  const st = S.stageState[id];
  const v = st.total ? Math.min(1, (st.idx + st.frac) / st.total) : 0;
  $(".bar div", stageEl(id)).style.width = (v * 100).toFixed(1) + "%";
  S.curFrac = v;
  updateOverall();
}
function updateOverall() {
  const per = S.stages.length;
  const v = (S.dsIndex * per + S.stagesDone + S.curFrac) / (S.dsTotal * per);
  $("#overallBar").style.width = (Math.min(1, v) * 100).toFixed(1) + "%";
  $("#overallPct").textContent = Math.round(Math.min(1, v) * 100) + "%";
}
function setChips(id) {
  const c = S.stageState[id].counts;
  $(".chips", stageEl(id)).innerHTML = (c.error ? `<span class="e">${c.error}</span>` : "") + (c.warning ? `<span class="w">${c.warning}</span>` : "") + (c.info ? `<span class="i">${c.info}</span>` : "");
}

/* ---------------- run + events ---------------- */
async function run() {
  let path = "";
  if (S.mode === "dataset") path = $("#dsSelect").value;
  else if (S.mode === "folder") path = $("#folderSelect").value;
  else path = $("#pathInput").value.trim();
  if (!path) { alert("Choose a dataset, folder or path first"); return; }
  const body = { path, pace_ms: +$("#paceSelect").value, profile_only: $("#profileOnly").checked, rules_path: $("#rulesPath").value.trim(), dispositions_path: $("#dispPath").value.trim() };
  const r = await fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) { alert(typeof j.detail === "string" ? j.detail : "Could not start"); return; }
  history.replaceState(null, "", "?job=" + j.job_id);
  attachJob(j.job_id);
}

function resetRun() {
  buildPipeline();
  $("#log").innerHTML = ""; $("#feed").innerHTML = "";
  S.counts = { error: 0, warning: 0, info: 0 }; S.checksRun = 0; S.stagesDone = 0; S.curFrac = 0; S.dsIndex = 0; S.dsTotal = 1;
  S.result = null; S.dsIdx = 0;
  updateCounts(); updateOverall();
  ["exportHtml", "exportCsv", "exportQuestions", "exportManifest"].forEach((id) => $("#" + id).classList.add("disabled"));
  $("#dash").className = "empty"; $("#dash").textContent = "Validation running...";
  $("#details").className = "empty"; $("#details").textContent = "Validation running...";
  $("#findTable tbody").innerHTML = ""; $("#tabFindCount").textContent = "";
  $("#resultDsWrap").classList.add("hidden");
}

function attachJob(id) {
  if (S.es) S.es.close();
  resetRun();
  S.job = id; S.t0 = Date.now();
  setStatus("running", "Running");
  $("#runBtn").disabled = true; $("#cancelBtn").disabled = false;
  showTab("live");
  clearInterval(S.timer);
  S.timer = setInterval(() => $("#stElapsed").textContent = ((Date.now() - S.t0) / 1000).toFixed(1) + " s", 200);
  const es = new EventSource(`/api/jobs/${id}/events`);
  S.es = es;
  const kinds = ["job_queued", "job_start", "dataset_start", "stage_start", "check_start", "check_progress", "check_end", "stage_end", "log", "finding", "dataset_end", "job_end", "job_error", "eof"];
  kinds.forEach((k) => es.addEventListener(k, (e) => handle(k, JSON.parse(e.data))));
  es.onerror = () => { /* browser reconnects with Last-Event-ID; the server resumes and closes with eof */ };
}

function setStatus(cls, txt) { const h = $("#hdrStatus"); h.className = "hdr-status " + cls; h.textContent = txt; }

let logCount = 0;
function logLine(html, cls = "") {
  const box = $("#log");
  const d = document.createElement("div");
  d.className = "ln " + cls; d.innerHTML = html;
  box.appendChild(d);
  if (++logCount > 4000) { box.firstChild.remove(); logCount--; }
  if ($("#autoScroll").checked) box.scrollTop = box.scrollHeight;
}
const ts = (t) => `<span class="t">${(t ?? 0).toFixed(1).padStart(6, " ")}s</span>`;

function handle(kind, ev) {
  switch (kind) {
    case "job_queued":
      setStatus("queued", "Queued");
      logLine(`${ts(ev.t)}Waiting for ${esc(ev.position)} other run(s) to finish`, "hd");
      break;
    case "job_start":
      setStatus("running", "Running");
      S.dsTotal = ev.targets.length;
      if (ev.profile_only && ev.stages && ev.stages.length) { S.stages = ev.stages; buildPipeline(); }
      logLine(`${ts(ev.t)}${ev.profile_only ? "Profile" : "Validation"} started: ${esc(ev.targets.length)} dataset(s)`, "hd");
      break;
    case "dataset_start":
      S.dsIndex = ev.index; S.stagesDone = 0; S.curFrac = 0;
      if (ev.index > 0) buildPipeline();
      $("#runLabel").textContent = `Dataset ${ev.index + 1} of ${ev.total}: ${ev.dataset}`;
      $("#stDataset").textContent = `${ev.index + 1}/${ev.total} ${ev.dataset}`;
      logLine(`${ts(ev.t)}━━ ${esc(ev.dataset)}  (${esc(ev.path)})`, "hd");
      break;
    case "stage_start": {
      const el = stageEl(ev.stage);
      el.classList.add("active");
      S.stageState[ev.stage].total = ev.checks.length; S.stageState[ev.stage].idx = 0; S.stageState[ev.stage].frac = 0;
      $(".st", el).textContent = `${ev.checks.length} checks queued`;
      logLine(`${ts(ev.t)}▶ Stage: ${esc(ev.name)}`, "hd");
      setStageBar(ev.stage);
      break;
    }
    case "check_start": {
      const st = S.stageState[ev.stage]; st.idx = ev.index; st.frac = 0;
      $(".st", stageEl(ev.stage)).innerHTML = `<b>${esc(ev.index + 1)}/${esc(ev.total)}</b> ${esc(ev.check)}`;
      logLine(`${ts(ev.t)}<span class="sg">[${esc(ev.stage)}]</span><span class="ck">● ${esc(ev.check)}</span>`);
      setStageBar(ev.stage);
      break;
    }
    case "check_progress": {
      const st = S.stageState[ev.stage]; st.frac = ev.frac;
      if (ev.label) $(".st", stageEl(ev.stage)).innerHTML = `<b>${esc(st.idx + 1)}/${esc(st.total)}</b> ${esc(ev.check)}<br>${esc(ev.label)}`;
      setStageBar(ev.stage);
      break;
    }
    case "check_end":
      S.checksRun++; $("#stChecks").textContent = S.checksRun;
      S.stageState[ev.stage].idx = ev.index + 1; S.stageState[ev.stage].frac = 0; setStageBar(ev.stage);
      if (ev.status === "error") logLine(`${ts(ev.t)}<span class="sg">[${esc(ev.stage)}]</span>✖ ${esc(ev.check)} failed`, "error");
      break;
    case "stage_end": {
      const el = stageEl(ev.stage);
      el.classList.remove("active"); el.classList.add("done");
      S.stagesDone++; S.curFrac = 0; updateOverall();
      $(".bar div", el).style.width = "100%";
      const sc = $(".score", el);
      if (num(ev.score) != null) { sc.textContent = Math.round(ev.score); sc.style.color = scoreColor(ev.score); } else sc.textContent = "✓";
      $(".st", el).textContent = `Done in ${ev.seconds}s`;
      logLine(`${ts(ev.t)}■ Stage finished${ev.score != null ? " · score " + esc(ev.score) : ""} · ${esc(ev.counts.error)} errors, ${esc(ev.counts.warning)} warnings, ${esc(ev.counts.info)} info`, "hd");
      break;
    }
    case "log":
      logLine(`${ts(ev.t)}<span class="sg">[${esc(ev.stage)}]</span>${esc(ev.msg)}`, ev.level === "warn" ? "warn" : ev.level === "error" ? "error" : "");
      break;
    case "finding": {
      if (!(ev.severity in S.counts)) break;
      S.counts[ev.severity]++; updateCounts();
      const st = S.stageState[ev.stage]; if (st) { st.counts[ev.severity]++; setChips(ev.stage); }
      const f = document.createElement("div");
      f.className = "fi " + ev.severity;
      f.innerHTML = `<small>${esc(ev.severity.toUpperCase())} · ${esc(ev.check)}${ev.layer ? " · " + esc(ev.layer) : ""}</small>${esc(ev.message)}`;
      const feed = $("#feed"); feed.prepend(f);
      if (feed.childElementCount > 300) feed.lastChild.remove();
      break;
    }
    case "dataset_end":
      logLine(`${ts(ev.t)}${ev.status === "failed" ? "✖" : "✔"} ${esc(ev.dataset)}: overall ${esc(ev.overall ?? "n/a")} (${esc(ev.grade)})${ev.status === "cancelled" ? " · stopped early" : ""}`, "hd");
      break;
    case "job_end":
      S.dsIndex = S.dsTotal - 1; S.stagesDone = S.stages.length; S.curFrac = 0; updateOverall();
      finish(true, ev.status);
      break;
    case "job_error":
      logLine(esc(ev.error) + "\n" + esc(ev.trace || ""), "error");
      finish(false);
      break;
    case "eof":
      S.es && S.es.close();
      if (!S.result && $("#hdrStatus").textContent === "Running") finish(true);
      break;
  }
}

function updateCounts() {
  $("#cntError").textContent = S.counts.error; $("#cntWarning").textContent = S.counts.warning; $("#cntInfo").textContent = S.counts.info;
}

async function finish(ok, status = "done") {
  clearInterval(S.timer);
  $("#runBtn").disabled = false; $("#cancelBtn").disabled = true;
  if (S.es) S.es.close();
  if (!ok) { setStatus("failed", "Failed"); return; }
  const r = await fetch(`/api/jobs/${S.job}/result`);
  if (!r.ok) { if (status === "cancelled") setStatus("cancelled", "Stopped"); else setStatus("failed", "No result"); return; }
  S.result = await r.json();
  if (status === "cancelled") setStatus("cancelled", "Stopped"); else setStatus("done", "Complete");
  $("#runLabel").textContent = S.result.datasets.length > 1
    ? `${S.result.datasets.length} datasets · mean score ${S.result.overall ?? "n/a"} (${S.result.grade})`
    : S.result.datasets.length ? `${S.result.datasets[0].dataset} · score ${S.result.overall ?? "n/a"} (${S.result.grade})` : "Stopped before the first dataset";
  if (!S.result.datasets.length) return;
  $("#exportHtml").href = `/api/jobs/${S.job}/report.html`; $("#exportCsv").href = `/api/jobs/${S.job}/findings.csv`;
  $("#exportQuestions").href = `/api/jobs/${S.job}/questions.csv`; $("#exportManifest").href = `/api/jobs/${S.job}/manifest.json`;
  ["exportHtml", "exportCsv", "exportQuestions", "exportManifest"].forEach((id) => $("#" + id).classList.remove("disabled"));
  const sel = $("#resultDs");
  sel.innerHTML = S.result.datasets.map((d, i) => `<option value="${i}">${esc(d.dataset)}</option>`).join("");
  $("#resultDsWrap").classList.toggle("hidden", S.result.datasets.length < 2);
  S.dsIdx = 0;
  renderResult();
  showTab("dash");
}

/* ---------------- results ---------------- */
function cur() { return S.result && S.result.datasets[S.dsIdx]; }

function renderResult() {
  renderDashboard(); renderFindingFilters(); renderFindings(); renderDetails();
  S.mapDirty = true;
  if ($("#pane-map").classList.contains("on")) renderMap();
}

function ring(v, grade) {
  v = num(v);
  const r = 50, c = 2 * Math.PI * r, f = v == null ? 0 : v / 100;
  return `<svg viewBox="0 0 120 120"><circle cx="60" cy="60" r="${r}" fill="none" stroke="#e7ebf2" stroke-width="11"/>
    <circle cx="60" cy="60" r="${r}" fill="none" stroke="${scoreColor(v)}" stroke-width="11" stroke-linecap="round"
      stroke-dasharray="${(c * f).toFixed(1)} ${c.toFixed(1)}" transform="rotate(-90 60 60)"/>
    <text x="60" y="62" text-anchor="middle" font-size="27" font-weight="700" fill="#1b2333">${v == null ? "n/a" : v.toFixed(1)}</text>
    <text x="60" y="84" text-anchor="middle" font-size="13" fill="#6a7489">grade ${esc(grade)}</text></svg>`;
}

function renderDashboard() {
  const d = cur(); if (!d) return;
  const s = d.summary || {};
  const stageNames = Object.fromEntries(S.stages.map((x) => [x.id, x.name]));
  const comps = d.components || {};
  const stageCards = ["metadata", "lineage", "schema", "quality"].map((k) => {
    const v = d.stages[k];
    const items = (comps[k] || []).map((c) => `<div class="comp"><span>${esc(c.label)}</span><b style="color:${scoreColor(c.score)}">${esc(Math.round(c.score))}</b></div>`).join("");
    return `<div class="card sc2"><h4>${esc(stageNames[k])}</h4><div class="big" style="color:${scoreColor(v)}">${num(v) == null ? "n/a" : Math.round(v)}</div>
      <div class="hbar"><div style="width:${num(v) ?? 0}%;background:${scoreColor(v)}"></div></div>${items}</div>`;
  }).join("");
  const top = d.findings.filter((f) => f.severity !== "info").slice(0, 8).map((f) =>
    `<div class="fi ${f.severity}"><small>${esc(f.severity.toUpperCase())} · ${esc(stageNames[f.stage] || f.stage)}${f.layer ? " · " + esc(f.layer) : ""}</small>${esc(f.message)}</div>`).join("") || '<div class="muted">No errors or warnings.</div>';
  const rank = (d.reference_ranking || []).slice(0, 3).map((r) => `<div class="comp"><span>${esc(String(r.name).replace("_AssetPackage", ""))}</span><b>${esc(r.score)}</b></div>`).join("");
  let multi = "";
  if (S.result.datasets.length > 1) {
    multi = `<div class="card span2"><h4>All datasets in this run (click a row for details) (mean ${esc(S.result.overall ?? "n/a")}, ${esc(S.result.grade)})</h4><table class="grid"><thead><tr><th>Dataset</th><th class="num">Overall</th><th>Grade</th><th class="num">Metadata</th><th class="num">Lineage</th><th class="num">Schema</th><th class="num">Quality</th><th class="num">Errors</th><th class="num">Warnings</th><th class="num">Features</th></tr></thead><tbody>` +
      S.result.datasets.map((x, i) => `<tr style="cursor:pointer" data-ds="${i}"><td>${esc(x.dataset)}</td><td class="num"><b style="color:${scoreColor(x.overall)}">${esc(x.overall ?? "n/a")}</b></td><td>${esc(x.grade)}</td>` +
        ["metadata", "lineage", "schema", "quality"].map((k) => `<td class="num">${esc(x.stages[k] ?? "n/a")}</td>`).join("") +
        `<td class="num">${esc(x.counts.error)}</td><td class="num">${esc(x.counts.warning)}</td><td class="num">${esc(fmt(x.summary.features))}</td></tr>`).join("") + "</tbody></table></div>";
  }
  $("#dash").className = "dash";
  $("#dash").innerHTML = multi + `
    <div class="card"><h4>Overall quality score</h4><div class="scorering">${ring(d.overall, d.grade)}
      <div class="ds-meta"><b>${esc(d.dataset)}</b><br>${esc(s.kind || "")}<br>
      <span style="color:#d23f3f">${esc(d.counts.error)} errors</span> · <span style="color:#b0700c">${esc(d.counts.warning)} warnings</span> · <span style="color:#2f7fd8">${esc(d.counts.info)} info</span></div></div>
      <div class="ds-meta" style="margin-top:8px">
        <b>${esc(fmt(s.feature_classes))}</b> feature classes · <b>${esc(fmt(s.tables))}</b> tables · <b>${esc(fmt(s.features))}</b> features<br>
        <b>${esc(fmt(s.domains))}</b> domains · ${s.utility_networks && s.utility_networks.length ? "utility network <b>" + esc(s.utility_networks.join(", ")) + "</b>" : "no utility network controller"}<br>
        Reference model: <b>${esc(String(s.reference_model || "none").replace("_AssetPackage", ""))}</b><br>
        ${S.result.signature ? `Signature <span class="sig">${esc(S.result.signature.slice(0, 16))}</span><br>` : ""}
        ${(S.result.questions || []).length ? `<b>${esc((S.result.questions || []).length)}</b> open questions<br>` : ""}
        ${d.status === "cancelled" ? '<b style="color:#b0700c">Stopped early: partial result</b><br>' : ""}Validated in <b>${esc(d.seconds)}</b> s<br><span style="font-size:11px">${esc(d.display_path || d.path)}</span></div>
      ${rank ? `<h4 style="margin-top:12px">Closest UN Foundation models</h4>${rank}` : ""}
    </div>
    <div style="display:flex;flex-direction:column;gap:14px">
      <div class="stagecards">${stageCards}</div>
      <div class="card topf"><h4>Top issues</h4>${top}</div>
    </div>`;
}
function selectDs(i) { S.dsIdx = i; $("#resultDs").value = i; renderResult(); }

function renderFindingFilters() {
  const d = cur(); if (!d) return;
  const stageNames = Object.fromEntries(S.stages.map((x) => [x.id, x.name]));
  const stages = [...new Set(d.findings.map((f) => f.stage))];
  $("#fStage").innerHTML = `<option value="">All stages</option>` + stages.map((s) => `<option value="${esc(s)}">${esc(stageNames[s] || s)}</option>`).join("");
  const layers = [...new Set(d.findings.map((f) => f.layer).filter(Boolean))].sort();
  $("#fLayer").innerHTML = `<option value="">All layers</option>` + layers.map((l) => `<option>${esc(l)}</option>`).join("");
  ["error", "warning", "info"].forEach((s) => $("#fc" + s[0].toUpperCase() + s.slice(1)).textContent = `(${(d.counts || {})[s] || 0})`);
  $("#tabFindCount").textContent = d.findings.length;
}

function renderFindings() {
  const d = cur(); if (!d) return;
  const stageNames = Object.fromEntries(S.stages.map((x) => [x.id, x.name]));
  const sev = new Set($$(".filters .chk input").filter((i) => i.checked).map((i) => i.value));
  const st = $("#fStage").value, ly = $("#fLayer").value, q = $("#fSearch").value.toLowerCase();
  const mapKeys = new Set(((d.map && d.map.flagged) || []).map((f) => f.properties.layer + "|" + f.properties.check));
  const rows = d.findings.filter((f) => sev.has(f.severity) && (!st || f.stage === st) && (!ly || f.layer === ly) &&
    (!q || (f.message + " " + f.detail + " " + f.layer + " " + f.check).toLowerCase().includes(q)));
  S.shownFindings = rows;
  $("#fShown").textContent = `${rows.length} of ${d.findings.length} shown`;
  $("#findTable tbody").innerHTML = rows.map((f, i) => {
    const det = f.detail && !f.detail.includes("Traceback") ? `<div class="det">${esc(f.detail)}</div>` : "";
    const canMap = f.layer && mapKeys.has(f.layer + "|" + f.check);
    return `<tr><td><span class="sev ${esc(f.severity)}">${esc(f.severity)}</span></td><td>${esc(stageNames[f.stage] || f.stage)}</td><td>${esc(f.check)}</td>
      <td>${esc(f.layer)}</td><td class="num">${fmt(f.count)}</td><td>${esc(f.message)}${det}</td>
      <td class="oids">${esc((f.sample_ids || []).slice(0, 8).join(", "))}${(f.sample_ids || []).length > 8 ? "…" : ""}</td>
      <td>${canMap ? `<button class="linkbtn" data-zoom="${i}">map ›</button>` : ""}</td></tr>`;
  }).join("");
}

/* ---------------- map ---------------- */
function renderMap() {
  const d = cur();
  if (!S.map) {
    S.map = L.map("map", { preferCanvas: true }).setView([39.5, -98.35], 4);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 20, maxNativeZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(S.map);
  }
  S.map.invalidateSize();
  if (!d || !S.mapDirty) return;
  S.mapDirty = false;
  if (S.mapLayers) S.mapLayers.forEach((l) => S.map.removeLayer(l));
  const m = d.map || { context: [], flagged: [] };
  const ctx = L.geoJSON({ type: "FeatureCollection", features: m.context }, {
    style: { color: "#6b7385", weight: 1, opacity: 0.55, fillOpacity: 0.05 },
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 2, color: "#6b7385", weight: 1, fillOpacity: 0.5 }),
    interactive: false,
  });
  const order = { info: 0, warning: 1, error: 2 };
  const flagged = [...m.flagged].sort((a, b) => order[a.properties.severity] - order[b.properties.severity]);
  const fl = L.geoJSON({ type: "FeatureCollection", features: flagged }, {
    style: (f) => ({ color: SEVCOL[f.properties.severity] || "#000", weight: 3, opacity: 0.9, fillOpacity: 0.25 }),
    pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 5, color: "#fff", weight: 1, fillColor: SEVCOL[f.properties.severity], fillOpacity: 0.95 }),
    onEachFeature: (f, l) => l.bindPopup(`<b>${esc(f.properties.label)}</b><br>${esc(f.properties.layer)} · OID ${esc(f.properties.oid)}<br><span style="color:#6a7489">${esc(f.properties.check)}</span>`),
  });
  ctx.addTo(S.map); fl.addTo(S.map);
  S.mapLayers = [ctx, fl]; S.flaggedLayer = fl;
  // fit to the 8th-92nd percentile of vertices so a few far-flung features don't zoom the map out
  const lats = [], lngs = [];
  const walk = (c) => { if (typeof c[0] === "number") { lngs.push(c[0]); lats.push(c[1]); } else c.forEach(walk); };
  [...m.context, ...m.flagged].forEach((f) => f.geometry && walk(f.geometry.coordinates));
  if (lats.length) {
    const q = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.max(0, Math.round(p * (s.length - 1))))]; };
    const b = L.latLngBounds([q(lats, 0.08), q(lngs, 0.08)], [q(lats, 0.92), q(lngs, 0.92)]);
    if (b.isValid()) S.map.fitBounds(b.pad(0.08));
  }
  const byLabel = {};
  m.flagged.forEach((f) => { const k = f.properties.severity + "|" + f.properties.label; byLabel[k] = (byLabel[k] || 0) + 1; });
  $("#mapLegend").innerHTML = `<b>${esc(d.dataset)}</b><div class="li"><i style="background:#6b7385"></i>Context sample (${m.context.length.toLocaleString()})</div>` +
    Object.entries(byLabel).sort((a, b) => order[b[0].split("|")[0]] - order[a[0].split("|")[0]] || b[1] - a[1]).map(([k, n]) => {
      const [sv, lb] = k.split("|");
      return `<div class="li"><i class="dot" style="background:${SEVCOL[sv] || "#000"}"></i>${esc(lb)} (${esc(n)})</div>`;
    }).join("") + `<div class="muted" style="margin-top:4px;font-size:11px">${m.flagged.length.toLocaleString()} flagged features shown (sampled per check when large); reprojected to WGS84.</div>`;
}
function zoomTo(layer, check) {
  showTab("map");
  setTimeout(() => {
    const ls = []; S.flaggedLayer.eachLayer((l) => { if (l.feature.properties.layer === layer && l.feature.properties.check === check) ls.push(l); });
    if (!ls.length) return;
    const g = L.featureGroup(ls); S.map.fitBounds(g.getBounds(), { padding: [40, 40], maxZoom: 18 });
    ls[0].openPopup();
  }, 80);
}

/* ---------------- details ---------------- */
function tableHtml(rows, cols, opts = {}) {
  if (!rows || !rows.length) return '<div class="muted">None.</div>';
  cols = cols || Object.keys(rows[0]);
  const cell = (c, v) => {
    if (opts.meta && opts.meta.includes(c)) return `<td><span class="mcell ${esc(v)}">${esc(v)}</span></td>`;
    if (typeof v === "number") return `<td class="num">${fmt(v)}</td>`;
    if (typeof v === "boolean") return `<td>${v ? "yes" : "<b style='color:#d23f3f'>no</b>"}</td>`;
    if (Array.isArray(v)) return `<td>${esc(v.join(", "))}</td>`;
    return `<td>${esc(v ?? "")}</td>`;
  };
  return `<table class="grid"><thead><tr>${cols.map((c) => `<th>${esc(c.replace(/_/g, " "))}</th>`).join("")}</tr></thead><tbody>` +
    rows.slice(0, 1000).map((r) => `<tr>${cols.map((c) => cell(c, r[c])).join("")}</tr>`).join("") + "</tbody></table>";
}
function renderDetails() {
  const d = cur(); if (!d) return;
  const t = d.tables || {};
  const META = ["summary", "description", "tags", "credits", "use_limits", "contact", "extent", "lineage"];
  const parts = [];
  parts.push(`<h4>Layers and tables</h4>` + tableHtml((t.layers || []).filter((r) => !r.system), ["name", "geometry", "count", "fields", "crs", "vertical", "readable"]));
  if (t.metadata_items) parts.push(`<h4>Metadata completeness by item</h4>` + tableHtml(t.metadata_items, ["item", "kind", "rows", "standard", "score", ...META], { meta: META }));
  if (t.crs) parts.push(`<h4>Coordinate systems</h4>` + tableHtml(t.crs));
  if (t.mapping_coverage) parts.push(`<h4>Field-mapping coverage (Esri data-loading match tables)</h4>` + tableHtml(t.mapping_coverage));
  if (t.conformance) parts.push(`<h4>Conformance to ${esc((d.summary.reference_model || "").replace("_AssetPackage", ""))}</h4>` + tableHtml(t.conformance));
  if (t.domain_violations && t.domain_violations.length) parts.push(`<h4>Coded-value violations</h4>` + tableHtml(t.domain_violations));
  if (t.connectivity) parts.push(`<h4>Line endpoint connectivity</h4>` + tableHtml(t.connectivity));
  if (t.un_tables) parts.push(`<h4>Utility network system tables</h4>` + tableHtml(t.un_tables));
  if (t.profile) {
    const flat = t.profile.map((r) => ({ layer: r.layer, geometry: r.geometry, rows: r.rows, fields: r.fields, key_candidates: r.key_candidates }));
    parts.push(`<h4>Profile</h4>` + tableHtml(flat, ["layer", "geometry", "rows", "fields", "key_candidates"]));
  }
  if (t.asset_mapping && t.asset_mapping.length) parts.push(`<h4>Asset type mapping</h4>` + tableHtml(t.asset_mapping, ["class_name", "asset_group", "asset_type", "features", "match", "confidence", "baseline_group", "baseline_type"]));
  $("#details").className = "details";
  $("#details").innerHTML = parts.join("");
}

init();
