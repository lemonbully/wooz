/* Orders tab: auto-ship toggle, select-all, courier sync, tracking popup,
 * the Ready to Ship "Generate & Request Pickup" modal and label reprints.
 * Ported from the reference _orders.html inline scripts, adapted to OneWooz's
 * app-dlg modal and store endpoints. No inline handlers, so it loads under CSP
 * without a nonce. */
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
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = String(s == null ? "" : s);
    return d.innerHTML;
  }
  function el(id) {
    return document.getElementById(id);
  }

  /* ── Auto-create-shipping toggle ─────────────────────────────────────── */
  (function () {
    var btn = el("autoShipToggle");
    var msg = el("autoShipMsg");
    if (!btn) return;

    function setMsg(text, ok) {
      if (!msg) return;
      msg.textContent = text || "";
      msg.hidden = !text;
      msg.className = "ord-autoship__msg" + (ok ? " is-ok" : text ? " is-err" : "");
      if (text) setTimeout(function () { msg.hidden = true; }, 2500);
    }
    function paint(on) {
      btn.setAttribute("aria-checked", on ? "true" : "false");
      btn.classList.toggle("is-on", !!on);
    }
    btn.addEventListener("click", function () {
      if (btn.hasAttribute("disabled")) return;
      var current = btn.getAttribute("aria-checked") === "true";
      var next = !current;
      paint(next);
      var fd = new FormData();
      fd.append("enabled", next ? "1" : "0");
      fetch(btn.getAttribute("data-url"), {
        method: "POST",
        body: fd,
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d || !d.success) { paint(current); setMsg("Couldn't update. Try again.", false); return; }
          setMsg(next ? "Auto-ship enabled" : "Auto-ship disabled", true);
          // The Pending tab disappears when auto-ship is on; reload to re-render.
          setTimeout(function () { window.location.reload(); }, 400);
        })
        .catch(function () { paint(current); setMsg("Couldn't update. Check connection.", false); });
    });
  })();

  /* ── Select-all (checkboxes join the bulk form via form="") ──────────── */
  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.id === "ordSelectAll") {
      Array.prototype.forEach.call(document.querySelectorAll(".ord-row-check"), function (cb) {
        cb.checked = !!t.checked;
      });
      return;
    }
    if (t && t.classList && t.classList.contains("ord-row-check")) {
      var all = document.getElementById("ordSelectAll");
      if (!all) return;
      var boxes = Array.prototype.slice.call(document.querySelectorAll(".ord-row-check"));
      var anyOn = boxes.some(function (cb) { return cb.checked; });
      var allOn = boxes.length > 0 && boxes.every(function (cb) { return cb.checked; });
      all.indeterminate = anyOn && !allOn;
      all.checked = allOn;
    }
  });

  /* ── Per-order "Sync status" button ──────────────────────────────────── */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-sync-order]");
    if (!btn) return;
    e.preventDefault();
    var url = btn.getAttribute("data-sync-url");
    if (!url) return;
    var orig = btn.innerHTML;
    btn.disabled = true;
    btn.classList.add("is-busy");
    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.success) {
          // Reload only when the status moved, so the order lands in its new tab.
          if (d.changed) { window.location.reload(); return; }
          alert(d.message || "Already up to date.");
        } else {
          alert("Sync failed: " + ((d && d.error) || "Unknown error"));
        }
      })
      .catch(function () { alert("Sync request failed. Check your connection."); })
      .finally(function () { btn.disabled = false; btn.classList.remove("is-busy"); btn.innerHTML = orig; });
  });

  /* ── Shipment tracking modal ─────────────────────────────────────────── */
  (function () {
    function open() { var m = el("ordTrackModal"); if (m) m.hidden = false; }
    function close() { var m = el("ordTrackModal"); if (m) m.hidden = true; }

    function show(which) {
      ["trkLoader", "trkError", "trkContent"].forEach(function (id) {
        var n = el(id); if (n) n.hidden = id !== which;
      });
    }
    function showError(msg) { show("trkError"); var m = el("trkErrorMsg"); if (m) m.textContent = msg || ""; }

    function statusMeta(raw) {
      var s = (raw || "").toString().toUpperCase();
      if (s.indexOf("DELIVERED") >= 0) return "app-badge--delivered";
      if (s.indexOf("OUT FOR DELIVERY") >= 0) return "app-badge--warn";
      if (s.indexOf("IN TRANSIT") >= 0 || s.indexOf("PICKED") >= 0) return "app-badge--shipped";
      if (s.indexOf("RTO") >= 0 || s.indexOf("RETURN") >= 0) return "app-badge--warn";
      if (s.indexOf("CANCEL") >= 0 || s.indexOf("FAIL") >= 0) return "app-badge--danger";
      return "app-badge--muted";
    }
    function fmtDate(raw) {
      if (!raw) return "";
      try {
        var d = new Date(String(raw).replace(" ", "T"));
        if (isNaN(d)) return raw;
        return d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
      } catch (e) { return raw; }
    }
    function renderTimeline(scans) {
      var tl = el("trkTimeline");
      if (!tl) return;
      if (!scans || !scans.length) { tl.innerHTML = '<li class="ord-trk-empty">No scan events recorded.</li>'; return; }
      var sorted = scans.slice().sort(function (a, b) { return new Date(b.date) - new Date(a.date); });
      tl.innerHTML = sorted.map(function (scan, i) {
        var latest = i === 0;
        var label = (scan["sr-status-label"] || scan.status || "").toString();
        var activity = scan.activity && scan.activity !== "NA" ? scan.activity : label;
        var loc = scan.location || "";
        return (
          '<li class="ord-trk-step' + (latest ? " is-latest" : "") + '">' +
          '<span class="ord-trk-dot"></span>' +
          '<div class="ord-trk-step__body">' +
          '<div class="ord-trk-step__row"><p class="ord-trk-step__act">' + esc(activity) + "</p>" +
          (latest ? '<span class="app-badge ' + statusMeta(label) + '">' + esc(label) + "</span>" : "") +
          "</div>" +
          (loc ? '<p class="ord-trk-step__loc">' + esc(loc) + "</p>" : "") +
          '<p class="ord-trk-step__date">' + fmtDate(scan.date) + "</p>" +
          "</div></li>"
        );
      }).join("");
    }
    function render(data) {
      var p = data.payload || {};
      var status = p.current_status || p.shipment_status || "";
      var badge = el("trkBadge");
      if (el("trkStatus")) el("trkStatus").textContent = status || "";
      if (badge) { badge.textContent = status || ""; badge.className = "app-badge " + statusMeta(status); }
      if (el("trkAwb")) el("trkAwb").textContent = p.awb || "";
      if (el("trkCourier")) el("trkCourier").textContent = p.courier_name || data.provider || "";
      if (el("trkEtd")) el("trkEtd").textContent = p.etd ? fmtDate(p.etd) : "";
      if (el("trkUpdated")) el("trkUpdated").textContent = data.received_at || "";
      var link = el("trkLink");
      if (link) {
        var turl = String(p.track_url || "");
        if (/^https:\/\//i.test(turl)) { link.href = turl; link.hidden = false; }
        else { link.removeAttribute("href"); link.hidden = true; }
      }
      renderTimeline(p.scans || []);
      show("trkContent");
    }
    function load(orderId, url) {
      if (el("trkOrderId")) el("trkOrderId").textContent = orderId;
      if (el("trkLink")) el("trkLink").hidden = true;
      show("trkLoader");
      open();
      fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
        .then(function (res) {
          if (!res.ok || !res.d.success) { showError(res.d.error || "Could not fetch tracking data."); return; }
          render(res.d);
        })
        .catch(function () { showError("Network error. Please try again."); });
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("[data-track-order]");
      if (btn) {
        e.preventDefault();
        load(btn.getAttribute("data-track-order"), btn.getAttribute("data-tracking-url"));
        return;
      }
      if (e.target.closest && e.target.closest("[data-track-close]")) close();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  })();

  /* ── Ready to Ship: "Generate & Request Pickup" ──────────────────────────
   * One modal collects the courier's pickup details (Shiprocket: courier +
   * date; Delhivery: date, time, warehouse, packages) and the label format.
   * Submit POSTs both together: the server schedules the pickup, then streams
   * the labels back as a PDF with an X-Redirect header naming the tab the
   * orders moved to. A failed pickup comes back as JSON and is shown inline,
   * leaving every order in Ready to Ship. */
  (function () {
    var modal = el("ordShipModal");
    var form = el("ordShipForm");
    if (!modal || !form) return;

    function openModal() { modal.hidden = false; }
    function closeModal() { modal.hidden = true; showError(""); }
    function showError(text) {
      var box = el("ordShipError");
      if (!box) return;
      box.textContent = text || "";
      box.hidden = !text;
      if (text && box.scrollIntoView) {
        try { box.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (e) { box.scrollIntoView(); }
      }
    }
    function selectedIds() {
      return Array.prototype.slice
        .call(document.querySelectorAll(".ord-row-check"))
        .filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.value; });
    }
    function downloadBlob(blob, filename) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = filename || "labels.pdf";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1500);
    }
    function isoDay(d) {
      return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    }
    // Pickup date rules (the server enforces the same): from 12:00 noon the
    // earliest pickup is tomorrow, and never more than 7 days ahead. The page
    // may have been opened before noon, so the window is re-derived on open.
    function prepPickupDate() {
      var dateEl = el("ordShipDate");
      if (!dateEl) return;
      var now = new Date();
      var earliest = new Date(now.getFullYear(), now.getMonth(), now.getDate() + (now.getHours() >= 12 ? 1 : 0));
      var latest = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 7);
      var min = isoDay(earliest);
      var max = isoDay(latest);
      // Never loosen the server's window, only tighten a stale one.
      if (!dateEl.min || dateEl.min < min) dateEl.min = min;
      if (!dateEl.max || dateEl.max > max) dateEl.max = max;
      if (!dateEl.value || dateEl.value < dateEl.min || dateEl.value > dateEl.max) dateEl.value = dateEl.min;
      var hint = el("ordShipDateHint");
      if (hint && now.getHours() >= 12) {
        hint.textContent = "It's past 12:00 noon, so the earliest pickup is tomorrow. Pickups can be scheduled up to 7 days ahead.";
      }
    }

    /* ── Per-order courier & rate picker (Shiprocket) ─────────────────────
     * Shiprocket prices one pickup → delivery pincode pair at a time, by the
     * parcel's weight and payment type. So every selected order is quoted on
     * its own route and gets its own courier <select name="courier_for_<id>">.
     * The mode pills fill those selects (cheapest / fastest / Shiprocket's
     * pick / one courier that serves every order); touching a row by hand
     * switches to a custom mix. The total is the sum of the chosen rates. */
    var rates = { data: null, cur: "₹", seq: 0 };

    // Selects are enhanced by the global custom-select component; after their
    // options or value change in code, it must re-read them.
    function refreshCustom(sel) {
      if (window.OwSelect && window.OwSelect.rebuild) {
        try { window.OwSelect.rebuild(sel); } catch (e) {}
      }
    }
    function money(v) {
      if (v === null || v === undefined || isNaN(Number(v))) return "";
      var n = Number(v);
      var s = n % 1 === 0 ? String(n) : n.toFixed(2);
      // "₹78" for a symbol, "INR 78" for a code.
      return rates.cur + (/^[A-Za-z]+$/.test(rates.cur) ? " " : "") + s;
    }
    function daysText(d) {
      if (d === null || d === undefined || d === "") return "";
      return d + (Number(d) === 1 ? " day" : " days");
    }
    function payClass(kind) {
      return { cod: "ord-pay--cod", partial: "ord-pay--partial", credit: "ord-pay--credit" }[kind] || "ord-pay--prepaid";
    }
    function courierOption(c) {
      var bits = [c.name, money(c.rate)];
      if (c.days !== null && c.days !== undefined) bits.push(daysText(c.days));
      if (c.cod_charges) bits.push("incl. " + money(c.cod_charges) + " COD fee");
      return '<option value="' + esc(String(c.id)) + '">' + esc(bits.filter(Boolean).join(" · ")) + "</option>";
    }
    function rowById(id) {
      var list = (rates.data && rates.data.orders) || [];
      for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
      return null;
    }
    function rowSelects() {
      return Array.prototype.slice.call(document.querySelectorAll("#ordRatesList select[data-rate-order]"));
    }

    function renderSkeleton(n) {
      var list = el("ordRatesList");
      if (!list) return;
      var html = "";
      for (var i = 0; i < Math.min(n, 4); i++) {
        html += '<div class="ord-rate ord-rate--skel" aria-hidden="true">' +
          '<div class="ord-rate__who"><div class="ord-rate__bar ord-rate__bar--short"></div><div class="ord-rate__bar"></div></div>' +
          '<div class="ord-rate__pick"><div class="ord-rate__bar"></div></div></div>';
      }
      list.innerHTML = html + '<p class="ord-rate__note ord-rate__msg">Fetching live rates for ' +
        n + (n === 1 ? " order" : " orders") + "…</p>";
    }

    function renderRows() {
      var list = el("ordRatesList");
      var data = rates.data;
      if (!list || !data) return;
      list.innerHTML = data.orders.map(function (o) {
        var dest = [o.city, o.state].filter(Boolean).join(", ") + (o.pincode ? " · " + o.pincode : "");
        var pay = '<span class="ord-pay ' + payClass(o.payment_kind) + '">' + esc(o.payment) +
          (o.cod && o.collect ? " " + esc(money(o.collect)) : "") + "</span>";
        var meta = [o.weight ? o.weight + " kg" : "", o.customer].filter(Boolean).map(esc).join(" · ");
        var who = '<div class="ord-rate__who">' +
          '<div class="ord-rate__line"><span class="ord-rate__num">#' + esc(o.number) + "</span>" + pay + "</div>" +
          '<span class="ord-rate__dest">' + esc(dest || "No delivery address") + "</span>" +
          (meta ? '<span class="ord-rate__meta">' + meta + "</span>" : "") + "</div>";
        var pick;
        if (o.assigned) {
          pick = '<div class="ord-rate__note ord-rate__note--ok">Courier already assigned: ' + esc(o.assigned) +
            (o.awb ? " (AWB " + esc(o.awb) + ")" : "") + "</div>";
        } else if (!o.couriers.length) {
          pick = '<div class="ord-rate__note ord-rate__note--warn">' + esc(o.error || "No live rates for this route.") +
            " Shiprocket will try its own courier when booking.</div>";
        } else {
          pick = '<div class="ord-rate__pick">' +
            '<select class="pal-select pal-select--sm" name="courier_for_' + esc(o.id) + '" data-rate-order="' + esc(o.id) +
            '" aria-label="Courier for order #' + esc(o.number) + '">' +
            '<option value="">Shiprocket recommended (priced at booking)</option>' +
            o.couriers.map(courierOption).join("") + "</select>" +
            '<span class="ord-rate__price" data-rate-price="' + esc(o.id) + '"></span></div>';
        }
        return '<div class="ord-rate">' + who + pick + "</div>";
      }).join("");
    }

    function renderSame() {
      var data = rates.data;
      var sel = el("ordRatesSameSel");
      var note = el("ordRatesSameNote");
      var modeEl = document.querySelector('input[name="courier_mode"][value="same"]');
      var open = data ? data.orders.filter(function (o) { return !o.assigned; }) : [];
      var common = (data && data.common) || [];
      var wrap = el("ordRatesSameMode");
      if (wrap) wrap.hidden = open.length < 2;
      if (modeEl) modeEl.disabled = !common.length;
      if (wrap) wrap.title = common.length ? "" : "No single courier serves every selected order";
      if (!sel) return;
      sel.innerHTML = common.map(function (c) {
        var bits = [c.name, c.total !== null ? money(c.total) + " total" : "", c.max_days ? "up to " + daysText(c.max_days) : ""];
        return '<option value="' + esc(String(c.id)) + '">' + esc(bits.filter(Boolean).join(" · ")) + "</option>";
      }).join("");
      refreshCustom(sel);
      if (note) note.textContent = common.length
        ? "Only couriers that serve every selected order's pincode (and collect COD where needed) are listed."
        : "";
    }

    function pickFor(o, mode) {
      if (!o || !o.couriers.length) return "";
      if (mode === "cheapest") return String(o.couriers[0].id); // server sorts by rate
      if (mode === "fastest") {
        var best = null;
        o.couriers.forEach(function (c) {
          if (c.days === null || c.days === undefined) return;
          if (!best || c.days < best.days || (c.days === best.days && (c.rate || 0) < (best.rate || 0))) best = c;
        });
        return String((best || o.couriers[0]).id);
      }
      if (mode === "same") {
        var sameSel = el("ordRatesSameSel");
        var want = sameSel ? sameSel.value : "";
        for (var i = 0; i < o.couriers.length; i++) if (String(o.couriers[i].id) === want) return want;
        return "";
      }
      return ""; // auto: Shiprocket recommends at booking
    }

    function applyMode(mode) {
      var same = el("ordRatesSame");
      if (same) same.hidden = mode !== "same";
      rowSelects().forEach(function (sel) {
        sel.value = pickFor(rowById(sel.getAttribute("data-rate-order")), mode);
        refreshCustom(sel);
      });
      updateTotals();
    }

    function updateTotals() {
      var data = rates.data;
      var box = el("ordRatesTotal");
      if (!data || !box) return;
      var sum = 0, priced = 0, unpriced = 0, slowest = null, codFees = 0;
      data.orders.forEach(function (o) {
        if (o.assigned) return;
        var sel = document.querySelector('#ordRatesList select[data-rate-order="' + o.id + '"]');
        var chosen = null;
        if (sel && sel.value) {
          for (var i = 0; i < o.couriers.length; i++) if (String(o.couriers[i].id) === sel.value) chosen = o.couriers[i];
        }
        var priceEl = document.querySelector('[data-rate-price="' + o.id + '"]');
        if (chosen && chosen.rate !== null && chosen.rate !== undefined) {
          sum += Number(chosen.rate); priced += 1;
          if (chosen.cod_charges) codFees += Number(chosen.cod_charges);
          if (chosen.days !== null && chosen.days !== undefined) slowest = slowest === null ? chosen.days : Math.max(slowest, chosen.days);
          if (priceEl) priceEl.innerHTML = esc(money(chosen.rate)) + (chosen.days !== null && chosen.days !== undefined ? "<small>" + esc(daysText(chosen.days)) + "</small>" : "");
        } else {
          unpriced += 1;
          if (priceEl) priceEl.innerHTML = "<small>at booking</small>";
        }
      });
      var assigned = data.orders.filter(function (o) { return o.assigned; }).length;
      var notes = [];
      if (priced) notes.push(priced + (priced === 1 ? " order" : " orders") + " priced" + (slowest !== null ? ", slowest arrives in " + daysText(slowest) : ""));
      if (codFees) notes.push("includes " + money(Math.round(codFees * 100) / 100) + " in COD fees");
      if (unpriced) notes.push(unpriced + (unpriced === 1 ? " order is" : " orders are") + " priced by Shiprocket when booked");
      if (assigned) notes.push(assigned + (assigned === 1 ? " order already has" : " orders already have") + " a courier");
      el("ordRatesTotalValue").textContent = priced ? money(Math.round(sum * 100) / 100) : "Decided at booking";
      el("ordRatesTotalNote").textContent = notes.length ? notes.join("; ") + ". Shiprocket deducts the final amount from your wallet." : "";
      box.hidden = false;
    }

    function currentMode() {
      var m = document.querySelector('input[name="courier_mode"]:checked');
      return m ? m.value : "";
    }

    function loadRates(ids) {
      var wrap = el("ordRates");
      if (!wrap) return; // preferred courier set in settings: no picker rendered
      var seq = ++rates.seq;
      rates.data = null;
      var total = el("ordRatesTotal");
      if (total) total.hidden = true;
      if (el("ordRatesOrigin")) el("ordRatesOrigin").textContent = "";
      if (el("ordRatesSame")) el("ordRatesSame").hidden = true;
      var cheapest = document.querySelector('input[name="courier_mode"][value="cheapest"]');
      if (cheapest) cheapest.checked = true;
      renderSkeleton(ids.length);
      var url = wrap.getAttribute("data-quotes-url") + "?" + ids.map(function (id) {
        return "order=" + encodeURIComponent(id);
      }).join("&");
      fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.json().catch(function () { return null; }); })
        .then(function (d) {
          if (seq !== rates.seq) return; // a newer open superseded this one
          if (!d || !d.success) {
            el("ordRatesList").innerHTML = '<p class="ord-rate__note ord-rate__note--warn ord-rate__msg">' +
              esc((d && d.error) || "Couldn't load live rates.") + " Shiprocket will pick a courier for each order.</p>";
            return;
          }
          rates.data = d;
          rates.cur = d.currency || "₹";
          if (el("ordRatesOrigin")) el("ordRatesOrigin").textContent = d.origin ? "Pickup from " + d.origin : "";
          renderRows();
          renderSame();
          applyMode(currentMode() || "cheapest");
        })
        .catch(function () {
          if (seq !== rates.seq) return;
          el("ordRatesList").innerHTML = '<p class="ord-rate__note ord-rate__note--warn ord-rate__msg">' +
            "Couldn't load live rates. Shiprocket will pick a courier for each order.</p>";
        });
    }

    form.addEventListener("change", function (e) {
      var t = e.target;
      if (!t) return;
      if (t.name === "courier_mode") { applyMode(t.value); return; }
      if (t.id === "ordRatesSameSel") { applyMode("same"); return; }
      if (t.hasAttribute && t.hasAttribute("data-rate-order")) {
        // A hand-picked row: the mix is now custom, so no mode pill is lit.
        var lit = document.querySelector('input[name="courier_mode"]:checked');
        if (lit) lit.checked = false;
        if (el("ordRatesSame")) el("ordRatesSame").hidden = true;
        updateTotals();
      }
    });

    document.addEventListener("click", function (e) {
      var openBtn = e.target.closest && e.target.closest("[data-ship-open]");
      if (openBtn) {
        e.preventDefault();
        var provider = openBtn.getAttribute("data-provider") || "";
        var ids = selectedIds();
        if (!ids.length) {
          alert("Select the orders to ship, then click Generate & Request Pickup.");
          return;
        }
        if (provider === "delhivery" && new Date().getHours() >= 11) {
          alert("Delhivery pickups can be requested only between 12:00 AM and 11:00 AM.");
          return;
        }
        var box = el("ordShipSelected");
        if (box) {
          box.innerHTML = ids.map(function (id) {
            return '<input type="hidden" name="selected_orders" value="' + esc(id) + '">';
          }).join("");
        }
        var cnt = el("ordShipCount");
        if (cnt) cnt.textContent = ids.length + " order(s) selected. The pickup is requested and their labels are generated in one go.";
        prepPickupDate();
        if (provider === "delhivery") {
          var whEl = el("ordShipLocation");
          if (whEl && !whEl.value) whEl.value = openBtn.getAttribute("data-warehouse") || "";
          var pkgEl = el("ordShipPackages");
          if (pkgEl) pkgEl.value = ids.length;
        } else {
          loadRates(ids);
        }
        showError("");
        openModal();
        return;
      }
      if (e.target.closest && e.target.closest("[data-ship-close]")) closeModal();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && !modal.hidden) closeModal(); });

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = el("ordShipSubmit");
      var orig = btn ? btn.innerHTML : "";
      if (btn) { btn.disabled = true; btn.textContent = "Requesting pickup…"; }
      showError("");
      fetch(form.getAttribute("action"), {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
      })
        .then(function (r) {
          var ct = r.headers.get("content-type") || "";
          if (r.ok && ct.indexOf("application/pdf") >= 0) {
            var dispo = r.headers.get("content-disposition") || "";
            var m = /filename="?([^"]+)"?/.exec(dispo);
            var next = r.headers.get("x-redirect") || "";
            return r.blob().then(function (b) {
              downloadBlob(b, m ? m[1] : "labels.pdf");
              // Give the download a moment to start before leaving the page.
              setTimeout(function () {
                if (next) window.location.href = next; else window.location.reload();
              }, 900);
              return true;
            });
          }
          return r.json().then(function (d) {
            if (d && d.success) {
              if (d.redirect) window.location.href = d.redirect; else window.location.reload();
              return true;
            }
            showError((d && d.error) || "The pickup couldn't be requested. Please try again.");
            return false;
          }).catch(function () {
            showError("The pickup couldn't be requested. Please try again.");
            return false;
          });
        })
        .catch(function () {
          showError("Request failed. Check your connection and try again.");
          return false;
        })
        .then(function (done) {
          if (!done && btn) { btn.disabled = false; btn.innerHTML = orig; }
          if (done && btn) btn.textContent = "Done";
        });
    });
  })();

  /* ── Label & Invoice format modal (reprint / self-ship) ──────────────────
   * Bulk button or a per-row printer icon opens the A4 / 4×6 / courier picker;
   * "Generate" POSTs to the orders endpoint and streams back a PDF we download
   * client-side (no page navigation). */
  (function () {
    function openModal() { var m = el("ordLabelModal"); if (m) m.hidden = false; }
    function closeModal() { var m = el("ordLabelModal"); if (m) m.hidden = true; }
    function selectedIds() {
      return Array.prototype.slice
        .call(document.querySelectorAll(".ord-row-check"))
        .filter(function (cb) { return cb.checked; })
        .map(function (cb) { return cb.value; });
    }
    function downloadBlob(blob, filename) {
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = filename || "labels.pdf";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 1500);
    }

    document.addEventListener("click", function (e) {
      var openBtn = e.target.closest && e.target.closest("[data-label-open]");
      if (openBtn) {
        e.preventDefault();
        var single = openBtn.getAttribute("data-order-id");
        var ids = single ? [single] : selectedIds();
        if (!ids.length) {
          alert("Select at least one order to generate labels.");
          return;
        }
        var box = el("ordLabelSelected");
        if (box) {
          box.innerHTML = ids.map(function (id) {
            return '<input type="hidden" name="selected_orders" value="' + esc(id) + '">';
          }).join("");
        }
        var cnt = el("ordLabelCount");
        if (cnt) cnt.textContent = "Choose how labels and invoices are generated for " + ids.length + " order(s).";
        openModal();
        return;
      }
      if (e.target.closest && e.target.closest("[data-label-close]")) closeModal();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeModal(); });

    var form = el("ordLabelForm");
    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var btn = el("ordLabelGenerate");
        var orig = btn ? btn.innerHTML : "";
        if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }
        fetch(form.getAttribute("action"), {
          method: "POST",
          body: new FormData(form),
          credentials: "same-origin",
          headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
        })
          .then(function (r) {
            var ct = r.headers.get("content-type") || "";
            if (r.ok && ct.indexOf("application/pdf") >= 0) {
              var dispo = r.headers.get("content-disposition") || "";
              var m = /filename="?([^"]+)"?/.exec(dispo);
              return r.blob().then(function (b) { downloadBlob(b, m ? m[1] : "labels.pdf"); });
            }
            return r.json()
              .then(function (d) { alert((d && d.error) || "Label generation failed."); })
              .catch(function () { alert("Label generation failed."); });
          })
          .catch(function () { alert("Label generation failed. Check your connection."); })
          .finally(function () {
            if (btn) { btn.disabled = false; btn.innerHTML = orig; }
            closeModal();
          });
      });
    }
  })();
})();
