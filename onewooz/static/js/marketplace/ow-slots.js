/*
 * OneWooz Apps: storefront slot mounting and small widget behaviours.
 *
 * Loaded only on pages that have app content (the {% app_tail %} tag prints it
 * only then). It:
 *   1. moves each <template data-ow-slot="..."> fragment next to that slot's
 *      anchor for the current theme (an explicit [data-ow-slot-anchor] wins);
 *      a slot with no anchor on this page simply does not show. Themes that
 *      follow the theme contract render inline slots in place with
 *      {% app_slot %}, so no template is printed for them and nothing moves;
 *      the selector maps below are only a fallback for older markup;
 *   2. runs countdowns, popups and dismissible bars.
 * No eval, no inline handlers: everything is attached with addEventListener.
 * Any error here is caught and only affects app widgets, never the page.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  var theme = (script && script.getAttribute("data-ow-theme")) || "";

  // slot -> list of [selector, closest-selector or "", position]
  // position: "after" (default) | "before" | "prepend" | "append"
  var SERVER = {
    "home.after_hero": [["#hero-style3", "", "after"], ["#hero-style2", "", "after"], ["#hero", "", "after"]],
    "home.after_categories": [["#cat2-section", "", "after"]],
    "home.after_bestsellers": [["#cat2-section", "", "after"]],
    "home.after_about": [["#about", "", "after"]],
    "home.after_faq": [["#why-choose-us", "", "before"]],
    "pdp.after_title": [["#pdp-title-base", "h1", "after"]],
    "pdp.after_price": [[".pdp-priceblock__row", "", "after"], ["#product-price", "div", "after"], ["#mobile-price", "div", "after"]],
    "pdp.after_variations": [["#pdp-variant-hint", "div", "after"], ["[data-vp-slot=\"features\"]", "", "before"]],
    "pdp.after_buy_box": [["#purch-buy", "div", "after"]],
    "pdp.after_details": [["#tabs-nav", "section", "after"], ["#tabs-nav", "div", "after"]],
    "collection.top": [["main", "", "prepend"]],
    "cart.after_items": [["main", "", "append"]],
    "checkout.after_summary": [["main", "", "append"]]
  };
  var JS = {
    "home.after_hero": [["[data-hero]", "", "after"]],
    "home.after_categories": [["[data-sec-cats]", "", "after"], ["[data-sec-best]", "", "before"]],
    "home.after_bestsellers": [["[data-sec-best]", "", "after"]],
    "home.after_about": [["[data-sec-about]", "", "after"]],
    "home.after_faq": [["[data-sec-faq]", "", "after"]],
    "pdp.after_title": [[".pdp__title", "", "after"]],
    "pdp.after_price": [[".pdp__tax", "", "after"], [".pdp__price", "", "after"]],
    "pdp.after_variations": [["[data-options]", "", "after"]],
    "pdp.after_buy_box": [["[data-buy]", "", "after"]],
    "pdp.after_details": [["[data-about-wrap]", "", "after"]],
    "collection.top": [["main", "", "prepend"]],
    "cart.after_items": [["main", "", "append"]],
    "checkout.after_summary": [["main", "", "append"]]
  };
  var MAP = (theme === "template3" || theme === "template4") ? JS : SERVER;

  function place(node, anchor, position) {
    if (position === "before") anchor.parentNode.insertBefore(node, anchor);
    else if (position === "prepend") anchor.insertBefore(node, anchor.firstChild);
    else if (position === "append") anchor.appendChild(node);
    else anchor.parentNode.insertBefore(node, anchor.nextSibling);
  }

  function findAnchor(slot) {
    var explicit = document.querySelector('[data-ow-slot-anchor="' + slot + '"]');
    if (explicit) return [explicit, "append"];
    var specs = MAP[slot] || [];
    for (var i = 0; i < specs.length; i++) {
      var el = document.querySelector(specs[i][0]);
      if (!el) continue;
      if (specs[i][1]) el = el.closest(specs[i][1]) || el;
      if (specs[i][2] !== "prepend" && specs[i][2] !== "append" && !el.parentNode) continue;
      return [el, specs[i][2] || "after"];
    }
    return null;
  }

  function mount() {
    var templates = document.querySelectorAll("template[data-ow-slot]");
    Array.prototype.forEach.call(templates, function (tpl) {
      try {
        var slot = tpl.getAttribute("data-ow-slot");
        var found = findAnchor(slot);
        if (!found) return;
        var box = document.createElement("div");
        box.className = "ow-slot ow-slot--" + slot.replace(/\./g, "-");
        box.appendChild(tpl.content.cloneNode(true));
        place(box, found[0], found[1]);
      } catch (e) { /* an app widget must never break the page */ }
    });
  }

  // ---- behaviours ---------------------------------------------------------
  function store(key, value) {
    try { if (value === undefined) return window.localStorage.getItem(key); window.localStorage.setItem(key, value); }
    catch (e) { return null; }
    return null;
  }

  function countdowns() {
    var nodes = document.querySelectorAll("[data-ow-countdown]");
    if (!nodes.length) return;
    function tick() {
      var now = Date.now();
      Array.prototype.forEach.call(nodes, function (n) {
        var end = Date.parse(n.getAttribute("data-ow-countdown"));
        var box = n.closest(".ow-app") || n;
        if (isNaN(end) || end <= now) { box.hidden = true; return; }
        var s = Math.floor((end - now) / 1000);
        var d = Math.floor(s / 86400); s -= d * 86400;
        var h = Math.floor(s / 3600); s -= h * 3600;
        var m = Math.floor(s / 60); s -= m * 60;
        var parts = n.querySelectorAll("[data-u]");
        Array.prototype.forEach.call(parts, function (p) {
          var u = p.getAttribute("data-u");
          var v = u === "d" ? d : u === "h" ? h : u === "m" ? m : s;
          p.textContent = (v < 10 ? "0" : "") + v;
        });
      });
    }
    tick();
    window.setInterval(tick, 1000);
  }

  function dismissibles() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-ow-dismiss]"), function (btn) {
      var key = "ow_dismiss_" + btn.getAttribute("data-ow-dismiss");
      var box = btn.closest(".ow-app");
      if (store(key) === "1" && box) { box.hidden = true; return; }
      btn.addEventListener("click", function () { if (box) box.hidden = true; store(key, "1"); });
    });
  }

  function popups() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-ow-popup]"), function (pop) {
      var id = pop.getAttribute("data-ow-popup");
      var once = pop.getAttribute("data-once") === "1";
      var key = "ow_popup_" + id;
      if (once && store(key) === "1") return;
      var delay = parseInt(pop.getAttribute("data-delay") || "5", 10) * 1000;
      function close() { pop.hidden = true; if (once) store(key, "1"); }
      Array.prototype.forEach.call(pop.querySelectorAll("[data-ow-close]"), function (b) {
        b.addEventListener("click", close);
      });
      pop.addEventListener("click", function (e) { if (e.target === pop) close(); });
      document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !pop.hidden) close(); });
      window.setTimeout(function () { pop.hidden = false; }, delay);
    });
  }

  function run() {
    try { mount(); } catch (e) {}
    try { countdowns(); } catch (e) {}
    try { dismissibles(); } catch (e) {}
    try { popups(); } catch (e) {}
  }

  // JS-rendered themes build their sections after DOMContentLoaded; give them
  // a moment so anchors exist, then mount.
  function ready() {
    if (MAP === JS) window.setTimeout(run, 120); else run();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();
})();
