r"""The review console as one HTML page, served from the API itself.

No build step, no framework, no bundle -- a page that talks to the same JSON
endpoints anything else would. Four tabs, one per queue, and every decision
posts to the same route a `curl` would.

The queue renderers, the stylesheet and the shared helpers live in
`assets.py`, because the admin console shows the same four queues and the two
copies had already drifted. See the note there about why these strings are raw.
"""

from review_console.assets import BASE_CSS, BASE_JS, QUEUE_JS

_HEAD = r"""<!doctype html>
<meta charset="utf-8">
<title>Review queues</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
"""

_BODY = r"""
<div class="wrap">
  <div class="head">
    <div class="head-row">
      <div>
        <h1>Review queues</h1>
        <p class="sub">Every decision here is recorded against a name and is
          permanent.</p>
      </div>
      <div class="grow"></div>
      <span id="when" class="dim">loading&hellip;</span>
    </div>
    <div class="head-row" style="margin-top:10px">
      <input id="who" type="text" class="grow"
             placeholder="Your name &mdash; recorded on every decision"
             autocomplete="name">
      <input id="key" type="password" placeholder="X-API-Key" autocomplete="off"
             class="hide" style="width:220px">
      <button id="refresh">Refresh</button>
    </div>
    <div class="tabs" role="tablist">
      <button class="tab" role="tab" data-q="mappings">
        Schema mappings <span class="n" id="n-mappings">&ndash;</span></button>
      <button class="tab" role="tab" data-q="candidates">
        Possible duplicates <span class="n" id="n-candidates">&ndash;</span></button>
      <button class="tab" role="tab" data-q="quarantine">
        Quarantined records <span class="n" id="n-quarantine">&ndash;</span></button>
      <button class="tab" role="tab" data-q="enrichment">
        AI enrichment proposals <span class="n" id="n-enrichment">&ndash;</span></button>
    </div>
  </div>

  <section><div id="body"></div></section>
</div>
"""

_SCRIPT = r"""
var current = "mappings";

/* ---------------- summary ---------------- */

function loadSummary() {
  return api("/review/summary").then(function (data) {
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
    $("when").textContent = "updated " + new Date().toLocaleTimeString();
  });
}

/* ---------------- loading ---------------- */

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
      $("body").innerHTML = errorBox(err.message);
    });
  loadSummary().catch(function () { $("when").textContent = "summary unavailable"; });
}

/* ---------------- decisions ---------------- */

function settle(id, promise, buttons) {
  var box = $("r-" + id);
  buttons.forEach(function (b) { b.disabled = true; });
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
    loadSummary();
  }).catch(function (err) {
    box.className = "result bad";
    box.textContent = err.message;
    buttons.forEach(function (b) { b.disabled = false; });
  });
}

function post(path, body) {
  return api(path, { method: "POST", body: JSON.stringify(body) });
}

document.addEventListener("click", function (event) {
  var pick = event.target.closest(".chip.pick");
  if (pick && pick.dataset.for) {
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
  } else if (act === "enrichaccept" || act === "enrichreject") {
    settle(id, post("/review/enrichment/" + id,
      { accept: act === "enrichaccept", reviewed_by: name }), siblings);
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
"""

REVIEW_PAGE = (
    _HEAD + BASE_CSS + _BODY
    + "<script>" + BASE_JS + QUEUE_JS + _SCRIPT + "</script>\n"
)
