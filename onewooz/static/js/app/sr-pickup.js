/* ==========================================================================
   OneWooz  Shiprocket pickup-location dropdown (Shipping settings)
   --------------------------------------------------------------------------
   "Load pickup locations" posts the API email/password currently in the form
   (nothing is saved) to store:shiprocket_pickup_locations and rebuilds the
   <select name="pickup_location"> with every pickup address in that
   Shiprocket account. The saved choice stays selected; with none saved the
   account's primary location is preselected. "Save carrier" stores it, and
   every order sent to Shiprocket uses it.
   ========================================================================== */
(function () {
  "use strict";

  var field = document.querySelector("[data-sr-pickup]");
  if (!field) return;
  var select = field.querySelector("[data-sr-pickup-select]");
  var btn = field.querySelector("[data-sr-pickup-load]");
  var hint = field.querySelector("[data-sr-pickup-hint]");
  var form = field.closest("form");
  if (!select || !btn || !form) return;

  function val(name) {
    var el = form.querySelector('[name="' + name + '"]');
    return el ? el.value : "";
  }

  function setHint(text, isError) {
    if (!hint) return;
    hint.textContent = "";
    var span = document.createElement("span");
    if (isError) span.className = "ship-hint--err";
    span.textContent = text;
    hint.appendChild(span);
  }

  function rebuild(locations, saved) {
    var current = select.value || saved || "";
    select.innerHTML = "";
    var ph = document.createElement("option");
    ph.value = "";
    ph.textContent = "Choose a pickup location";
    select.appendChild(ph);
    var names = locations.map(function (l) { return l.name; });
    if (current && names.indexOf(current) < 0) {
      var keep = document.createElement("option");
      keep.value = current;
      keep.textContent = current + " (not found in Shiprocket)";
      select.appendChild(keep);
    }
    locations.forEach(function (l) {
      var o = document.createElement("option");
      o.value = l.name;
      var bits = [l.name];
      if (l.city) bits.push(l.city + (l.pincode ? " " + l.pincode : ""));
      o.textContent = bits.join(" · ") + (l.primary ? " (primary)" : "");
      select.appendChild(o);
    });
    var pick = current;
    if (!pick) {
      var primary = locations.filter(function (l) { return l.primary; })[0] || (locations.length === 1 ? locations[0] : null);
      pick = primary ? primary.name : "";
    }
    select.value = pick;
    if (window.OwSelect && window.OwSelect.rebuild) {
      try { window.OwSelect.rebuild(select); } catch (e) {}
    }
  }

  btn.addEventListener("click", function () {
    var email = val("api_email").trim();
    var password = val("api_password");
    if (!email || !password) {
      setHint("Enter your Shiprocket API email and API password above first, then load your pickup locations.", true);
      return;
    }
    var body = new FormData();
    body.append("csrfmiddlewaretoken", val("csrfmiddlewaretoken"));
    body.append("api_email", email);
    body.append("api_password", password);
    var label = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Loading…";
    fetch(field.getAttribute("data-lookup-url"), {
      method: "POST",
      body: body,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": val("csrfmiddlewaretoken") },
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.success) {
          setHint((d && d.error) || "Couldn't load your pickup locations.", true);
          return;
        }
        rebuild(d.locations || [], d.selected || "");
        setHint(
          "Loaded " + d.locations.length + " pickup location(s) from your Shiprocket account. " +
          "Choose one and click “Save carrier”; it is then used for every order you send to Shiprocket."
        );
      })
      .catch(function () { setHint("Couldn't reach the server. Try again.", true); })
      .then(function () { btn.disabled = false; btn.textContent = label; });
  });
})();
