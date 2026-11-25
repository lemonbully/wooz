/*
 * Quantity Discounts app: owner pages (rule list + rule editor).
 * CSP-safe: external file, no inline handlers. Progressive: the form posts and
 * validates on the server without any of this.
 */
(function () {
  "use strict";

  // Confirm deletes on the list page.
  document.querySelectorAll("form[data-qd-confirm]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      if (!window.confirm(f.getAttribute("data-qd-confirm"))) e.preventDefault();
    });
  });

  var form = document.getElementById("qd-form");
  if (!form) return;
  var cur = form.getAttribute("data-currency") || "₹";
  var maxTiers = parseInt(form.getAttribute("data-max-tiers"), 10) || 5;

  function checked(name) {
    var el = form.querySelector('input[name="' + name + '"]:checked');
    return el ? el.value : "";
  }
  function each(sel, fn) { Array.prototype.forEach.call(form.querySelectorAll(sel), fn); }

  // Radio cards and segmented controls show their selected state.
  function paintChoices() {
    each(".disc-type", function (l) { var i = l.querySelector("input"); l.classList.toggle("is-on", !!(i && i.checked)); });
    each("[data-seg]", function (l) { var i = l.querySelector("input"); l.classList.toggle("is-on", !!(i && i.checked)); });
  }

  function toggleSections() {
    var scope = checked("scope"), beyond = checked("beyond"), type = checked("discount_type");
    each("[data-qd-for-scope]", function (el) { el.hidden = el.getAttribute("data-qd-for-scope") !== scope; });
    each("[data-qd-for-beyond]", function (el) {
      el.hidden = (el.getAttribute("data-qd-for-beyond") || "").split(" ").indexOf(beyond) < 0;
    });
    each("[data-qd-for-type]", function (el) { el.hidden = el.getAttribute("data-qd-for-type") !== type; });
    var pct = type === "percent";
    each("[data-qd-prefix]", function (el) { el.hidden = pct; });
    each("[data-qd-suffix]", function (el) { el.hidden = !pct; });
    var head = form.querySelector("[data-qd-valuehead]");
    if (head) head.textContent = type === "fixed_price" ? "Price per unit" : (type === "amount_off" ? "Off each unit" : "Discount");
  }

  // Picker search + counts.
  each("[data-qd-search]", function (input) {
    var list = input.parentNode.querySelector(".disc-picker__list");
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      Array.prototype.forEach.call(list.querySelectorAll(".disc-pick"), function (row) {
        row.classList.toggle("is-hidden", q && (row.getAttribute("data-search") || "").indexOf(q) < 0);
      });
    });
  });
  function counts() {
    each("[data-qd-count]", function (el) {
      var n = form.querySelectorAll('input[name="' + el.getAttribute("data-qd-count") + '"]:checked').length;
      el.textContent = n ? "(" + n + " selected)" : "";
    });
  }

  // Tier rows.
  function rows() { return Array.prototype.slice.call(form.querySelectorAll("[data-qd-row]")); }
  function visibleRows() { return rows().filter(function (r) { return !r.hidden; }); }
  var addBtn = form.querySelector("[data-qd-addtier]");
  function paintAdd() { if (addBtn) addBtn.hidden = visibleRows().length >= maxTiers; }
  if (addBtn) addBtn.addEventListener("click", function () {
    var next = rows().filter(function (r) { return r.hidden; })[0];
    if (!next) return;
    var vis = visibleRows(), last = vis[vis.length - 1];
    var lastQ = last ? parseInt(last.querySelector('[name="tier_qty"]').value, 10) || 1 : 1;
    next.hidden = false;
    next.querySelector('[name="tier_qty"]').value = Math.min(lastQ + 5, parseInt(form.getAttribute("data-max-qty"), 10) || 100);
    next.querySelector('[name="tier_value"]').focus();
    paintAdd(); preview();
  });
  form.addEventListener("click", function (e) {
    var x = e.target.closest("[data-qd-remove]");
    if (!x) return;
    if (visibleRows().length <= 1) return;
    var row = x.closest("[data-qd-row]");
    row.querySelectorAll("input").forEach(function (i) { i.value = ""; });
    row.hidden = true;
    // Keep the visible rows first so the posted order matches what is shown.
    row.parentNode.appendChild(row);
    paintAdd(); preview();
  });

  // Live preview: "Qty / price each" for the first picked product (or ₹1,000).
  function money(n) {
    var r = Math.round(n * 100) / 100;
    return cur + r.toLocaleString("en-IN", { minimumFractionDigits: r % 1 ? 2 : 0, maximumFractionDigits: 2 });
  }
  function preview() {
    var tbody = form.querySelector("[data-qd-preview] tbody"), note = form.querySelector("[data-qd-preview-note]");
    if (!tbody) return;
    var type = checked("discount_type"), scope = checked("scope");
    var pick = scope === "products" ? form.querySelector('input[name="product_ids"]:checked') : null;
    var base = pick ? parseFloat(pick.getAttribute("data-price")) || 0 : 1000;
    var tiers = visibleRows().map(function (r) {
      return { q: parseInt(r.querySelector('[name="tier_qty"]').value, 10), v: parseFloat(r.querySelector('[name="tier_value"]').value) };
    }).filter(function (t) { return t.q >= 2 && t.v > 0; }).sort(function (a, b) { return a.q - b.q; });
    var beyond = checked("beyond"), limit = parseInt((form.querySelector('[name="beyond_qty"]') || {}).value, 10) || null;
    if (beyond === "same") limit = null;
    var html = "";
    if (tiers.length && tiers[0].q > 1) html += "<tr><td>1" + (tiers[0].q > 2 ? "-" + (tiers[0].q - 1) : "") + "</td><td>" + money(base) + "</td></tr>";
    tiers.forEach(function (t, i) {
      var unit = type === "percent" ? base * (1 - t.v / 100) : (type === "amount_off" ? base - t.v : Math.min(base, t.v));
      if (!(unit > 0) || unit >= base) return;
      var end = i + 1 < tiers.length ? tiers[i + 1].q - 1 : limit;
      var rng = end ? (end === t.q ? String(t.q) : t.q + "-" + end) : t.q + "+";
      var pct = Math.round((1 - unit / base) * 100);
      html += "<tr><td>" + rng + "</td><td>" + money(unit) + '<span class="qd-preview__save">-' + pct + "%</span></td></tr>";
    });
    tbody.innerHTML = html || '<tr><td colspan="2">Add a tier to see the prices.</td></tr>';
    var bits = [];
    bits.push(pick ? "Using " + (pick.getAttribute("data-title") || "the first product") + " at " + money(base) + "." : "Example with a " + money(base) + " product.");
    if (beyond === "limit" && limit) bits.push("Orders stop at " + limit + " units.");
    else if ((beyond === "whatsapp" || beyond === "custom") && limit) bits.push("Above " + limit + " units, shoppers get your contact button.");
    else if (beyond === "same") bits.push("Larger orders keep the last tier's price.");
    else bits.push("Choose what happens above your last tier (card 3).");
    if (note) note.textContent = bits.join(" ");
  }

  form.addEventListener("change", function () { paintChoices(); toggleSections(); counts(); preview(); });
  form.addEventListener("input", function (e) { if (e.target.name === "tier_qty" || e.target.name === "tier_value" || e.target.name === "beyond_qty") preview(); });
  paintChoices(); toggleSections(); counts(); paintAdd(); preview();
})();
