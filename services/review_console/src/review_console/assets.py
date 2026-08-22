r"""The stylesheet and the helper functions the three consoles share.

There were three copies of these. Not similar -- copied, and then drifted:
`ago()` returned "unknown" in two pages and an em-dash in the third, `.empty`
was green in two and grey in the third, and the ninety-line style block existed
three times, so every fix to one of them was a fix to a third of the console.

Both constants are raw strings, deliberately. These files are Python strings
containing JavaScript, and a `\n` written for the browser gets consumed by
Python first: the browser then receives a real line break in the middle of a
string literal, the script stops parsing, and the panel renders blank with no
error anybody sees. That has happened twice. In a raw string an escape means
what it says, and `test_pages.py` guards the seam either way.
"""

# The palette is defined once, dark first, with the light variant as an
# override. Everything else is expressed in those variables, so a theme change
# is six lines rather than a search.
BASE_CSS = r"""<style>
  :root {
    --bg:#0d1218; --card:#141b23; --raised:#1a232d; --line:#26313b;
    --ink:#e6ecf1; --dim:#8fa0ae; --faint:#5d6b78;
    --accent:#5aa9d6; --ok:#6bb98c; --warn:#d9a154; --bad:#e06c60;
    --chip:#1b242e; --shadow:0 1px 2px rgba(0,0,0,.4);
    --radius:8px;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f8f9; --card:#ffffff; --raised:#f0f4f7; --line:#dde3e8;
            --ink:#10171d; --dim:#5d6b78; --faint:#8fa0ae; --chip:#eef2f5;
            --shadow:0 1px 2px rgba(16,23,29,.06); }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         padding:0 20px 80px;
         font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; }
  h1 { font-size:22px; margin:0; letter-spacing:-.02em; }
  h2 { font-size:15px; margin:24px 0 8px; letter-spacing:-.01em; }
  h3 { font-size:13px; margin:16px 0 6px; color:var(--dim);
       text-transform:uppercase; letter-spacing:.06em; }
  p { margin:0 0 12px; }
  a { color:var(--accent); }
  .sub { color:var(--dim); margin:2px 0 0; font-size:13px; }
  .dim { color:var(--dim); font-size:13px; }
  .faint { color:var(--faint); }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }
  .num { font-variant-numeric:tabular-nums; }
  .hide { display:none !important; }
  .right { text-align:right; }
  .nowrap { white-space:nowrap; }

  /* The header stays put. On a page whose whole job is to be watched while
     something else is happening, scrolling away from the identity field, the
     health of the system and the refresh control is the wrong default. */
  .head { position:sticky; top:0; z-index:20; background:var(--bg);
          padding:18px 0 10px; margin-bottom:2px;
          border-bottom:1px solid var(--line); }
  .head-row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  .head-row .grow { flex:1; min-width:200px; }

  input, select, textarea, button {
    background:var(--card); border:1px solid var(--line); color:var(--ink);
    border-radius:6px; padding:8px 11px; font:inherit;
  }
  textarea { width:100%; resize:vertical; min-height:38px; }
  button { cursor:pointer; }
  button:hover:not(:disabled) { border-color:var(--accent); }
  button:disabled { opacity:.5; cursor:default; }
  button.primary { border-color:var(--ok); }
  button.danger { border-color:var(--bad); color:var(--bad); }
  button.small { padding:4px 9px; font-size:12.5px; }
  label.check { color:var(--dim); font-size:13px; display:inline-flex;
                gap:6px; align-items:center; }
  :focus-visible { outline:2px solid var(--accent); outline-offset:2px; }

  .bar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
  .tabs { display:flex; gap:4px; margin:12px 0 0; flex-wrap:wrap; }
  .tab { border:1px solid transparent; background:transparent; border-radius:6px;
         padding:6px 12px; font-size:13.5px; cursor:pointer; color:var(--dim);
         display:inline-flex; gap:7px; align-items:center; }
  .tab:hover { background:var(--chip); color:var(--ink); border-color:transparent; }
  .tab[aria-selected="true"] { background:var(--card); border-color:var(--line);
                               color:var(--ink); box-shadow:var(--shadow); }
  .tab .n { font-variant-numeric:tabular-nums; font-size:12px;
            background:var(--chip); border-radius:999px; padding:1px 7px;
            min-width:20px; text-align:center; }
  .tab[aria-selected="true"] .n { background:var(--raised); }
  .tab .n.warn { color:var(--warn); } .tab .n.bad { color:var(--bad); }
  .kbd { font-size:10.5px; color:var(--faint); border:1px solid var(--line);
         border-radius:3px; padding:0 4px; font-family:ui-monospace,monospace; }

  /* One health word, always in the same place. "Everything running" and
     "we cannot tell" must never render the same way, so there is a third
     state and it is not silent. */
  .pill { display:inline-flex; gap:7px; align-items:center; font-size:12.5px;
          border:1px solid var(--line); border-radius:999px; padding:4px 12px;
          background:var(--card); white-space:nowrap; }
  .pill .dot { width:8px; height:8px; border-radius:50%; background:var(--dim); }
  .pill.ok .dot { background:var(--ok); }
  .pill.warn .dot { background:var(--warn); }
  .pill.bad .dot { background:var(--bad); }
  .pill.bad { border-color:var(--bad); color:var(--bad); }
  .pill.warn { border-color:var(--warn); color:var(--warn); }

  section { padding-top:14px; }

  .cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr));
           gap:10px; }
  .stat { border:1px solid var(--line); background:var(--card);
          border-radius:var(--radius); padding:12px 14px; box-shadow:var(--shadow); }
  .stat .v { font-size:22px; font-weight:600; font-variant-numeric:tabular-nums; }
  .stat .k { color:var(--dim); font-size:11.5px; text-transform:uppercase;
             letter-spacing:.05em; margin-top:2px; }
  .stat.warn .v { color:var(--warn); } .stat.bad .v { color:var(--bad); }
  .stat.ok .v { color:var(--ok); }

  table { width:100%; border-collapse:collapse; font-size:13.5px; }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
           vertical-align:top; }
  th { color:var(--dim); font-weight:500; font-size:11.5px; text-transform:uppercase;
       letter-spacing:.05em; position:sticky; top:0; background:var(--card); }
  tbody tr.pickable { cursor:pointer; }
  tbody tr.pickable:hover { background:var(--chip); }
  tbody tr.sel { background:var(--chip); box-shadow:inset 2px 0 0 var(--accent); }
  .scroll { overflow-x:auto; }

  .card { border:1px solid var(--line); background:var(--card);
          border-radius:var(--radius); padding:14px 16px; margin-bottom:12px;
          box-shadow:var(--shadow); }
  .card.flush { padding:0; overflow:hidden; }
  .card.done { opacity:.5; }
  .top { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap;
         margin-bottom:8px; }
  .name { font-weight:600; }

  .tag { font-size:10.5px; text-transform:uppercase; letter-spacing:.06em;
         color:var(--dim); border:1px solid var(--line); border-radius:4px;
         padding:1px 6px; white-space:nowrap; display:inline-block; }
  .tag.ok { color:var(--ok); border-color:var(--ok); }
  .tag.run { color:var(--accent); border-color:var(--accent); }
  .tag.warn { color:var(--warn); border-color:var(--warn); }
  .tag.bad { color:var(--bad); border-color:var(--bad); }

  .chips { display:flex; gap:6px; flex-wrap:wrap; margin:6px 0; }
  .chip { background:var(--chip); border-radius:4px; padding:3px 8px;
          font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
          max-width:100%; overflow-wrap:anywhere; }
  .chip.pick { cursor:pointer; border:1px solid var(--line); }
  .chip.pick:hover { border-color:var(--accent); }

  .cmp { display:grid; grid-template-columns:150px 1fr 1fr; gap:1px;
         background:var(--line); border:1px solid var(--line);
         border-radius:6px; overflow:hidden; margin:8px 0; font-size:13px; }
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

  .result { margin-top:10px; border-left:2px solid var(--ok);
            padding:6px 0 6px 10px; font-size:13px; }
  .result.bad { border-left-color:var(--bad); }
  .result.busy { border-left-color:var(--accent); }
  .result ul { margin:6px 0 0; padding-left:18px; color:var(--dim); }

  .step { margin:10px 0; }
  .step .row { display:flex; justify-content:space-between; align-items:baseline;
               font-size:13px; margin-bottom:4px; }
  .step .name { font-weight:600; }
  .step .n { font-variant-numeric:tabular-nums; color:var(--dim); }
  .track { height:7px; border-radius:5px; background:var(--chip); overflow:hidden; }
  .fill { height:100%; background:var(--accent); border-radius:5px; }
  .step.stuck .fill { background:var(--warn); }
  .breakdown { display:flex; gap:10px; flex-wrap:wrap; margin-top:4px; }
  .breakdown span { font-size:12px; color:var(--dim); }
  .breakdown b { color:var(--ink); font-variant-numeric:tabular-nums; }

  .empty { border:1px dashed var(--line); background:var(--card);
           border-radius:var(--radius); padding:26px; text-align:center;
           color:var(--dim); }
  .empty.good { color:var(--ok); }
  .err { border:1px solid var(--bad); color:var(--bad); background:var(--card);
         border-radius:var(--radius); padding:12px 14px; }
  .note { border:1px solid var(--warn); color:var(--warn); background:var(--card);
          border-radius:var(--radius); padding:12px 14px; }

  .dropzone { border:2px dashed var(--line); border-radius:var(--radius);
              padding:26px; text-align:center; color:var(--dim); font-size:13.5px; }
  .dropzone.drag { border-color:var(--accent); color:var(--ink);
                   background:var(--chip); }
  .pick { color:var(--accent); cursor:pointer; text-decoration:underline; }
  .field-row { display:flex; gap:8px; flex-wrap:wrap; align-items:center;
               margin-top:10px; }
  .field-row input, .field-row select { flex:0 0 auto; }
  .field-row input[type=text] { flex:1; min-width:160px; }
  details.adv summary { color:var(--dim); font-size:13px; cursor:pointer;
                        margin-top:8px; }

  /* The activity feed. A log is read by scanning down the left edge for the
     one line that is a different colour, so severity is a bar, not a word. */
  .log { border-left:3px solid var(--line); padding:7px 0 7px 11px;
         margin-bottom:2px; font-size:13px; }
  .log.error { border-left-color:var(--bad); }
  .log.warn { border-left-color:var(--warn); }
  .log.info { border-left-color:var(--line); }
  .log .when { color:var(--faint); font-size:11.5px;
               font-variant-numeric:tabular-nums; }
  .log .who { color:var(--dim); font-size:11.5px; }
  .log .msg { overflow-wrap:anywhere; }
  .log .ctx { color:var(--dim); font-size:12px; overflow-wrap:anywhere;
              font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
  .log pre { margin:6px 0 0; padding:8px; background:var(--raised);
             border-radius:5px; font-size:11.5px; overflow-x:auto;
             white-space:pre-wrap; overflow-wrap:anywhere; color:var(--dim); }
</style>"""


