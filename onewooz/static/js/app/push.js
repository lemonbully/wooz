/* Browser push notifications for the owner dashboard.
 *
 * STATUS: BROWSER PUSH IS CURRENTLY DISABLED. This script is only loaded by
 * templates/accounts/push/_prompt.html, whose include in
 * ud/_topbar_actions.html is commented out, so it never runs. To turn it
 * back on, uncomment every spot marked "BROWSER PUSH: DISABLED".
 *
 * Markup: templates/accounts/push/_prompt.html (top bar), the strip in the
 * bell panel, and the "Browser notifications" card on Profile. Server side:
 * accounts.push_views + accounts.webpush.
 *
 * States, per browser:
 *   ask          can ask; the pill pulses and the explainer opens by itself
 *                (once, then again after "Maybe later" expires)
 *   on           allowed and subscribed: everything hides
 *   off          allowed, but the owner switched this browser off on Profile
 *   blocked      the browser blocks us; we show how to unblock
 *   ios          iPhone/iPad Safari outside a Home Screen app (no push there)
 *   unsupported  nothing is shown
 *
 * The permission prompt is only ever requested from a click on our own
 * button, after the explainer has shown which button to press, and while an
 * arrow points at where the browser's box appears. That ordering is what keeps
 * owners from blocking by accident; browsers never let a site ask again after
 * a block.
 */
