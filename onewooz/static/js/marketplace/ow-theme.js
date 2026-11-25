/*
 * OneWooz Apps: theme-contract safety net.
 *
 * Every theme declares the --ow-* style tokens app blocks are drawn with (see
 * static/css/ow-apps.css). If a theme ships without them, this reads the look
 * from the page's own elements (body text, the main button, a form field) and
 * fills only the tokens that are missing, so app blocks still match the store.
 * A theme that declares its tokens is never touched.
 */
(function () {
  "use strict";
  try {
    var root = document.documentElement;
    var have = getComputedStyle(root);
    function missing(name) { return !have.getPropertyValue(name).trim(); }
    function set(name, value) {
      if (value && missing(name)) root.style.setProperty(name, value);
    }
    if (!missing("--ow-accent") && !missing("--ow-choice-border")) return;
    var transparent = /^(transparent|rgba\(0, 0, 0, 0\))$/;
    var body = getComputedStyle(document.body);
    set("--ow-font-body", body.fontFamily);
    set("--ow-text", body.color);
    var h = document.querySelector("h1, h2");
    if (h) set("--ow-font-heading", getComputedStyle(h).fontFamily);
    var btn = document.querySelector('[data-ow-role="primary"], [data-atc], #purch-add, .btn--black, button[type="submit"], .btn, button');
    if (btn) {
      var b = getComputedStyle(btn);
      if (!transparent.test(b.backgroundColor)) {
        set("--ow-accent", b.backgroundColor);
        set("--ow-on-accent", b.color);
        set("--ow-choice-active-bg", b.backgroundColor);
        set("--ow-choice-active-fg", b.color);
        set("--ow-choice-active-border", b.backgroundColor);
      }
      set("--ow-radius-control", b.borderTopLeftRadius);
    }
    var field = document.querySelector('[data-ow-qty], input:not([type="hidden"]), select');
    if (field) {
      var f = getComputedStyle(field);
      set("--ow-choice-border", f.borderTopColor);
      set("--ow-line", f.borderTopColor);
      set("--ow-choice-radius", f.borderTopLeftRadius);
    }
  } catch (e) { /* never break the page */ }
})();
