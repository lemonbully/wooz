/* Categories tab: mega-menu toggle, Mega-Highlight star, trusted-icons
 * manager. Ported from the reference _categories.html, adapted to OneWooz. */
(function () {
  "use strict";

  // Results surface in the universal toast stack (js/app/toast.js): errors in
  // red, confirmations in green. Falls back to a plain alert if it's missing.
  function owAlert(msg, ok) {
    if (window.OWToast) { if (ok) window.OWToast.success(msg); else window.OWToast.error(msg); }
    else window.alert(msg);
  }

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
  function postForm(url, data) {
    var fd = new FormData();
    Object.keys(data || {}).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, {
      method: "POST", body: fd, credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
  }

  /* ── Confirm destructive submits (shared idiom) ──────────────────────── */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.matches && form.matches("[data-confirm]") && !window.confirm(form.getAttribute("data-confirm"))) {
      e.preventDefault();
    }
  });

  /* ── Mega / Simple menu toggle ───────────────────────────────────────── */
  (function () {
    var toggle = document.getElementById("megaMenuToggle");
    var state = document.getElementById("megaMenuState");
    if (!toggle) return;
    toggle.addEventListener("change", function () {
      var on = toggle.checked;
      if (state) state.textContent = on ? "MEGA" : "Simple";
      postForm(toggle.getAttribute("data-url"), { enabled: on ? "1" : "0" }).then(function (res) {
        if (!res.ok || !res.d.success) { toggle.checked = !on; if (state) state.textContent = !on ? "MEGA" : "Simple"; }
      }).catch(function () { toggle.checked = !on; if (state) state.textContent = !on ? "MEGA" : "Simple"; });
    });
  })();

  /* ── Template 2: feature two categories (Categories page top pair) ──── */
  // Each card's feature button toggles it; the server keeps pick order
  // (1 = left card) and refuses a third, so the UI is redrawn from its reply.
  function renderFeatured(pair) {
    Array.prototype.forEach.call(document.querySelectorAll(".cat-card[data-category-id]"), function (card) {
      var pos = pair.indexOf(card.getAttribute("data-category-id")) + 1;
      var btn = card.querySelector("[data-feature]");
      var badge = card.querySelector("[data-feature-pos]");
      card.classList.toggle("is-featured", pos > 0);
      if (btn) { btn.classList.toggle("is-on", pos > 0); btn.setAttribute("aria-pressed", pos > 0 ? "true" : "false"); }
      if (badge) { badge.textContent = pos > 0 ? String(pos) : ""; badge.hidden = pos === 0; }
    });
    var status = document.getElementById("featureStatus");
    if (status) {
      var text = status.querySelector("[data-feature-text]");
      var dot = status.querySelector(".inv-dot");
      if (text) text.textContent = pair.length ? "Featured " + pair.length + " of 2" : "None featured yet, your first two categories show";
      if (dot) dot.classList.toggle("inv-dot--ok", pair.length > 0);
    }
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-feature]");
    if (!btn || btn.disabled) return;
    e.preventDefault();
    if (btn.getAttribute("aria-busy") === "true") return;
    btn.setAttribute("aria-busy", "true");
    postForm(btn.getAttribute("data-url"), {}).then(function (res) {
      btn.removeAttribute("aria-busy");
      if (!res.ok || !res.d || !res.d.success) {
        owAlert((res.d && res.d.error) || "Couldn't update the featured categories. Please try again.");
        return;
      }
      renderFeatured(res.d.pair || []);
      owAlert(res.d.featured ? "Category featured on your Categories page." : "Category removed from the top of your Categories page.", true);
    }).catch(function () {
      btn.removeAttribute("aria-busy");
      owAlert("Couldn't reach the server. Check your connection and try again.");
    });
  });

  /* ── Mega-Highlight star (single highlight) ──────────────────────────── */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-highlight]");
    if (!btn) return;
    e.preventDefault();
    if (btn.getAttribute("aria-busy") === "true") return;  // ignore double taps

    var wasOn = btn.classList.contains("is-on");
    // Optimistic: reflect the intended state immediately so the star never
    // looks like it "did nothing". Single highlight  clear the others first.
    Array.prototype.forEach.call(document.querySelectorAll("[data-highlight]"), function (b) {
      b.classList.remove("is-on");
    });
    if (!wasOn) btn.classList.add("is-on");
    btn.setAttribute("aria-busy", "true");

    function revert() { btn.classList.toggle("is-on", wasOn); }

    postForm(btn.getAttribute("data-url"), {}).then(function (res) {
      btn.removeAttribute("aria-busy");
      if (!res.ok || !res.d || !res.d.success) {
        // Surface the ACTUAL reason (plan/subscription/permission gates return
        // a specific message) instead of a generic "couldn't update", so a
        // silent refusal is visible rather than looking like a failed save.
        revert();
        owAlert((res.d && res.d.error) || "Couldn't update the highlight. Please try again.");
        return;
      }
      // Reconcile with the server's truth (it may have toggled off).
      btn.classList.toggle("is-on", !!res.d.highlighted);
      owAlert(res.d.highlighted ? "Category highlighted." : "Category highlight removed.", true);
    }).catch(function () {
      btn.removeAttribute("aria-busy");
      revert();
      owAlert("Couldn't reach the server to update the highlight. Check your connection and try again.");
    });
  });

  /* ── Trusted icons manager ───────────────────────────────────────────── */
  (function () {
    var modal = document.getElementById("iconsModal");
    if (!modal) return;
    var openBtn = document.getElementById("manageIconsBtn");
    var rows = document.getElementById("iconRows");
    var empty = document.getElementById("iconsEmpty");
    var addBtn = document.getElementById("addIconRow");
    var saveBtn = document.getElementById("saveIcons");
    var toggle = document.getElementById("trustedIconsToggle");
    var status = document.getElementById("trustedIconsStatus");

    function open() { modal.hidden = false; }
    function close() { modal.hidden = true; }
    function refreshEmpty() { if (empty) empty.hidden = rows.querySelectorAll("[data-icon-row]").length > 0; }

    if (openBtn) openBtn.addEventListener("click", open);
    modal.addEventListener("click", function (e) { if (e.target.closest("[data-icons-close]")) close(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });

    if (addBtn) addBtn.addEventListener("click", function () {
      var row = document.createElement("div");
      row.className = "cat-iconrow";
      row.setAttribute("data-icon-row", "");
      row.innerHTML =
        '<input type="text" class="pal-input pal-input--sm" data-icon-image placeholder="Image URL">' +
        '<input type="text" class="pal-input pal-input--sm" data-icon-label placeholder="Label (optional)">' +
        '<button type="button" class="ord-actbtn ord-actbtn--danger" data-icon-remove aria-label="Remove">×</button>';
      rows.appendChild(row);
      refreshEmpty();
    });

    rows.addEventListener("click", function (e) {
      if (e.target.closest("[data-icon-remove]")) {
        var row = e.target.closest("[data-icon-row]");
        if (row) row.remove();
        refreshEmpty();
      }
    });

    if (toggle) toggle.addEventListener("change", function () {
      var on = toggle.checked;
      postForm(toggle.getAttribute("data-url"), { enabled: on ? "1" : "0" }).then(function (res) {
        if (!res.ok || !res.d.success) { toggle.checked = !on; return; }
        if (status) {
          status.setAttribute("data-on", on ? "1" : "0");
          status.innerHTML = '<span class="inv-dot ' + (on ? "inv-dot--ok" : "") + '"></span>Trusted icons mode ' + (on ? "ON" : "OFF");
        }
      }).catch(function () { toggle.checked = !on; });
    });

    if (saveBtn) saveBtn.addEventListener("click", function () {
      var icons = Array.prototype.map.call(rows.querySelectorAll("[data-icon-row]"), function (r) {
        return {
          image: (r.querySelector("[data-icon-image]").value || "").trim(),
          label: (r.querySelector("[data-icon-label]").value || "").trim(),
        };
      }).filter(function (i) { return i.image; });
      saveBtn.disabled = true;
      fetch(saveBtn.getAttribute("data-url"), {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
        body: JSON.stringify({ icons: icons }),
      }).then(function (r) { return r.json(); })
        .then(function (d) { if (d && d.success) { close(); owAlert("Trusted icons saved.", true); } else owAlert("Couldn't save icons."); })
        .catch(function () { owAlert("Couldn't save icons."); })
        .finally(function () { saveBtn.disabled = false; });
    });
  })();
})();