(function () {
  "use strict";

  var root = document.getElementById("ow-push");
  if (!root) return;

  var cfg = {
    key: root.getAttribute("data-key") || "",
    sw: root.getAttribute("data-sw") || "",
    subscribe: root.getAttribute("data-subscribe") || "",
    unsubscribe: root.getAttribute("data-unsubscribe") || "",
    test: root.getAttribute("data-test") || "",
    csrf: root.getAttribute("data-csrf") || "",
    uid: root.getAttribute("data-user") || "me",
    bound: root.getAttribute("data-bound") === "1"
  };
  var K = {
    snoozeUntil: "ow_push_snooze:" + cfg.uid,
    snoozes: "ow_push_snoozes:" + cfg.uid,
    off: "ow_push_off:" + cfg.uid,
    sub: "ow_push_sub:" + cfg.uid,          // server id of this browser's subscription (bell presence uses it)
    endpoint: "ow_push_ep:" + cfg.uid,
    synced: "ow_push_synced:" + cfg.uid,
    blockedShown: "ow_push_blocked_seen:" + cfg.uid
  };
  var HOUR = 3600 * 1000, DAY = 24 * HOUR;

  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function put(k, v) { try { localStorage.setItem(k, String(v)); } catch (e) {} }
  function del(k) { try { localStorage.removeItem(k); } catch (e) {} }

  /* ---- environment ---- */
  var ua = navigator.userAgent || "";
  var isIOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  var isAndroid = /Android/i.test(ua);
  var standalone = (window.matchMedia && matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
  var browser = /Edg\//.test(ua) ? "edge"
    : /OPR\//.test(ua) ? "opera"
    : /Firefox\//.test(ua) ? "firefox"
    : (/Safari\//.test(ua) && !/Chrome|Chromium|CriOS|FxiOS/.test(ua)) ? "safari"
    : "chrome";
  var supported = !!(window.isSecureContext && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window);

  function state() {
    if (!supported) return (isIOS && !standalone) ? "ios" : "unsupported";
    var p = Notification.permission;
    if (p === "denied") return "blocked";
    if (p === "granted") return get(K.off) ? "off" : (get(K.sub) ? "on" : "ask");
    return "ask";
  }

  /* ---- move overlays to <body> so the sticky top bar never clips them ---- */
  var layers = {};
  Array.prototype.forEach.call(document.querySelectorAll("[data-push-layer]"), function (el) {
    layers[el.getAttribute("data-push-layer")] = el;
    document.body.appendChild(el);
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-push-host]"), function (el) { el.textContent = location.host; });
  Array.prototype.forEach.call(document.querySelectorAll("[data-push-device]"), function (el) {
    el.textContent = isAndroid || isIOS ? "phone" : "computer";
  });

  var lastFocus = null;
  function open(name) {
    var el = layers[name];
    if (!el) return;
    Object.keys(layers).forEach(function (n) { if (n !== name && n !== "coach") layers[n].hidden = true; });
    if (!el.hidden) return;
    lastFocus = document.activeElement;
    el.hidden = false;
    document.documentElement.classList.add("ow-push-open");
    var first = el.querySelector("[data-push-step]:not([hidden]) .ow-push-cta, .ow-push-cta");
    if (first) { try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); } }
  }
  function close(name) {
    var el = layers[name];
    if (!el || el.hidden) return;
    el.hidden = true;
    if (!Object.keys(layers).some(function (n) { return !layers[n].hidden; })) {
      document.documentElement.classList.remove("ow-push-open");
    }
    if (lastFocus && lastFocus.focus) { try { lastFocus.focus({ preventScroll: true }); } catch (e) {} }
  }
  function step(name) {
    var ask = layers.ask;
    if (!ask) return;
    ask.setAttribute("data-step", name);   // the two-column layout is for the explainer only
    Array.prototype.forEach.call(ask.querySelectorAll("[data-push-step]"), function (s) {
      s.hidden = s.getAttribute("data-push-step") !== name;
    });
    open("ask");
    var cta = ask.querySelector('[data-push-step="' + name + '"] .ow-push-cta');
    if (cta) { try { cta.focus({ preventScroll: true }); } catch (e) {} }
  }

  /* ---- render: pill, bell strip, Profile card ---- */
  var PILL = { ask: "Turn on alerts", blocked: "Alerts blocked", ios: "Get alerts on iPhone" };
  var STATUS = {
    ask: "Off in this browser. Turn them on to get instant alerts.",
    on: "On. This browser gets an alert for every new notification.",
    off: "Switched off in this browser.",
    blocked: "Blocked by this browser’s settings.",
    ios: "On iPhone and iPad, add OneWooz to your Home Screen first.",
    unsupported: "This browser doesn’t support notifications. Try Chrome, Edge, Firefox or Safari."
  };
  var STRIP = {
    ask: ["Get these alerts on your " + (isAndroid || isIOS ? "phone" : "desktop") + ", even when OneWooz is closed.", "Turn on"],
    blocked: ["Notifications are blocked in this browser, so you’ll miss alerts when OneWooz is closed.", "Fix it"],
    ios: ["Get these alerts on your iPhone: add OneWooz to your Home Screen.", "Show me"],
    off: ["Alerts are switched off in this browser.", "Turn on"]
  };

  function render() {
    var s = state();
    document.documentElement.setAttribute("data-push-state", s);
    Array.prototype.forEach.call(document.querySelectorAll("[data-push-when]"), function (el) {
      var on = (" " + el.getAttribute("data-push-when") + " ").indexOf(" " + s + " ") !== -1;
      el.hidden = !on;
    });
    var label = document.querySelector("[data-push-pill-label]");
    var pill = document.querySelector("[data-push-pill]");
    if (label && PILL[s]) label.textContent = PILL[s];
    if (pill) {
      pill.classList.toggle("ow-push-pill--warn", s === "blocked");
      pill.setAttribute("aria-label", PILL[s] || "Notifications");
    }
    Array.prototype.forEach.call(document.querySelectorAll("[data-push-strip]"), function (strip) {
      var t = STRIP[s];
      strip.hidden = !t;
      strip.classList.toggle("ow-push-strip--warn", s === "blocked");
      if (!t) return;
      var txt = strip.querySelector("[data-push-strip-text]");
      var btn = strip.querySelector("[data-push-strip-btn]");
      if (txt) txt.textContent = t[0];
      if (btn) btn.textContent = t[1];
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-push-status-text]"), function (el) {
      el.textContent = STATUS[s] || "";
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-push-status]"), function (el) {
      el.setAttribute("data-state", s);
    });
    var which = isAndroid ? "android" : (browser === "firefox" || browser === "safari") ? browser : "chrome";
    if (layers.blocked) {
      Array.prototype.forEach.call(layers.blocked.querySelectorAll("[data-push-browser]"), function (ol) {
        ol.hidden = ol.getAttribute("data-push-browser") !== which;
      });
    }
  }

  /* ---- server calls ---- */
  function postJSON(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": cfg.csrf, "X-Requested-With": "fetch" },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false }; }).then(function (d) { d.status = r.status; return d; });
    });
  }

  function keyBytes(b64) {
    var pad = "=".repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }
  function sameKey(sub) {
    try {
      var have = new Uint8Array(sub.options.applicationServerKey);
      var want = keyBytes(cfg.key);
      if (have.length !== want.length) return false;
      for (var i = 0; i < want.length; i++) if (have[i] !== want[i]) return false;
      return true;
    } catch (e) { return true; } // older browsers don't expose it; keep the subscription
  }

  var regPromise = null;
  function registration() {
    if (!regPromise) {
      var scope = cfg.sw.replace(/[^/]*$/, "");
      regPromise = navigator.serviceWorker.register(cfg.sw, { scope: scope }).then(function () {
        return navigator.serviceWorker.ready;
      });
    }
    return regPromise;
  }

  // Make sure this browser has a push subscription and that the server has
  // it bound to the current login. Quiet: no UI.
  function ensureSubscribed(force) {
    return registration().then(function (reg) {
      return reg.pushManager.getSubscription().then(function (sub) {
        if (sub && sameKey(sub)) return sub;
        var drop = sub ? sub.unsubscribe().catch(function () {}) : Promise.resolve();
        return drop.then(function () {
          return reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(cfg.key) });
        });
      });
    }).then(function (sub) {
      var json = sub.toJSON();
      var synced = parseInt(get(K.synced) || "0", 10) || 0;
      var fresh = cfg.bound && get(K.endpoint) === json.endpoint && get(K.sub) && (Date.now() - synced) < 6 * HOUR;
      if (fresh && !force) return { ok: true };
      return postJSON(cfg.subscribe, { subscription: json }).then(function (d) {
        if (d && d.ok) {
          put(K.sub, d.id);
          put(K.endpoint, json.endpoint);
          put(K.synced, Date.now());
          cfg.bound = true;
        }
        return d;
      });
    });
  }

  /* ---- the coach: arrow toward the browser's own box ---- */
  var hintTimer = null;
  function showCoach() {
    var c = layers.coach;
    if (!c) return;
    var pos = isAndroid ? "bottom" : (browser === "safari" ? "topcenter" : "topleft");
    c.setAttribute("data-pos", pos);
    var text = c.querySelector("[data-push-coach-text]");
    if (text) {
      text.innerHTML = pos === "bottom"
        ? 'Tap <mark class="ow-push-mark">Allow</mark> in the box at the bottom of your screen'
        : pos === "topcenter"
          ? 'Click <mark class="ow-push-mark">Allow</mark> in the box at the top of your screen'
          : 'Click <mark class="ow-push-mark">Allow</mark> in the box at the top left of your browser, just under the address bar';
    }
    var hint = c.querySelector("[data-push-coach-hint]");
    if (hint) hint.hidden = true;
    clearTimeout(hintTimer);
    // Chrome and Edge sometimes show a quieter prompt: only a crossed-out
    // bell in the address bar. Explain it if nothing happens for a while.
    if (!isAndroid) hintTimer = setTimeout(function () { if (hint) hint.hidden = false; }, 4500);
    c.hidden = false;
    document.documentElement.classList.add("ow-push-coaching");
  }
  function hideCoach() {
    clearTimeout(hintTimer);
    if (layers.coach) layers.coach.hidden = true;
    document.documentElement.classList.remove("ow-push-coaching");
  }

  /* ---- actions ---- */
  function askPermission() {
    // Called synchronously from a click so the browser accepts it.
    return new Promise(function (resolve) {
      var done = false;
      function fin(v) { if (!done) { done = true; resolve(v); } }
      try {
        var r = Notification.requestPermission(fin);  // old Safari: callback form
        if (r && r.then) r.then(fin, function () { fin(Notification.permission); });
      } catch (e) { fin(Notification.permission); }
    });
  }

  function enable() {
    var s = state();
    if (s === "ios") { open("ios"); return; }
    if (s === "blocked") { open("blocked"); return; }
    if (s === "unsupported") return;
    del(K.off);
    if (Notification.permission === "granted") { connect(); return; }

    registration().catch(function () {});   // warm the worker while the box is open
    if (layers.ask) layers.ask.hidden = true;
    showCoach();
    askPermission().then(function (result) {
      hideCoach();
      if (result === "granted") {
        connect();
      } else if (result === "denied") {
        put(K.blockedShown, Date.now());
        render();
        open("blocked");
      } else {
        step("dismissed");  // closed the box without choosing: Chrome counts these, so ask only on a click
      }
    });
  }

  function connect() {
    step("working");
    ensureSubscribed(true).then(function (d) {
      if (d && d.ok) {
        del(K.snoozeUntil); del(K.snoozes);
        render();
        step("done");
      } else {
        var msg = layers.ask && layers.ask.querySelector("[data-push-error]");
        if (msg && d && d.error) msg.textContent = d.error;
        step("error");
      }
    }).catch(function () { step("error"); });
  }

  function disable() {
    put(K.off, 1);
    var id = get(K.sub);
    del(K.sub); del(K.endpoint); del(K.synced);
    var p = registration().then(function (reg) { return reg.pushManager.getSubscription(); }).then(function (sub) {
      var body = sub ? { endpoint: sub.endpoint } : { id: id };
      return postJSON(cfg.unsubscribe, body).then(function () { return sub && sub.unsubscribe(); });
    }).catch(function () {});
    render();
    toast("Notifications switched off in this browser.", "success");
    return p;
  }

  function toast(msg, kind) {
    if (window.OWToast && window.OWToast.show) window.OWToast.show(msg, kind || "info");
  }

  var testing = false;
  function sendTest(btn) {
    if (testing) return;
    var id = get(K.sub);
    if (!id) { enable(); return; }
    testing = true;
    if (btn) btn.disabled = true;
    postJSON(cfg.test, { id: id }).then(function (d) {
      if (d && d.ok) toast("Test sent. It should pop up on this " + (isAndroid || isIOS ? "phone" : "computer") + " in a moment.", "success");
      else {
        if (d && d.gone) { del(K.sub); render(); }
        toast((d && d.error) || "Couldn’t send the test. Try again.", "error");
      }
    }).catch(function () { toast("Couldn’t send the test. Try again.", "error"); })
      .then(function () { testing = false; if (btn) btn.disabled = false; });
  }

  function snooze() {
    // Each "Maybe later" waits longer before the popup opens by itself again;
    // the pill and the bell strip stay as a gentle reminder meanwhile.
    var n = (parseInt(get(K.snoozes) || "0", 10) || 0) + 1;
    put(K.snoozes, n);
    put(K.snoozeUntil, Date.now() + (n >= 3 ? 14 : n === 2 ? 5 : 2) * DAY);
  }
  function later() {
    snooze();
    close("ask");
  }
  function closeAll() {
    if (layers.ios && !layers.ios.hidden) snooze();   // the iPhone guide counts as "later" too
    close("ask"); close("blocked"); close("ios");
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest(
      "[data-push-enable],[data-push-pill],[data-push-strip-btn],[data-push-later],[data-push-close],[data-push-test],[data-push-disable],[data-push-reload],[data-push-blocked-help]"
    );
    if (!t) return;
    if (t.hasAttribute("data-push-enable")) { e.preventDefault(); enable(); }
    else if (t.hasAttribute("data-push-pill") || t.hasAttribute("data-push-strip-btn")) {
      e.preventDefault();
      var bell = t.closest("details");
      if (bell) bell.removeAttribute("open");
      var s = state();
      if (s === "ask" || s === "off") { del(K.off); step("ask"); }
      else if (s === "blocked") open("blocked");
      else if (s === "ios") open("ios");
    }
    else if (t.hasAttribute("data-push-later")) { e.preventDefault(); later(); }
    else if (t.hasAttribute("data-push-close")) { e.preventDefault(); closeAll(); }
    else if (t.hasAttribute("data-push-test")) { e.preventDefault(); sendTest(t); }
    else if (t.hasAttribute("data-push-disable")) { e.preventDefault(); disable(); }
    else if (t.hasAttribute("data-push-blocked-help")) { e.preventDefault(); open("blocked"); }
    else if (t.hasAttribute("data-push-reload")) { e.preventDefault(); location.reload(); }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    if (layers.ask && !layers.ask.hidden) later();
    else closeAll();
  });

  // The permission can change outside the page (site settings, another tab).
  try {
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({ name: "notifications" }).then(function (status) {
        status.onchange = function () {
          if (Notification.permission === "granted" && !get(K.off)) ensureSubscribed(true).then(render, render);
          else render();
        };
      }).catch(function () {});
    }
  } catch (e) {}

  /* ---- Profile: mark this browser in the device list; removing it there
     also stops it re-subscribing on the next page load ---- */
  (function () {
    var mine = get(K.sub);
    Array.prototype.forEach.call(document.querySelectorAll("[data-push-device]"), function (row) {
      var isMine = mine && row.getAttribute("data-push-device") === mine;
      var badge = row.querySelector("[data-push-this]");
      if (badge) badge.hidden = !isMine;
      var form = row.querySelector("form");
      if (isMine && form) form.addEventListener("submit", function () {
        put(K.off, 1); del(K.sub); del(K.endpoint); del(K.synced);
      });
    });
  })();

  /* ---- a notification click with nowhere specific to go opens its popup ---- */
  window.addEventListener("load", function () {
    var m = /^#notif-([0-9a-f-]{36})$/i.exec(location.hash || "");
    if (!m) return;
    var btn = document.querySelector('[data-popup-open="notif-' + m[1] + '"]');
    if (btn) btn.click();
  });

  // Another popup is on screen (the agreement update, a store dialog): never
  // stack ours on top of it.
  function busy() {
    var html = document.documentElement;
    if (html.classList.contains("owau-lock") || html.classList.contains("ow-push-open")) return true;
    var dialogs = document.querySelectorAll(".app-modal, .app-dlg, .owau, [role='dialog'][aria-modal='true']");
    for (var i = 0; i < dialogs.length; i++) {
      var d = dialogs[i];
      if (d.closest("[data-push-layer]")) continue;
      if (!d.hidden && getComputedStyle(d).display !== "none" && getComputedStyle(d).visibility !== "hidden") return true;
    }
    return false;
  }

  /* ---- start ---- */
  render();
  var s0 = state();
  if (s0 === "on" || (s0 === "ask" && Notification.permission === "granted" && !get(K.off))) {
    // Allowed already: keep the subscription bound to this login (a new
    // sign-in on the same browser re-binds it here, with no prompt).
    ensureSubscribed(false).then(render, render);
  } else if (s0 === "ask" || s0 === "ios") {
    var until = parseInt(get(K.snoozeUntil) || "0", 10) || 0;
    if (Date.now() > until) {
      setTimeout(function () {
        if (busy()) return;
        if (state() === "ask") step("ask");
        else if (state() === "ios") open("ios");
      }, 1400);
    }
  } else if (s0 === "blocked" && !get(K.blockedShown)) {
    // Blocked before we ever explained it (or in another session): show the
    // fix once; after that the pill and bell strip carry it.
    setTimeout(function () {
      if (busy()) return;
      put(K.blockedShown, Date.now());
      open("blocked");
    }, 1400);
  }
})();
