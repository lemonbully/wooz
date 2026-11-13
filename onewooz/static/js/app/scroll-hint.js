/* Scroll-for-more hints for hidden-scrollbar navs.
 *
 * For every element marked [data-scroller] (containing a [data-scroller-area]
 * that scrolls and optional .ow-hint--start / .ow-hint--end buttons), detect
 * which axis overflows and reveal a gently pulsing hint at the edge the user
 * can still scroll toward. Re-evaluates on scroll, resize and content changes,
 * so it appears the moment what's shown isn't the whole list  and disappears
 * once that edge is reached. Clicking a hint scrolls a page in that direction.
 */
(function () {
  "use strict";

  var EPS = 2; // px tolerance for "at the edge"

  function init(scroller) {
    if (scroller.__owScrollHint) return; // idempotent
    scroller.__owScrollHint = true;

    var area = scroller.querySelector("[data-scroller-area]") ||
               scroller.querySelector(".ow-scroller__area");
    if (!area) return;
    var startBtn = scroller.querySelector(".ow-hint--start");
    var endBtn = scroller.querySelector(".ow-hint--end");

    function axis() {
      var v = area.scrollHeight - area.clientHeight > EPS;
      var h = area.scrollWidth - area.clientWidth > EPS;
      // Prefer the vertical axis (sidebar); fall back to horizontal (mobile tabs).
      if (v) return "v";
      if (h) return "h";
      return "none";
    }

    function update() {
      var ax = axis();
      scroller.classList.toggle("is-vertical", ax === "v");
      scroller.classList.toggle("is-horizontal", ax === "h");

      var atStart = true, atEnd = true;
      if (ax === "v") {
        atStart = area.scrollTop <= EPS;
        atEnd = area.scrollTop + area.clientHeight >= area.scrollHeight - EPS;
      } else if (ax === "h") {
        atStart = area.scrollLeft <= EPS;
        atEnd = area.scrollLeft + area.clientWidth >= area.scrollWidth - EPS;
      }
      var can = ax !== "none";
      if (startBtn) startBtn.classList.toggle("is-visible", can && !atStart);
      if (endBtn) endBtn.classList.toggle("is-visible", can && !atEnd);
    }

    function page(dir) {
      var vertical = scroller.classList.contains("is-vertical");
      var step = (vertical ? area.clientHeight : area.clientWidth) * 0.8 * dir;
      if (vertical) area.scrollBy({ top: step, behavior: "smooth" });
      else area.scrollBy({ left: step, behavior: "smooth" });
    }

    if (startBtn) startBtn.addEventListener("click", function () { page(-1); });
    if (endBtn) endBtn.addEventListener("click", function () { page(1); });

    // Persist and restore the scroll position across page navigations, so the
    // sidebar stays where the user left it (e.g. on the item they clicked)
    // instead of jumping back to the top on every full-page reload. Keyed by
    // [data-scroller-key], which is stable across pages.
    persistScroll(scroller, area);

    area.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);

    if (window.ResizeObserver) {
      var ro = new ResizeObserver(update);
      ro.observe(area);
      if (area.firstElementChild) ro.observe(area.firstElementChild);
    }
    if (window.MutationObserver) {
      new MutationObserver(update).observe(area, { childList: true, subtree: true });
    }

    update();
    // Content may still be laying out (fonts, JS-built lists); re-check shortly.
    setTimeout(update, 300);
    setTimeout(update, 1200);
  }

  function persistScroll(scroller, area) {
    var key = scroller.getAttribute("data-scroller-key");
    if (!key) return;
    var storeKey = "owscroll:" + key;

    function save() {
      try { sessionStorage.setItem(storeKey, String(area.scrollTop)); } catch (e) {}
    }
    function read() {
      try { return sessionStorage.getItem(storeKey); } catch (e) { return null; }
    }

    function activeVisible() {
      var active = area.querySelector('[aria-current="page"]');
      if (!active) return true;
      var ir = active.getBoundingClientRect();
      var ar = area.getBoundingClientRect();
      return ir.top >= ar.top && ir.bottom <= ar.bottom;
    }
    function centerActive() {
      var active = area.querySelector('[aria-current="page"]');
      if (!active) return;
      var ir = active.getBoundingClientRect();
      var ar = area.getBoundingClientRect();
      area.scrollTop += (ir.top - ar.top) - (area.clientHeight / 2 - active.offsetHeight / 2);
    }

    // Restore the saved position; if the current page's active item isn't shown
    // by it, center that item so the user always sees where they are.
    var saved = read();
    if (saved !== null) area.scrollTop = parseInt(saved, 10) || 0;
    if (saved === null || !activeVisible()) centerActive();

    area.addEventListener("scroll", save, { passive: true });
  }

  function initAll(root) {
    (root || document).querySelectorAll("[data-scroller]").forEach(init);
  }

  window.OWScrollHint = { init: init, initAll: initAll };

  if (document.readyState !== "loading") initAll();
  else document.addEventListener("DOMContentLoaded", function () { initAll(); });
})();
