/*
 * Universal toast notifications for every dashboard page.
 *
 *   OWToast.success("Product saved.")   → green, fades after a few seconds
 *   OWToast.error("Couldn't save.")     → red, stays until dismissed
 *   OWToast.warning(...) / OWToast.info(...)
 *
 * Django messages are rendered server-side into #ow-toasts by
 * templates/partials/_toasts.html; this script wires their close buttons and
 * timers, and exposes the same look to in-page (AJAX) actions.
 *
 * A form that comes back with validation errors (field errors or a non-field
 * error alert) also raises one red toast, so every failed save is announced in
 * red even where the view only re-rendered the form.
 */
(function () {
  "use strict";

  var TTL = { success: 5000, info: 6000, warning: 9000, error: 0 };
  var TITLES = { success: "Success", error: "Error", warning: "Heads up", info: "Notice" };
  var ICONS = {
    success: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.6 2.6L16 9.7"/></svg>',
    error: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5M12 16.4v.1"/></svg>',
    warning: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.4v.1"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.1"/></svg>',
  };

  function kindOf(raw) {
    raw = String(raw || "").toLowerCase();
    if (raw.indexOf("error") !== -1 || raw.indexOf("danger") !== -1) return "error";
    if (raw.indexOf("success") !== -1) return "success";
    if (raw.indexOf("warning") !== -1) return "warning";
    return "info";
  }

  function stack() {
    var el = document.getElementById("ow-toasts");
    if (!el) {
      el = document.createElement("div");
      el.id = "ow-toasts";
      el.className = "ow-toasts";
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    return el;
  }

  function dismiss(toast) {
    if (!toast || toast.__gone) return;
    toast.__gone = true;
    toast.classList.add("is-leaving");
    setTimeout(function () { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 220);
  }

  function arm(toast) {
    if (toast.__armed) return;
    toast.__armed = true;
    var kind = kindOf(toast.getAttribute("data-kind"));
    var btn = toast.querySelector(".ow-toast__close");
    if (btn) btn.addEventListener("click", function () { dismiss(toast); });
    var ttl = TTL[kind];
    if (!ttl) return;
    var timer = null;
    var start = function () { timer = setTimeout(function () { dismiss(toast); }, ttl); };
    var stop = function () { if (timer) clearTimeout(timer); timer = null; };
    toast.addEventListener("mouseenter", stop);
    toast.addEventListener("mouseleave", start);
    toast.addEventListener("focusin", stop);
    start();
  }

  function build(kind, message) {
    var t = document.createElement("div");
    t.className = "ow-toast ow-toast--" + kind;
    t.setAttribute("data-kind", kind);
    t.setAttribute("role", kind === "error" ? "alert" : "status");
    var icon = document.createElement("span");
    icon.className = "ow-toast__icon";
    icon.innerHTML = ICONS[kind];
    var body = document.createElement("div");
    body.className = "ow-toast__body";
    var title = document.createElement("strong");
    title.className = "ow-toast__title";
    title.textContent = TITLES[kind];
    var text = document.createElement("span");
    text.className = "ow-toast__msg";
    text.textContent = String(message == null ? "" : message);
    body.appendChild(title);
    body.appendChild(text);
    var close = document.createElement("button");
    close.type = "button";
    close.className = "ow-toast__close";
    close.setAttribute("aria-label", "Dismiss");
    close.innerHTML = "&times;";
    t.appendChild(icon);
    t.appendChild(body);
    t.appendChild(close);
    return t;
  }

  function show(message, kind) {
    kind = kindOf(kind);
    if (!message) return null;
    var host = stack();
    // Collapse an identical toast already on screen instead of stacking copies.
    var dupes = host.querySelectorAll(".ow-toast");
    for (var i = 0; i < dupes.length; i++) {
      var m = dupes[i].querySelector(".ow-toast__msg");
      if (!dupes[i].__gone && dupes[i].getAttribute("data-kind") === kind && m && m.textContent === String(message)) {
        return dupes[i];
      }
    }
    var t = build(kind, message);
    host.appendChild(t);
    arm(t);
    // Keep the stack manageable: drop the oldest beyond five.
    var all = host.querySelectorAll(".ow-toast");
    for (var j = 0; j < all.length - 5; j++) dismiss(all[j]);
    return t;
  }

  var api = {
    show: show,
    success: function (m) { return show(m, "success"); },
    error: function (m) { return show(m, "error"); },
    warning: function (m) { return show(m, "warning"); },
    info: function (m) { return show(m, "info"); },
  };
  window.OWToast = api;

  function boot() {
    var host = document.getElementById("ow-toasts");
    var hadError = false;
    if (host) {
      Array.prototype.forEach.call(host.querySelectorAll(".ow-toast"), function (t) {
        if (kindOf(t.getAttribute("data-kind")) === "error") hadError = true;
        arm(t);
      });
    }
    // A form re-rendered with validation errors: announce it once, in red.
    if (!hadError) {
      var main = document.getElementById("main") || document.body;
      var visible = function (sel) {
        var list = main.querySelectorAll(sel);
        for (var k = 0; k < list.length; k++) {
          if (!list[k].closest("[hidden]") && (list[k].textContent || "").trim()) return list[k];
        }
        return null;
      };
      var fieldErr = visible(".app-field-err, .errorlist li, .field-error");
      var formAlert = visible("form .pal-alert[role='alert'], .pal-alert[role='alert'] .errorlist");
      if (fieldErr || formAlert) {
        var txt = "";
        if (formAlert) txt = (formAlert.textContent || "").replace(/\s+/g, " ").trim();
        show(txt && txt.length < 220 ? txt : "Couldn't save. Please fix the highlighted fields and try again.", "error");
      }
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
