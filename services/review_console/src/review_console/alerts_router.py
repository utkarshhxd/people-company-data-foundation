"""Alerts, as data and as a page.

Both are served from the same projection, so the page can never show something
the API does not also serve.

The page is one self-contained document rather than a build step. It exists so
somebody can see what is firing while the system runs locally; it is not a
frontend application, and giving it a toolchain would be more infrastructure
than the job needs.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse

from review_console.alerts import AlertsUnavailable, active_alerts, summarize
from review_console.auth import require_api_key

router = APIRouter(tags=["alerts"])


@router.get("/alerts", dependencies=[Depends(require_api_key)])
async def get_alerts() -> dict:
    """What is firing now, worst first."""
    try:
        alerts = await active_alerts()
    except AlertsUnavailable as exc:
        # 503 rather than an empty list: "nothing is wrong" and "we cannot tell
        # whether anything is wrong" are opposite states, and a caller that
        # renders them identically is worse than one showing an error.
        raise HTTPException(
            status_code=503, detail=f"Alertmanager is unreachable: {exc}"
        ) from exc
    return {"summary": summarize(alerts), "alerts": alerts}


# Deliberately not behind the key: the page is an empty shell that fetches
# /alerts, and that request carries the key. Gating the shell too would mean a
# browser could never reach the point of being asked for one.
@router.get("/alerts/page", response_class=HTMLResponse, include_in_schema=False)
async def alerts_page() -> str:
    return _PAGE


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Alerts</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg:#0d1218; --card:#141b23; --line:#26313b; --ink:#e6ecf1; --dim:#8fa0ae;
    --critical:#e06c60; --warning:#d9a154; --info:#5aa9d6; --ok:#6bb98c;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f6f8f9; --card:#ffffff; --line:#dde3e8; --ink:#10171d; --dim:#5d6b78; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink); padding:28px 20px 64px;
         font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif; }
  .wrap { max-width:940px; margin:0 auto; }
  h1 { font-size:24px; margin:0 0 4px; letter-spacing:-.02em; }
  .sub { color:var(--dim); margin:0 0 22px; font-size:13px; }
  .counts { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:22px; }
  .pill { border:1px solid var(--line); background:var(--card); border-radius:999px;
          padding:5px 13px; font-size:13px; }
  .pill b { font-variant-numeric:tabular-nums; }
  .alert { border:1px solid var(--line); border-left:3px solid var(--dim);
           background:var(--card); border-radius:8px; padding:13px 15px; margin-bottom:10px; }
  .alert.critical { border-left-color:var(--critical); }
  .alert.warning  { border-left-color:var(--warning); }
  .alert.info     { border-left-color:var(--info); }
  .top { display:flex; gap:10px; align-items:baseline; flex-wrap:wrap; }
  .name { font-weight:600; }
  .tag { font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:var(--dim);
         border:1px solid var(--line); border-radius:4px; padding:1px 6px; }
  .desc { color:var(--dim); font-size:13.5px; margin-top:6px; }
  .meta { color:var(--dim); font-size:12px; margin-top:8px; font-variant-numeric:tabular-nums; }
  .quiet { color:var(--ok); border:1px solid var(--line); background:var(--card);
           border-radius:8px; padding:22px; text-align:center; }
  .err { color:var(--critical); border:1px solid var(--critical); border-radius:8px;
         padding:16px; background:var(--card); }
  .keyrow { display:flex; gap:8px; margin-bottom:18px; }
  input { flex:1; background:var(--card); border:1px solid var(--line); color:var(--ink);
          border-radius:6px; padding:8px 11px; font:inherit; }
  button { background:var(--card); border:1px solid var(--line); color:var(--ink);
           border-radius:6px; padding:8px 15px; font:inherit; cursor:pointer; }
</style>
<div class="wrap">
  <h1>Alerts</h1>
  <p class="sub">
    From Prometheus via Alertmanager &middot;
    <span id="when">loading&hellip;</span> &middot; refreshes every 30s
  </p>
  <div id="keyrow" class="keyrow" hidden>
    <input id="key" type="password" placeholder="X-API-Key" autocomplete="off">
    <button id="save">Use key</button>
  </div>
  <div id="counts" class="counts"></div>
  <div id="body"></div>
</div>
<script>
var KEY = "pcdf_api_key";
function $(id) { return document.getElementById(id); }

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function ago(iso) {
  if (!iso) { return "unknown"; }
  var secs = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  var d = Math.floor(secs / 86400);
  var h = Math.floor(secs / 3600);
  var m = Math.floor(secs / 60);
  if (d >= 1) { return d + "d " + (h % 24) + "h"; }
  if (h >= 1) { return h + "h " + (m % 60) + "m"; }
  return Math.max(1, m) + "m";
}

function fail(message) {
  $("counts").innerHTML = "";
  $("body").innerHTML = '<div class="err">' + message + "</div>";
  $("when").textContent = "error";
}

function render(data) {
  var summary = data.summary, alerts = data.alerts;
  $("when").textContent = "updated " + new Date().toLocaleTimeString();

  var order = ["critical", "warning", "info"];
  var pills = '<span class="pill"><b>' + summary.total + "</b> firing</span>";
  order.forEach(function (s) {
    if (summary.by_severity[s]) {
      pills += '<span class="pill">' + s + " <b>" + summary.by_severity[s] + "</b></span>";
    }
  });
  $("counts").innerHTML = pills;

  if (!alerts.length) {
    $("body").innerHTML = '<div class="quiet">Nothing is firing.</div>';
    return;
  }

  $("body").innerHTML = alerts.map(function (a) {
    return '<div class="alert ' + esc(a.severity) + '">' +
      '<div class="top">' +
        '<span class="name">' + esc(a.name) + "</span>" +
        '<span class="tag">' + esc(a.severity) + "</span>" +
        (a.inhibited ? '<span class="tag">inhibited</span>' : "") +
        (a.silenced ? '<span class="tag">silenced</span>' : "") +
      "</div>" +
      "<div>" + esc(a.summary) + "</div>" +
      '<div class="desc">' + esc(a.description) + "</div>" +
      '<div class="meta">firing for ' + ago(a.started_at) + "</div>" +
    "</div>";
  }).join("");
}

function load() {
  var key = sessionStorage.getItem(KEY);
  fetch("/alerts", { headers: key ? { "X-API-Key": key } : {} })
    .then(function (res) {
      if (res.status === 401) {
        $("keyrow").hidden = false;
        fail("This API needs a key.");
        return null;
      }
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (d) {
          fail(esc(d.detail || "HTTP " + res.status));
          return null;
        });
      }
      return res.json();
    })
    .then(function (data) { if (data) { render(data); } })
    .catch(function () { fail("Cannot reach the API. Is it running?"); });
}

$("save").onclick = function () {
  sessionStorage.setItem(KEY, $("key").value.trim());
  $("keyrow").hidden = true;
  load();
};

load();
setInterval(load, 30000);
</script>
"""
