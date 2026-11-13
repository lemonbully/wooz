/* Locked-until-Edit settings forms (Payments, Shipping carrier, Settings).
 *
 * Markup (see templates/store/_edit_lock_bar.html):
 *   <form data-edit-lock[="open"]>
 *     {% include "store/_edit_lock_bar.html" %}
 *     <fieldset class="elock-fs" data-edit-lock-fields> …inputs… </fieldset>
 *     <div data-edit-lock-foot> <button data-edit-lock-cancel> <button type=submit> </div>
 *   </form>
 *
 * The form opens locked: its fieldset is disabled, so no input, select,
 * checkbox or picker button can change, and the Save footer is hidden. "Edit"
 * unlocks it. "Cancel" relocks it, reloading the saved values if anything was
 * touched. data-edit-lock="open" (a submit that came back with errors) starts
 * unlocked so the owner can fix the fields straight away.
 */
(function () {
  "use strict";

  function init(form) {
    var bar = form.querySelector("[data-edit-lock-bar]");
    var fields = form.querySelector("[data-edit-lock-fields]");
    if (!bar || !fields) return;
    var foot = form.querySelector("[data-edit-lock-foot]");
    var editBtn = bar.querySelector("[data-edit-lock-edit]");
    var text = bar.querySelector("[data-edit-lock-text]");
    var cancels = form.querySelectorAll("[data-edit-lock-cancel]");
    var lockedText = bar.getAttribute("data-locked-text") || "";
    var editingText = bar.getAttribute("data-editing-text") || "";
    var touched = false;
    var locked = false;

    function setLocked(on) {
      locked = on;
      fields.disabled = on;
      fields.setAttribute("aria-disabled", on ? "true" : "false");
      form.classList.toggle("is-edit-locked", on);
      form.classList.toggle("is-editing", !on);
      if (foot) foot.hidden = on;
      if (editBtn) editBtn.hidden = !on;
      Array.prototype.forEach.call(cancels, function (b) {
        // Only the bar's own Cancel follows the state; a footer Cancel is
        // already hidden with its footer.
        if (bar.contains(b)) b.hidden = on;
      });
      if (text) text.textContent = on ? lockedText : editingText;
    }

    function firstControl() {
      return fields.querySelector(
        "input:not([type='hidden']):not([disabled]), select:not([tabindex='-1']), textarea, .ow-cs__btn, button"
      );
    }

    if (editBtn) {
      editBtn.addEventListener("click", function () {
        setLocked(false);
        var first = firstControl();
        if (first && first.focus) {
          try { first.focus({ preventScroll: true }); } catch (e) { first.focus(); }
        }
      });
    }

    Array.prototype.forEach.call(cancels, function (b) {
      b.addEventListener("click", function () {
        if (touched) {
          // Throw the unsaved edits away by reloading the saved values (a GET,
          // even when this page is the result of a POST).
          window.location.assign(window.location.pathname + window.location.search);
          return;
        }
        setLocked(true);
        if (bar.scrollIntoView) bar.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    });

    // Anything that may have changed a value. Picker buttons (signature,
    // pickup locations) change hidden inputs without an input event.
    fields.addEventListener("input", function () { touched = true; });
    fields.addEventListener("change", function () { touched = true; });
    fields.addEventListener("click", function (e) {
      if (locked) {
        // A click on the locked area points the owner at the Edit button.
        bar.classList.remove("is-nudge");
        void bar.offsetWidth;
        bar.classList.add("is-nudge");
        return;
      }
      if (e.target.closest && e.target.closest("button")) touched = true;
    });

    // Never submit a locked form (e.g. Enter in a field a script re-enabled).
    form.addEventListener("submit", function (e) { if (locked) e.preventDefault(); });

    bar.hidden = false;
    setLocked(form.getAttribute("data-edit-lock") !== "open");
  }

  function start() {
    Array.prototype.forEach.call(document.querySelectorAll("form[data-edit-lock]"), init);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
