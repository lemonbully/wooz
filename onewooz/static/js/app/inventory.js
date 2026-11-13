/* Inventory tab: instant search/filter + Edit-Qty modal with live restock.
 * Ported from the reference _inventory.html, adapted to OneWooz. */
(function () {
  "use strict";

  function cookie(name) {
    try {
      var m = ("; " + document.cookie).split("; " + name + "=");
      if (m.length === 2) return m.pop().split(";").shift();
    } catch (e) {}
    return "";
  }
  function csrf() {
    var i = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return (i && i.value) || cookie("csrftoken") || "";
  }
  function el(id) { return document.getElementById(id); }

  /* ── Search + category filter ────────────────────────────────────────── */
  (function () {
    var search = el("inventory-search");
    var cat = el("inventory-category");
    var tbody = el("inventory-tbody");
    var noMatch = el("inv-no-match");
    if (!tbody) return;
    function apply() {
      var q = (search && search.value || "").trim().toLowerCase();
      var c = (cat && cat.value) || "";
      var shown = 0;
      Array.prototype.forEach.call(tbody.querySelectorAll("tr[data-title]"), function (row) {
        var okQ = !q || (row.getAttribute("data-title") || "").indexOf(q) >= 0 || (row.getAttribute("data-sku") || "").indexOf(q) >= 0;
        var okC = !c || (row.getAttribute("data-category") || "") === c;
        var vis = okQ && okC;
        row.hidden = !vis;
        if (vis) shown++;
      });
      if (noMatch) noMatch.hidden = shown !== 0;
    }
    if (search) search.addEventListener("input", apply);
    if (cat) cat.addEventListener("change", apply);
  })();

  /* ── Edit Qty modal ──────────────────────────────────────────────────── */
  (function () {
    var modal = el("invModal");
    if (!modal) return;
    var qtyEl = el("inventory-restock-qty");
    var endpointEl = el("inventory-restock-endpoint");
    var pidEl = el("inventory-restock-product-id");
    var vkeyEl = el("inventory-restock-variant-key");
    var saveBtn = el("inventory-restock-save");
    var activeRow = null;

    function open() { modal.hidden = false; if (qtyEl) { qtyEl.focus(); } }
    function close() { modal.hidden = true; activeRow = null; }

    document.addEventListener("click", function (e) {
      var trigger = e.target.closest && e.target.closest("[data-restock]");
      if (trigger) {
        e.preventDefault();
        activeRow = trigger.closest("tr");
        endpointEl.value = trigger.getAttribute("data-endpoint") || "";
        pidEl.value = trigger.getAttribute("data-product-id") || "";
        vkeyEl.value = trigger.getAttribute("data-variant-key") || "";
        var badge = activeRow && activeRow.querySelector("[data-inventory-value]");
        qtyEl.value = badge ? badge.getAttribute("data-inventory-value") : "";
        open();
        return;
      }
      if (e.target.closest && e.target.closest("[data-inv-close]")) close();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });

    function paintBadge(total) {
      if (!activeRow) return;
      var badge = activeRow.querySelector("[data-inventory-value]");
      if (!badge) return;
      badge.setAttribute("data-inventory-value", String(total));
      var num = badge.childNodes[badge.childNodes.length - 1];
      if (num) num.textContent = String(total);
      badge.classList.remove("is-out", "is-low", "is-ok");
      badge.classList.add(total <= 0 ? "is-out" : total <= 5 ? "is-low" : "is-ok");
    }

    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        var q = parseInt(qtyEl.value, 10);
        if (isNaN(q) || q < 0) { alert("Enter a valid quantity."); return; }
        saveBtn.disabled = true;
        fetch(endpointEl.value, {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
          body: JSON.stringify({ quantity: q, variant_key: vkeyEl.value || null }),
        })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (d && d.status === "success") { paintBadge(q); close(); }
            else { alert((d && d.message) || "Failed to update inventory."); }
          })
          .catch(function () { alert("Failed to update inventory."); })
          .finally(function () { saveBtn.disabled = false; });
      });
    }
  })();
})();
