"""One page: pipeline progress and the four review queues, tabbed.

Not a third source of truth. Every fetch here hits the exact same JSON
endpoints `review_page.py` and `dashboard_page.py` already serve
(`/review/*`, `/dashboard/*`), and every decision posts to the exact same
`/review/*` write endpoints those pages post to -- so a decision made from
this page, from `/review/page`, or from a terminal CLI are the same code
path. This page only combines the two views a person previously had to open
separately; it adds no state and no endpoint of its own.
"""

ADMIN_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Admin dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg:#0d1218; --card:#141b23; --line:#26313b; --ink:#e6ecf1; --dim:#8fa0ae;
    --accent:#5aa9d6; --ok:#6bb98c; --warn:#d9a154; --bad:#e06c60;
    --chip:#1b242e;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f8f9; --card:#ffffff; --line:#dde3e8; --ink:#10171d;
            --dim:#5d6b78; --chip:#eef2f5; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); padding:26px 20px 80px;
         font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif; }
  .wrap { max-width:1080px; margin:0 auto; }
  h1 { font-size:24px; margin:0 0 4px; letter-spacing:-.02em; }
  h2 { font-size:16px; margin:22px 0 10px; }
  .sub { color:var(--dim); margin:0 0 20px; font-size:13px; }
  a { color:var(--accent); }

  .bar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:16px; align-items:center; }
  input, select, textarea, button {
    background:var(--card); border:1px solid var(--line); color:var(--ink);
    border-radius:6px; padding:8px 11px; font:inherit;
  }
  textarea { width:100%; resize:vertical; min-height:38px; }
  button { cursor:pointer; }
  button:hover:not(:disabled) { border-color:var(--accent); }
  button:disabled { opacity:.5; cursor:default; }
  button.primary { border-color:var(--ok); }
  button.danger { border-color:var(--bad); }
  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

  .tabs { display:flex; gap:6px; margin-bottom:18px; flex-wrap:wrap; }
  .tab { border:1px solid var(--line); background:var(--card); border-radius:999px;
         padding:7px 15px; font-size:13.5px; cursor:pointer; }
  .tab[aria-selected="true"] { border-color:var(--accent); color:var(--accent); }
  .tab .n { font-variant-numeric:tabular-nums; opacity:.8; }

  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr));
           gap:10px; margin-bottom:8px; }
  .stat { border:1px solid var(--line); background:var(--card); border-radius:8px;
          padding:12px 14px; }
  .stat .v { font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; }
  .stat .k { color:var(--dim); font-size:12px; text-transform:uppercase;
             letter-spacing:.05em; margin-top:2px; }
  .stat.warn .v { color:var(--warn); } .stat.bad .v { color:var(--bad); }

  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); }
  th { color:var(--dim); font-weight:500; font-size:12px; text-transform:uppercase;
       letter-spacing:.05em; }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:var(--chip); }
  tbody tr.sel { background:var(--chip); outline:1px solid var(--accent); }

  .card { border:1px solid var(--line); background:var(--card); border-radius:8px;
          padding:14px 16px; margin-bottom:12px; }
  .card.done { opacity:.55; }
  .top { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;
         margin-bottom:8px; }
  .name { font-weight:600; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }
  .tag { font-size:11px; text-transform:uppercase; letter-spacing:.06em;
         color:var(--dim); border:1px solid var(--line); border-radius:4px;
         padding:1px 6px; white-space:nowrap; }
  .tag.ok { color:var(--ok); border-color:var(--ok); }
  .tag.run { color:var(--accent); border-color:var(--accent); }
  .tag.warn { color:var(--warn); } .tag.bad { color:var(--bad); border-color:var(--bad); }
  .dim { color:var(--dim); font-size:13px; }

  .chips { display:flex; gap:6px; flex-wrap:wrap; margin:6px 0; }
  .chip { background:var(--chip); border-radius:4px; padding:3px 8px;
          font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
          max-width:100%; overflow-wrap:anywhere; }
  .chip.pick { cursor:pointer; border:1px solid var(--line); }
  .chip.pick:hover { border-color:var(--accent); }

  .cmp { display:grid; grid-template-columns:130px 1fr 1fr; gap:1px;
         background:var(--line); border:1px solid var(--line); border-radius:6px;
         overflow:hidden; margin:8px 0; font-size:13px; }
  .cmp > div { background:var(--card); padding:6px 9px; overflow-wrap:anywhere; }
  .cmp .h { color:var(--dim); font-size:11px; text-transform:uppercase;
            letter-spacing:.06em; }
  .cmp .differ { color:var(--warn); }

  .fails { margin:8px 0 0; padding:0; list-style:none; }
  .fails li { padding:4px 0; border-top:1px solid var(--line); font-size:13px; }
  .fails li:first-child { border-top:0; }

  .actions { display:flex; gap:8px; flex-wrap:wrap; align-items:center;
             margin-top:10px; }
  .actions input[type=text] { flex:1; min-width:180px; }

  .result { margin-top:10px; border-left:2px solid var(--ok); padding:6px 0 6px 10px;
            font-size:13px; }
  .result.bad { border-left-color:var(--bad); }
  .result ul { margin:6px 0 0; padding-left:18px; color:var(--dim); }

  .step { margin:10px 0; }
  .step .row { display:flex; justify-content:space-between; align-items:baseline;
               font-size:13px; margin-bottom:4px; }
  .step .name { font-weight:600; }
  .step .n { font-variant-numeric:tabular-nums; color:var(--dim); }
  .track { height:8px; border-radius:5px; background:var(--chip); overflow:hidden; }
  .fill { height:100%; background:var(--accent); border-radius:5px; }
  .step.stuck .fill { background:var(--warn); }
  .breakdown { display:flex; gap:10px; flex-wrap:wrap; margin-top:4px; }
  .breakdown span { font-size:12px; color:var(--dim); }
  .breakdown b { color:var(--ink); font-variant-numeric:tabular-nums; }

  .empty { border:1px solid var(--line); background:var(--card); border-radius:8px;
           padding:26px; text-align:center; color:var(--ok); }
  .err { border:1px solid var(--bad); color:var(--bad); background:var(--card);
         border-radius:8px; padding:14px; }
  .hide { display:none; }

  .dropzone { border:2px dashed var(--line); border-radius:8px; padding:22px;
              text-align:center; color:var(--dim); font-size:13.5px; }
  .dropzone.drag { border-color:var(--accent); color:var(--ink); }
  .pick { color:var(--accent); cursor:pointer; text-decoration:underline; }
  .field-row { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:10px; }
  .field-row input, .field-row select { flex:0 0 auto; }
  .field-row input[type=text] { flex:1; min-width:160px; }
  details.adv summary { color:var(--dim); font-size:13px; cursor:pointer; margin-top:8px; }
</style>

