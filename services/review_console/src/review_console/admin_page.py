r"""One page: intake, pipeline progress, the review queues, and operations.

Not a third source of truth. Every fetch here hits the exact same JSON
endpoints `review_page.py` and `dashboard_page.py` already serve, and every
decision posts to the exact same write endpoints those pages post to -- so a
decision made from this page, from `/review/page`, or from a terminal CLI are
the same code path. This page combines views a person previously had to open
separately; it adds no state and no endpoint of its own.

The style block and the shared helpers come from `assets.py`, which is where
they live now that three pages need them. The strings here are raw, so an
escape written for JavaScript reaches JavaScript -- see the note in
`assets.py`, and `test_pages.py` for the check that keeps it that way.
"""

from review_console.assets import BASE_CSS, BASE_JS, QUEUE_JS

_HEAD = r"""<!doctype html>
<meta charset="utf-8">
<title>Admin dashboard</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
"""

_BODY = r"""
<div class="wrap">
  <div class="head">
    <div class="head-row">
      <div>
        <h1>Data foundation console</h1>
        <p class="sub">Load files, watch them through, decide what needs
          deciding, and stop the machine when it is doing the wrong thing.</p>
      </div>
      <div class="grow"></div>
      <span id="health" class="pill"><span class="dot"></span>
        <span id="healthText">checking&hellip;</span></span>
      <span id="when" class="dim">connecting&hellip;</span>
    </div>
    <div class="head-row" style="margin-top:10px">
      <input id="who" type="text" class="grow"
             placeholder="Your name &mdash; recorded on every decision"
             autocomplete="name">
      <input id="key" type="password" placeholder="X-API-Key" autocomplete="off"
             class="hide" style="width:220px">
      <button id="refresh" title="Reload this tab (r)">Refresh</button>
    </div>
    <div class="tabs" role="tablist">
      <button class="tab" role="tab" data-tab="pipeline">
        Pipeline <span class="kbd">1</span></button>
      <button class="tab" role="tab" data-tab="intake">
        Intake <span class="n" id="n-intake">&ndash;</span>
        <span class="kbd">2</span></button>
      <button class="tab" role="tab" data-tab="entities">
        Entities <span class="kbd">3</span></button>
      <button class="tab" role="tab" data-tab="mappings">
        Schema mappings <span class="n" id="n-mappings">&ndash;</span>
        <span class="kbd">4</span></button>
      <button class="tab" role="tab" data-tab="candidates">
        Possible duplicates <span class="n" id="n-candidates">&ndash;</span>
        <span class="kbd">5</span></button>
      <button class="tab" role="tab" data-tab="quarantine">
        Quarantined <span class="n" id="n-quarantine">&ndash;</span>
        <span class="kbd">6</span></button>
      <button class="tab" role="tab" data-tab="enrichment">
        AI proposals <span class="n" id="n-enrichment">&ndash;</span>
        <span class="kbd">7</span></button>
      <button class="tab" role="tab" data-tab="operations">
        Operations <span class="n" id="n-operations">&ndash;</span>
        <span class="kbd">8</span></button>
      <button class="tab" role="tab" data-tab="activity">
        Activity <span class="n" id="n-activity">&ndash;</span>
        <span class="kbd">9</span></button>
    </div>
  </div>

  <div id="banner"></div>

  <section id="pipelinePanel">
    <h2>All batches, combined</h2>
    <div id="summary" class="cards"></div>
    <h2>Recent batches</h2>
    <div class="card flush scroll"><div id="batches"></div></div>
    <div id="detailWrap" class="hide">
      <h2 id="detailTitle">Batch</h2>
      <div id="detail"></div>
    </div>
  </section>

  <section id="intakePanel" class="hide">
    <h2>Add files to the queue</h2>
    <p class="dim">
      Drop as many files as you like. They are written to disk, queued, and
      loaded one at a time in the order they arrived &mdash; so a ten-file drop
      is a queue rather than ten loads competing for the same tables. The
      settings below apply to every file in this drop, because entity type,
      source name and reliability are properties of the feed, not of the file.
    </p>
    <div class="card">
      <div id="dropzone" class="dropzone">
        Drag CSV or Excel files here, or
        <label class="pick">choose files<input type="file" id="uploadFile" multiple
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
        <button id="uploadGo" class="primary">Add to queue</button>
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
            placeholder="sheet name (single multi-sheet Excel file only)">
          <input type="text" id="uploadRecordIdColumn"
            placeholder="record id column (optional)">
          <input type="number" id="uploadReliability" value="0.5" min="0" max="1"
            step="0.05" title="source reliability, 0-1" style="width:90px">
          <select id="uploadDescribes">
            <option value="">describes (unset)</option>
            <option value="organisation">organisation</option>
            <option value="location">location</option>
          </select>
          <label class="check"><input type="checkbox" id="uploadAllowReingest">
            allow re-ingesting these exact files</label>
        </div>
      </details>
      <div id="uploadStatus" class="result hide"></div>
    </div>

    <h2>The queue</h2>
    <div id="queueStats" class="cards" style="margin-bottom:12px"></div>
    <div class="bar" style="margin-bottom:10px">
      <select id="queueFilter">
        <option value="">everything</option>
        <option value="queued">queued</option>
        <option value="held">held</option>
        <option value="running">running</option>
        <option value="completed">completed</option>
        <option value="failed">failed</option>
        <option value="cancelled">cancelled</option>
      </select>
    </div>
    <div class="card flush scroll"><div id="ingestQueue"></div></div>
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
    <h2>Stop and start the pipeline</h2>
    <p class="dim">
      Pausing a stage never discards anything. Whatever is mid-record finishes
      and is recorded, queued work waits in its Kafka topic, files stay in their
      feed directories, and uploaded files are held rather than failed &mdash;
      what stops is anything new starting. Validation also stops itself when
      almost everything coming through it is invalid.
    </p>
    <div class="card flush scroll">
      <div id="stages"><p class="dim" style="padding:14px">Loading&hellip;</p></div>
    </div>

    <h2>Automatic stop</h2>
    <div class="card"><div id="supervisor"><p class="dim">Loading&hellip;</p></div></div>

    <h2>Services</h2>
    <p class="dim">
      A container being up is not the same as its loop turning. Each of these
      records a heartbeat as it works; what is shown is how long ago.
    </p>
    <div class="card flush scroll">
      <div id="services"><p class="dim" style="padding:14px">Loading&hellip;</p></div>
    </div>

    <h2>Queued work</h2>
    <p class="dim">
      Messages published to a topic that the consumer has not committed yet.
      Non-zero during a load is normal; non-zero and not falling means a stage
      is stopped or cannot keep up.
    </p>
    <div class="card flush scroll">
      <div id="lag"><p class="dim" style="padding:14px">Loading&hellip;</p></div>
    </div>

    <h2>Alerts</h2>
    <div class="card"><div id="alerts"><p class="dim">Loading&hellip;</p></div></div>

    <h2>Data integrity</h2>
    <p class="dim">
      Twelve reconciliation checks that each return rows only when something is
      wrong, so all-empty is the pass. They read every table, so they run when
      asked rather than on every render.
    </p>
    <div class="card">
      <button id="runIntegrity">Run the checks</button>
      <div id="integrity" style="margin-top:12px"></div>
    </div>
  </section>

  <section id="activityPanel" class="hide">
    <h2>What happened</h2>
    <p class="dim">
      Loads, stops and starts, rows that failed, and what the services said
      &mdash; in one place, newest first. The services' full output still goes
      to their container logs; what is kept here is WARNING and above, which is
      the part that says something went wrong.
    </p>
    <div class="bar" style="margin-bottom:12px">
      <select id="activityLevel">
        <option value="">everything</option>
        <option value="warn">warnings and errors</option>
        <option value="error">errors only</option>
      </select>
      <select id="activityKind">
        <option value="">every kind</option>
        <option value="load">file loads</option>
        <option value="queue">intake queue</option>
        <option value="control">stops and starts</option>
        <option value="record">failed rows</option>
        <option value="service">service messages</option>
      </select>
      <label class="check"><input type="checkbox" id="activityFollow" checked>
        keep up to date</label>
    </div>
    <div class="card"><div id="activity"><p class="dim">Loading&hellip;</p></div></div>
  </section>

  <section id="queuePanel" class="hide">
    <div id="queueBody"></div>
  </section>
</div>
"""

