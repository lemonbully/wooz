/*
 * SubscriptionPlan admin  reveal the "Email marketing" limits fieldset only
 * when the plan actually grants email marketing.
 *
 * Availability is the ordinary gate: paid / one-time / custom plans get every
 * feature, so the limits always apply there; a FREE plan grants email marketing
 * only when its "Email marketing" box is ticked under Free-plan features. So the
 * fieldset (tagged `.ow-email-marketing`) shows whenever the plan type is not
 * Free, or the email_marketing feature checkbox is ticked  and hides otherwise,
 * with no page reload.
 */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    var typeSel = document.getElementById("id_plan_type");
    var panels = Array.prototype.slice.call(
      document.querySelectorAll("fieldset.ow-email-marketing")
    );
    if (!panels.length) return;

    var featureBoxes = Array.prototype.slice.call(
      document.querySelectorAll('input[name="included_features"]')
    );

    function emFeatureTicked() {
      for (var i = 0; i < featureBoxes.length; i++) {
        if (featureBoxes[i].value === "email_marketing") {
          return featureBoxes[i].checked;
        }
      }
      return false;
    }

    function sync() {
      var isFree = typeSel ? typeSel.value === "free" : false;
      var show = !isFree || emFeatureTicked();
      panels.forEach(function (panel) {
        panel.style.display = show ? "" : "none";
      });
    }

    if (typeSel) typeSel.addEventListener("change", sync);
    featureBoxes.forEach(function (box) {
      box.addEventListener("change", sync);
    });
    sync();
  });
})();