<div class="wrap">
  <h1>Admin dashboard</h1>
  <p class="sub">
    Pipeline progress, and the queues waiting on a decision. Every decision
    here is recorded against a name and is permanent.
    &middot; <span id="when">loading&hellip;</span>
  </p>

  <div class="bar">
    <input id="who" type="text" placeholder="Your name — recorded on every decision"
           autocomplete="name" style="flex:1;min-width:240px">
    <input id="key" type="password" placeholder="X-API-Key" autocomplete="off"
           class="hide" style="width:220px">
    <button id="refresh">Refresh</button>
  </div>

  <div class="tabs" role="tablist">
    <button class="tab" role="tab" data-tab="pipeline">Pipeline</button>
    <button class="tab" role="tab" data-tab="entities">Entities</button>
    <button class="tab" role="tab" data-tab="mappings">
      Schema mappings <span class="n" id="n-mappings">–</span></button>
    <button class="tab" role="tab" data-tab="candidates">
      Possible duplicates <span class="n" id="n-candidates">–</span></button>
    <button class="tab" role="tab" data-tab="quarantine">
      Quarantined records <span class="n" id="n-quarantine">–</span></button>
    <button class="tab" role="tab" data-tab="enrichment">
      AI enrichment proposals <span class="n" id="n-enrichment">–</span></button>
    <button class="tab" role="tab" data-tab="operations">
      Operations <span class="n" id="n-operations">–</span></button>
  </div>

  <section id="pipelinePanel">
    <h2>Upload a file</h2>
    <div class="card">
      <div id="dropzone" class="dropzone">
        Drag a CSV/Excel file here, or
        <label class="pick">choose a file<input type="file" id="uploadFile"
          accept=".csv,.tsv,.txt,.xlsx,.xls" class="hide"></label>
        <div id="uploadFileName" class="dim"></div>
      </div>
      <div class="field-row">
        <select id="uploadEntityType">
          <option value="" disabled selected>type&hellip;</option>
          <option value="person">person</option>
          <option value="company">company</option>
        </select>
        <input type="text" id="uploadSourceName" placeholder="source name (vendor/feed)">
        <input type="number" id="uploadBatchSize" value="5000" min="1"
          title="rows read and committed at a time" style="width:110px">
        <button id="uploadGo" class="primary">Ingest</button>
      </div>
      <details class="adv">
        <summary>Advanced</summary>
        <div class="field-row">
          <select id="uploadSourceType">
            <option value="">auto-detect type</option>
            <option value="csv">csv</option>
            <option value="excel">excel</option>
          </select>
          <input type="text" id="uploadSheet"
            placeholder="sheet name (multi-sheet Excel only)">
          <input type="text" id="uploadRecordIdColumn"
            placeholder="record id column (optional)">
          <input type="number" id="uploadReliability" value="0.5" min="0" max="1"
            step="0.05" title="source reliability, 0-1" style="width:90px">
          <select id="uploadDescribes">
            <option value="">describes (unset)</option>
            <option value="organisation">organisation</option>
            <option value="location">location</option>
          </select>
          <label class="dim"><input type="checkbox" id="uploadAllowReingest">
            allow re-ingesting this exact file</label>
        </div>
      </details>
      <div id="uploadStatus" class="result hide"></div>
    </div>

    <h2>All batches, combined</h2>
    <div id="summary" class="cards"></div>
    <h2>Recent batches</h2>
    <div id="batches"></div>
    <div id="detailWrap" class="hide">
      <h2 id="detailTitle">Batch</h2>
      <div id="detail"></div>
    </div>
  </section>

  <section id="entitiesPanel" class="hide">
    <div class="field-row">
      <select id="entitySearchType">
        <option value="">any type</option>
        <option value="person">person</option>
        <option value="company">company</option>
      </select>
      <input type="text" id="entitySearchQuery"
        placeholder="name, email, domain, vendor id&hellip;" style="flex:1">
      <button id="entitySearchGo" class="primary">Search</button>
    </div>
    <div id="entityResults"></div>
    <div id="entityDetailWrap" class="hide">
      <h2 id="entityDetailTitle">Entity</h2>
      <div id="entityDetail"></div>
    </div>
  </section>

  <section id="operationsPanel" class="hide">
    <div id="opsBanner"></div>

    <h2>Stop and start the pipeline</h2>
    <p class="dim" style="margin:0 0 10px">
      Pausing a stage never discards anything. Whatever is mid-record finishes
      and is recorded, queued work waits in its Kafka topic, and files stay in
      their feed directories &mdash; what stops is anything new starting.
      Validation also stops itself when almost everything coming through it is
      invalid.
    </p>
    <div class="card">
      <div id="stages"><p class="dim">Loading&hellip;</p></div>
    </div>

    <h2>Services</h2>
    <p class="dim" style="margin:0 0 10px">
      A container being up is not the same as its loop turning. Each of these
      records a heartbeat as it works; what is shown is how long ago.
    </p>
    <div class="card"><div id="services"><p class="dim">Loading&hellip;</p></div></div>

    <h2>Queued work</h2>
    <p class="dim" style="margin:0 0 10px">
      Messages published to a topic that the consumer has not committed yet.
      Non-zero during a load is normal; non-zero and not falling means a stage
      is stopped or cannot keep up.
    </p>
    <div class="card"><div id="lag"><p class="dim">Loading&hellip;</p></div></div>

    <h2>Alerts</h2>
    <div class="card"><div id="alerts"><p class="dim">Loading&hellip;</p></div></div>

    <h2>Data integrity</h2>
    <p class="dim" style="margin:0 0 10px">
      Twelve reconciliation checks that each return rows only when something is
      wrong, so all-empty is the pass. They read every table, so they run when
      asked rather than on every render.
    </p>
    <div class="card">
      <button id="runIntegrity">Run the checks</button>
      <div id="integrity" style="margin-top:12px"></div>
    </div>
  </section>

  <section id="queuePanel" class="hide">
    <div id="queueBody"></div>
  </section>
</div>

<script>
var KEY = "pcdf_api_key", WHO = "pcdf_reviewer";
var currentTab = "pipeline";
var selectedBatch = null;
var fieldCache = {};
var selectedFile = null;

function $(id) { return document.getElementById(id); }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function fmt(n) { return Number(n || 0).toLocaleString(); }

function dur(secs) {
  var d = Math.floor(secs / 86400), h = Math.floor(secs / 3600),
      m = Math.floor(secs / 60);
  if (d >= 1) { return d + "d"; }
  if (h >= 1) { return h + "h"; }
  if (m >= 1) { return m + "m"; }
  return "just now";
}

function ago(iso) {
  if (!iso) { return "unknown"; }
  return dur(Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000));
}

function headers() {
  var k = sessionStorage.getItem(KEY);
  var h = { "Content-Type": "application/json" };
  if (k) { h["X-API-Key"] = k; }
  return h;
}

function api(path, options) {
  return fetch(path, Object.assign({ headers: headers() }, options || {}))
    .then(function (res) {
      if (res.status === 401 || res.status === 403) {
        $("key").classList.remove("hide");
        throw new Error("This dashboard needs a key. Enter it above and refresh.");
      }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { throw new Error(data.detail || ("HTTP " + res.status)); }
        return data;
      });
    });
}

/* Multipart upload: no Content-Type header of our own -- the browser sets
   one with the correct boundary for a FormData body, and overriding it
   breaks the parse on the server side. */
function apiUpload(path, formData) {
  var h = {};
  var k = sessionStorage.getItem(KEY);
  if (k) { h["X-API-Key"] = k; }
  return fetch(path, { method: "POST", headers: h, body: formData })
    .then(function (res) {
      if (res.status === 401 || res.status === 403) {
        $("key").classList.remove("hide");
        throw new Error("This dashboard needs a key. Enter it above and refresh.");
      }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { throw new Error(data.detail || ("HTTP " + res.status)); }
        return data;
      });
    });
}

