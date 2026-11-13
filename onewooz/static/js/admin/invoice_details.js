/*
 * "Invoice details" admin (billing.InvoiceDetails).
 *
 * 1. GSTIN → state: as the GSTIN is typed, its first two digits (the GST state
 *    code) select the matching state, and the "GST on invoices" line updates to
 *    show which buyers get CGST + SGST and which get IGST. The server applies
 *    the same rule on save, so this is a live preview of what will be stored.
 * 2. Terms & Conditions: the hidden textarea holds a JSON list of points; this
 *    builds a numbered editor on top of it (add, edit, move up/down, remove),
 *    writing the list back on every change.
 */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", fn);
    else fn();
  }

  var GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]Z[0-9A-Z]$/;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // ---- 1. GSTIN → state -------------------------------------------------
  function initGstin() {
    var gstin = document.getElementById("id_gstin");
    var state = document.getElementById("id_state");
    if (!gstin || !state) return;
    var codes = {};
    try { codes = JSON.parse(gstin.getAttribute("data-gst-states") || "{}"); } catch (e) { codes = {}; }

    var note = el("div", "ow-inv-detect");
    note.setAttribute("aria-live", "polite");
    gstin.parentNode.appendChild(note);

    var rule = document.querySelector(".field-tax_rule .readonly");

    function renderRule(name) {
      if (!rule) return;
      rule.textContent = "";
      if (!name) {
        rule.textContent = "Enter a GSTIN (or pick a state) to fix the place of supply.";
        return;
      }
      rule.appendChild(document.createTextNode("Buyers in "));
      rule.appendChild(el("strong", "", name));
      rule.appendChild(document.createTextNode(": CGST 9% + SGST 9%. Buyers in any other state: IGST 18%."));
    }

    function detect() {
      var v = String(gstin.value || "").replace(/\s+/g, "").toUpperCase();
      if (gstin.value !== v) gstin.value = v;
      note.className = "ow-inv-detect";
      if (!v) {
        note.textContent = "";
        state.disabled = false;
        renderRule(state.value);
        return;
      }
      if (v.length < 15) {
        note.textContent = "Keep typing: a GSTIN has 15 characters (" + v.length + "/15).";
        state.disabled = false;
        return;
      }
      if (!GSTIN_RE.test(v)) {
        note.textContent = "This doesn’t look like a valid GSTIN.";
        note.className = "ow-inv-detect is-error";
        state.disabled = false;
        return;
      }
      var name = codes[v.slice(0, 2)];
      if (!name) {
        note.textContent = "Unknown GST state code “" + v.slice(0, 2) + "”. Pick the state manually.";
        note.className = "ow-inv-detect is-error";
        state.disabled = false;
        return;
      }
      state.value = name;
      note.textContent = "State detected from GSTIN: " + name + " (code " + v.slice(0, 2) + ").";
      note.className = "ow-inv-detect is-ok";
      renderRule(name);
    }

    gstin.addEventListener("input", detect);
    state.addEventListener("change", function () { renderRule(state.value); });
    detect();
  }

  // ---- 2. Terms & Conditions points ------------------------------------
  function initTerms() {
    var store = document.querySelector("textarea[data-terms-editor]");
    if (!store) return;
    var points;
    try { points = JSON.parse(store.value || "[]"); } catch (e) { points = []; }
    if (!Array.isArray(points)) points = [];
    points = points.map(function (p) { return String(p == null ? "" : p); });

    store.style.display = "none";
    var box = el("div", "ow-terms");
    var list = el("ol", "ow-terms__list");
    var add = el("button", "button ow-terms__add", "+ Add point");
    add.type = "button";
    var empty = el("p", "ow-terms__empty", "No terms yet. Add the first point.");
    box.appendChild(list);
    box.appendChild(empty);
    box.appendChild(add);
    store.parentNode.insertBefore(box, store.nextSibling);

    function sync() {
      store.value = JSON.stringify(points.map(function (p) { return p.trim(); }).filter(Boolean));
    }

    function grow(ta) {
      ta.style.height = "auto";
      ta.style.height = Math.max(ta.scrollHeight, 34) + "px";
    }

    function render(focusIndex) {
      list.textContent = "";
      empty.style.display = points.length ? "none" : "";
      points.forEach(function (text, i) {
        var li = el("li", "ow-terms__item");
        var num = el("span", "ow-terms__num", (i + 1) + ".");
        var ta = el("textarea", "vLargeTextField ow-terms__text");
        ta.rows = 1;
        ta.value = text;
        ta.placeholder = "Point " + (i + 1);
        ta.setAttribute("aria-label", "Terms point " + (i + 1));
        ta.addEventListener("input", function () { points[i] = ta.value; grow(ta); sync(); });

        var acts = el("span", "ow-terms__acts");
        function btn(label, title, fn, disabled) {
          var b = el("button", "ow-terms__btn", label);
          b.type = "button";
          b.title = title;
          b.setAttribute("aria-label", title);
          if (disabled) b.disabled = true;
          b.addEventListener("click", fn);
          acts.appendChild(b);
        }
        btn("↑", "Move up", function () {
          var t = points[i - 1]; points[i - 1] = points[i]; points[i] = t; sync(); render(i - 1);
        }, i === 0);
        btn("↓", "Move down", function () {
          var t = points[i + 1]; points[i + 1] = points[i]; points[i] = t; sync(); render(i + 1);
        }, i === points.length - 1);
        btn("✕", "Remove point", function () {
          points.splice(i, 1); sync(); render(Math.min(i, points.length - 1));
        }, false);

        li.appendChild(num);
        li.appendChild(ta);
        li.appendChild(acts);
        list.appendChild(li);
        grow(ta);
        if (focusIndex === i) ta.focus();
      });
    }

    add.addEventListener("click", function () {
      points.push("");
      sync();
      render(points.length - 1);
    });
    var form = store.form;
    if (form) form.addEventListener("submit", sync);
    render();
    sync();
  }

  ready(function () {
    initGstin();
    initTerms();
  });
})();
