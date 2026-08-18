"""The review console: one page, no build step.

It exists so the three review queues can be worked by someone who is not at a
terminal, which until now they could not be. It is deliberately one
self-contained document served by the API rather than a frontend application:
giving it a toolchain would be more infrastructure than the job needs, and the
job is showing a person the thing they have to decide about.

It renders only what the endpoints return and posts only what they accept, so it
cannot show a state the API does not also serve, and cannot make a decision the
CLI could not make.
"""

REVIEW_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Review queues</title>
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

  .card { border:1px solid var(--line); background:var(--card); border-radius:8px;
          padding:14px 16px; margin-bottom:12px; }
  .card.done { opacity:.55; }
  .top { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;
         margin-bottom:8px; }
  .name { font-weight:600; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }
  .tag { font-size:11px; text-transform:uppercase; letter-spacing:.06em;
         color:var(--dim); border:1px solid var(--line); border-radius:4px;
         padding:1px 6px; }
  .tag.ok { color:var(--ok); } .tag.warn { color:var(--warn); }
  .tag.bad { color:var(--bad); }
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

  .empty { border:1px solid var(--line); background:var(--card); border-radius:8px;
           padding:26px; text-align:center; color:var(--ok); }
  .err { border:1px solid var(--bad); color:var(--bad); background:var(--card);
         border-radius:8px; padding:14px; }
  .hide { display:none; }
</style>

<div class="wrap">
  <h1>Review queues</h1>
  <p class="sub">
    Every decision here is recorded against a name and is permanent.
    &middot; <span id="when">loading&hellip;</span>
    &middot; <a href="/alerts/page">alerts</a>
  </p>

  <div class="bar">
    <input id="who" type="text" placeholder="Your name — recorded on every decision"
           autocomplete="name" style="flex:1;min-width:240px">
    <input id="key" type="password" placeholder="X-API-Key" autocomplete="off"
           class="hide" style="width:220px">
    <button id="refresh">Refresh</button>
  </div>

  <div class="tabs" role="tablist">
    <button class="tab" role="tab" data-q="mappings">
      Schema mappings <span class="n" id="n-mappings">–</span></button>
    <button class="tab" role="tab" data-q="candidates">
      Possible duplicates <span class="n" id="n-candidates">–</span></button>
    <button class="tab" role="tab" data-q="quarantine">
      Quarantined records <span class="n" id="n-quarantine">–</span></button>
  </div>

  <div id="body"></div>
</div>

<script>
var KEY = "pcdf_api_key", WHO = "pcdf_reviewer";
var current = "mappings";
var fieldCache = {};

function $(id) { return document.getElementById(id); }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function ago(iso) {
  if (!iso) { return "unknown"; }
  return dur(Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000));
}

function dur(secs) {
  var d = Math.floor(secs / 86400), h = Math.floor(secs / 3600),
      m = Math.floor(secs / 60);
  if (d >= 1) { return d + "d"; }
  if (h >= 1) { return h + "h"; }
  if (m >= 1) { return m + "m"; }
  return "just now";
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
        throw new Error("This API needs a key. Enter it above and refresh.");
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

/* ---------------- summary ---------------- */

function loadSummary() {
  return api("/review/summary").then(function (data) {
    ["mappings", "candidates", "quarantine"].forEach(function (q) {
      var info = data.queues[q];
      var label = String(info.open);
      if (info.open > 0 && info.oldest_seconds > 0) {
        label += " · " + dur(info.oldest_seconds);
      }
      $("n-" + q).textContent = label;
    });
    $("when").textContent = "updated " + new Date().toLocaleTimeString();
  });
}

/* ---------------- rendering ---------------- */

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

/* ---------------- loading ---------------- */

var QUEUES = {
  mappings: { url: "/review/mappings", render: renderMappings },
  candidates: { url: "/review/candidates", render: renderCandidates },
  quarantine: { url: "/review/quarantine", render: renderQuarantine }
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

function load() {
  document.querySelectorAll(".tab").forEach(function (t) {
    t.setAttribute("aria-selected", String(t.dataset.q === current));
  });
  $("body").innerHTML = '<p class="dim">Loading&hellip;</p>';

  var queue = QUEUES[current];
  Promise.all([api(queue.url), current === "mappings" ? datalists() : ""])
    .then(function (out) {
      $("body").innerHTML = (out[1] || "") + queue.render(out[0].results);
    })
    .catch(function (err) {
      $("body").innerHTML = '<div class="err">' + esc(err.message) + "</div>";
    });
  loadSummary().catch(function () { $("when").textContent = "summary unavailable"; });
}

/* ---------------- decisions ---------------- */

function settle(id, promise, buttons) {
  var box = $("r-" + id);
  buttons.forEach(function (b) { b.disabled = true; });
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
    loadSummary();
  }).catch(function (err) {
    box.classList.add("bad");
    box.textContent = err.message;
    buttons.forEach(function (b) { b.disabled = false; });
  });
}

function post(path, body) {
  return api(path, { method: "POST", body: JSON.stringify(body) });
}

document.addEventListener("click", function (event) {
  var pick = event.target.closest(".chip.pick");
  if (pick) {
    $("f-" + pick.dataset.for).value = pick.dataset.field;
    return;
  }

  var tab = event.target.closest(".tab");
  if (tab) { current = tab.dataset.q; load(); return; }

  var button = event.target.closest("button[data-act]");
  if (!button) { return; }

  var id = button.dataset.id, act = button.dataset.act;
  var name = who();
  if (!name) { return; }

  var siblings = Array.prototype.slice.call(
    $("c-" + id).querySelectorAll("button[data-act]"));
  var noteEl = $("note-" + id);
  var note = noteEl ? noteEl.value.trim() || null : null;

  if (act === "map" || act === "unmap") {
    var field = act === "map" ? $("f-" + id).value.trim() : null;
    if (act === "map" && !field) { $("f-" + id).focus(); return; }
    settle(id, post("/review/mappings/" + id,
      { canonical_field: field, reviewed_by: name }), siblings);
  } else if (act === "merge" || act === "separate") {
    settle(id, post("/review/candidates/" + id,
      { accept: act === "merge", reviewed_by: name, note: note }), siblings);
  } else if (act === "release" || act === "rejectq") {
    settle(id, post("/review/quarantine/" + id,
      { action: act === "release" ? "release" : "reject",
        reviewed_by: name, note: note }), siblings);
  }
});

$("refresh").onclick = load;
$("key").onchange = function () {
  sessionStorage.setItem(KEY, $("key").value.trim());
  load();
};
$("who").value = localStorage.getItem(WHO) || "";

load();
setInterval(loadSummary, 30000);
</script>
"""