function who() {
  var name = $("who").value.trim();
  if (!name) {
    $("who").focus();
    return null;
  }
  localStorage.setItem(WHO, name);
  return name;
}

/* ================= pipeline tab ================= */

function statCard(label, value, cls) {
  return '<div class="stat' + (cls ? " " + cls : "") + '">' +
    '<div class="v">' + fmt(value) + '</div><div class="k">' + esc(label) + '</div></div>';
}

function renderSummary(f) {
  return statCard("Ingested", f.ingested) +
    statCard("Normalized", f.normalized) +
    statCard("Validated OK", f.validated.valid + f.validated.warning) +
    statCard("Resolved", f.resolved.auto_linked + f.resolved.new_entity + f.resolved.manual) +
    statCard("Golden built", f.golden_built) +
    statCard("Quarantine open", f.quarantine.open, f.quarantine.open ? "warn" : "") +
    statCard("Mapping needs review", f.needs_review.mapping, f.needs_review.mapping ? "warn" : "") +
    statCard("Duplicate candidates open", f.needs_review.candidates, f.needs_review.candidates ? "warn" : "") +
    statCard("Failed to process", f.failed, f.failed ? "bad" : "");
}

function statusTag(s) {
  var cls = s === "completed" ? "ok" : s === "failed" ? "bad" : "run";
  return '<span class="tag ' + cls + '">' + esc(s) + "</span>";
}

function renderBatches(rows) {
  if (!rows.length) {
    return '<div class="empty">No batches have been loaded yet.</div>';
  }
  var html = "<table><thead><tr>" +
    "<th>Source</th><th>File</th><th>Status</th><th>Read</th><th>Ingested</th>" +
    "<th>Failed</th><th>Started</th></tr></thead><tbody>";
  rows.forEach(function (b) {
    html += '<tr data-id="' + esc(b.batch_id) + '"' +
      (b.batch_id === selectedBatch ? ' class="sel"' : "") + ">" +
      "<td>" + esc(b.source_name) + "</td>" +
      '<td class="mono">' + esc(b.file_name) + "</td>" +
      "<td>" + statusTag(b.status) + "</td>" +
      "<td>" + fmt(b.rows_read) + "</td>" +
      "<td>" + fmt(b.rows_ingested) + "</td>" +
      "<td>" + (b.rows_failed ? fmt(b.rows_failed) : "\\u2014") + "</td>" +
      "<td>" + esc(ago(b.started_at)) + "</td>" +
      "</tr>";
  });
  return html + "</tbody></table>";
}

function step(name, value, base, breakdown, stuck, control) {
  var pct = base > 0 ? Math.min(100, (value / base) * 100) : 0;
  var bd = "";
  if (breakdown && breakdown.length) {
    bd = '<div class="breakdown">' + breakdown.map(function (b) {
      return "<span>" + esc(b[0]) + " <b>" + fmt(b[1]) + "</b></span>";
    }).join("") + "</div>";
  }
  return '<div class="step' + (stuck ? " stuck" : "") + '">' +
    '<div class="row"><span class="name">' + esc(name) + '</span>' +
    '<span class="n">' + fmt(value) + (base > 0 ? " \\u00b7 " + pct.toFixed(1) + "%" : "") +
    "</span></div>" +
    '<div class="track"><div class="fill" style="width:' + pct.toFixed(1) + '%"></div></div>' +
    bd + (control || "") + "</div>";
}

/* One Run button + status badge per controllable stage. Posts to
   /dashboard/batches/{id}/stages/{stage}/run; the badge reflects the last
   run this server process knows about for (stage, batch) -- see stages.py. */
function stageControl(stage, batchId, batchReady, stagesStatus) {
  var s = (stagesStatus || {})[stage];
  var status = s ? s.status : null;
  var running = status === "running";
  var disabled = running || !batchReady;
  var badge = "";
  if (running) {
    badge = '<span class="tag run">running\\u2026</span>';
  } else if (status === "completed") {
    badge = '<span class="tag ok">done ' + esc(ago(s.finished_at)) + "</span>";
  } else if (status === "failed") {
    badge = '<span class="tag bad">failed: ' + esc(s.error || "") + "</span>";
  } else if (!batchReady) {
    badge = '<span class="dim">batch still ingesting</span>';
  }
  return '<div class="actions">' +
    '<button data-stage-run="' + esc(stage) + '" data-batch="' + esc(batchId) + '"' +
    (disabled ? " disabled" : "") + ">Run</button>" + badge + "</div>";
}

function renderFunnel(b) {
  var f = b.funnel, st = b.stages, ready = b.status === "completed";
  var base = f.ingested;
  var validatedOk = f.validated.valid + f.validated.warning;
  var resolvedTotal = f.resolved.auto_linked + f.resolved.new_entity + f.resolved.manual;
  return '<div class="card">' +
    '<p class="dim">Manual stage runs are not safe to run at the same time as ' +
    'live ingestion on overlapping data \\u2014 resolution in particular has no ' +
    'lock against concurrent runs and can create duplicate entities. Don\\u2019t ' +
    'trigger a stage here while the watcher is actively loading a file from the ' +
    'same source.</p>' +
    step("Ingested", f.ingested, base, [["failed", f.failed]], f.failed > 0) +
    step("Mapping needing review (this layout)", f.needs_review.mapping, base, null,
      f.needs_review.mapping > 0,
      stageControl("mapping", b.batch_id, ready, st)) +
    step("Normalized", f.normalized, base, null, false,
      stageControl("normalization", b.batch_id, ready, st)) +
    step("Validated", validatedOk, base,
      [["valid", f.validated.valid], ["warning", f.validated.warning],
       ["invalid", f.validated.invalid]], f.validated.invalid > 0,
      stageControl("validation", b.batch_id, ready, st)) +
    step("Quarantine (of the invalid)", f.quarantine.open + f.quarantine.released +
      f.quarantine.rejected + f.quarantine.resolved, base,
      [["open", f.quarantine.open], ["released", f.quarantine.released],
       ["rejected", f.quarantine.rejected], ["resolved", f.quarantine.resolved]],
      f.quarantine.open > 0) +
    step("Resolved to an entity", resolvedTotal, base,
      [["auto-linked", f.resolved.auto_linked], ["new entity", f.resolved.new_entity],
       ["manual", f.resolved.manual], ["possible duplicates open", f.needs_review.candidates]],
      f.needs_review.candidates > 0,
      stageControl("resolution", b.batch_id, ready, st)) +
    step("Golden record built", f.golden_built, base, null, false,
      stageControl("golden", b.batch_id, ready, st)) +
    "</div>";
}

/* ================= upload ================= */

function pickFile(file) {
  selectedFile = file || null;
  $("uploadFileName").textContent = selectedFile ? selectedFile.name : "";
}

function uploadStatusBox(cls, html) {
  var box = $("uploadStatus");
  box.classList.remove("hide", "bad");
  if (cls) { box.classList.add(cls); }
  box.innerHTML = html;
}

