/* ==========================================================================
   OneWooz  Shiprocket wallet balance box
   --------------------------------------------------------------------------
   Fills every [data-sr-wallet] box (Orders page, Shipping settings) with the
   store's live Shiprocket wallet balance from its data-url, on load, on
   "Refresh" (?refresh=1, read fresh from Shiprocket) and when a hidden box is
   revealed. Below the threshold the box switches to a low-balance warning.
   CSP-safe: no inline handlers, text only via textContent.
   ========================================================================== */
(function () {
  "use strict";

  function q(box, sel) { return box.querySelector(sel); }

  function setState(box, state) {
    box.classList.remove("is-low", "is-error", "is-setup", "is-ok", "is-loading");
    if (state) box.classList.add(state);
  }

  function showWarn(box, text, link) {
    var warn = q(box, "[data-sr-wallet-warn]");
    if (!warn) return;
    warn.textContent = text || "";
    if (text && link) {
      warn.appendChild(document.createTextNode(" "));
      var a = document.createElement("a");
      a.href = link;
      a.textContent = "Open Shipping settings";
      warn.appendChild(a);
    }
    warn.hidden = !text;
  }

  function load(box, refresh) {
    var url = box.getAttribute("data-url");
    if (!url || box.hidden) return;
    var value = q(box, "[data-sr-wallet-value]");
    var meta = q(box, "[data-sr-wallet-meta]");
    var btn = q(box, "[data-sr-wallet-refresh]");
    setState(box, "is-loading");
    if (btn) btn.disabled = true;
    if (refresh && meta) meta.textContent = "Checking with Shiprocket…";
    fetch(url + (refresh ? "?refresh=1" : ""), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.success) {
          setState(box, d && d.needs_setup ? "is-setup" : "is-error");
          if (value) value.textContent = d && d.needs_setup ? "Not connected" : "Unavailable";
          if (meta) meta.textContent = "";
          // On the Shipping page itself the settings link would point here.
          var onShipping = !!document.querySelector("[data-sr-pickup]");
          showWarn(box, (d && d.error) || "Couldn't load the wallet balance.", onShipping ? "" : d && d.shipping_url);
          return;
        }
        if (value) value.textContent = d.balance_display;
        if (meta) meta.textContent = "Updated " + d.checked_at;
        if (d.low) {
          setState(box, "is-low");
          showWarn(
            box,
            "Low balance: your Shiprocket wallet is below " + d.threshold_display +
              ". Recharge it in your Shiprocket dashboard so couriers can be assigned and pickups scheduled. " +
              "We've also emailed you about it."
          );
        } else {
          setState(box, "is-ok");
          showWarn(box, "");
        }
      })
      .catch(function () {
        setState(box, "is-error");
        if (value) value.textContent = "Unavailable";
        showWarn(box, "Couldn't reach the server. Check your connection and refresh.");
      })
      .then(function () { if (btn) btn.disabled = false; });
  }

  function boxes() { return Array.prototype.slice.call(document.querySelectorAll("[data-sr-wallet]")); }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-sr-wallet-refresh]");
    if (!btn) return;
    e.preventDefault();
    var box = btn.closest("[data-sr-wallet]");
    if (box) load(box, true);
  });

  // Shipping settings: the box follows the carrier dropdown, so it shows (and
  // loads) as soon as Shiprocket is picked.
  function bindCarrierSelect() {
    var sel = document.querySelector('select[name="provider"]');
    if (!sel) return;
    sel.addEventListener("change", function () {
      boxes().forEach(function (box) {
        if (!box.hasAttribute("data-follow-carrier")) return;
        var wasHidden = box.hidden;
        box.hidden = sel.value !== "shiprocket";
        if (wasHidden && !box.hidden) load(box, false);
      });
    });
  }

  function boot() {
    bindCarrierSelect();
    boxes().forEach(function (box) { load(box, false); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
