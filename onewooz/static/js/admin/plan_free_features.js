/*
 * SubscriptionPlan admin  reveal the "Free-plan features" fieldset only when
 * the plan type is Free.
 *
 * The included-features checkboxes decide which dashboard tabs (Edit content,
 * Media, Orders, …) a FREE plan may open; everything left unticked is shown
 * locked as an "Upgrade". They are meaningless for paid / one-time / custom
 * plans, so the whole fieldset (tagged `.ow-free-only` in the ModelAdmin
 * fieldsets) is hidden until "Free" is chosen, and reappears the moment it is 
 * no page reload.
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
    var select = document.getElementById("id_plan_type");
    if (!select) return;

    var panels = Array.prototype.slice.call(
      document.querySelectorAll("fieldset.ow-free-only")
    );
    if (!panels.length) return;

    function sync() {
      var isFree = select.value === "free";
      panels.forEach(function (panel) {
        panel.style.display = isFree ? "" : "none";
      });
    }

    select.addEventListener("change", sync);
    sync();
  });
})();