function pollUpload(uploadId) {
  api("/dashboard/ingest/" + uploadId).then(function (u) {
    if (u.status === "running") {
      uploadStatusBox(null, "Ingesting\\u2026");
      setTimeout(function () { pollUpload(uploadId); }, 1000);
      return;
    }
    if (u.status === "completed") {
      uploadStatusBox(null,
        "Ingested batch <b>" + esc(u.batch_id) + "</b>: " +
        fmt(u.detail.rows_read) + " read, " + fmt(u.detail.rows_ingested) +
        " ingested. <button id=\\"uploadJump\\" data-batch=\\"" + esc(u.batch_id) +
        "\\">View it</button>");
      // No explicit reload: the stream's next tick (\\u22642s) already
      // carries the new batch.
    } else {
      var msg = esc(u.error || "ingestion failed");
      var sheets = u.detail && u.detail.available_sheets;
      if (sheets && sheets.length) {
        msg += '<p class="dim" style="margin:8px 0 4px">Pick a sheet to load it as its own batch:</p>' +
          '<div class="chips">' + sheets.map(function (name) {
            return '<span class="chip pick" data-sheet-pick="' + esc(name) + '">' +
              esc(name) + "</span>";
          }).join("") + "</div>";
      }
      uploadStatusBox("bad", msg);
    }
    $("uploadGo").disabled = false;
  }).catch(function (err) {
    uploadStatusBox("bad", esc(err.message));
    $("uploadGo").disabled = false;
  });
}

function submitUpload() {
  if (!selectedFile) { $("dropzone").scrollIntoView({ block: "center" }); return; }
  var entityType = $("uploadEntityType").value;
  if (!entityType) { $("uploadEntityType").focus(); return; }
  var sourceName = $("uploadSourceName").value.trim();
  if (!sourceName) { $("uploadSourceName").focus(); return; }

  var form = new FormData();
  form.append("file", selectedFile);
  form.append("entity_type", entityType);
  form.append("source_name", sourceName);
  form.append("batch_size", $("uploadBatchSize").value || "5000");
  var sourceType = $("uploadSourceType").value;
  if (sourceType) { form.append("source_type", sourceType); }
  var sheet = $("uploadSheet").value.trim();
  if (sheet) { form.append("sheet", sheet); }
  var recordIdColumn = $("uploadRecordIdColumn").value.trim();
  if (recordIdColumn) { form.append("record_id_column", recordIdColumn); }
  form.append("reliability", $("uploadReliability").value || "0.5");
  var describes = $("uploadDescribes").value;
  if (describes) { form.append("describes", describes); }
  form.append("allow_reingest", $("uploadAllowReingest").checked ? "true" : "false");

  $("uploadGo").disabled = true;
  uploadStatusBox(null, "Uploading\\u2026");
  apiUpload("/dashboard/ingest", form).then(function (started) {
    pollUpload(started.upload_id);
  }).catch(function (err) {
    uploadStatusBox("bad", esc(err.message));
    $("uploadGo").disabled = false;
  });
}

/* ================= live stream (push, not poll) =================
   /dashboard/stream stays open and sends a fresh snapshot roughly every 2s
   (see stream.py) -- the summary cards, the batch list, the review-queue
   tab badges, and the selected batch's funnel all come from it. Nothing on
   this tab is fetched on a timer any more; this connection IS the timer. */

var streamAbort = null;

function applyStreamPayload(payload) {
  $("summary").innerHTML = renderSummary(payload.summary);
  $("batches").innerHTML = renderBatches(payload.batches);
  applyQueueSummary(payload.queues);
  if (payload.batch) {
    var b = payload.batch;
    $("detailWrap").classList.remove("hide");
    $("detailTitle").textContent = b.source_name + " \\u2014 " + b.file_name;
    $("detail").innerHTML =
      '<p class="dim">' + statusTag(b.status) + " &middot; started " + esc(ago(b.started_at)) +
      (b.finished_at ? " &middot; finished " + esc(ago(b.finished_at)) : "") +
      (b.error_message ? " &middot; " + esc(b.error_message) : "") + "</p>" +
      renderFunnel(b);
  }
  $("when").textContent = "live \\u00b7 " + new Date().toLocaleTimeString();
}

function startStream() {
  if (streamAbort) { streamAbort.abort(); }
  var abort = new AbortController();
  streamAbort = abort;
  var url = "/dashboard/stream" +
    (selectedBatch ? "?batch_id=" + encodeURIComponent(selectedBatch) : "");

  fetch(url, { headers: headers(), signal: abort.signal }).then(function (res) {
    if (res.status === 401 || res.status === 403) {
      $("key").classList.remove("hide");
      throw new Error("This dashboard needs a key. Enter it above and refresh.");
    }
    if (!res.ok || !res.body) { throw new Error("stream failed: HTTP " + res.status); }
    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    function pump() {
      return reader.read().then(function (result) {
        if (result.done) { return; }
        buffer += decoder.decode(result.value, { stream: true });
        var frames = buffer.split("\\n\\n");
        buffer = frames.pop();
        frames.forEach(function (frame) {
          var line = frame.split("\\n").filter(function (l) {
            return l.lastIndexOf("data: ", 0) === 0;
          })[0];
          if (!line) { return; }
          try { applyStreamPayload(JSON.parse(line.slice(6))); }
          catch (parseErr) { /* partial or malformed frame; next one recovers */ }
        });
        return pump();
      });
    }
    return pump();
  }).catch(function (err) {
    if (err.name === "AbortError") { return; } // we did that, on purpose
    $("when").textContent = "reconnecting\\u2026";
    setTimeout(function () {
      if (streamAbort === abort) { startStream(); }
    }, 3000);
  });
}

/* ================= review queue tabs ================= */

function card(id, inner) {
  return '<div class="card" id="c-' + esc(id) + '">' + inner + '</div>';
}

function actionRow(id, buttons, withNote) {
  return '<div class="actions">' +
    (withNote ? '<input type="text" id="note-' + esc(id) +
                '" placeholder="Note (optional) — recorded permanently">' : "") +
    buttons +
    "</div>" +
    '<div class="result hide" id="r-' + esc(id) + '"></div>';
}

function renderMappings(rows) {
  if (!rows.length) {
    return '<div class="empty">No columns are waiting for a decision.</div>';
  }
  return rows.map(function (m) {
    var id = m.mapping_id;
    var samples = m.samples.length
      ? '<div class="chips">' + m.samples.map(function (v) {
          return '<span class="chip">' + esc(v.slice(0, 60)) + "</span>";
        }).join("") + "</div>"
      : '<p class="dim">Every sampled row is empty in this column.</p>';

    var alts = (m.alternatives || []).map(function (a) {
      var f = a.canonical_field || a.field || a;
      return '<span class="chip pick" data-field="' + esc(f) + '" data-for="' +
        esc(id) + '">' + esc(f) +
        (a.confidence != null ? " · " + Number(a.confidence).toFixed(2) : "") +
        "</span>";
    }).join("");

    var collision = m.collision
      ? '<p class="dim">Collides with ' +
        esc((m.collision.competing_columns || []).join(", ")) +
        " over " + esc(m.collision.canonical_field) + ".</p>"
      : "";

    return card(id,
      '<div class="top">' +
        '<span class="name mono">' + esc(m.source_column) + "</span>" +
        '<span class="tag">' + esc(m.source_name) + "</span>" +
        '<span class="tag">' + esc(m.entity_type) +
          (m.subject === "employer" ? " · employer" : "") + "</span>" +
        '<span class="tag warn">' + esc(m.mapping_method) + " " +
          m.mapping_confidence.toFixed(2) + "</span>" +
        '<span class="tag">waiting ' + esc(ago(m.created_at)) + "</span>" +
      "</div>" +
      '<p class="dim">The engine' +
        (m.canonical_field
          ? " proposed <b>" + esc(m.canonical_field) + "</b>"
          : " could not name this column") +
        ". What it contains:</p>" +
      samples +
      (alts ? '<p class="dim">Alternatives it considered:</p><div class="chips">' +
              alts + "</div>" : "") +
      collision +
      actionRow(id,
        '<input type="text" id="f-' + esc(id) + '" list="fields-' + esc(m.entity_type) +
          '" placeholder="canonical field" value="' + esc(m.canonical_field || "") + '">' +
        '<button class="primary" data-act="map" data-id="' + esc(id) + '">Approve</button>' +
        '<button data-act="unmap" data-id="' + esc(id) + '">Not a field</button>',
        false)
    );
  }).join("");
}

function compareRows(a, b) {
  var keys = {};
  Object.keys(a.fields).forEach(function (k) { keys[k] = 1; });
  Object.keys(b.fields).forEach(function (k) { keys[k] = 1; });
  var names = Object.keys(keys).sort();
  if (!names.length) {
    return '<p class="dim">Neither side has any comparable values recorded.</p>';
  }
  var html = '<div class="cmp"><div class="h">field</div>' +
    '<div class="h">already in the database</div><div class="h">this record</div>';
  names.forEach(function (k) {
    var left = (a.fields[k] || []).join(" / ");
    var right = (b.fields[k] || []).join(" / ");
    var cls = left && right && left !== right ? " differ" : "";
    html += '<div class="mono">' + esc(k) + "</div>" +
      '<div class="' + cls.trim() + '">' + esc(left || "—") + "</div>" +
      '<div class="' + cls.trim() + '">' + esc(right || "—") + "</div>";
  });
  return html + "</div>";
}

function renderCandidates(rows) {
  if (!rows.length) {
    return '<div class="empty">No matches are waiting for a decision.</div>';
  }
  return rows.map(function (c) {
    var id = c.candidate_id;
    return card(id,
      '<div class="top">' +
        '<span class="name">Possible duplicate ' + esc(c.entity_type) + "</span>" +
        '<span class="tag warn">' + esc(c.match_method) + " " +
          c.match_confidence.toFixed(2) + "</span>" +
        '<span class="tag">' + esc(c.source_name) + " row " +
          esc(c.row_number) + "</span>" +
        '<span class="tag">waiting ' + esc(ago(c.created_at)) + "</span>" +
      "</div>" +
      '<p class="dim">Merging keeps ' +
        '<span class="mono">' + esc(c.existing.entity_id) + "</span> and folds " +
        '<span class="mono">' + esc(c.incoming.entity_id || "—") +
        "</span> into it. The absorbed id keeps resolving.</p>" +
      compareRows(c.existing, c.incoming) +
      actionRow(id,
        '<button class="primary" data-act="merge" data-id="' + esc(id) +
          '">Same one — merge</button>' +
        '<button data-act="separate" data-id="' + esc(id) +
          '">Different — keep both</button>',
        true)
    );
  }).join("");
}

function renderQuarantine(rows) {
  if (!rows.length) {
    return '<div class="empty">No records are being held back.</div>';
  }
  return rows.map(function (q) {
    var id = q.record_id;
    var fails = q.failures.length
      ? '<ul class="fails">' + q.failures.map(function (f) {
          return "<li>" +
            '<span class="tag ' + (f.severity === "error" ? "bad" : "warn") + '">' +
              esc(f.severity) + "</span> " +
            '<span class="mono">' + esc(f.rule_id) + "</span> " +
            (f.source_column
              ? '<span class="mono">' + esc(f.source_column) + "</span> " : "") +
            (f.raw_value
              ? '<span class="chip">' + esc(String(f.raw_value).slice(0, 60)) +
                "</span> " : "") +
            esc(f.message || "") +
          "</li>";
        }).join("") + "</ul>"
      : '<p class="dim">No failing rules are recorded against this record.</p>';

    return card(id,
      '<div class="top">' +
        '<span class="name">' + esc(q.entity_type) + " row " +
          esc(q.row_number) + "</span>" +
        '<span class="tag">' + esc(q.source_name) + "</span>" +
        '<span class="tag bad">' + esc(q.reason_codes.join(", ") || "no reason") +
          "</span>" +
        '<span class="tag">held ' + esc(ago(q.quarantined_at)) + "</span>" +
      "</div>" +
      '<p class="dim mono">' + esc(q.file_name) + "</p>" +
      fails +
      actionRow(id,
        '<button class="primary" data-act="release" data-id="' + esc(id) +
          '">Use it anyway</button>' +
        '<button class="danger" data-act="rejectq" data-id="' + esc(id) +
          '">Confirm unusable</button>',
        true)
    );
  }).join("");
}

function renderEnrichment(rows) {
  if (!rows.length) {
    return '<div class="empty">No AI proposals are waiting for a decision.</div>';
  }
  return rows.map(function (p) {
    var id = p.proposal_id;
    var known = Object.keys(p.known_values || {});
    var context = known.length
      ? '<div class="chips">' + known.map(function (k) {
          return '<span class="chip">' + esc(k) + " = " +
            esc(String(p.known_values[k]).slice(0, 60)) + "</span>";
        }).join("") + "</div>"
      : '<p class="dim">Nothing else is known about this entity yet.</p>';

    return card(id,
      '<div class="top">' +
        '<span class="name">' + esc(p.entity_type) + " " +
          '<span class="mono">' + esc(p.entity_id) + "</span></span>" +
        '<span class="tag warn">' + esc(p.model) + " · confidence " +
          p.stated_confidence.toFixed(2) + "</span>" +
        '<span class="tag">waiting ' + esc(ago(p.created_at)) + "</span>" +
      "</div>" +
      '<p class="dim">Proposes <b>' + esc(p.canonical_field) + "</b> = " +
        '<span class="chip">' + esc(String(p.proposed_value).slice(0, 80)) +
        "</span></p>" +
      '<p class="dim">Already known about this entity:</p>' +
      context +
      actionRow(id,
        '<button class="primary" data-act="enrichaccept" data-id="' + esc(id) +
          '">Confirm — write it</button>' +
        '<button class="danger" data-act="enrichreject" data-id="' + esc(id) +
          '">Decline</button>',
        false)
    );
  }).join("");
}

var QUEUES = {
  mappings: { url: "/review/mappings", render: renderMappings },
  candidates: { url: "/review/candidates", render: renderCandidates },
  quarantine: { url: "/review/quarantine", render: renderQuarantine },
  enrichment: { url: "/review/enrichment", render: renderEnrichment }
};

function datalists() {
  return Promise.all(["person", "company"].map(function (t) {
    if (fieldCache[t]) { return Promise.resolve(); }
    return api("/review/fields/" + t).then(function (d) { fieldCache[t] = d.fields; });
  })).then(function () {
    return ["person", "company"].map(function (t) {
      return '<datalist id="fields-' + t + '">' +
        fieldCache[t].map(function (f) {
          return '<option value="' + esc(f.name) + '">' + esc(f.description) +
            "</option>";
        }).join("") + "</datalist>";
    }).join("");
  });
}

function applyQueueSummary(data) {
  ["mappings", "candidates", "quarantine", "enrichment"].forEach(function (q) {
    var info = data.queues[q];
    var label = String(info.open);
    if (info.open > 0 && info.oldest_seconds > 0) {
      label += " · " + dur(info.oldest_seconds);
    }
    $("n-" + q).textContent = label;
  });
}

function loadQueue() {
  var queue = QUEUES[currentTab];
  if (!queue) { return Promise.resolve(); }
  $("queueBody").innerHTML = '<p class="dim">Loading&hellip;</p>';
  return Promise.all([api(queue.url), currentTab === "mappings" ? datalists() : ""])
    .then(function (out) {
      $("queueBody").innerHTML = (out[1] || "") + queue.render(out[0].results);
    });
}

/* ================= entity search ================= */

function renderEntityResults(rows) {
  if (!rows.length) {
    return '<div class="empty">No entities match.</div>';
  }
  var html = "<table><thead><tr><th>Name</th><th>Type</th><th>Records</th>" +
    "</tr></thead><tbody>";
  rows.forEach(function (e) {
    html += '<tr data-entity-id="' + esc(e.entity_id) + '">' +
      "<td>" + esc(e.display_name || "(no golden value yet)") + "</td>" +
      "<td>" + esc(e.entity_type) + "</td>" +
      "<td>" + fmt(e.record_count) + "</td>" +
      "</tr>";
  });
  return html + "</tbody></table>";
}

function searchEntities() {
  var q = $("entitySearchQuery").value.trim();
  if (!q) { $("entitySearchQuery").focus(); return; }
  var type = $("entitySearchType").value;
  $("entityResults").innerHTML = '<p class="dim">Searching&hellip;</p>';
  var url = "/entities/search?q=" + encodeURIComponent(q) +
    (type ? "&entity_type=" + type : "");
  api(url).then(function (data) {
    $("entityResults").innerHTML = renderEntityResults(data.results);
  }).catch(function (err) {
    $("entityResults").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
}

function contributionRow(c, golden) {
  var tags = "";
  if (c.is_winning_record) { tags += '<span class="tag ok">winning</span> '; }
  else if (c.agrees_with_golden) { tags += '<span class="tag">agrees</span> '; }
  else { tags += '<span class="tag warn">differs</span> '; }
  var fails = (c.validation || []).filter(function (v) { return v.outcome === "fail"; });
  return "<tr>" +
    "<td>" + esc(c.source_name) + "</td>" +
    '<td class="mono">' + esc(c.file_name) + " row " + esc(c.row_number) + "</td>" +
    "<td>" + esc(c.raw_value) + '<span class="dim"> \\u2192 </span>' +
      esc(c.normalized_value) + "</td>" +
    "<td>" + tags +
      (fails.length ? '<span class="tag bad">' + fails.length + " failing</span>" : "") +
      "</td>" +
    "</tr>";
}

function loadFieldExplain(entityId, field) {
  var row = $("explain-" + field);
  if (!row) { return; }
  if (!row.classList.contains("hide")) {
    row.classList.add("hide");
    return;
  }
  row.classList.remove("hide");
  row.firstElementChild.innerHTML = '<p class="dim">Loading\\u2026</p>';
  api("/entities/" + entityId + "/fields/" + encodeURIComponent(field)).then(function (d) {
    var html = "<table><thead><tr><th>Source</th><th>File / row</th>" +
      "<th>Raw \\u2192 normalized</th><th></th></tr></thead><tbody>" +
      d.contributions.map(function (c) { return contributionRow(c, d.golden); }).join("") +
      "</tbody></table>";
    row.firstElementChild.innerHTML = html;
  }).catch(function (err) {
    row.firstElementChild.innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
}

function attrRows(entityId, attrs) {
  var names = Object.keys(attrs).sort();
  if (!names.length) {
    return '<p class="dim">No golden values yet -- run resolution and golden build.</p>';
  }
  var html = "<table><thead><tr><th>Field</th><th>Value</th><th>Confidence</th>" +
    "<th>Strategy</th><th>Sources</th></tr></thead><tbody>";
  names.forEach(function (name) {
    var a = attrs[name];
    html += '<tr class="explainable" data-explain-field="' + esc(name) +
      '" data-entity-id="' + esc(entityId) + '">' +
      '<td class="mono">' + esc(name) + "</td>" +
      "<td>" + esc(a.value) + "</td>" +
      "<td>" + Number(a.confidence).toFixed(2) + "</td>" +
      "<td>" + esc(a.strategy) + "</td>" +
      "<td>" + fmt(a.supporting_sources) + " agreed" +
        (a.competing_values > 0
          ? ", " + fmt(a.competing_values) + " competing" : "") +
      "</td></tr>" +
      '<tr class="hide" id="explain-' + esc(name) +
      '"><td colspan="5"></td></tr>';
  });
  return html + "</tbody></table>";
}

function renderRelationships(rels) {
  if (!rels.length) { return ""; }
  return '<h3 style="font-size:14px;margin:16px 0 6px">Relationships</h3>' +
    '<div class="chips">' + rels.map(function (r) {
      var label = (r.direction === "employer" ? "works at " : "employs ") +
        (r.display_name || r.entity_id);
      return '<span class="chip pick" data-jump-entity="' + esc(r.entity_id) + '">' +
        esc(label) + "</span>";
    }).join("") + "</div>";
}

function renderObservedBy(rows) {
  if (!rows.length) { return ""; }
  return '<h3 style="font-size:14px;margin:16px 0 6px">Observed by</h3>' +
    "<table><thead><tr><th>Source</th><th>File / row</th><th>Match</th>" +
    "</tr></thead><tbody>" + rows.map(function (o) {
      return "<tr><td>" + esc(o.source_name) + "</td>" +
        '<td class="mono">' + esc(o.file_name) + " row " + esc(o.row_number) + "</td>" +
        "<td>" + esc(o.match_method) + " " + Number(o.match_confidence).toFixed(2) +
        " (" + esc(o.match_status) + ")</td></tr>";
    }).join("") + "</tbody></table>";
}

function loadEntity(entityId) {
  return api("/entities/" + entityId).then(function (d) {
    $("entityDetailWrap").classList.remove("hide");
    var name = d.attributes.full_name || d.attributes.company_name;
    $("entityDetailTitle").textContent =
      (name ? name.value : d.entity_type) + (d.was_merged ? " (merged)" : "");
    $("entityDetail").innerHTML =
      '<p class="dim mono">' + esc(d.entity_id) + " \\u00b7 " + esc(d.entity_type) +
      "</p>" +
      attrRows(d.entity_id, d.attributes) +
      renderRelationships(d.relationships) +
      renderObservedBy(d.observed_by);
    $("entityDetailWrap").scrollIntoView({ block: "start" });
  });
}

/* ================= operations ================= */

// Ages are the whole point of this tab: "up" and "working" are different
// facts, and only the second one is worth looking at.
function ageTag(seconds, warnAt, badAt) {
  if (seconds == null) { return '<span class="tag">never</span>'; }
  var cls = seconds >= badAt ? "bad" : (seconds >= warnAt ? "warn" : "ok");
  return '<span class="tag ' + cls + '">' + dur(seconds) + " ago</span>";
}

function renderStages(data) {
  return '<table><thead><tr><th>Stage</th><th>State</th><th>Why</th>' +
    "<th></th></tr></thead><tbody>" +
    data.stages.map(function (s) {
      var control = s.paused
        ? '<button data-resume="' + esc(s.stage) + '">Resume</button>'
        : '<button data-pause="' + esc(s.stage) + '">Pause</button>';
      return "<tr><td>" + esc(s.stage) + "</td>" +
        '<td><span class="tag ' + (s.paused ? "bad" : "ok") + '">' +
          esc(s.state) + "</span></td>" +
        "<td>" + (s.paused
          ? esc(s.reason || "no reason recorded") +
            ' <span class="dim">— by ' + esc(s.changed_by) + "</span>"
          : '<span class="dim">—</span>') + "</td>" +
        '<td style="text-align:right">' + control + "</td></tr>";
    }).join("") + "</tbody></table>";
}

function renderServices(rows) {
  if (!rows.length) {
    return '<p class="dim">Nothing has reported yet. Services record a ' +
      "heartbeat as they work; an empty table means none of them has run " +
      "since the last database reset.</p>";
  }
  return '<table><thead><tr><th>Service</th><th>Last completed a loop</th>' +
    "<th>Detail</th></tr></thead><tbody>" +
    rows.map(function (r) {
      var detail = Object.keys(r.detail || {}).map(function (k) {
        return '<span class="chip">' + esc(k) + " = " + esc(r.detail[k]) + "</span>";
      }).join(" ");
      // 15 minutes is the consumers' own staleness limit; an hour is well past
      // anything a slow batch explains.
      return "<tr><td>" + esc(r.service) + "</td>" +
        "<td>" + ageTag(r.age_seconds, 900, 3600) + "</td>" +
        '<td class="chips">' + detail + "</td></tr>";
    }).join("") + "</tbody></table>";
}

function renderLag(kafka) {
  if (!kafka.reachable) {
    return '<div class="err">Consumer lag cannot be read: ' +
      esc(kafka.error || "the broker is unreachable") +
      ". While this is true, no batch is being announced and no stage " +
      "consumer is advancing.</div>";
  }
  if (!kafka.groups.length) {
    return '<p class="empty">No consumer group has committed an offset yet.</p>';
  }
  return '<table><thead><tr><th>Consumer</th><th>Topic</th><th>Waiting</th>' +
    "</tr></thead><tbody>" +
    kafka.groups.map(function (g) {
      return "<tr><td>" + esc(g.group) + "</td>" +
        '<td class="mono">' + esc(g.topic) + "</td>" +
        '<td><span class="tag ' + (g.lag > 0 ? "warn" : "ok") + '">' +
          fmt(g.lag) + " message(s)</span></td></tr>";
    }).join("") + "</tbody></table>";
}

function renderAlerts(data) {
  if (data.unavailable) {
    // "Nothing is firing" and "we cannot tell whether anything is firing" are
    // opposite states, and rendering them the same way is worse than an error.
    return '<div class="err">Alertmanager is unreachable, so it is not known ' +
      "whether anything is firing: " + esc(data.unavailable) + "</div>";
  }
  if (!data.alerts.length) {
    return '<p class="empty">Nothing is firing.</p>';
  }
  return data.alerts.map(function (a) {
    return '<div class="top" style="padding:8px 0;border-bottom:1px solid var(--line)">' +
      '<span class="tag ' +
        (a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "") +
        '">' + esc(a.severity) + "</span> " +
      '<span class="name">' + esc(a.name) + "</span> " +
      '<span class="dim">' + esc(a.summary || "") + "</span></div>";
  }).join("");
}

function renderIntegrity(data) {
  var head = data.ok
    ? '<p class="empty">All ' + data.total + " checks pass.</p>"
    : '<div class="err">' + data.failed + " of " + data.total +
      " checks found something" +
      (data.errored ? ", and " + data.errored + " could not run" : "") + ".</div>";
  return head + '<table style="margin-top:10px"><thead><tr><th>Check</th>' +
    "<th>Result</th></tr></thead><tbody>" +
    data.checks.map(function (c) {
      var verdict = c.error
        ? '<span class="tag bad">could not run</span> <span class="dim">' +
          esc(c.error) + "</span>"
        : c.ok
          ? '<span class="tag ok">pass</span>'
          : '<span class="tag bad">' + fmt(c.offending) + " row(s)</span>";
      return "<tr><td>" + esc(c.name) + "</td><td>" + verdict + "</td></tr>";
    }).join("") + "</tbody></table>";
}

function loadOperations() {
  return Promise.all([
    api("/control/health"),
    // The alerts endpoint 503s when Alertmanager is unreachable, which is a
    // real answer rather than a failure of this page -- so it is caught here
    // and rendered, not allowed to blank the whole tab.
    api("/alerts").catch(function (err) {
      return { unavailable: err.message, alerts: [] };
    }),
  ]).then(function (out) {
    var health = out[0];
    $("stages").innerHTML = renderStages(health);
    $("services").innerHTML = renderServices(health.services || []);
    $("lag").innerHTML = renderLag(health.kafka || { reachable: false });
    $("alerts").innerHTML = renderAlerts(out[1]);

    var paused = health.paused || [];
    $("n-operations").textContent = paused.length ? String(paused.length) : "0";
    $("opsBanner").innerHTML = paused.length
      ? '<div class="err" style="margin-bottom:16px"><strong>' +
        paused.map(function (s) { return esc(s.stage); }).join(", ") +
        " is paused.</strong> Nothing is being lost — queued work is waiting " +
        "and files are sitting in their feeds. Nothing is moving either.</div>"
      : "";
  }).catch(function (err) {
    $("stages").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
}

/* ================= tab switching + polling ================= */

function showTab() {
  document.querySelectorAll(".tab").forEach(function (t) {
    t.setAttribute("aria-selected", String(t.dataset.tab === currentTab));
  });
  $("pipelinePanel").classList.toggle("hide", currentTab !== "pipeline");
  $("entitiesPanel").classList.toggle("hide", currentTab !== "entities");
  $("operationsPanel").classList.toggle("hide", currentTab !== "operations");
  $("queuePanel").classList.toggle("hide", currentTab === "pipeline" ||
    currentTab === "entities" || currentTab === "operations");
}

function loadCurrent() {
  // Pipeline data is stream-driven (see startStream) -- nothing to fetch for
  // it or the entity browser here, only the review queue tabs still poll.
  if (currentTab === "pipeline" || currentTab === "entities") {
    return Promise.resolve();
  }
  if (currentTab === "operations") {
    return loadOperations();
  }
  return loadQueue().catch(function (err) {
    $("queueBody").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
}

function loadAll() {
  loadCurrent();
}

document.addEventListener("click", function (event) {
  var tab = event.target.closest(".tab");
  if (tab) {
    currentTab = tab.dataset.tab;
    showTab();
    loadCurrent();
    return;
  }

  var pause = event.target.closest("[data-pause]");
  if (pause) {
    // Who, before why: who() focuses the name field and returns null when it
    // is empty, and asking for a reason first would throw the answer away.
    var pauseBy = who();
    if (!pauseBy) { return; }
    // The reason is required, not optional. Whoever finds the pipeline stopped
    // is rarely the person who stopped it, and "paused" with nothing attached
    // is indistinguishable from a bug.
    var why = window.prompt(
      "Why is " + pause.dataset.pause + " being paused?\\n\\n" +
      "Nothing will be lost: queued work waits in its topic and files stay in " +
      "their feed directories.");
    if (!why) { return; }
    pause.disabled = true;
    api("/control/" + pause.dataset.pause + "/pause", {
      method: "POST",
      body: JSON.stringify({ reason: why, reviewed_by: pauseBy }),
    }).then(loadOperations).catch(function (err) {
      $("stages").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
    });
    return;
  }

  var resume = event.target.closest("[data-resume]");
  if (resume) {
    var resumeBy = who();
    if (!resumeBy) { return; }
    var note = window.prompt(
      "Resuming " + resume.dataset.resume + ". What did you find?\\n\\n" +
      "Recorded against the resume, so the next person can see what this was.");
    if (note === null) { return; }
    resume.disabled = true;
    api("/control/" + resume.dataset.resume + "/resume", {
      method: "POST",
      body: JSON.stringify({ reviewed_by: resumeBy, note: note || null }),
    }).then(loadOperations).catch(function (err) {
      $("stages").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
    });
    return;
  }

  if (event.target.closest("#runIntegrity")) {
    var button = $("runIntegrity");
    button.disabled = true;
    $("integrity").innerHTML = '<p class="dim">Running twelve checks&hellip;</p>';
    api("/control/integrity", { method: "POST" }).then(function (data) {
      $("integrity").innerHTML = renderIntegrity(data);
    }).catch(function (err) {
      $("integrity").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
    }).then(function () { button.disabled = false; });
    return;
  }

  var jump = event.target.closest("#uploadJump");
  if (jump) {
    selectedBatch = jump.dataset.batch;
    startStream(); // reconnect scoped to this batch_id; its detail arrives with the stream's next frame
    $("detailWrap").classList.remove("hide");
    $("detailWrap").scrollIntoView({ block: "start" });
    return;
  }

  var row = event.target.closest("tr[data-id]");
  if (row) {
    selectedBatch = row.dataset.id;
    document.querySelectorAll("tbody tr").forEach(function (r) { r.classList.remove("sel"); });
    row.classList.add("sel");
    startStream();
    $("detailWrap").classList.remove("hide");
    return;
  }

  var runBtn = event.target.closest("button[data-stage-run]");
  if (runBtn) {
    runBtn.disabled = true;
    api("/dashboard/batches/" + runBtn.dataset.batch + "/stages/" +
        runBtn.dataset.stageRun + "/run", { method: "POST", body: "{}" })
      .catch(function (err) {
        $("detail").innerHTML += '<div class="err">' + esc(err.message) + "</div>";
        runBtn.disabled = false;
      });
    // No explicit refresh on success: the stream's next tick (\\u22642s)
    // already shows "running", then the funnel counts climbing.
    return;
  }

  var sheetPick = event.target.closest("[data-sheet-pick]");
  if (sheetPick) {
    $("uploadSheet").value = sheetPick.dataset.sheetPick;
    submitUpload();
    return;
  }

  var jumpEntity = event.target.closest("[data-jump-entity]");
  if (jumpEntity) {
    loadEntity(jumpEntity.dataset.jumpEntity).catch(function (err) {
      $("entityDetail").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
    });
    return;
  }

  var entityRow = event.target.closest("tr[data-entity-id]");
  if (entityRow) {
    loadEntity(entityRow.dataset.entityId).catch(function (err) {
      $("entityDetailWrap").classList.remove("hide");
      $("entityDetail").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
    });
    return;
  }

  var explainRow = event.target.closest("tr[data-explain-field]");
  if (explainRow) {
    loadFieldExplain(explainRow.dataset.entityId, explainRow.dataset.explainField);
    return;
  }

  var pick = event.target.closest(".chip.pick");
  if (pick) {
    $("f-" + pick.dataset.for).value = pick.dataset.field;
    return;
  }

  var button = event.target.closest("button[data-act]");
  if (!button) { return; }

  var id = button.dataset.id, act = button.dataset.act;
  var name = who();
  if (!name) { return; }

  var siblings = Array.prototype.slice.call(
    $("c-" + id).querySelectorAll("button[data-act]"));
  var noteEl = $("note-" + id);
  var note = noteEl ? noteEl.value.trim() || null : null;

  var promise = null;
  if (act === "map" || act === "unmap") {
    var field = act === "map" ? $("f-" + id).value.trim() : null;
    if (act === "map" && !field) { $("f-" + id).focus(); return; }
    promise = api("/review/mappings/" + id,
      { method: "POST", body: JSON.stringify({ canonical_field: field, reviewed_by: name }) });
  } else if (act === "merge" || act === "separate") {
    promise = api("/review/candidates/" + id,
      { method: "POST", body: JSON.stringify(
        { accept: act === "merge", reviewed_by: name, note: note }) });
  } else if (act === "release" || act === "rejectq") {
    promise = api("/review/quarantine/" + id,
      { method: "POST", body: JSON.stringify(
        { action: act === "release" ? "release" : "reject", reviewed_by: name, note: note }) });
  } else if (act === "enrichaccept" || act === "enrichreject") {
    promise = api("/review/enrichment/" + id,
      { method: "POST", body: JSON.stringify(
        { accept: act === "enrichaccept", reviewed_by: name }) });
  }
  if (!promise) { return; }

  var box = $("r-" + id);
  siblings.forEach(function (b) { b.disabled = true; });
  box.classList.remove("hide", "bad");
  box.textContent = "Working…";

  promise.then(function (data) {
    var html = esc(data.summary);
    if (data.follow_up && data.follow_up.length) {
      html += "<ul>" + data.follow_up.map(function (f) {
        return "<li>" + esc(f) + "</li>";
      }).join("") + "</ul>";
    }
    box.innerHTML = html;
    $("c-" + id).classList.add("done");
    // No explicit refresh: the stream's next tick carries the updated
    // queue-badge counts within ~2s.
  }).catch(function (err) {
    box.classList.add("bad");
    box.textContent = err.message;
    siblings.forEach(function (b) { b.disabled = false; });
  });
});

$("uploadFile").onchange = function () { pickFile(this.files[0]); };
$("uploadGo").onclick = submitUpload;
["dragenter", "dragover"].forEach(function (evt) {
  $("dropzone").addEventListener(evt, function (e) {
    e.preventDefault();
    $("dropzone").classList.add("drag");
  });
});
["dragleave", "drop"].forEach(function (evt) {
  $("dropzone").addEventListener(evt, function (e) {
    e.preventDefault();
    $("dropzone").classList.remove("drag");
  });
});
$("dropzone").addEventListener("drop", function (e) {
  var file = e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) { pickFile(file); }
});

$("entitySearchGo").onclick = searchEntities;
$("entitySearchQuery").addEventListener("keydown", function (e) {
  if (e.key === "Enter") { searchEntities(); }
});

$("refresh").onclick = loadAll;
$("key").onchange = function () {
  sessionStorage.setItem(KEY, $("key").value.trim());
  startStream();
  loadAll();
};
$("who").value = localStorage.getItem(WHO) || "";

showTab();
startStream();
loadAll();
setInterval(loadAll, 5000);
</script>
"""
