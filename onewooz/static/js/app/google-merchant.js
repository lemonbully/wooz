/* ==========================================================================
   OneWooz  Google Merchant page
   --------------------------------------------------------------------------
   Starts a background sync, polls live progress + the approval tally while it
   runs, refreshes statuses, toggles auto-sync, and confirms disconnect inline
   (no browser pop-ups). CSRF comes from the token Django rendered on the page.
   ========================================================================== */
(function () {
  "use strict";

  var root = document.querySelector("[data-gm]");
  if (!root) return;

  function csrf() {
    var i = root.querySelector('input[name="csrfmiddlewaretoken"]') ||
      document.querySelector('input[name="csrfmiddlewaretoken"]');
    return (i && i.value) || "";
  }
  function post(url, data) {
    var fd = new FormData();
    Object.keys(data || {}).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, {
      method: "POST", body: fd, credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { return { ok: r.ok, d: d }; });
    });
  }
  function get(url) {
    return fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); });
  }
  function q(sel) { return root.querySelector(sel); }

  var statusUrl = root.getAttribute("data-status-url");
  var progressEl = q("[data-gm-progress]");
  var fillEl = q("[data-gm-fill]");
  var progressTxt = q("[data-gm-progress-txt]");
  var lastEl = q("[data-gm-last]");
  var errEl = q("[data-gm-error]");
  var syncBtns = Array.prototype.slice.call(root.querySelectorAll("[data-gm-sync]"));
  var pollTimer = null;

  function setError(msg) {
    if (!errEl) return;
    errEl.textContent = msg || "";
    errEl.hidden = !msg;
  }

  function render(s) {
    if (!s) return;
    var p = s.progress || {};
    var running = s.sync_state === "running";
    if (progressEl) progressEl.hidden = !running;
    if (running) {
      var pct = p.total ? Math.round((p.done / p.total) * 100) : 4;
      if (fillEl) fillEl.style.width = Math.max(pct, 4) + "%";
      if (progressTxt) {
        progressTxt.textContent = p.total
          ? "Syncing " + p.done + " of " + p.total + " products…"
          : "Preparing your products…";
      }
    }
    syncBtns.forEach(function (b) { b.disabled = running; });
    if (lastEl && !running) {
      var bits = [];
      if (s.last_synced) bits.push("Last synced " + s.last_synced);
      if (p.total) bits.push(p.done + " checked");
      if (p.invalid) bits.push(p.invalid + " missing details");
      if (p.failed) bits.push(p.failed + " failed");
      lastEl.textContent = bits.length ? bits.join(" · ") : "Not synced yet";
    }
    setError(s.last_error || "");
    var sum = s.summary || {};
    Array.prototype.forEach.call(root.querySelectorAll("[data-gm-n]"), function (el) {
      var k = el.getAttribute("data-gm-n");
      el.textContent = sum[k] != null ? sum[k] : 0;
    });
    return running;
  }

  var wasRunning = root.getAttribute("data-running") === "1";
  function poll() {
    if (!statusUrl) return;
    clearTimeout(pollTimer);
    get(statusUrl).then(function (s) {
      var running = render(s);
      if (running) {
        wasRunning = true;
        pollTimer = setTimeout(poll, 2500);
      } else if (wasRunning) {
        // Finished: reload once so the per-product table shows the results.
        wasRunning = false;
        window.location.reload();
      }
    }).catch(function () { pollTimer = setTimeout(poll, 5000); });
  }

  syncBtns.forEach(function (btn) {
    btn.addEventListener("click", function () {
      var label = btn.textContent;
      syncBtns.forEach(function (b) { b.disabled = true; });
      btn.textContent = "Starting…";
      setError("");
      post(btn.getAttribute("data-url"), { force: btn.getAttribute("data-force") ? "1" : "" }).then(function (res) {
        btn.textContent = label;
        if (!res.ok || res.d.success === false) {
          syncBtns.forEach(function (b) { b.disabled = false; });
          setError((res.d && res.d.error) || "Couldn't start the sync. Please try again.");
          return;
        }
        wasRunning = true;
        render(res.d);
        poll();
      }).catch(function () {
        btn.textContent = label;
        syncBtns.forEach(function (b) { b.disabled = false; });
        setError("Network error. Please try again.");
      });
    });
  });

  var refreshBtn = q("[data-gm-refresh]");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", function () {
      var label = refreshBtn.textContent;
      refreshBtn.disabled = true;
      refreshBtn.textContent = "Checking Google…";
      post(refreshBtn.getAttribute("data-url"), {}).then(function (res) {
        if (!res.ok || res.d.success === false) {
          refreshBtn.disabled = false;
          refreshBtn.textContent = label;
          setError((res.d && res.d.error) || "Couldn't refresh statuses.");
          return;
        }
        window.location.reload();
      }).catch(function () {
        refreshBtn.disabled = false;
        refreshBtn.textContent = label;
        setError("Network error. Please try again.");
      });
    });
  }

  var auto = document.getElementById("gmAutoSync");
  if (auto) {
    auto.addEventListener("change", function () {
      post(auto.getAttribute("data-url"), { enabled: auto.checked ? "1" : "0" }).then(function (res) {
        if (!res.ok || !res.d.success) auto.checked = !auto.checked;
      }).catch(function () { auto.checked = !auto.checked; });
    });
  }

  // Disconnect: the first click arms the button, the second submits.
  var dc = q("[data-gm-disconnect]");
  if (dc) {
    var dcBtn = dc.querySelector("button[type=submit]");
    var armed = false, armTimer = null;
    dc.addEventListener("submit", function (e) {
      if (armed) return;
      e.preventDefault();
      armed = true;
      var label = dcBtn.textContent;
      dcBtn.textContent = "Click again to disconnect";
      clearTimeout(armTimer);
      armTimer = setTimeout(function () { armed = false; dcBtn.textContent = label; }, 5000);
    });
  }

  if (wasRunning) poll();
})();
