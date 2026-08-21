"""The pipeline dashboard: one page, no build step, same shape as review_page.py.

It answers one question: for a batch (a file someone loaded), how far did its
records get? `rows_read` on the batches list is live while a batch is still
`running` -- the loop that reads the file commits one record at a time and
writes this column after every one (`record_pipeline/runner.py`), so this page
is watching the same counter the pipeline is incrementing, not a cached
snapshot of it.

It only reads. There is nothing to decide here, unlike /review/page -- so
there are no actions, no notes, no "who" field.
"""

DASHBOARD_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Pipeline dashboard</title>
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
  input, select, button {
    background:var(--card); border:1px solid var(--line); color:var(--ink);
    border-radius:6px; padding:8px 11px; font:inherit;
  }
  button { cursor:pointer; }
  button:hover:not(:disabled) { border-color:var(--accent); }
  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

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
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }
  .tag { font-size:11px; text-transform:uppercase; letter-spacing:.06em;
         color:var(--dim); border:1px solid var(--line); border-radius:4px;
         padding:1px 6px; white-space:nowrap; }
  .tag.ok { color:var(--ok); border-color:var(--ok); }
  .tag.run { color:var(--accent); border-color:var(--accent); }
  .tag.bad { color:var(--bad); border-color:var(--bad); }
  .dim { color:var(--dim); font-size:13px; }

  .card { border:1px solid var(--line); background:var(--card); border-radius:8px;
          padding:14px 16px; margin-bottom:12px; }

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
           padding:26px; text-align:center; color:var(--dim); }
  .err { border:1px solid var(--bad); color:var(--bad); background:var(--card);
         border-radius:8px; padding:14px; }
  .hide { display:none; }
</style>

<div class="wrap">
  <h1>Pipeline dashboard</h1>
  <p class="sub">
    Where records are, from ingestion through golden-record build.
    &middot; <span id="when">loading&hellip;</span>
  </p>

  <div class="bar">
    <input id="key" type="password" placeholder="X-API-Key" autocomplete="off"
           class="hide" style="width:220px">
    <button id="refresh">Refresh</button>
  </div>

  <h2>All batches, combined</h2>
  <div id="summary" class="cards"></div>

  <h2>Recent batches</h2>
  <div id="batches"></div>

  <div id="detailWrap" class="hide">
    <h2 id="detailTitle">Batch</h2>
    <div id="detail"></div>
  </div>
</div>

<script>
var KEY = "pcdf_api_key";
var selected = null;

function $(id) { return document.getElementById(id); }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function ago(iso) {
  if (!iso) { return "\\u2014"; }
  var secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  var d = Math.floor(secs / 86400), h = Math.floor(secs / 3600), m = Math.floor(secs / 60);
  if (d >= 1) { return d + "d ago"; }
  if (h >= 1) { return h + "h ago"; }
  if (m >= 1) { return m + "m ago"; }
  return "just now";
}

function fmt(n) { return Number(n || 0).toLocaleString(); }

function headers() {
  var k = sessionStorage.getItem(KEY);
  var h = { "Content-Type": "application/json" };
  if (k) { h["X-API-Key"] = k; }
  return h;
}

function api(path) {
  return fetch(path, { headers: headers() }).then(function (res) {
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

/* ---------------- summary cards ---------------- */

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

/* ---------------- batches table ---------------- */

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
      (b.batch_id === selected ? ' class="sel"' : "") + ">" +
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

/* ---------------- funnel detail ---------------- */

function step(name, value, base, breakdown, stuck) {
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
    bd + "</div>";
}

function renderFunnel(f) {
  var base = f.ingested;
  var validatedOk = f.validated.valid + f.validated.warning;
  var resolvedTotal = f.resolved.auto_linked + f.resolved.new_entity + f.resolved.manual;
  return '<div class="card">' +
    step("Ingested", f.ingested, base, [["failed", f.failed]], f.failed > 0) +
    step("Normalized", f.normalized, base) +
    step("Validated", validatedOk, base,
      [["valid", f.validated.valid], ["warning", f.validated.warning],
       ["invalid", f.validated.invalid]], f.validated.invalid > 0) +
    step("Quarantine (of the invalid)", f.quarantine.open + f.quarantine.released +
      f.quarantine.rejected + f.quarantine.resolved, base,
      [["open", f.quarantine.open], ["released", f.quarantine.released],
       ["rejected", f.quarantine.rejected], ["resolved", f.quarantine.resolved]],
      f.quarantine.open > 0) +
    step("Resolved to an entity", resolvedTotal, base,
      [["auto-linked", f.resolved.auto_linked], ["new entity", f.resolved.new_entity],
       ["manual", f.resolved.manual], ["possible duplicates open", f.needs_review.candidates]],
      f.needs_review.candidates > 0) +
    step("Golden record built", f.golden_built, base) +
    step("Mapping needing review (this layout)", f.needs_review.mapping, base, null,
      f.needs_review.mapping > 0) +
    "</div>";
}

/* ---------------- loading ---------------- */

function loadSummary() {
  return api("/dashboard/summary").then(function (data) {
    $("summary").innerHTML = renderSummary(data);
    $("when").textContent = "updated " + new Date().toLocaleTimeString();
  });
}

function loadBatches() {
  return api("/dashboard/batches").then(function (data) {
    $("batches").innerHTML = renderBatches(data.results);
  });
}

function loadDetail() {
  if (!selected) { return Promise.resolve(); }
  return api("/dashboard/batches/" + selected).then(function (b) {
    $("detailWrap").classList.remove("hide");
    $("detailTitle").textContent = b.source_name + " \\u2014 " + b.file_name;
    $("detail").innerHTML =
      '<p class="dim">' + statusTag(b.status) + " &middot; started " + esc(ago(b.started_at)) +
      (b.finished_at ? " &middot; finished " + esc(ago(b.finished_at)) : "") +
      (b.error_message ? " &middot; " + esc(b.error_message) : "") + "</p>" +
      renderFunnel(b.funnel);
  });
}

function loadAll() {
  loadSummary().catch(function (err) {
    $("summary").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
  loadBatches().catch(function (err) {
    $("batches").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
  loadDetail().catch(function (err) {
    $("detail").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
}

document.addEventListener("click", function (event) {
  var row = event.target.closest("tr[data-id]");
  if (!row) { return; }
  selected = row.dataset.id;
  document.querySelectorAll("tbody tr").forEach(function (r) { r.classList.remove("sel"); });
  row.classList.add("sel");
  loadDetail().catch(function (err) {
    $("detailWrap").classList.remove("hide");
    $("detail").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
  });
});

$("refresh").onclick = loadAll;
$("key").onchange = function () {
  sessionStorage.setItem(KEY, $("key").value.trim());
  loadAll();
};

loadAll();
setInterval(loadAll, 5000);
</script>
"""
