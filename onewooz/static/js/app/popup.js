/* Simple popups for the dashboard, on the .app-modal shell.
 *
 *   <button data-popup-open="my-modal">…</button>
 *   <div class="app-modal app-modal--auto" id="my-modal" role="dialog" aria-modal="true" hidden>
 *     <div class="app-modal__backdrop" data-popup-close></div>
 *     <div class="app-modal__panel">… <button data-popup-close>×</button> …</div>
 *   </div>
 *
 * Escape and the backdrop close it; focus returns to the opener. Pages can also
 * call window.OWPopup.open(id) / .close(id). Opening one popup closes another.
 */
(function () {
  "use strict";
  var current = null;
  var opener = null;

  function byId(id) { return id ? document.getElementById(id) : null; }

  function close(id) {
    var el = id ? byId(id) : current;
    if (!el) return;
    el.classList.remove("is-open");
    el.hidden = true;
    if (el === current) current = null;
    document.body.classList.remove("app-popup-open");
    el.dispatchEvent(new CustomEvent("popup:close"));
    if (opener && typeof opener.focus === "function") { try { opener.focus(); } catch (e) {} }
    opener = null;
  }

  function open(id, trigger) {
    var el = byId(id);
    if (!el) return null;
    if (current && current !== el) close();
    opener = trigger || document.activeElement;
    // Lift it to the top of the themed page (the ".pal" root, which defines the
    // colour variables the modal uses) so no positioned ancestor (the sticky
    // top bar, a closed <details>) can clip or hide the fixed overlay.
    var host = document.querySelector("body > .pal") || document.querySelector(".pal") || document.body;
    if (el.parentNode !== host) host.appendChild(el);
    el.hidden = false;
    el.classList.add("is-open");
    current = el;
    document.body.classList.add("app-popup-open");
    el.dispatchEvent(new CustomEvent("popup:open", { detail: { trigger: trigger || null } }));
    var focusable = el.querySelector("[data-popup-focus]") || el.querySelector(".app-modal__x");
    if (focusable) { try { focusable.focus(); } catch (e) {} }
    return el;
  }

  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-popup-open]") : null;
    if (t) {
      e.preventDefault();
      // Close a surrounding <details> menu (the bell) before opening.
      var menu = t.closest("details[open]");
      if (menu) menu.removeAttribute("open");
      open(t.getAttribute("data-popup-open"), t);
      return;
    }
    var c = e.target.closest ? e.target.closest("[data-popup-close]") : null;
    if (c && current && current.contains(c)) { e.preventDefault(); close(); }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && current) close();
  });

  window.OWPopup = { open: open, close: close };
})();
