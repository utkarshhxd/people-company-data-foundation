r"""The pipeline dashboard: one page, no build step, same shape as review_page.py.

It answers one question: for a batch (a file someone loaded), how far did its
records get? `rows_read` on the batches list is live while a batch is still
`running` -- the loop that reads the file commits one record at a time and
writes this column after every one (`record_pipeline/runner.py`), so this page
is watching the same counter the pipeline is incrementing, not a cached
snapshot of it.

It only reads. There is nothing to decide here, unlike /review/page -- so
there are no actions, no notes, no "who" field.

The style block and the shared helpers come from `assets.py`; see the note
there about why these strings are raw.
"""

from review_console.assets import BASE_CSS, BASE_JS

_HEAD = r"""<!doctype html>
<meta charset="utf-8">
<title>Pipeline dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
"""

_BODY = r"""
<div class="wrap">
  <div class="head">
    <div class="head-row">
      <div>
        <h1>Pipeline dashboard</h1>
        <p class="sub">Where records are, from ingestion through
          golden-record build.</p>
      </div>
      <div class="grow"></div>
      <span id="when" class="dim">loading&hellip;</span>
      <input id="key" type="password" placeholder="X-API-Key" autocomplete="off"
             class="hide" style="width:220px">
      <button id="refresh">Refresh</button>
    </div>
  </div>

  <section>
    <h2>All batches, combined</h2>
    <div id="summary" class="cards"></div>

    <h2>Recent batches</h2>
    <div class="card flush scroll"><div id="batches"></div></div>

    <div id="detailWrap" class="hide">
      <h2 id="detailTitle">Batch</h2>
      <div id="detail"></div>
    </div>
  </section>
</div>
"""

_SCRIPT = r"""
var selected = null;

/* ---------------- summary cards ---------------- */

function renderSummary(f) {
  return statCard("Ingested", f.ingested) +
    statCard("Normalized", f.normalized) +
    statCard("Validated OK", f.validated.valid + f.validated.warning) +
    statCard("Resolved", f.resolved.auto_linked + f.resolved.new_entity + f.resolved.manual) +
    statCard("Golden built", f.golden_built) +
    statCard("Quarantine open", f.quarantine.open, f.quarantine.open ? "warn" : "") +
    statCard("Mapping needs review", f.needs_review.mapping, f.needs_review.mapping ? "warn" : "") +
    statCard("Duplicate candidates open", f.needs_review.candidates, f.needs_review.candidates ? "warn" : "") +
    statCard("Rows that threw", f.failed, f.failed ? "bad" : "");
}

/* ---------------- batches table ----------------
   "Skipped" is batch.rows_skipped -- rows the reader could not turn into a
   record at all. It is a different number from the funnel's "rows that threw"
   (record_error), and they were both labelled "Failed" until it became clear
   nobody could tell which was which. */

function renderBatches(rows) {
  if (!rows.length) {
    return '<div class="empty">No batches have been loaded yet.</div>';
  }
  var html = "<table><thead><tr>" +
    "<th>Source</th><th>File</th><th>Status</th><th>Read</th><th>Ingested</th>" +
    "<th>Skipped</th><th>Started</th></tr></thead><tbody>";
  rows.forEach(function (b) {
    html += '<tr class="pickable' + (b.batch_id === selected ? " sel" : "") +
      '" data-id="' + esc(b.batch_id) + '">' +
      "<td>" + esc(b.source_name) + "</td>" +
      '<td class="mono">' + esc(b.file_name) + "</td>" +
      "<td>" + statusTag(b.status) + "</td>" +
      '<td class="num">' + fmt(b.rows_read) + "</td>" +
      '<td class="num">' + fmt(b.rows_ingested) + "</td>" +
      '<td class="num">' + (b.rows_failed ? fmt(b.rows_failed) : "—") + "</td>" +
      '<td class="nowrap">' + esc(ago(b.started_at)) + "</td>" +
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
    '<span class="n">' + fmt(value) + (base > 0 ? " · " + pct.toFixed(1) + "%" : "") +
    "</span></div>" +
    '<div class="track"><div class="fill" style="width:' + pct.toFixed(1) + '%"></div></div>' +
    bd + "</div>";
}

function renderFunnel(f) {
  var base = f.ingested;
  var validatedOk = f.validated.valid + f.validated.warning;
  var resolvedTotal = f.resolved.auto_linked + f.resolved.new_entity + f.resolved.manual;
  return '<div class="card">' +
    step("Ingested", f.ingested, base, [["rows that threw", f.failed]], f.failed > 0) +
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
    $("detailTitle").textContent = b.source_name + " — " + b.file_name;
    $("detail").innerHTML =
      '<p class="dim">' + statusTag(b.status) + " &middot; started " + esc(ago(b.started_at)) +
      (b.finished_at ? " &middot; finished " + esc(ago(b.finished_at)) : "") +
      (b.error_message ? " &middot; " + esc(b.error_message) : "") + "</p>" +
      renderFunnel(b.funnel);
  });
}

function loadAll() {
  loadSummary().catch(function (err) {
    $("summary").innerHTML = errorBox(err.message);
  });
  loadBatches().catch(function (err) {
    $("batches").innerHTML = errorBox(err.message);
  });
  loadDetail().catch(function (err) {
    $("detail").innerHTML = errorBox(err.message);
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
    $("detail").innerHTML = errorBox(err.message);
  });
});

$("refresh").onclick = loadAll;
$("key").onchange = function () {
  sessionStorage.setItem(KEY, $("key").value.trim());
  loadAll();
};

loadAll();
setInterval(loadAll, 5000);
"""

DASHBOARD_PAGE = (
    _HEAD + BASE_CSS + _BODY + "<script>" + BASE_JS + _SCRIPT + "</script>\n"
)
