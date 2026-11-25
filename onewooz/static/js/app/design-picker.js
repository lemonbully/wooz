/* Storefront design cards (templates/store/_design_cards.html), used by the
 * OneWooz Templates page and Settings > Storefront design.
 *
 *  - "Use this design" asks once before switching the live store.
 *  - Buy (only while designs are sold from here rather than the marketplace)
 *    opens OneWooz's Cashfree checkout; on return the payment is confirmed
 *    server-side and the design switches on by itself.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-design-cards]");
  if (!root) return;

  function each(list, fn) { Array.prototype.forEach.call(list, fn); }

  function notify(msg) {
    if (window.OWToast) window.OWToast.error(msg);
    else window.alert(msg);
  }

  each(root.querySelectorAll("form[data-design-use]"), function (form) {
    var btn = form.querySelector("button[type=submit]");
    form.addEventListener("submit", function (e) {
      if (btn.disabled) { e.preventDefault(); return; }
      var name = form.getAttribute("data-design-use") || "this design";
      if (!window.confirm("Switch your live store to " + name + "? Your content and products stay as they are, and you can switch back any time.")) {
        e.preventDefault();
        return;
      }
      btn.disabled = true;
      btn.textContent = "Switching…";
    });
  });

  // Back from the browser's history: the buttons work again.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    each(root.querySelectorAll("form[data-design-use] button[type=submit]"), function (btn) {
      btn.disabled = false;
      btn.textContent = "Use this design";
    });
  });

  var buyUrl = root.getAttribute("data-design-buy-url") || "";
  var csrf = root.getAttribute("data-csrf") || "";
  each(root.querySelectorAll("[data-design-buy]"), function (b) {
    b.addEventListener("click", function () {
      if (b.disabled || !buyUrl) return;
      var name = b.getAttribute("data-name") || "this design";
      if (!window.confirm("Buy " + name + " for ₹" + b.getAttribute("data-total") + " (incl. GST)? It switches on as soon as the payment goes through.")) return;
      var label = b.textContent;
      b.disabled = true;
      b.textContent = "Starting payment…";
      var body = new URLSearchParams();
      body.set("template", b.getAttribute("data-design-buy"));
      fetch(buyUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString()
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (d) {
          if (!d || !d.ok || !d.payment_session_id) throw new Error((d && d.error) || "We couldn't start the payment. Please try again.");
          if (typeof window.Cashfree !== "function") throw new Error("Couldn't open the payment window. Please refresh and try again.");
          window.Cashfree({ mode: d.mode }).checkout({ paymentSessionId: d.payment_session_id, redirectTarget: "_self" });
        })
        .catch(function (e) {
          notify((e && e.message) || "Network error. Please try again.");
          b.disabled = false;
          b.textContent = label;
        });
    });
  });
})();
