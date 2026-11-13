/* Products tab: instant search/filter, storefront toggles, best-seller star,
 * best-sellers reorder + save, subtitle save. Ported from the reference
 * _products.html, adapted to OneWooz endpoints and design classes. */
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
  function postForm(url, data) {
    var fd = new FormData();
    Object.keys(data || {}).forEach(function (k) { fd.append(k, data[k]); });
    return fetch(url, {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
  }
  function postJSON(url, obj) {
    return fetch(url, {
      method: "POST",
      body: JSON.stringify(obj),
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest" },
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
  }

  function setText(selector, value) {
    Array.prototype.forEach.call(document.querySelectorAll(selector), function (el) { el.textContent = value; });
  }

  /* ── Section tabs: All products / Best sellers order / Display settings ──
   * The active section is mirrored into the URL hash (#products,
   * #best-sellers, #settings) with replaceState, so a reload or a shared link
   * reopens the same section without adding Back-button entries. The inline
   * script in the template applies the hash before first paint; this wires
   * clicks, keyboard and later hash changes. */
  (function () {
    var root = document.querySelector(".prd[data-section]");
    if (!root) return;
    var tabs = Array.prototype.slice.call(root.querySelectorAll("[data-prd-tab]"));
    var strip = root.querySelector(".prd-tabs");
    var names = tabs.map(function (t) { return t.getAttribute("data-prd-tab"); });
    var aliases = { all: "products", best: "best-sellers" };

    function fromHash() {
      var h = (location.hash || "").replace("#", "");
      h = aliases[h] || h;
      return names.indexOf(h) >= 0 ? h : "products";
    }
    function show(name, opts) {
      opts = opts || {};
      if (names.indexOf(name) < 0) name = "products";
      root.setAttribute("data-section", name);
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-prd-tab") === name;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.setAttribute("tabindex", on ? "0" : "-1");
      });
      if (opts.updateUrl && window.history && history.replaceState) {
        try { history.replaceState(null, "", name === "products" ? location.pathname + location.search : "#" + name); } catch (e) {}
      }
      // A switch triggered from further down the page brings the strip back
      // into view (below the sticky top bar) instead of leaving the owner mid-page.
      if (opts.reveal && strip) {
        var top = strip.getBoundingClientRect().top;
        if (top < 0) window.scrollTo({ top: window.pageYOffset + top - 80, behavior: "smooth" });
      }
    }

    tabs.forEach(function (t, i) {
      t.addEventListener("click", function (e) {
        e.preventDefault();
        show(t.getAttribute("data-prd-tab"), { updateUrl: true });
      });
      t.addEventListener("keydown", function (e) {
        var k = e.key, j = -1;
        if (k === "ArrowRight") j = (i + 1) % tabs.length;
        else if (k === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
        else if (k === "Home") j = 0;
        else if (k === "End") j = tabs.length - 1;
        if (j < 0) return;
        e.preventDefault();
        tabs[j].focus();
        show(tabs[j].getAttribute("data-prd-tab"), { updateUrl: true });
      });
    });
    // In-page shortcuts ("Go to All products", "How it works" links).
    document.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("[data-prd-goto]");
      if (!a) return;
      e.preventDefault();
      show(a.getAttribute("data-prd-goto"), { updateUrl: true, reveal: true });
    });
    window.addEventListener("hashchange", function () { show(fromHash()); });
    show(fromHash());
  })();

  /* ── Instant search + category filter + quick status chips ───────────── */
  var refilter = function () {};
  (function () {
    var search = document.getElementById("manage-products-search");
    var cat = document.getElementById("manage-products-category");
    var tbody = document.getElementById("manage-products-tbody");
    var noMatch = document.getElementById("prd-no-match");
    var countEl = document.getElementById("prd-filter-count");
    var quickBtns = Array.prototype.slice.call(document.querySelectorAll("[data-prd-quick]"));
    var quick = "";
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.querySelectorAll("tr[data-title]"));
    function norm(s) { return String(s || "").toLowerCase().replace(/\s+/g, " ").trim(); }
    // Live: runs on every keystroke / category change, so there is no search
    // button. Every word typed must appear somewhere in the product's name, SKU
    // or category names; the category filter matches the primary category or
    // any additional one; the quick chips narrow to Active / Draft / starred.
    function apply() {
      var words = norm(search && search.value).split(" ").filter(Boolean);
      var c = (cat && cat.value) || "";
      var shown = 0;
      rows.forEach(function (row) {
        var hay = norm(row.getAttribute("data-search") || row.getAttribute("data-title"));
        var okQ = words.every(function (w) { return hay.indexOf(w) >= 0; });
        var cats = (row.getAttribute("data-categories") || row.getAttribute("data-category") || "").split(/\s+/);
        var okC = !c || cats.indexOf(c) >= 0;
        var okS = !quick ||
          (quick === "best" ? row.getAttribute("data-best") === "1" : row.getAttribute("data-status") === quick);
        var vis = okQ && okC && okS;
        row.hidden = !vis;
        if (vis) shown++;
      });
      var filtering = words.length > 0 || !!c || !!quick;
      if (noMatch) noMatch.hidden = !(filtering && shown === 0 && rows.length > 0);
      if (countEl) {
        countEl.hidden = !filtering;
        countEl.textContent = "Showing " + shown + " of " + rows.length;
      }
    }
    refilter = apply;
    if (search) {
      search.addEventListener("input", apply);
      search.addEventListener("search", apply);  // the field's own clear (×) button
      // No form to submit, but keep Enter from doing anything unexpected.
      search.addEventListener("keydown", function (e) { if (e.key === "Enter") e.preventDefault(); });
    }
    if (cat) cat.addEventListener("change", apply);
    quickBtns.forEach(function (b) {
      b.addEventListener("click", function () {
        quick = b.getAttribute("data-prd-quick") || "";
        quickBtns.forEach(function (o) {
          var on = o === b;
          o.classList.toggle("is-active", on);
          o.setAttribute("aria-pressed", on ? "true" : "false");
        });
        apply();
      });
    });
    apply();
  })();

  /* ── Storefront presentation toggles ─────────────────────────────────── */
  function countFlags() {
    setText("[data-flags-on]", document.querySelectorAll(".prd-flag:checked").length);
  }
  Array.prototype.forEach.call(document.querySelectorAll(".prd-flag"), function (input) {
    input.addEventListener("change", function () {
      var on = input.checked;
      var status = input.closest(".prd-setting").querySelector("[data-status]");
      if (status) status.textContent = on ? "Enabled" : "Disabled";
      countFlags();
      function revert() {
        input.checked = !on;
        if (status) status.textContent = !on ? "Enabled" : "Disabled";
        countFlags();
      }
      postForm(input.getAttribute("data-url"), { enabled: on ? "1" : "0" })
        .then(function (res) { if (!res.ok || !res.d.success) revert(); })
        .catch(revert);
    });
  });

  /* ── Best-seller star toggle ─────────────────────────────────────────── */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-best-seller]");
    if (!btn) return;
    e.preventDefault();
    postForm(btn.getAttribute("data-url"), {})
      .then(function (res) {
        if (!res.ok || !res.d.success) { alert((res.d && res.d.error) || "Unable to update Best Seller."); return; }
        var on = !!res.d.is_best_seller;
        btn.classList.toggle("is-on", on);
        btn.title = on ? "Unmark Best Seller" : "Mark as Best Seller";
        var row = btn.closest("tr");
        if (row) row.setAttribute("data-best", on ? "1" : "0");
        setText("[data-best-starred]", document.querySelectorAll('#manage-products-tbody tr[data-best="1"]').length);
        // Keep the Best Sellers reorder list in step: add/remove this product live.
        if (window.OneWoozBestSellers) window.OneWoozBestSellers.sync(btn, on);
        refilter();
      })
      .catch(function () { alert("Unable to update Best Seller."); });
  });

  /* ── Confirm destructive submits ─────────────────────────────────────── */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.matches && form.matches("[data-confirm]")) {
      if (!window.confirm(form.getAttribute("data-confirm"))) e.preventDefault();
    }
  });

  /* ── B2B enquiry form mode (auto-save on change) ─────────────────────── */
  (function () {
    var card = document.getElementById("enquiryModeCard");
    if (!card) return;
    var feedback = document.getElementById("enqModeFeedback");
    card.addEventListener("change", function (e) {
      var input = e.target;
      if (!input || input.name !== "enquiry_form_mode") return;
      Array.prototype.forEach.call(card.querySelectorAll(".prd-enqmode"), function (l) {
        l.classList.toggle("is-on", l.contains(input));
      });
      postForm(card.getAttribute("data-url"), { mode: input.value }).then(function (res) {
        if (feedback) {
          feedback.hidden = false;
          feedback.textContent = res.ok && res.d.success ? "Saved" : "Failed";
          feedback.className = "prd-feedback " + (res.ok && res.d.success ? "is-ok" : "is-err");
          setTimeout(function () { feedback.hidden = true; }, 1800);
        }
      }).catch(function () {});
    });
  })();

  /* ── Best Sellers reorder + save + subtitle ──────────────────────────── */
  (function () {
    var list = document.getElementById("bestSellersOrderList");
    var emptyEl = document.getElementById("bestSellersEmpty");
    var reorderBtn = document.getElementById("bestSellersReorderToggle");
    var resetBtn = document.getElementById("bestSellersResetBtn");
    var saveBtn = document.getElementById("bestSellersSaveBtn");
    var dirtyEl = document.getElementById("bestSellersDirty");
    var reorder = false;
    var dirtyTimer = null;

    function readDefault() {
      try { return JSON.parse(document.getElementById("bestSellersDefaultOrderJson").textContent) || []; }
      catch (e) { return []; }
    }
    function items() { return list ? Array.prototype.slice.call(list.querySelectorAll(".prd-best-item")) : []; }
    function currentOrder() { return items().map(function (li) { return li.getAttribute("data-product-id"); }); }
    // The order the storefront is showing right now. Starring / unstarring
    // adjusts it in place (a newly starred product lands last on the
    // storefront too), so only a manual move counts as unsaved.
    var savedOrder = readDefault();
    function updateDirty() {
      if (!dirtyEl || dirtyEl.classList.contains("is-ok")) return;
      dirtyEl.hidden = currentOrder().join(",") === savedOrder.join(",");
    }
    function flashSaved() {
      if (!dirtyEl) return;
      clearTimeout(dirtyTimer);
      dirtyEl.textContent = "Order saved";
      dirtyEl.classList.add("is-ok");
      dirtyEl.hidden = false;
      dirtyTimer = setTimeout(function () {
        dirtyEl.classList.remove("is-ok");
        dirtyEl.textContent = "Unsaved order";
        updateDirty();
      }, 2000);
    }
    function renumber() {
      items().forEach(function (li, i) {
        var r = li.querySelector(".prd-best-rank");
        if (r) r.textContent = i + 1;
      });
      setText("[data-best-count]", items().length);
      updateDirty();
    }
    function setMode(on) {
      reorder = on;
      if (reorderBtn) {
        var label = reorderBtn.querySelector(".reorder-label");
        if (label) label.textContent = on ? "Done" : "Reorder";
        reorderBtn.classList.toggle("is-on", on);
      }
      items().forEach(function (li) {
        var h = li.querySelector(".prd-best-handle");
        var up = li.querySelector(".prd-best-up");
        var down = li.querySelector(".prd-best-down");
        if (h) h.hidden = !on;
        if (up) up.hidden = !on;
        if (down) down.hidden = !on;
        li.draggable = on;
        li.classList.toggle("is-reordering", on);
      });
    }

    // Open slots up to the cap, drawn under the ranked rows so the owner sees
    // capacity at a glance (5 ranked + 3 open = 8). Outside the sortable list.
    var slotsEl = document.getElementById("bestSellersSlots");
    function renderSlots() {
      if (!slotsEl) return;
      var max = parseInt(slotsEl.getAttribute("data-max"), 10) || 8;
      var n = items().length;
      slotsEl.innerHTML = "";
      slotsEl.hidden = n === 0 || n >= max;
      for (var k = n + 1; k <= max; k++) {
        var li = document.createElement("li"); li.className = "prd-best-slot";
        var rank = document.createElement("span"); rank.className = "prd-best-rank"; rank.textContent = k;
        var txt = document.createElement("span"); txt.textContent = "Open slot";
        li.appendChild(rank); li.appendChild(txt);
        if (k === n + 1) {
          var a = document.createElement("a");
          a.className = "prd-link"; a.href = "#products"; a.setAttribute("data-prd-goto", "products");
          a.textContent = "Star a product";
          li.appendChild(a);
        }
        slotsEl.appendChild(li);
      }
    }
    // Show the list vs the "No Best Sellers yet" placeholder based on count.
    function refreshEmpty() {
      var has = items().length > 0;
      if (list) list.hidden = !has;
      if (emptyEl) emptyEl.hidden = has;
      renderSlots();
    }
    // Build a reorder row matching the server-rendered markup.
    function buildItem(pid, title, thumbSrc, isActive) {
      var li = document.createElement("li");
      li.className = "prd-best-item"; li.setAttribute("data-product-id", pid);
      var h = document.createElement("span"); h.className = "prd-best-handle"; h.title = "Drag to reorder"; h.hidden = true; h.textContent = "⋮⋮";
      var rank = document.createElement("span"); rank.className = "prd-best-rank";
      var thumb = document.createElement("span"); thumb.className = "ord-thumb";
      if (thumbSrc) { var img = document.createElement("img"); img.src = thumbSrc; img.alt = ""; thumb.appendChild(img); }
      var info = document.createElement("span"); info.className = "prd-best-info";
      var name = document.createElement("span"); name.className = "prd-best-name"; name.textContent = title || "Untitled";
      var state = document.createElement("span"); state.className = "prd-best-state" + (isActive ? " is-active" : ""); state.textContent = isActive ? "Active" : "Draft";
      info.appendChild(name); info.appendChild(state);
      var moves = document.createElement("span"); moves.className = "prd-best-moves";
      var up = document.createElement("button"); up.type = "button"; up.className = "prd-best-up ord-actbtn"; up.hidden = true; up.setAttribute("aria-label", "Move up"); up.textContent = "↑";
      var down = document.createElement("button"); down.type = "button"; down.className = "prd-best-down ord-actbtn"; down.hidden = true; down.setAttribute("aria-label", "Move down"); down.textContent = "↓";
      moves.appendChild(up); moves.appendChild(down);
      li.appendChild(h); li.appendChild(rank); li.appendChild(thumb); li.appendChild(info); li.appendChild(moves);
      return li;
    }
    // Add / remove a product's reorder row live when its Best Seller ★ is toggled.
    function syncItem(starBtn, isBestSeller) {
      if (!list || !starBtn) return;
      var pid = starBtn.getAttribute("data-product-id"); if (!pid) return;
      var existing = list.querySelector('.prd-best-item[data-product-id="' + pid + '"]');
      if (isBestSeller) {
        if (savedOrder.indexOf(pid) < 0) savedOrder.push(pid);
      } else {
        savedOrder = savedOrder.filter(function (id) { return id !== pid; });
      }
      if (isBestSeller) {
        if (!existing) {
          var row = starBtn.closest("tr");
          var titleEl = row ? row.querySelector(".app-table__strong") : null;
          var thumbImg = row ? row.querySelector("img.ord-thumb") : null;
          var active = row ? !!row.querySelector(".app-badge--active") : true;
          list.appendChild(buildItem(pid, titleEl ? titleEl.textContent.trim() : "", thumbImg ? thumbImg.getAttribute("src") : "", active));
        }
      } else if (existing) {
        existing.remove();
      }
      renumber();
      refreshEmpty();
      if (reorder) setMode(true); // keep the drag handle / draggable on the new row
    }
    // Exposed so the ★ toggle handler above can keep the reorder list in step.
    window.OneWoozBestSellers = { sync: syncItem };
    refreshEmpty();

    if (reorderBtn) reorderBtn.addEventListener("click", function () { setMode(!reorder); });

    if (list) {
      list.addEventListener("click", function (e) {
        var up = e.target.closest(".prd-best-up");
        var down = e.target.closest(".prd-best-down");
        if (!up && !down) return;
        var li = e.target.closest(".prd-best-item");
        if (!li) return;
        if (up && li.previousElementSibling) list.insertBefore(li, li.previousElementSibling);
        if (down && li.nextElementSibling) list.insertBefore(li.nextElementSibling, li);
        renumber();
      });

      // Drag & drop reorder.
      var dragging = null;
      list.addEventListener("dragstart", function (e) {
        var li = e.target.closest(".prd-best-item");
        if (!li || !reorder) return;
        dragging = li;
        li.classList.add("is-dragging");
      });
      list.addEventListener("dragend", function () {
        if (dragging) dragging.classList.remove("is-dragging");
        dragging = null;
        renumber();
      });
      list.addEventListener("dragover", function (e) {
        if (!dragging) return;
        e.preventDefault();
        var after = null;
        items().forEach(function (li) {
          if (li === dragging) return;
          var box = li.getBoundingClientRect();
          if (e.clientY < box.top + box.height / 2 && !after) after = li;
        });
        if (after) list.insertBefore(dragging, after);
        else list.appendChild(dragging);
      });
    }

    if (resetBtn && list) {
      resetBtn.addEventListener("click", function () {
        var def = readDefault();
        var byId = {};
        items().forEach(function (li) { byId[li.getAttribute("data-product-id")] = li; });
        def.forEach(function (id) { if (byId[id]) list.appendChild(byId[id]); });
        renumber();
      });
    }

    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        var order = currentOrder();
        saveBtn.disabled = true;
        postJSON(saveBtn.getAttribute("data-url"), { order: order })
          .then(function (res) {
            if (!res.ok || !res.d.success) { alert("Couldn't save order."); return; }
            savedOrder = (res.d.order || order).slice();
            flashSaved();
          })
          .catch(function () { alert("Couldn't save order."); })
          .finally(function () { saveBtn.disabled = false; setMode(false); });
      });
    }

    var subInput = document.getElementById("best-sellers-subtitle-input");
    var subSave = document.getElementById("best-sellers-subtitle-save");
    var subFeedback = document.getElementById("best-sellers-subtitle-feedback");
    if (subSave && subInput) {
      subSave.addEventListener("click", function () {
        postForm(subSave.getAttribute("data-url"), { subtitle: subInput.value })
          .then(function (res) {
            if (subFeedback) {
              subFeedback.hidden = false;
              subFeedback.textContent = res.ok && res.d.success ? "Saved" : "Failed";
              subFeedback.className = "prd-feedback " + (res.ok && res.d.success ? "is-ok" : "is-err");
              setTimeout(function () { subFeedback.hidden = true; }, 2000);
            }
          })
          .catch(function () {});
      });
    }
  })();
})();
