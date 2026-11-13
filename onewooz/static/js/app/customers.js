/* ==========================================================================
   OneWooz  Customers page actions
   --------------------------------------------------------------------------
   Row actions (View, Edit, Credits, Notify, Delete) and the header bulk
   actions for the store Customers page. Edit and Delete are plain form posts;
   View, Credits and Notify are same-origin JSON calls. CSP-safe: every listener
   is bound here, the CSRF token is read from the field Django rendered, and
   reversed URLs arrive as data-* templates carrying the all-zero UUID we swap.
   ========================================================================== */
(function () {
  "use strict";

  var root = document.querySelector("[data-customers]");
  if (!root) return;

  var PH = "00000000-0000-0000-0000-000000000000";
  var CFG = {
    jsonTmpl: root.getAttribute("data-json-tmpl"),
    creditListTmpl: root.getAttribute("data-credit-list-tmpl"),
    creditAddTmpl: root.getAttribute("data-credit-add-tmpl"),
    notifyTmpl: root.getAttribute("data-notify-tmpl"),
    editTmpl: root.getAttribute("data-edit-tmpl"),
    notifyAllUrl: root.getAttribute("data-notify-all-url"),
    creditBulkUrl: root.getAttribute("data-credit-bulk-url"),
    creditDelTmpl: root.getAttribute("data-credit-del-tmpl"),
    creditBulkListUrl: root.getAttribute("data-credit-bulk-list-url"),
    creditBulkDelUrl: root.getAttribute("data-credit-bulk-del-url"),
    currency: root.getAttribute("data-currency") || "",
  };

  function urlFor(tmpl, id) { return tmpl ? tmpl.replace(PH, id) : ""; }
  function csrf() {
    var el = root.querySelector("input[name=csrfmiddlewaretoken]");
    if (el) return el.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }
  function dlg(name) { return root.querySelector('[data-dlg="' + name + '"]'); }
  function open(el) { if (el) el.hidden = false; }
  function close(el) { if (el) el.hidden = true; }
  function q(el, sel) { return el ? el.querySelector(sel) : null; }

  // In-page confirmation (replaces the browser's alert()). Shown after the
  // dialog has closed, then fades away on its own.
  function toast(msg, kind) {
    var wrap = document.querySelector(".app-toasts");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "app-toasts";
      wrap.setAttribute("role", "status");
      wrap.setAttribute("aria-live", "polite");
      document.body.appendChild(wrap);
    }
    var t = document.createElement("div");
    t.className = "app-toast" + (kind === "error" ? " app-toast--error" : "");
    var dot = document.createElement("span"); dot.className = "app-toast__dot"; dot.setAttribute("aria-hidden", "true");
    var txt = document.createElement("span"); txt.textContent = msg;
    var x = document.createElement("button"); x.type = "button"; x.className = "app-toast__close"; x.setAttribute("aria-label", "Dismiss"); x.textContent = "×";
    t.appendChild(dot); t.appendChild(txt); t.appendChild(x);
    wrap.appendChild(t);
    requestAnimationFrame(function () { t.classList.add("is-in"); });
    function dismiss() {
      t.classList.remove("is-in");
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 200);
    }
    x.addEventListener("click", dismiss);
    setTimeout(dismiss, 4000);
  }

  // Escape untrusted (customer-entered) text before injecting via innerHTML.
  function escHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m];
    });
  }
  function setText(d, key, val) { var el = q(d, '[data-cv="' + key + '"]'); if (el) el.textContent = val; }
  function fmtDate(iso) { return (iso || "").replace("T", " ").slice(0, 10); }
  function addrTypeLabel(t) { return t === "shipping" ? "Shipping" : t === "billing" ? "Billing" : "Shipping & billing"; }

  function renderAddresses(list) {
    if (!list || !list.length) return '<p class="app-dlg__muted">No saved addresses.</p>';
    return list.map(function (a) {
      var line1 = (escHtml(a.house_no) + " " + escHtml(a.street_area)).trim() || "";
      var line2 = [a.city, a.state, a.pincode].filter(Boolean).map(escHtml).join(", ");
      var comp = a.company_name ? '<div class="app-custaddr__row">Company: ' + escHtml(a.company_name) + "</div>" : "";
      var gst = a.has_gst ? '<div class="app-custaddr__row">GST: ' + escHtml(a.gstin || "Yes") + "</div>" : "";
      return '<div class="app-custaddr">' +
        '<div class="app-custaddr__top"><span class="app-dlg__pill">' + addrTypeLabel(a.address_type) + "</span>" +
        (a.is_default ? ' <span class="app-dlg__pill is-ok">Default</span>' : "") + "</div>" +
        '<div class="app-custaddr__line">' + line1 + "</div>" +
        (line2 ? '<div class="app-custaddr__row">' + line2 + "</div>" : "") + comp + gst + "</div>";
    }).join("");
  }

  // ---- Purchase history: one accordion per order ---------------------------
  // Each order opens to its full detail inside the popup (the dialog body
  // scrolls). Only one order is open at a time: while one is open the other
  // headers are locked until it is closed again.
  function money(cur, v) { return escHtml(cur) + " " + escHtml(v); }
  function nonZero(v) { return parseFloat(v || "0") > 0; }
  function fmtDateTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return fmtDate(iso);
    return d.toLocaleString(undefined, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  function kv(label, value) {
    return value ? "<div><dt>" + escHtml(label) + "</dt><dd>" + value + "</dd></div>" : "";
  }
  function statusTone(status) {
    if (status === "delivered") return " is-ok";
    if (status === "cancelled" || status === "archived") return " is-bad";
    return "";
  }

  function renderOrderBody(o, cur) {
    var items = (o.items || []).map(function (it) {
      var img = it.image
        ? '<img class="app-custord__img" src="' + escHtml(it.image) + '" alt="" loading="lazy">'
        : '<span class="app-custord__img app-custord__img--empty" aria-hidden="true"></span>';
      var meta = [it.variant, it.sku ? "SKU " + it.sku : ""].filter(Boolean).map(escHtml).join(" · ");
      return '<li class="app-custord__item">' + img +
        '<div class="app-custord__itxt"><p class="app-custord__ititle">' + escHtml(it.title) + "</p>" +
        (meta ? '<p class="app-custord__imeta">' + meta + "</p>" : "") +
        '<p class="app-custord__imeta">' + escHtml(it.quantity) + " × " + money(cur, it.unit_price) + "</p></div>" +
        '<span class="app-custord__iamt">' + money(cur, it.line_total) + "</span></li>";
    }).join("");

    var a = o.amounts || {};
    var sums = '<div class="app-custord__sum"><span>Subtotal</span><span>' + money(cur, a.subtotal) + "</span></div>" +
      (nonZero(a.discount) ? '<div class="app-custord__sum"><span>Discount' + (o.coupon_code ? " (" + escHtml(o.coupon_code) + ")" : "") + "</span><span>- " + money(cur, a.discount) + "</span></div>" : "") +
      (nonZero(a.credit_discount) ? '<div class="app-custord__sum"><span>Store credit</span><span>- ' + money(cur, a.credit_discount) + "</span></div>" : "") +
      '<div class="app-custord__sum"><span>Shipping</span><span>' + (nonZero(a.shipping) ? money(cur, a.shipping) : "Free") + "</span></div>" +
      (nonZero(a.tax) ? '<div class="app-custord__sum"><span>Tax</span><span>' + money(cur, a.tax) + "</span></div>" : "") +
      '<div class="app-custord__sum app-custord__sum--total"><span>Total</span><span>' + money(cur, a.total) + "</span></div>";

    var p = o.payment || {};
    var payText = escHtml(p.label || "");
    if (p.mode === "partial") payText += " · paid " + money(cur, p.paid) + ", collect " + money(cur, p.collect);
    else if (p.mode === "cod") payText += " · collect " + money(cur, p.collect);
    else if (p.paid_online) payText += " · paid online";

    var sh = o.shipping || {};
    var place = [sh.city, sh.state, sh.pincode].filter(Boolean).map(escHtml).join(", ");
    var addr = [escHtml(sh.address), place].filter(Boolean).join("<br>");

    var c = o.courier || {};
    var courier = "";
    if (c.awb || c.name || c.status) {
      var awb = escHtml(c.awb || "");
      if (awb && /^https:\/\//i.test(c.tracking_url || "")) {
        awb = '<a href="' + escHtml(c.tracking_url) + '" target="_blank" rel="noopener noreferrer">' + awb + "</a>";
      }
      courier = '<div class="app-custord__block"><p class="app-custord__h">Shipping</p><dl class="app-dlg__list">' +
        kv("Courier", escHtml(c.name || "")) +
        kv("AWB", awb) +
        kv("Shipment", c.status ? escHtml(c.status) + (c.returning ? " (returning to origin)" : "") : "") +
        kv("Pickup date", escHtml(c.pickup_date || "")) +
        "</dl></div>";
    }

    var timeline = (o.timeline || []).map(function (t) {
      return "<li><span>" + escHtml(t.label) + "</span><span>" + escHtml(fmtDateTime(t.at)) + "</span></li>";
    }).join("");

    return '<div class="app-custord__block"><p class="app-custord__h">Items</p><ul class="app-custord__items">' +
        (items || '<li class="app-dlg__muted">No items recorded.</li>') + "</ul>" + sums + "</div>" +
      '<div class="app-custord__block"><p class="app-custord__h">Payment &amp; delivery</p><dl class="app-dlg__list">' +
        kv("Payment", payText) +
        kv("Deliver to", escHtml(sh.name || "")) +
        kv("Phone", escHtml(sh.phone || "")) +
        kv("Email", escHtml(sh.email || "")) +
        kv("Address", addr) +
        kv("Billing address", escHtml(o.billing_address || "")) +
        kv("Company", escHtml(o.company_name || "")) +
        kv("GSTIN", escHtml(o.gstin || "")) +
        "</dl></div>" +
      courier +
      (timeline ? '<div class="app-custord__block"><p class="app-custord__h">Timeline</p><ul class="app-custord__tl">' + timeline + "</ul></div>" : "") +
      (o.edit_url ? '<div class="app-custord__foot"><a class="pal-btn pal-btn--ghost pal-btn--sm" href="' + escHtml(o.edit_url) + '">Open order</a></div>' : "");
  }

  function renderHistory(orders, cur) {
    if (!orders || !orders.length) return '<p class="app-dlg__muted">No orders yet.</p>';
    return '<div class="app-custords" data-custords>' + orders.map(function (o, i) {
      var pid = "custord-" + i;
      return '<section class="app-custord">' +
        '<button type="button" class="app-custord__head" data-custord-toggle aria-expanded="false" aria-controls="' + pid + '">' +
          '<span class="app-custord__num">#' + escHtml(o.number) + "</span>" +
          '<span class="app-custord__date">' + escHtml(fmtDate(o.created_at)) + "</span>" +
          '<span class="app-dlg__pill' + statusTone(o.status) + '">' + escHtml(o.status_display) + "</span>" +
          '<span class="app-custord__meta">' + escHtml(o.item_count) + (o.item_count === 1 ? " item" : " items") + "</span>" +
          '<span class="app-custord__total">' + money(cur, o.total) + "</span>" +
          '<span class="app-custord__chev" aria-hidden="true"></span>' +
        "</button>" +
        '<div class="app-custord__body" id="' + pid + '" hidden>' + renderOrderBody(o, cur) + "</div>" +
        "</section>";
    }).join("") + "</div>";
  }

  document.addEventListener("click", function (e) {
    var head = e.target.closest && e.target.closest("[data-custord-toggle]");
    if (!head) return;
    var list = head.closest("[data-custords]");
    var body = document.getElementById(head.getAttribute("aria-controls"));
    if (!list || !body) return;
    var heads = list.querySelectorAll("[data-custord-toggle]");
    if (head.getAttribute("aria-expanded") === "true") {
      head.setAttribute("aria-expanded", "false");
      body.hidden = true;
      list.classList.remove("has-open");
      Array.prototype.forEach.call(heads, function (h) {
        h.removeAttribute("aria-disabled");
        h.removeAttribute("title");
      });
      return;
    }
    // One order at a time: another one is open, so this one stays shut.
    if (list.querySelector('[data-custord-toggle][aria-expanded="true"]')) return;
    head.setAttribute("aria-expanded", "true");
    body.hidden = false;
    list.classList.add("has-open");
    Array.prototype.forEach.call(heads, function (h) {
      if (h !== head) {
        h.setAttribute("aria-disabled", "true");
        h.setAttribute("title", "Close the open order to view another");
      }
    });
    if (head.scrollIntoView) {
      try { head.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (err) { head.scrollIntoView(); }
    }
  });

  // ---- Address card editor (Edit dialog) ------------------------------------
  var ADDR_FIELDS = ["house_no", "street_area", "city", "state", "pincode", "company_name", "gstin"];
  function addrList(d) { return q(d, "[data-addr-list]"); }
  function makeAddressCard(a) {
    var tpl = root.querySelector("[data-addr-template]");
    var node = tpl.content.firstElementChild.cloneNode(true);
    a = a || {};
    node.querySelector("[data-addr-id]").value = a.id || "";
    node.querySelector('[data-addr-field="address_type"]').value = a.address_type || "both";
    ADDR_FIELDS.forEach(function (f) {
      var el = node.querySelector('[data-addr-field="' + f + '"]');
      if (el) el.value = a[f] || "";
    });
    var def = node.querySelector("[data-addr-default]");
    if (def) def.checked = !!a.is_default;
    return node;
  }
  function renderAddressCards(d, list) {
    var wrap = addrList(d);
    wrap.innerHTML = "";
    (list || []).forEach(function (a) { wrap.appendChild(makeAddressCard(a)); });
    updateAddrEmpty(d);
  }
  function updateAddrEmpty(d) {
    var wrap = addrList(d);
    var empty = q(d, "[data-addr-empty]");
    if (empty) empty.hidden = wrap.children.length > 0;
  }

  // ---- Close controls (backdrop / × / Cancel / Escape) ----------------------
  document.addEventListener("click", function (e) {
    var closer = e.target.closest && e.target.closest("[data-dlg-close]");
    if (closer) {
      e.preventDefault();
      var d = closer.closest(".app-dlg");
      if (d) close(d);
    }
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    Array.prototype.forEach.call(root.querySelectorAll(".app-dlg"), function (d) { d.hidden = true; });
  });

  /* ======================================================================
     VIEW
     =================================================================== */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-cust-view]");
    if (!btn) return;
    e.preventDefault();
    var d = dlg("view");
    fetch(urlFor(CFG.jsonTmpl, btn.getAttribute("data-id")), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || !data.success) return;
        var c = data.customer;
        var cur = c.currency || CFG.currency;
        setText(d, "name", c.name || c.email || "Customer");
        setText(d, "email", c.email || "");
        setText(d, "name2", c.name || "");
        setText(d, "email2", c.email || "");
        setText(d, "phone", c.phone || "");
        setText(d, "orders", String(c.order_count));
        setText(d, "spent", cur + " " + c.spent);
        setText(d, "created", fmtDate(c.created_at));
        q(d, '[data-cv="addresses"]').innerHTML = renderAddresses(c.addresses);
        q(d, '[data-cv="orders-wrap"]').innerHTML = renderHistory(c.orders, cur);
        open(d);
      })
      .catch(function () {});
  });

  /* ======================================================================
     EDIT (profile + addresses; plain form post → redirect)
     =================================================================== */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-cust-edit]");
    if (!btn) return;
    e.preventDefault();
    var d = dlg("edit");
    var id = btn.getAttribute("data-id");
    var form = q(d, "[data-cust-edit-form]");
    form.setAttribute("action", urlFor(CFG.editTmpl, id));
    // Access controls via `.elements`  `form.name` resolves to a built-in
    // property, not the input of that name.
    function fill(c) {
      form.elements["name"].value = (c && c.name) || btn.getAttribute("data-name") || "";
      form.elements["phone"].value = (c && c.phone) || btn.getAttribute("data-phone") || "";
      form.elements["email"].value = (c && c.email) || btn.getAttribute("data-email") || "";
      renderAddressCards(d, (c && c.addresses) || []);
      open(d);
    }
    fetch(urlFor(CFG.jsonTmpl, id), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (data) { fill(data && data.success ? data.customer : null); })
      .catch(function () { fill(null); });
  });

  // Add / remove address cards.
  document.addEventListener("click", function (e) {
    var add = e.target.closest && e.target.closest("[data-addr-add]");
    if (add) {
      e.preventDefault();
      var d = dlg("edit");
      addrList(d).appendChild(makeAddressCard({}));
      updateAddrEmpty(d);
      return;
    }
    var rm = e.target.closest && e.target.closest("[data-addr-remove]");
    if (rm) {
      e.preventDefault();
      var card = rm.closest("[data-addr]");
      if (card && card.parentNode) card.parentNode.removeChild(card);
      updateAddrEmpty(dlg("edit"));
      return;
    }
  });

  // Serialize address cards into the hidden field just before the form submits.
  var editForm = dlg("edit") && dlg("edit").querySelector("[data-cust-edit-form]");
  if (editForm) {
    editForm.addEventListener("submit", function () {
      var d = dlg("edit");
      var rows = [];
      Array.prototype.forEach.call(addrList(d).querySelectorAll("[data-addr]"), function (card) {
        var o = {
          id: card.querySelector("[data-addr-id]").value || "",
          address_type: card.querySelector('[data-addr-field="address_type"]').value,
          is_default: card.querySelector("[data-addr-default]").checked,
        };
        ADDR_FIELDS.forEach(function (f) {
          o[f] = (card.querySelector('[data-addr-field="' + f + '"]').value || "").trim();
        });
        rows.push(o);
      });
      q(d, "[data-addresses-json]").value = JSON.stringify(rows);
      // No preventDefault  let the form post normally.
    });
  }

  /* ======================================================================
     CREDITS  (history + add; single or bulk)
     =================================================================== */
  var creditState = { id: null, bulk: false };

  function creditTab(name) {
    var d = dlg("credit");
    Array.prototype.forEach.call(d.querySelectorAll("[data-credit-tab]"), function (t) {
      t.classList.toggle("is-active", t.getAttribute("data-credit-tab") === name);
    });
    Array.prototype.forEach.call(d.querySelectorAll("[data-credit-pane]"), function (p) {
      p.hidden = p.getAttribute("data-credit-pane") !== name;
    });
  }

  // Shared pieces of a credit card (value line, Delete, the inline confirm).
  function creditValue(cr) {
    return cr.credit_type === "amount" ? (CFG.currency + " " + escHtml(cr.credit_value)) : (escHtml(cr.credit_value) + "%");
  }
  function creditScope(cr) {
    return cr.scope === "all" ? "All categories" : (cr.category_names || []).filter(Boolean).map(escHtml).join(", ") || "Selected categories";
  }
  function creditValid(cr) { return cr.valid_until ? ("Valid until " + escHtml(cr.valid_until)) : "No expiry"; }
  function delButton(attr, value) {
    return '<button type="button" class="app-dlg__creditdel" ' + attr + '="' + escHtml(value) + '">Delete</button>';
  }

  function creditListParts() {
    var d = dlg("credit");
    return { d: d, loading: q(d, "[data-credit-loading]"), list: q(d, "[data-credit-list]"), empty: q(d, "[data-credit-empty]"), hint: q(d, "[data-credit-hint]") };
  }

  function loadCreditHistory(id) {
    var p = creditListParts();
    var loading = p.loading, list = p.list, empty = p.empty;
    empty.textContent = "No credits for this customer yet.";
    if (p.hint) p.hint.hidden = true;
    loading.hidden = false; list.hidden = true; empty.hidden = true; list.innerHTML = "";
    fetch(urlFor(CFG.creditListTmpl, id), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        loading.hidden = true;
        if (!data || !data.success || !data.credits || !data.credits.length) { empty.hidden = false; return; }
        data.credits.forEach(function (cr) {
          var card = document.createElement("div");
          card.className = "app-dlg__credit" + (cr.is_active ? " is-active" : "");
          card.setAttribute("data-credit-card", "");
          card.innerHTML =
            '<div class="app-dlg__creditrow"><span class="app-dlg__creditval">' + creditValue(cr) + "</span>" +
            '<span class="app-dlg__creditside"><span class="app-dlg__pill' + (cr.is_active ? " is-ok" : "") + '">' + (cr.is_active ? "Active" : "Inactive") + "</span>" +
            delButton("data-credit-del", cr.id) + "</span></div>" +
            '<div class="app-dlg__creditmeta">' + creditScope(cr) + " · " + creditValid(cr) +
            (cr.remaining_amount !== null && cr.credit_type === "amount" ? " · Remaining " + CFG.currency + " " + escHtml(cr.remaining_amount) : "") +
            (cr.from_bulk ? ' · <span class="app-dlg__credittag">From bulk credits</span>' : "") +
            (cr.note ? '<div class="app-dlg__creditnote">' + escHtml(cr.note) + "</div>" : "") + "</div>";
          list.appendChild(card);
        });
        list.hidden = false;
        if (p.hint) p.hint.hidden = false;
      })
      .catch(function () { loading.hidden = true; empty.hidden = false; });
  }

  // "Given to all": every universal grant, each deletable as a whole.
  function loadBulkHistory() {
    var p = creditListParts();
    var loading = p.loading, list = p.list, empty = p.empty;
    empty.textContent = "No credits have been given to all customers yet.";
    if (p.hint) p.hint.hidden = true;
    loading.hidden = false; list.hidden = true; empty.hidden = true; list.innerHTML = "";
    fetch(CFG.creditBulkListUrl, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        loading.hidden = true;
        if (!data || !data.success || !data.batches || !data.batches.length) { empty.hidden = false; return; }
        data.batches.forEach(function (b) {
          var card = document.createElement("div");
          card.className = "app-dlg__credit" + (b.usable > 0 ? " is-active" : "");
          card.setAttribute("data-credit-card", "");
          card.setAttribute("data-customers", String(b.customers));
          card.innerHTML =
            '<div class="app-dlg__creditrow"><span class="app-dlg__creditval">' + creditValue(b) + "</span>" +
            '<span class="app-dlg__creditside"><span class="app-dlg__pill' + (b.usable > 0 ? " is-ok" : "") + '">' +
            (b.usable > 0 ? escHtml(b.usable) + " usable" : "None usable") + "</span>" +
            delButton("data-credit-bulk-del", b.key) + "</span></div>" +
            '<div class="app-dlg__creditmeta">' + creditScope(b) + " · " + creditValid(b) +
            " · Given to " + escHtml(b.customers) + " customer" + (b.customers === 1 ? "" : "s") + " on " + escHtml(fmtDate(b.created_at)) +
            (b.used ? " · Used by " + escHtml(b.used) : "") +
            (b.note ? '<div class="app-dlg__creditnote">' + escHtml(b.note) + "</div>" : "") + "</div>";
          list.appendChild(card);
        });
        list.hidden = false;
        if (p.hint) p.hint.hidden = false;
      })
      .catch(function () { loading.hidden = true; empty.hidden = false; });
  }

  // Delete: first click asks inline, the second (on "Delete") does it.
  function askDelete(card, message, onYes) {
    var old = card.querySelector("[data-credit-confirm]");
    if (old) { old.parentNode.removeChild(old); }
    var box = document.createElement("div");
    box.className = "app-dlg__creditconfirm";
    box.setAttribute("data-credit-confirm", "");
    box.innerHTML = "<p></p><div>" +
      '<button type="button" class="pal-btn pal-btn--ghost pal-btn--sm" data-credit-keep>Keep</button>' +
      '<button type="button" class="pal-btn pal-btn--danger pal-btn--sm" data-credit-yes>Delete</button></div>';
    box.querySelector("p").textContent = message;
    card.appendChild(box);
    box.querySelector("[data-credit-keep]").addEventListener("click", function () { box.parentNode.removeChild(box); });
    box.querySelector("[data-credit-yes]").addEventListener("click", function () { onYes(box); });
    box.querySelector("[data-credit-keep]").focus();
  }

  function postDelete(url, body, card, box, after) {
    card.classList.add("is-busy");
    var fd = new FormData();
    Object.keys(body || {}).forEach(function (k) { fd.append(k, body[k]); });
    fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }, body: fd })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok || !res.j || !res.j.success) {
          card.classList.remove("is-busy");
          toast((res.j && res.j.error) || "Could not delete the credit.", "error");
          if (box && box.parentNode) box.parentNode.removeChild(box);
          after();
          return;
        }
        toast(res.j.message || "Credit deleted.");
        after();
      })
      .catch(function () {
        card.classList.remove("is-busy");
        toast("Network error. Please try again.", "error");
      });
  }

  document.addEventListener("click", function (e) {
    var one = e.target.closest && e.target.closest("[data-credit-del]");
    if (one && creditState.id) {
      e.preventDefault();
      var card = one.closest("[data-credit-card]");
      var creditId = one.getAttribute("data-credit-del");
      var who = q(dlg("credit"), "[data-credit-sub]").textContent.split(" · ")[0] || "This customer";
      askDelete(card, "Delete this credit? " + who + " can no longer use what is left of it. Orders already placed keep their discount.", function (box) {
        var url = urlFor(CFG.creditDelTmpl, creditState.id).replace("11111111-1111-1111-1111-111111111111", creditId);
        postDelete(url, null, card, box, function () { loadCreditHistory(creditState.id); });
      });
      return;
    }
    var bulk = e.target.closest && e.target.closest("[data-credit-bulk-del]");
    if (bulk) {
      e.preventDefault();
      var bcard = bulk.closest("[data-credit-card]");
      var n = parseInt(bcard.getAttribute("data-customers") || "0", 10);
      var key = bulk.getAttribute("data-credit-bulk-del");
      askDelete(bcard, "Delete this credit from all " + n + " customer" + (n === 1 ? "" : "s") + "? Whatever is left of it disappears from their accounts. Orders already placed keep their discount.", function (box) {
        postDelete(CFG.creditBulkDelUrl, { batch: key }, bcard, box, loadBulkHistory);
      });
    }
  });

  function resetCreditForm() {
    var d = dlg("credit");
    var form = q(d, "[data-cust-credit-form]");
    if (form) {
      form.reset();
      // Every new credit starts as Amount. Set it explicitly and fire "change"
      // so the custom dropdown's visible label follows the native select.
      var type = form.elements["credit_type"];
      if (type) {
        type.value = "amount";
        type.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
    var all = q(d, "[data-credit-scope-all]");
    if (all) all.checked = true;
    var cats = q(d, "[data-credit-cats]");
    if (cats) cats.hidden = true;
    Array.prototype.forEach.call(d.querySelectorAll(".app-credit-cat"), function (cb) { cb.checked = false; });
    var err = q(d, "[data-credit-err]");
    if (err) { err.hidden = true; err.textContent = ""; }
  }

  // Open for a single customer
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-cust-credit]");
    if (!btn) return;
    e.preventDefault();
    var d = dlg("credit");
    creditState = { id: btn.getAttribute("data-id"), bulk: false };
    q(d, "[data-credit-title]").textContent = "Customer credits";
    q(d, "[data-credit-sub]").textContent = (btn.getAttribute("data-name") || "Customer") + " · " + (btn.getAttribute("data-email") || "");
    d.querySelector("[data-credit-tabs]").hidden = false;
    q(d, "[data-credit-history-label]").textContent = "History";
    resetCreditForm();
    creditTab("history");
    open(d);
    loadCreditHistory(creditState.id);
  });

  // Open for all customers (bulk): opens on Add credit, as before; the
  // "Given to all" tab lists earlier universal grants so one can be deleted.
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-cust-bulk-credit]");
    if (!btn) return;
    e.preventDefault();
    var d = dlg("credit");
    creditState = { id: null, bulk: true };
    q(d, "[data-credit-title]").textContent = "Bulk credits";
    q(d, "[data-credit-sub]").textContent = "Apply one credit to every customer";
    d.querySelector("[data-credit-tabs]").hidden = false;
    q(d, "[data-credit-history-label]").textContent = "Given to all";
    resetCreditForm();
    creditTab("add");
    open(d);
  });

  // Tabs
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-credit-tab]");
    if (!t) return;
    creditTab(t.getAttribute("data-credit-tab"));
    if (t.getAttribute("data-credit-tab") === "history" && creditState.bulk) loadBulkHistory();
  });

  // Scope toggle
  document.addEventListener("change", function (e) {
    var all = e.target.closest && e.target.closest("[data-credit-scope-all]");
    if (!all) return;
    var cats = dlg("credit").querySelector("[data-credit-cats]");
    if (cats) cats.hidden = all.checked;
  });

  // Submit credit
  var creditForm = dlg("credit") && dlg("credit").querySelector("[data-cust-credit-form]");
  if (creditForm) {
    creditForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var d = dlg("credit");
      var err = q(d, "[data-credit-err]");
      function fail(m) { if (err) { err.hidden = false; err.textContent = m; } }
      if (err) err.hidden = true;

      var isAll = q(d, "[data-credit-scope-all]").checked;
      var els = creditForm.elements;
      var fd = new FormData();
      fd.append("credit_type", els["credit_type"].value);
      fd.append("credit_value", els["credit_value"].value);
      fd.append("valid_until", els["valid_until"].value || "");
      fd.append("note", els["note"].value || "");
      fd.append("scope", isAll ? "all" : "categories");
      if (!isAll) {
        Array.prototype.forEach.call(d.querySelectorAll(".app-credit-cat:checked"), function (cb) {
          fd.append("category_ids", cb.value);
        });
      }
      var url = creditState.bulk ? CFG.creditBulkUrl : urlFor(CFG.creditAddTmpl, creditState.id);
      var btn = q(d, "[data-credit-submit]");
      if (btn) btn.disabled = true;
      fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }, body: fd })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (btn) btn.disabled = false;
          if (!res.ok || !res.j.success) { fail((res.j && res.j.error) || "Could not apply credit."); return; }
          if (creditState.bulk) { close(d); toast(res.j.message || "Credits applied."); }
          else { resetCreditForm(); creditTab("history"); loadCreditHistory(creditState.id); }
        })
        .catch(function () { if (btn) btn.disabled = false; fail("Network error. Please try again."); });
    });
  }

  /* ======================================================================
     NOTIFY  (single or bulk)
     =================================================================== */
  // Filters apply as soon as a product / category is picked.
  Array.prototype.forEach.call(root.querySelectorAll("select[data-autosubmit]"), function (sel) {
    sel.addEventListener("change", function () { if (sel.form) sel.form.submit(); });
  });

  var notifyState = { id: null, bulk: false };

  function openNotify(bulk, btn) {
    var d = dlg("notify");
    notifyState = { id: bulk ? null : btn.getAttribute("data-id"), bulk: bulk };
    var form = q(d, "[data-cust-notify-form]");
    if (form) form.reset();
    q(d, "[data-notify-title]").textContent = bulk ? "Notify all customers" : "Send notification";
    q(d, "[data-notify-sub]").textContent = bulk ? "Email every customer of this store" : ("To: " + (btn.getAttribute("data-name") || btn.getAttribute("data-email") || ""));
    q(d, "[data-notify-submit]").textContent = bulk ? "Send to all" : "Send";
    var err = q(d, "[data-notify-err]");
    if (err) err.hidden = true;
    open(d);
  }
  document.addEventListener("click", function (e) {
    var single = e.target.closest && e.target.closest("[data-cust-notify]");
    if (single) { e.preventDefault(); openNotify(false, single); return; }
    var all = e.target.closest && e.target.closest("[data-cust-notify-all]");
    if (all) { e.preventDefault(); openNotify(true, all); return; }
  });

  var notifyForm = dlg("notify") && dlg("notify").querySelector("[data-cust-notify-form]");
  if (notifyForm) {
    notifyForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var d = dlg("notify");
      var err = q(d, "[data-notify-err]");
      function fail(m) { if (err) { err.hidden = false; err.textContent = m; } }
      if (err) err.hidden = true;
      var els = notifyForm.elements;
      var title = els["title"].value.trim();
      var message = els["message"].value.trim();
      if (!title || !message) { fail("Title and message are required."); return; }
      var fd = new FormData();
      fd.append("title", title);
      fd.append("message", message);
      fd.append("link", els["link"].value.trim());
      var fileEl = els["attachments"];
      if (fileEl && fileEl.files) {
        Array.prototype.forEach.call(fileEl.files, function (f) { fd.append("attachments", f); });
      }
      var url = notifyState.bulk ? CFG.notifyAllUrl : urlFor(CFG.notifyTmpl, notifyState.id);
      var btn = q(d, "[data-notify-submit]");
      if (btn) btn.disabled = true;
      fetch(url, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" }, body: fd })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (res) {
          if (btn) btn.disabled = false;
          if (!res.ok || !res.j.success) { fail((res.j && res.j.error) || "Could not send."); return; }
          close(d);
          notifyForm.reset();
          toast(res.j.message || "Notification was sent.");
        })
        .catch(function () { if (btn) btn.disabled = false; fail("Network error. Please try again."); });
    });
  }
})();
