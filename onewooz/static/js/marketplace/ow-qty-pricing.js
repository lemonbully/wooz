/*
 * Quantity Discounts app: Amazon-style price per piece on the product page.
 *
 * No block, no extra UI. When the shopper changes the quantity, the design's
 * own price per piece is rewritten in place with the tier price for that
 * quantity (and back to the regular price below the first tier).
 *
 * Talks to the design only through the theme contract, so it works on every
 * design, present and future:
 *   [data-ow-qty]       the design's quantity box(es) (input or select)
 *   [data-ow-price]     element(s) whose whole text is the price per piece
 *   "ow:variant" event  the regular unit price now showing (detail.price),
 *                       fired after the design re-renders its price; the last
 *                       one is kept on window.__owVariant for late loaders
 *   window.owTheme.notify(text)   optional: the design's own toast
 * The price is written in the design's own number format (its symbol, comma
 * grouping and decimals are copied from the text it printed).
 *
 * The cart and checkout re-price everything on the server, so nothing here is
 * trusted for money. CSP-safe external file; any error only affects this.
 */
(function () {
  "use strict";

  var cfgEl = document.querySelector("[data-ow-qd-config]");
  if (!cfgEl) return;
  var cfg;
  try { cfg = JSON.parse(cfgEl.textContent || "{}"); } catch (e) { return; }

  var tiers = (cfg.tiers || []).map(function (t) { return { min: +t.min, v: parseFloat(t.v) }; })
    .sort(function (a, b) { return a.min - b.min; });
  if (!tiers.length) return;
  var base = parseFloat(cfg.regular) || 0;
  var limit = cfg.limit ? +cfg.limit : null;
  var busy = false;
  var told = false;

  function tierFor(q) {
    var found = null;
    for (var i = 0; i < tiers.length; i++) if (q >= tiers[i].min) found = tiers[i];
    return found;
  }
  function unitFor(q) {
    var t = tierFor(q);
    if (!t || !(base > 0)) return base;
    var u = cfg.type === "percent" ? base * (1 - Math.min(t.v, 99.99) / 100)
      : (cfg.type === "amount_off" ? base - t.v : Math.min(base, t.v));
    u = Math.round(u * 100) / 100;
    return (u > 0 && u < base) ? u : base;
  }

  // Write ``n`` the way the design wrote the number already in ``el``.
  function formatLike(el, n) {
    var text = (el.textContent || "").trim();
    var m = text.match(/^([^\d]*)([\d][\d.,]*)(.*)$/);
    var pre = m ? m[1] : "", num = m ? m[2] : "", post = m ? m[3] : "";
    var dec = (/\.\d{1,2}$/.test(num) || Math.round(n * 100) % 100 !== 0) ? 2 : 0;
    var grouped = num.indexOf(",") >= 0;
    var body;
    if (grouped) {
      try { body = n.toLocaleString("en-IN", { minimumFractionDigits: dec, maximumFractionDigits: dec }); }
      catch (e) { body = n.toFixed(dec); }
    } else {
      body = n.toFixed(dec);
    }
    return pre + body + post;
  }

  function visible(el) {
    try { return !!(el && !el.disabled && el.getClientRects().length > 0); } catch (e) { return false; }
  }
  function qtyBoxes() { return Array.prototype.slice.call(document.querySelectorAll("[data-ow-qty]")); }
  function readQty(source) {
    var el = source || qtyBoxes().filter(visible)[0] || qtyBoxes()[0];
    var n = el ? parseInt(el.value, 10) : 1;
    return isFinite(n) && n > 0 ? n : 1;
  }

  // The owner's per-order limit: the quantity box never offers more.
  function applyLimit() {
    if (!limit) return;
    qtyBoxes().forEach(function (el) {
      if (el.tagName === "SELECT") {
        Array.prototype.forEach.call(el.options, function (o) {
          var over = parseInt(o.value, 10) > limit;
          o.disabled = over;
          o.hidden = over;
        });
      } else {
        el.setAttribute("max", String(limit));
      }
    });
  }
  function clamp(source) {
    if (!limit) return false;
    var q = readQty(source);
    if (q <= limit) return false;
    busy = true;
    qtyBoxes().forEach(function (el) {
      if (parseInt(el.value, 10) > limit) {
        el.value = String(limit);
        try { el.dispatchEvent(new Event("change", { bubbles: true })); } catch (e) {}
      }
    });
    busy = false;
    if (!told && cfg.notice && window.owTheme && typeof window.owTheme.notify === "function") {
      told = true;
      try { window.owTheme.notify(cfg.notice); } catch (e) {}
    }
    return true;
  }

  function paint(source) {
    var q = readQty(source);
    if (limit && q > limit) q = limit;
    var unit = unitFor(q);
    Array.prototype.forEach.call(document.querySelectorAll("[data-ow-price]"), function (el) {
      var next = formatLike(el, unit);
      if (el.textContent !== next) el.textContent = next;
    });
  }

  function onQty(e) {
    var t = e && e.target;
    if (busy || !t || !t.matches || !t.matches("[data-ow-qty]")) return;
    clamp(t);
    paint(t);
  }
  document.addEventListener("input", onQty, true);
  document.addEventListener("change", onQty, true);
  document.addEventListener("ow:variant", function (e) {
    var p = e && e.detail ? parseFloat(e.detail.price) : NaN;
    if (p > 0) base = p;
    paint();
  });

  function boot() {
    if (window.__owVariant && parseFloat(window.__owVariant.price) > 0) base = parseFloat(window.__owVariant.price);
    applyLimit();
    clamp();
    paint();
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