# Everything below is defined identically by all three pages, or was until one
# of the copies drifted. Concatenated into each page's script block, before
# anything page-specific.
BASE_JS = r"""
var KEY = "pcdf_api_key", WHO = "pcdf_reviewer";

function $(id) { return document.getElementById(id); }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function fmt(n) { return Number(n || 0).toLocaleString(); }

/* Coarse on purpose. Nobody reading an operations console needs "3h 41m 12s";
   they need to know whether it was minutes ago or yesterday. */
function dur(secs) {
  if (secs == null) { return "unknown"; }
  var d = Math.floor(secs / 86400), h = Math.floor(secs / 3600),
      m = Math.floor(secs / 60);
  if (d >= 1) { return d + "d"; }
  if (h >= 1) { return h + "h"; }
  if (m >= 1) { return m + "m"; }
  return "just now";
}

function ago(iso) {
  if (!iso) { return "unknown"; }
  var secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  var text = dur(secs);
  return text === "just now" ? text : text + " ago";
}

function clock(iso) {
  if (!iso) { return ""; }
  var d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit",
                                    second: "2-digit" });
}

function bytes(n) {
  n = Number(n || 0);
  if (n >= 1073741824) { return (n / 1073741824).toFixed(1) + " GB"; }
  if (n >= 1048576) { return (n / 1048576).toFixed(1) + " MB"; }
  if (n >= 1024) { return Math.round(n / 1024) + " KB"; }
  return n + " B";
}

function headers() {
  var k = sessionStorage.getItem(KEY);
  var h = { "Content-Type": "application/json" };
  if (k) { h["X-API-Key"] = k; }
  return h;
}

function unauthorized() {
  var field = $("key");
  if (field) { field.classList.remove("hide"); }
  return new Error("This console needs a key. Enter it above and refresh.");
}

function api(path, options) {
  return fetch(path, Object.assign({ headers: headers() }, options || {}))
    .then(function (res) {
      if (res.status === 401 || res.status === 403) { throw unauthorized(); }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { throw new Error(data.detail || ("HTTP " + res.status)); }
        return data;
      });
    });
}

/* Multipart upload: no Content-Type header of our own -- the browser sets one
   with the correct boundary for a FormData body, and overriding it breaks the
   parse on the server side. */
function apiUpload(path, formData) {
  var h = {};
  var k = sessionStorage.getItem(KEY);
  if (k) { h["X-API-Key"] = k; }
  return fetch(path, { method: "POST", headers: h, body: formData })
    .then(function (res) {
      if (res.status === 401 || res.status === 403) { throw unauthorized(); }
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { throw new Error(data.detail || ("HTTP " + res.status)); }
        return data;
      });
    });
}

function who() {
  var field = $("who");
  var name = field ? field.value.trim() : "";
  if (!name) {
    if (field) { field.focus(); }
    return null;
  }
  localStorage.setItem(WHO, name);
  return name;
}

function tag(text, cls) {
  return '<span class="tag' + (cls ? " " + cls : "") + '">' + esc(text) + "</span>";
}

function statCard(label, value, cls) {
  return '<div class="stat' + (cls ? " " + cls : "") + '">' +
    '<div class="v">' + fmt(value) + '</div>' +
    '<div class="k">' + esc(label) + "</div></div>";
}

function statusTag(s) {
  var cls = s === "completed" ? "ok"
          : s === "failed" || s === "cancelled" ? "bad"
          : s === "held" || s === "queued" ? "warn" : "run";
  return tag(s, cls);
}

function errorBox(message) {
  return '<div class="err">' + esc(message) + "</div>";
}
"""


# The four review queues render identically on `/review/page` and on the admin
# console's four queue tabs, and they read the same endpoints -- so they are one
# copy. They were two, and the two had already drifted apart in what they call
# the sides of a duplicate comparison, which is not a difference anybody chose.
#
# `datalists()` belongs with them: the canonical-field vocabulary is only ever
# needed by the mapping queue.
QUEUE_JS = r"""
var fieldCache = {};

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

/* ---------------- loading ---------------- */

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
"""