_SCRIPT = r"""
var currentTab = "pipeline";
var selectedBatch = null;
var selectedFiles = [];

/* ================= pipeline tab ================= */

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

/* Two different numbers were both labelled "Failed" here: the batches table
   shows batch.rows_skipped (rows the reader could not turn into a record) and
   the funnel shows count(*) from record_error (rows that threw part-way
   through). They legitimately disagree, so they are named differently. */
function renderBatches(rows) {
  if (!rows.length) {
    return '<div class="empty">No batches have been loaded yet.</div>';
  }
  var html = "<table><thead><tr>" +
    "<th>Source</th><th>File</th><th>Status</th><th>Read</th><th>Ingested</th>" +
    "<th>Skipped</th><th>Started</th></tr></thead><tbody>";
  rows.forEach(function (b) {
    html += '<tr class="pickable' +
      (b.batch_id === selectedBatch ? " sel" : "") + '" data-id="' +
      esc(b.batch_id) + '">' +
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
    '<span class="n">' + fmt(value) + (base > 0 ? " · " + pct.toFixed(1) + "%" : "") +
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
    badge = tag("running…", "run");
  } else if (status === "completed") {
    badge = tag("done " + ago(s.finished_at), "ok");
  } else if (status === "failed") {
    badge = tag("failed: " + (s.error || ""), "bad");
  } else if (!batchReady) {
    badge = '<span class="dim">batch still ingesting</span>';
  }
  return '<div class="actions">' +
    '<button class="small" data-stage-run="' + esc(stage) + '" data-batch="' +
    esc(batchId) + '"' + (disabled ? " disabled" : "") + ">Run</button>" +
    badge + "</div>";
}

function renderFunnel(b) {
  var f = b.funnel, st = b.stages, ready = b.status === "completed";
  var base = f.ingested;
  var validatedOk = f.validated.valid + f.validated.warning;
  var resolvedTotal = f.resolved.auto_linked + f.resolved.new_entity + f.resolved.manual;
  return '<div class="card">' +
    '<p class="dim">Manual stage runs are not safe to run at the same time as ' +
    'live ingestion on overlapping data — resolution in particular has no ' +
    'lock against concurrent runs and can create duplicate entities. Do not ' +
    'trigger a stage here while the watcher is actively loading a file from the ' +
    'same source.</p>' +
    step("Ingested", f.ingested, base, [["rows that threw", f.failed]], f.failed > 0) +
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

/* ================= intake: files in, queue below ================= */

function pickFiles(files) {
  selectedFiles = Array.prototype.slice.call(files || []);
  var box = $("uploadFileName");
  if (!selectedFiles.length) { box.textContent = ""; return; }
  var total = selectedFiles.reduce(function (n, f) { return n + f.size; }, 0);
  box.innerHTML = selectedFiles.map(function (f) {
    return '<span class="chip">' + esc(f.name) + " · " + bytes(f.size) +
      "</span>";
  }).join("") + '<div style="margin-top:6px">' + selectedFiles.length +
    " file(s), " + bytes(total) + "</div>";
}

function uploadStatusBox(cls, html) {
  var box = $("uploadStatus");
  box.className = "result" + (cls ? " " + cls : "");
  box.innerHTML = html;
}

function submitUpload() {
  if (!selectedFiles.length) {
    $("dropzone").scrollIntoView({ block: "center" });
    return;
  }
  var entityType = $("uploadEntityType").value;
  if (!entityType) { $("uploadEntityType").focus(); return; }
  var sourceName = $("uploadSourceName").value.trim();
  if (!sourceName) { $("uploadSourceName").focus(); return; }

  var form = new FormData();
  selectedFiles.forEach(function (f) { form.append("files", f); });
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
  form.append("queued_by", ($("who").value.trim() || "console"));

  $("uploadGo").disabled = true;
  uploadStatusBox("busy", "Uploading " + selectedFiles.length + " file(s)…");
  apiUpload("/dashboard/ingest", form).then(function (out) {
    uploadStatusBox(null, "Queued <b>" + out.queued +
      "</b> file(s). They will be loaded one at a time, in this order.");
    pickFiles([]);
    $("uploadFile").value = "";
    loadIngestQueue();
  }).catch(function (err) {
    uploadStatusBox("bad", esc(err.message));
  }).then(function () {
    $("uploadGo").disabled = false;
  });
}

function queueRowActions(item) {
  if (item.status === "queued" || item.status === "held") {
    return '<button class="small danger" data-queue-cancel="' +
      esc(item.queue_id) + '">Cancel</button>';
  }
  if (item.status === "running") {
    return '<span class="dim">loading&hellip;</span>';
  }
  return '<button class="small" data-queue-requeue="' + esc(item.queue_id) +
    '">Queue again</button>';
}

function queueRowDetail(item) {
  if (item.error) {
    var sheets = (item.detail || {}).available_sheets || [];
    return '<div class="dim" style="color:var(--bad)">' + esc(item.error) +
      "</div>" + (sheets.length
        ? '<div class="chips">' + sheets.map(function (name) {
            return '<span class="chip pick" data-sheet-pick="' + esc(name) +
              '">' + esc(name) + "</span>";
          }).join("") + "</div>"
        : "");
  }
  if (item.status === "completed") {
    return '<span class="dim">' + fmt(item.rows_ingested) + " row(s) ingested" +
      (item.rows_skipped ? ", " + fmt(item.rows_skipped) + " skipped" : "") +
      "</span>";
  }
  if (item.status === "held") {
    return '<span class="dim">waiting for ingestion to be started again</span>';
  }
  return '<span class="dim">' + esc(item.entity_type) + " · " +
    esc(item.source_name) + "</span>";
}

function renderIngestQueue(data) {
  if (!data.items.length) {
    return '<div class="empty">Nothing has been queued yet.</div>';
  }
  return "<table><thead><tr><th>File</th><th>Status</th><th>Source</th>" +
    "<th>Size</th><th>Queued</th><th>What happened</th><th></th>" +
    "</tr></thead><tbody>" +
    data.items.map(function (item) {
      return "<tr>" +
        '<td class="mono">' + esc(item.file_name) +
          (item.attempts > 1
            ? ' <span class="tag warn">attempt ' + item.attempts + "</span>" : "") +
          "</td>" +
        "<td>" + statusTag(item.status) + "</td>" +
        "<td>" + esc(item.source_name) + "</td>" +
        '<td class="num nowrap">' + bytes(item.size_bytes) + "</td>" +
        '<td class="nowrap">' + esc(ago(item.queued_at)) +
          '<div class="dim">by ' + esc(item.queued_by) + "</div></td>" +
        "<td>" + queueRowDetail(item) +
          (item.batch_id
            ? '<div><span class="chip pick" data-queue-batch="' +
              esc(item.batch_id) + '">view batch</span></div>' : "") +
          "</td>" +
        '<td class="right">' + queueRowActions(item) + "</td>" +
        "</tr>";
    }).join("") + "</tbody></table>";
}

function renderQueueStats(data) {
  var t = data.totals || {};
  function n(name) { return (t[name] || {}).count || 0; }
  return statCard("Waiting", data.waiting, data.waiting ? "warn" : "") +
    statCard("Held", n("held"), n("held") ? "warn" : "") +
    statCard("Loading now", n("running")) +
    statCard("Loaded", n("completed"), "ok") +
    statCard("Failed", n("failed"), n("failed") ? "bad" : "");
}

function loadIngestQueue() {
  var status = $("queueFilter").value;
  return api("/dashboard/queue?limit=100" + (status ? "&status=" + status : ""))
    .then(function (data) {
      $("ingestQueue").innerHTML = renderIngestQueue(data);
      $("queueStats").innerHTML = renderQueueStats(data);
      var badge = $("n-intake");
      badge.textContent = String(data.waiting + data.running);
      badge.className = "n" + (data.waiting ? " warn" : "");
    }).catch(function (err) {
      $("ingestQueue").innerHTML = errorBox(err.message);
    });
}

/* ================= live stream (push, not poll) =================
   /dashboard/stream stays open and sends a fresh snapshot roughly every 2s
   (see stream.py) -- the summary cards, the batch list, the review-queue
   tab badges, and the selected batch's funnel all come from it. Nothing on
   the pipeline tab is fetched on a timer; this connection IS the timer. */

var streamAbort = null;

function applyStreamPayload(payload) {
  $("summary").innerHTML = renderSummary(payload.summary);
  $("batches").innerHTML = renderBatches(payload.batches);
  applyQueueSummary(payload.queues);
  if (payload.batch) {
    var b = payload.batch;
    $("detailWrap").classList.remove("hide");
    $("detailTitle").textContent = b.source_name + " — " + b.file_name;
    $("detail").innerHTML =
      '<p class="dim">' + statusTag(b.status) + " &middot; started " + esc(ago(b.started_at)) +
      (b.finished_at ? " &middot; finished " + esc(ago(b.finished_at)) : "") +
      (b.error_message ? " &middot; " + esc(b.error_message) : "") + "</p>" +
      renderFunnel(b);
  }
  $("when").textContent = "live · " + new Date().toLocaleTimeString();
}

function startStream() {
  if (streamAbort) { streamAbort.abort(); }
  var abort = new AbortController();
  streamAbort = abort;
  var url = "/dashboard/stream" +
    (selectedBatch ? "?batch_id=" + encodeURIComponent(selectedBatch) : "");

  fetch(url, { headers: headers(), signal: abort.signal }).then(function (res) {
    if (res.status === 401 || res.status === 403) { throw unauthorized(); }
    if (!res.ok || !res.body) { throw new Error("stream failed: HTTP " + res.status); }
    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = "";
    function pump() {
      return reader.read().then(function (result) {
        if (result.done) { return; }
        buffer += decoder.decode(result.value, { stream: true });
        var frames = buffer.split("\n\n");
        buffer = frames.pop();
        frames.forEach(function (frame) {
          var line = frame.split("\n").filter(function (l) {
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
    $("when").textContent = "reconnecting…";
    setTimeout(function () {
      if (streamAbort === abort) { startStream(); }
    }, 3000);
  });
}

/* ================= review queue tabs =================
   card / actionRow / the four renderers / QUEUES / datalists all live in
   assets.QUEUE_JS, shared with /review/page. Only what is specific to
   having them as tabs is here. */

function applyQueueSummary(data) {
  ["mappings", "candidates", "quarantine", "enrichment"].forEach(function (q) {
    var info = data.queues[q];
    var label = String(info.open);
    if (info.open > 0 && info.oldest_seconds > 0) {
      label += " · " + dur(info.oldest_seconds);
    }
    var badge = $("n-" + q);
    badge.textContent = label;
    badge.className = "n" + (info.open ? " warn" : "");
  });
}

/* A refresh that lands while somebody is typing a note throws the note away.
   The queues used to be replaced wholesale every five seconds, which made a
   considered note a race against a timer. */
function queueIsBeingUsed() {
  var body = $("queueBody");
  if (!body || body.classList.contains("hide")) { return false; }
  if (body.contains(document.activeElement)) { return true; }
  var fields = body.querySelectorAll("input[type=text], textarea");
  for (var i = 0; i < fields.length; i++) {
    if (fields[i].value.trim()) { return true; }
  }
  return false;
}

function loadQueue(force) {
  var queue = QUEUES[currentTab];
  if (!queue) { return Promise.resolve(); }
  if (!force && queueIsBeingUsed()) { return Promise.resolve(); }
  if (!$("queueBody").innerHTML) {
    $("queueBody").innerHTML = '<p class="dim">Loading&hellip;</p>';
  }
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
  var html = '<div class="card flush scroll"><table><thead><tr><th>Name</th>' +
    "<th>Type</th><th>Records</th></tr></thead><tbody>";
  rows.forEach(function (e) {
    html += '<tr class="pickable" data-entity-id="' + esc(e.entity_id) + '">' +
      "<td>" + esc(e.display_name || "(no golden value yet)") + "</td>" +
      "<td>" + esc(e.entity_type) + "</td>" +
      '<td class="num">' + fmt(e.record_count) + "</td>" +
      "</tr>";
  });
  return html + "</tbody></table></div>";
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
    $("entityResults").innerHTML = errorBox(err.message);
  });
}

function contributionRow(c) {
  var tags = "";
  if (c.is_winning_record) { tags += tag("winning", "ok") + " "; }
  else if (c.agrees_with_golden) { tags += tag("agrees") + " "; }
  else { tags += tag("differs", "warn") + " "; }
  var fails = (c.validation || []).filter(function (v) { return v.outcome === "fail"; });
  return "<tr>" +
    "<td>" + esc(c.source_name) + "</td>" +
    '<td class="mono">' + esc(c.file_name) + " row " + esc(c.row_number) + "</td>" +
    "<td>" + esc(c.raw_value) + '<span class="dim"> → </span>' +
      esc(c.normalized_value) + "</td>" +
    "<td>" + tags +
      (fails.length ? tag(fails.length + " failing", "bad") : "") +
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
  row.firstElementChild.innerHTML = '<p class="dim">Loading&hellip;</p>';
  api("/entities/" + entityId + "/fields/" + encodeURIComponent(field)).then(function (d) {
    var html = '<div class="scroll"><table><thead><tr><th>Source</th>' +
      "<th>File / row</th><th>Raw → normalized</th><th></th></tr></thead><tbody>" +
      d.contributions.map(contributionRow).join("") +
      "</tbody></table></div>";
    row.firstElementChild.innerHTML = html;
  }).catch(function (err) {
    row.firstElementChild.innerHTML = errorBox(err.message);
  });
}

function attrRows(entityId, attrs) {
  var names = Object.keys(attrs).sort();
  if (!names.length) {
    return '<p class="dim">No golden values yet &mdash; run resolution and golden build.</p>';
  }
  var html = '<div class="card flush scroll"><table><thead><tr><th>Field</th>' +
    "<th>Value</th><th>Confidence</th><th>Strategy</th><th>Sources</th>" +
    "</tr></thead><tbody>";
  names.forEach(function (name) {
    var a = attrs[name];
    html += '<tr class="pickable" data-explain-field="' + esc(name) +
      '" data-entity-id="' + esc(entityId) + '">' +
      '<td class="mono">' + esc(name) + "</td>" +
      "<td>" + esc(a.value) + "</td>" +
      '<td class="num">' + Number(a.confidence).toFixed(2) + "</td>" +
      "<td>" + esc(a.strategy) + "</td>" +
      "<td>" + fmt(a.supporting_sources) + " agreed" +
        (a.competing_values > 0
          ? ", " + fmt(a.competing_values) + " competing" : "") +
      "</td></tr>" +
      '<tr class="hide" id="explain-' + esc(name) +
      '"><td colspan="5"></td></tr>';
  });
  return html + "</tbody></table></div>";
}

function renderRelationships(rels) {
  if (!rels.length) { return ""; }
  return "<h3>Relationships</h3>" +
    '<div class="chips">' + rels.map(function (r) {
      var label = (r.direction === "employer" ? "works at " : "employs ") +
        (r.display_name || r.entity_id);
      return '<span class="chip pick" data-jump-entity="' + esc(r.entity_id) + '">' +
        esc(label) + "</span>";
    }).join("") + "</div>";
}

function renderObservedBy(rows) {
  if (!rows.length) { return ""; }
  return "<h3>Observed by</h3>" +
    '<div class="card flush scroll"><table><thead><tr><th>Source</th>' +
    "<th>File / row</th><th>Match</th></tr></thead><tbody>" +
    rows.map(function (o) {
      return "<tr><td>" + esc(o.source_name) + "</td>" +
        '<td class="mono">' + esc(o.file_name) + " row " + esc(o.row_number) + "</td>" +
        "<td>" + esc(o.match_method) + " " + Number(o.match_confidence).toFixed(2) +
        " (" + esc(o.match_status) + ")</td></tr>";
    }).join("") + "</tbody></table></div>";
}

function loadEntity(entityId) {
  return api("/entities/" + entityId).then(function (d) {
    $("entityDetailWrap").classList.remove("hide");
    var name = d.attributes.full_name || d.attributes.company_name;
    $("entityDetailTitle").textContent =
      (name ? name.value : d.entity_type) + (d.was_merged ? " (merged)" : "");
    $("entityDetail").innerHTML =
      '<p class="dim mono">' + esc(d.entity_id) + " · " + esc(d.entity_type) +
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
  if (seconds == null) { return tag("never"); }
  var cls = seconds >= badAt ? "bad" : (seconds >= warnAt ? "warn" : "ok");
  return tag(dur(seconds) + " ago", cls);
}

function renderStages(data) {
  return "<table><thead><tr><th>Stage</th><th>State</th><th>Why</th>" +
    "<th></th></tr></thead><tbody>" +
    data.stages.map(function (s) {
      var control = s.paused
        ? '<button class="small primary" data-resume="' + esc(s.stage) + '">Start</button>'
        : '<button class="small danger" data-pause="' + esc(s.stage) + '">Stop</button>';
      return "<tr><td>" + esc(s.stage) + "</td>" +
        "<td>" + tag(s.state, s.paused ? "bad" : "ok") + "</td>" +
        "<td>" + (s.paused
          ? esc(s.reason || "no reason recorded") +
            '<div class="dim">by ' + esc(s.changed_by) + " · " +
            esc(ago(s.changed_at)) + "</div>"
          : '<span class="dim">—</span>') + "</td>" +
        '<td class="right">' + control + "</td></tr>";
    }).join("") + "</tbody></table>";
}

function renderSupervisor(s) {
  if (!s.enabled) {
    return '<div class="note">Automatic stopping is switched off. If a ' +
      "consumer or the watcher stops, files will keep being accepted and the " +
      "backlog will grow until somebody notices.</div>";
  }
  var last = s.last;
  var head = last == null
    ? '<p class="dim">No check has completed yet.</p>'
    : '<p>' + (last.ok
        ? '<span class="pill ok"><span class="dot"></span>everything is responding</span>'
        : '<span class="pill bad"><span class="dot"></span>' + esc(last.reason) +
          "</span>") +
      ' <span class="dim">checked ' + esc(ago(last.at)) + "</span></p>";
  return head +
    '<p class="dim">Ingestion is stopped automatically when any of ' +
    esc(s.watching.join(", ")) + " has not reported for " +
    dur(s.stale_after_seconds) + ", or the broker cannot be reached. " +
    "Nothing already in the system is discarded: records in flight finish, " +
    "queued files are held, and backlogs wait in their topics. " +
    (s.auto_resume
      ? "It is started again on its own once everything has been back for " +
        s.checks_before_resume + " consecutive checks"
      : "It stays stopped until somebody starts it") + ", and only ever " +
    "if the supervisor is what stopped it &mdash; a stop by a person, or by " +
    "validation stopping itself, is never undone automatically.</p>" +
    (s.last_error ? errorBox("Last check failed: " + s.last_error) : "") +
    '<div class="actions"><button class="small" id="checkNow">Check now</button>' +
    '<span class="dim">every ' + dur(s.interval_seconds) + "</span></div>";
}

function renderServices(rows) {
  if (!rows.length) {
    return '<p class="dim" style="padding:14px">Nothing has reported yet. ' +
      "Services record a heartbeat as they work; an empty table means none of " +
      "them has run since the last database reset.</p>";
  }
  return "<table><thead><tr><th>Service</th><th>Last completed a loop</th>" +
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
    return '<div class="err" style="margin:14px">Consumer lag cannot be read: ' +
      esc(kafka.error || "the broker is unreachable") +
      ". While this is true, no batch is being announced and no stage " +
      "consumer is advancing.</div>";
  }
  if (!kafka.groups.length) {
    return '<p class="dim" style="padding:14px">No consumer group has ' +
      "committed an offset yet.</p>";
  }
  return "<table><thead><tr><th>Consumer</th><th>Topic</th><th>Waiting</th>" +
    "</tr></thead><tbody>" +
    kafka.groups.map(function (g) {
      return "<tr><td>" + esc(g.group) + "</td>" +
        '<td class="mono">' + esc(g.topic) + "</td>" +
        "<td>" + tag(fmt(g.lag) + " message(s)", g.lag > 0 ? "warn" : "ok") +
        "</td></tr>";
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
    return '<p class="dim">Nothing is firing.</p>';
  }
  return data.alerts.map(function (a) {
    return '<div class="top" style="padding:8px 0;border-bottom:1px solid var(--line)">' +
      tag(a.severity,
          a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "") +
      ' <span class="name">' + esc(a.name) + "</span> " +
      '<span class="dim">' + esc(a.summary || "") + "</span></div>";
  }).join("");
}

function renderIntegrity(data) {
  var head = data.ok
    ? '<p class="dim">All ' + data.total + " checks pass.</p>"
    : errorBox(data.failed + " of " + data.total + " checks found something" +
        (data.errored ? ", and " + data.errored + " could not run" : "") + ".");
  return head + '<div class="scroll"><table style="margin-top:10px"><thead><tr>' +
    "<th>Check</th><th>Result</th></tr></thead><tbody>" +
    data.checks.map(function (c) {
      var verdict = c.error
        ? tag("could not run", "bad") + ' <span class="dim">' + esc(c.error) + "</span>"
        : c.ok ? tag("pass", "ok") : tag(fmt(c.offending) + " row(s)", "bad");
      return "<tr><td>" + esc(c.name) + "</td><td>" + verdict + "</td></tr>";
    }).join("") + "</tbody></table></div>";
}

/* One banner, above every tab, because a stopped pipeline is not a fact that
   belongs on the tab you happen not to be looking at. */
function applyHealth(health) {
  var paused = health.paused || [];
  var pill = $("health"), text = $("healthText");
  var queue = health.queue || {};
  var supervisorLast = (health.supervisor || {}).last;

  if (paused.length) {
    pill.className = "pill bad";
    text.textContent = paused.map(function (s) { return s.stage; }).join(", ") +
      " stopped";
  } else if (!(health.kafka || {}).reachable) {
    pill.className = "pill warn";
    text.textContent = "broker unreachable";
  } else if (supervisorLast && !supervisorLast.ok) {
    pill.className = "pill warn";
    text.textContent = "a service is not responding";
  } else {
    pill.className = "pill ok";
    text.textContent = "running";
  }

  var badge = $("n-operations");
  badge.textContent = paused.length ? String(paused.length) : "0";
  badge.className = "n" + (paused.length ? " bad" : "");

  // The intake badge is global too: files waiting to load matter whatever tab
  // is open, and especially when they are held because something is stopped.
  var waiting = (queue.waiting || 0) + (queue.running || 0);
  var intake = $("n-intake");
  intake.textContent = String(waiting);
  intake.className = "n" + (queue.held ? " bad" : (waiting ? " warn" : ""));

  $("banner").innerHTML = paused.length
    ? '<div class="err" style="margin:14px 0"><strong>' +
      paused.map(function (s) { return esc(s.stage); }).join(", ") +
      " is stopped.</strong> " +
      esc(paused[0].reason || "No reason was recorded.") +
      " Nothing is being lost — queued work is waiting, uploaded files are " +
      "held, and files are sitting in their feeds. Nothing is moving either." +
      (queue.waiting ? " " + fmt(queue.waiting) + " file(s) are waiting to load." : "") +
      "</div>"
    : "";
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
    $("supervisor").innerHTML = renderSupervisor(health.supervisor || {});
    $("services").innerHTML = renderServices(health.services || []);
    $("lag").innerHTML = renderLag(health.kafka || { reachable: false });
    $("alerts").innerHTML = renderAlerts(out[1]);
    applyHealth(health);
  }).catch(function (err) {
    $("stages").innerHTML = errorBox(err.message);
  });
}

/* ================= activity ================= */

function logEntry(e) {
  var detail = e.detail || {};
  var trace = detail.traceback
    ? "<pre>" + esc(detail.traceback) + "</pre>" : "";
  return '<div class="log ' + esc(e.level) + '">' +
    '<div class="when">' + esc(clock(e.at)) + " · " + esc(ago(e.at)) +
      " · " + esc(e.kind) + " · " + esc(e.actor || "") + "</div>" +
    '<div class="msg">' + esc(e.title) + "</div>" +
    (e.context ? '<div class="ctx">' + esc(e.context) + "</div>" : "") +
    trace +
    "</div>";
}

function renderActivity(data) {
  if (!data.entries.length) {
    return '<div class="empty">Nothing recorded yet at this level.</div>';
  }
  return data.entries.map(logEntry).join("") +
    (data.truncated
      ? '<p class="dim" style="margin-top:12px">Showing the most recent ' +
        data.count + "; there may be more.</p>"
      : "");
}

function loadActivity() {
  var level = $("activityLevel").value;
  var kind = $("activityKind").value;
  var url = "/control/activity?limit=150" +
    (level ? "&level=" + level : "") + (kind ? "&kind=" + kind : "");
  return api(url).then(function (data) {
    $("activity").innerHTML = renderActivity(data);
    var bad = (data.levels.error || 0);
    var badge = $("n-activity");
    badge.textContent = bad ? String(bad) : "0";
    badge.className = "n" + (bad ? " bad" : "");
  }).catch(function (err) {
    $("activity").innerHTML = errorBox(err.message);
  });
}

/* ================= tab switching + polling ================= */

var PANELS = {
  pipeline: "pipelinePanel", intake: "intakePanel", entities: "entitiesPanel",
  operations: "operationsPanel", activity: "activityPanel",
};

function showTab() {
  document.querySelectorAll(".tab").forEach(function (t) {
    t.setAttribute("aria-selected", String(t.dataset.tab === currentTab));
  });
  Object.keys(PANELS).forEach(function (name) {
    $(PANELS[name]).classList.toggle("hide", currentTab !== name);
  });
  $("queuePanel").classList.toggle("hide", PANELS[currentTab] !== undefined);
  try {
    history.replaceState(null, "", "#" + currentTab);
  } catch (e) { /* file:// and some embedded views refuse this; harmless */ }
}

function loadCurrent(force) {
  // Pipeline data is stream-driven (see startStream) -- nothing to fetch for
  // it or the entity browser here.
  if (currentTab === "pipeline" || currentTab === "entities") {
    return Promise.resolve();
  }
  if (currentTab === "intake") { return loadIngestQueue(); }
  if (currentTab === "operations") { return loadOperations(); }
  if (currentTab === "activity") {
    if (!force && !$("activityFollow").checked) { return Promise.resolve(); }
    return loadActivity();
  }
  return loadQueue(force).catch(function (err) {
    $("queueBody").innerHTML = errorBox(err.message);
  });
}

/* The health pill and the banner are global, so they are refreshed whatever
   tab is open -- a stopped pipeline should not be discoverable only by
   clicking on Operations. */
function loadHealth() {
  return api("/control/health").then(applyHealth).catch(function () {
    var pill = $("health");
    pill.className = "pill warn";
    $("healthText").textContent = "cannot reach the console API";
  });
}

function loadAll() {
  loadHealth();
  loadCurrent(false);
}

function selectTab(name) {
  currentTab = name;
  showTab();
  loadCurrent(true);
}

document.addEventListener("click", function (event) {
  var tab = event.target.closest(".tab");
  if (tab) { selectTab(tab.dataset.tab); return; }

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
      "Why is " + pause.dataset.pause + " being stopped?\n\n" +
      "Nothing will be lost: queued work waits in its topic, uploaded files " +
      "are held, and files stay in their feed directories.");
    if (!why) { return; }
    pause.disabled = true;
    api("/control/" + pause.dataset.pause + "/pause", {
      method: "POST",
      body: JSON.stringify({ reason: why, reviewed_by: pauseBy }),
    }).then(loadOperations).catch(function (err) {
      $("stages").innerHTML = errorBox(err.message);
    });
    return;
  }

  var resume = event.target.closest("[data-resume]");
  if (resume) {
    var resumeBy = who();
    if (!resumeBy) { return; }
    var note = window.prompt(
      "Starting " + resume.dataset.resume + " again. What did you find?\n\n" +
      "Recorded against the start, so the next person can see what this was.");
    if (note === null) { return; }
    resume.disabled = true;
    api("/control/" + resume.dataset.resume + "/resume", {
      method: "POST",
      body: JSON.stringify({ reviewed_by: resumeBy, note: note || null }),
    }).then(loadOperations).catch(function (err) {
      $("stages").innerHTML = errorBox(err.message);
    });
    return;
  }

  if (event.target.closest("#checkNow")) {
    var checkButton = $("checkNow");
    checkButton.disabled = true;
    api("/control/supervisor/check", { method: "POST", body: "{}" })
      .then(loadOperations)
      .catch(function (err) {
        $("supervisor").innerHTML = errorBox(err.message);
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
      $("integrity").innerHTML = errorBox(err.message);
    }).then(function () { button.disabled = false; });
    return;
  }

  var cancelItem = event.target.closest("[data-queue-cancel]");
  if (cancelItem) {
    var cancelBy = who();
    if (!cancelBy) { return; }
    cancelItem.disabled = true;
    api("/dashboard/queue/" + cancelItem.dataset.queueCancel + "/cancel", {
      method: "POST", body: JSON.stringify({ reviewed_by: cancelBy }),
    }).then(loadIngestQueue).catch(function (err) {
      uploadStatusBox("bad", esc(err.message));
      cancelItem.disabled = false;
    });
    return;
  }

  var requeueItem = event.target.closest("[data-queue-requeue]");
  if (requeueItem) {
    var requeueBy = who();
    if (!requeueBy) { return; }
    requeueItem.disabled = true;
    api("/dashboard/queue/" + requeueItem.dataset.queueRequeue + "/requeue", {
      method: "POST", body: JSON.stringify({ reviewed_by: requeueBy }),
    }).then(loadIngestQueue).catch(function (err) {
      uploadStatusBox("bad", esc(err.message));
      requeueItem.disabled = false;
    });
    return;
  }

  var queueBatch = event.target.closest("[data-queue-batch]");
  if (queueBatch) {
    selectedBatch = queueBatch.dataset.queueBatch;
    selectTab("pipeline");
    startStream();
    $("detailWrap").classList.remove("hide");
    return;
  }

  var row = event.target.closest("tr[data-id]");
  if (row) {
    selectedBatch = row.dataset.id;
    document.querySelectorAll("tbody tr").forEach(function (r) {
      r.classList.remove("sel");
    });
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
        $("detail").innerHTML += errorBox(err.message);
        runBtn.disabled = false;
      });
    // No explicit refresh on success: the stream's next tick already shows
    // "running", then the funnel counts climbing.
    return;
  }

  var sheetPick = event.target.closest("[data-sheet-pick]");
  if (sheetPick) {
    $("uploadSheet").value = sheetPick.dataset.sheetPick;
    $("uploadSheet").scrollIntoView({ block: "center" });
    return;
  }

  var jumpEntity = event.target.closest("[data-jump-entity]");
  if (jumpEntity) {
    loadEntity(jumpEntity.dataset.jumpEntity).catch(function (err) {
      $("entityDetail").innerHTML = errorBox(err.message);
    });
    return;
  }

  var entityRow = event.target.closest("tr[data-entity-id]");
  if (entityRow) {
    loadEntity(entityRow.dataset.entityId).catch(function (err) {
      $("entityDetailWrap").classList.remove("hide");
      $("entityDetail").innerHTML = errorBox(err.message);
    });
    return;
  }

  var explainRow = event.target.closest("tr[data-explain-field]");
  if (explainRow) {
    loadFieldExplain(explainRow.dataset.entityId, explainRow.dataset.explainField);
    return;
  }

  var pick = event.target.closest(".chip.pick");
  if (pick && pick.dataset.for) {
    $("f-" + pick.dataset.for).value = pick.dataset.field;
    return;
  }

  var actButton = event.target.closest("button[data-act]");
  if (!actButton) { return; }

  var id = actButton.dataset.id, act = actButton.dataset.act;
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
  box.className = "result busy";
  box.textContent = "Working…";

  promise.then(function (data) {
    var html = esc(data.summary);
    if (data.follow_up && data.follow_up.length) {
      html += "<ul>" + data.follow_up.map(function (f) {
        return "<li>" + esc(f) + "</li>";
      }).join("") + "</ul>";
    }
    box.className = "result";
    box.innerHTML = html;
    $("c-" + id).classList.add("done");
    // No explicit refresh: the stream's next tick carries the updated
    // queue-badge counts within ~2s.
  }).catch(function (err) {
    box.className = "result bad";
    box.textContent = err.message;
    siblings.forEach(function (b) { b.disabled = false; });
  });
});

/* Number keys switch tabs; r refreshes. Ignored while typing, so a name with
   a digit in it does not jump the page out from under the person typing it. */
var TAB_ORDER = ["pipeline", "intake", "entities", "mappings", "candidates",
                 "quarantine", "enrichment", "operations", "activity"];

document.addEventListener("keydown", function (event) {
  if (event.metaKey || event.ctrlKey || event.altKey) { return; }
  var el = document.activeElement;
  if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" ||
             el.tagName === "SELECT")) { return; }
  var index = TAB_ORDER.indexOf(currentTab);
  if (event.key >= "1" && event.key <= "9") {
    var pick = TAB_ORDER[Number(event.key) - 1];
    if (pick) { selectTab(pick); }
  } else if (event.key === "r") {
    loadCurrent(true);
  } else if (event.key === "[" && index > 0) {
    selectTab(TAB_ORDER[index - 1]);
  } else if (event.key === "]" && index < TAB_ORDER.length - 1) {
    selectTab(TAB_ORDER[index + 1]);
  }
});

$("uploadFile").onchange = function () { pickFiles(this.files); };
$("uploadGo").onclick = submitUpload;
$("queueFilter").onchange = loadIngestQueue;
$("activityLevel").onchange = loadActivity;
$("activityKind").onchange = loadActivity;

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
  if (e.dataTransfer.files && e.dataTransfer.files.length) {
    pickFiles(e.dataTransfer.files);
  }
});

$("entitySearchGo").onclick = searchEntities;
$("entitySearchQuery").addEventListener("keydown", function (e) {
  if (e.key === "Enter") { searchEntities(); }
});

$("refresh").onclick = function () { loadHealth(); loadCurrent(true); };
$("key").onchange = function () {
  sessionStorage.setItem(KEY, $("key").value.trim());
  startStream();
  loadAll();
};
$("who").value = localStorage.getItem(WHO) || "";

var fromHash = (location.hash || "").replace("#", "");
if (TAB_ORDER.indexOf(fromHash) >= 0) { currentTab = fromHash; }

showTab();
startStream();
loadAll();
setInterval(loadAll, 5000);
"""

ADMIN_PAGE = (
    _HEAD + BASE_CSS + _BODY
    + "<script>" + BASE_JS + QUEUE_JS + _SCRIPT + "</script>\n"
)
