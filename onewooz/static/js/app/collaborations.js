/* Collaborations: scope reveal, earnings modal, copy contract link, resend
 * invite, suspend confirm. Faithful to the reference referral suite. */
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
  function post(url) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest" },
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); });
  }

  /* Confirm destructive submits (suspend). */
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (f.matches && f.matches("[data-confirm]") && !window.confirm(f.getAttribute("data-confirm"))) e.preventDefault();
  });

  /* Scope reveal: "All products/categories" hides the specific list. */
  Array.prototype.forEach.call(document.querySelectorAll("[data-scope-all]"), function (cb) {
    var kind = cb.getAttribute("data-scope-all");
    var list = document.querySelector('[data-scope-list="' + kind + '"]');
    function sync() { if (list) list.hidden = cb.checked; }
    cb.addEventListener("change", sync);
    sync();
  });

  /* Earnings modal + record-payout. */
  (function () {
    var modal = document.getElementById("earningsModal");
    if (!modal) return;
    var set = function (id, v) { var n = document.getElementById(id); if (n) n.textContent = v; };
    function close() { modal.hidden = true; }

    // Reflect the outstanding balance: reveal the payout form only when money
    // is owed; otherwise show the "all paid up" note.
    function reflectOutstanding(outStr) {
      var section = modal.querySelector("[data-payout]");
      var settled = modal.querySelector("[data-payout-settled]");
      var due = parseFloat(outStr) || 0;
      if (section) section.hidden = due <= 0;
      if (settled) settled.hidden = due > 0;
    }

    // Keep the partner card's inline tiles in step after a payout.
    function updateCard(collabId, paid, outstanding) {
      var card = document.querySelector('.collab-partner[data-collab-id="' + collabId + '"]');
      if (!card) return;
      var tiles = card.querySelectorAll(".collab-partner__earn .collab-earn");
      // Order of tiles: Orders, Earned, Paid, Outstanding.
      if (tiles[2]) { var p = tiles[2].querySelector(".collab-earn__n"); if (p) p.textContent = "₹" + paid; }
      if (tiles[3]) { var o = tiles[3].querySelector(".collab-earn__n"); if (o) o.textContent = "₹" + outstanding; }
    }

    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest("[data-earnings]");
      if (btn) {
        e.preventDefault();
        modal.dataset.payoutUrl = btn.getAttribute("data-payout-url") || "";
        modal.dataset.collabId = btn.getAttribute("data-collab-id") || "";
        fetch(btn.getAttribute("data-url"), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d.success) return;
            set("earnName", d.name); set("earnEmail", d.email); set("earnCommission", d.commission);
            set("earnOrders", d.total_orders); set("earnEarned", "₹" + d.total_earned);
            set("earnPaid", "₹" + d.total_paid); set("earnOutstanding", "₹" + d.outstanding);
            reflectOutstanding(d.outstanding);
            var amt = modal.querySelector("[data-payout-amount]"); if (amt) amt.value = "";
            var msg = modal.querySelector("[data-payout-msg]"); if (msg) msg.hidden = true;
            modal.hidden = false;
          });
        return;
      }

      var payBtn = e.target.closest && e.target.closest("[data-payout-submit]");
      if (payBtn) {
        e.preventDefault();
        var url = modal.dataset.payoutUrl;
        var input = modal.querySelector("[data-payout-amount]");
        var msg = modal.querySelector("[data-payout-msg]");
        var value = (input && input.value || "").trim();
        function showMsg(text, ok) {
          if (!msg) return;
          msg.textContent = text; msg.hidden = false;
          msg.classList.toggle("is-ok", !!ok); msg.classList.toggle("is-err", !ok);
        }
        if (!value || parseFloat(value) <= 0) { showMsg("Enter an amount greater than zero.", false); return; }
        if (!url) return;
        payBtn.disabled = true;
        var body = new URLSearchParams(); body.append("amount", value);
        fetch(url, {
          method: "POST", credentials: "same-origin",
          headers: { "X-CSRFToken": csrf(), "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded" },
          body: body.toString(),
        })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
          .then(function (res) {
            payBtn.disabled = false;
            if (!res.ok || !res.d.success) { showMsg(res.d.error || "Couldn't record the payout.", false); return; }
            set("earnPaid", "₹" + res.d.total_paid);
            set("earnOutstanding", "₹" + res.d.outstanding);
            reflectOutstanding(res.d.outstanding);
            updateCard(modal.dataset.collabId, res.d.total_paid, res.d.outstanding);
            if (input) input.value = "";
            showMsg("Recorded ₹" + res.d.paid_now + " paid.", true);
          })
          .catch(function () { payBtn.disabled = false; showMsg("Couldn't record the payout.", false); });
        return;
      }

      if (e.target.closest && e.target.closest("[data-earn-close]")) close();
    });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });
  })();

  /* Copy contract link. */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-copy-contract]");
    if (!btn) return;
    e.preventDefault();
    post(btn.getAttribute("data-url")).then(function (res) {
      if (!res.ok || !res.d.success) { owAlert("Couldn't generate the link."); return; }
      var url = res.d.contract_url;
      var done = function () { owAlert("Contract link copied.", true); var t = btn.textContent; btn.textContent = "Copied!"; setTimeout(function () { btn.textContent = t; }, 1500); };
      if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, function () { window.prompt("Copy this contract link:", url); });
      else window.prompt("Copy this contract link:", url);
    });
  });

  /* Resend invite. */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest("[data-resend]");
    if (!btn) return;
    e.preventDefault();
    btn.disabled = true;
    post(btn.getAttribute("data-url")).then(function (res) {
      btn.disabled = false;
      if (!res.ok || !res.d.success) { owAlert("Couldn't resend the invite."); return; }
      window.alert("Invite refreshed. New OTP: " + res.d.otp + "\nContract link:\n" + res.d.contract_url);
    }).catch(function () { btn.disabled = false; owAlert("Couldn't resend the invite."); });
  });
})();
