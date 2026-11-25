/* Sidebar unread counters (Orders, Requests, Reviews …) that stay true.
 *
 * The server renders each badge from commerce.nav_counts, and opening a page
 * marks it seen. A rendered page never changes on its own, though, so a badge
 * could outlive the moment it was cleared:
 *
 *   - the Back / Forward buttons restore an earlier page (from the browser's
 *     back/forward cache or HTTP cache) with the badges it was drawn with;
 *   - a second dashboard tab keeps showing what it showed when it loaded.
 *
 * So this script:
 *
 *   1. drops a badge the moment its link is clicked (in every copy of the nav:
 *      desktop sidebar and mobile menu), so the page left behind, the one Back
 *      returns to, no longer shows it;
 *   2. re-reads the counts from [data-nav-counts] (ud:store_nav_counts) when a
 *      page is shown from history, when the tab becomes visible again, and
 *      once a minute while it is visible. Reading never marks anything seen.
 */
(function () {
  "use strict";

  var navs = Array.prototype.slice.call(document.querySelectorAll("[data-nav-counts]"));
  if (!navs.length) return;
  var url = navs[0].getAttribute("data-nav-counts");
  if (!url) return;

  var POLL_MS = 60000;
  var MIN_GAP_MS = 10000; // ignore focus flapping
  var lastFetch = 0;
  var inflight = false;

  function linksFor(key) {
    var out = [];
    navs.forEach(function (nav) {
      Array.prototype.forEach.call(nav.querySelectorAll("a[data-nav-key]"), function (a) {
        if (a.getAttribute("data-nav-key") === key) out.push(a);
      });
    });
    return out;
  }

  function setBadge(link, n, max) {
    var badge = link.querySelector(".app-nav__count");
    if (!(n > 0)) {
      if (badge) badge.parentNode.removeChild(badge);
      return;
    }
    if (!badge) {
      badge = document.createElement("span");
      badge.className = "app-nav__count";
      link.appendChild(badge);
    }
    badge.textContent = n > max ? max + "+" : String(n);
    badge.setAttribute("aria-label", n + " new");
  }

  function apply(data) {
    if (!data || typeof data.counts !== "object" || data.counts === null) return;
    var max = typeof data.max === "number" ? data.max : 99;
    Object.keys(data.counts).forEach(function (key) {
      var n = Number(data.counts[key]) || 0;
      linksFor(key).forEach(function (link) { setBadge(link, n, max); });
    });
  }

  function refresh(force) {
    var now = Date.now();
    if (inflight || (!force && now - lastFetch < MIN_GAP_MS)) return;
    inflight = true;
    lastFetch = now;
    fetch(url, {
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
      cache: "no-store",
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(apply)
      .catch(function () {})
      .then(function () { inflight = false; });
  }

  // 1. Opening a page clears its badge right away, everywhere on this page.
  document.addEventListener("click", function (e) {
    var link = e.target.closest ? e.target.closest("a[data-nav-key]") : null;
    if (!link) return;
    var inNav = navs.some(function (nav) { return nav.contains(link); });
    if (!inNav) return;
    linksFor(link.getAttribute("data-nav-key")).forEach(function (a) { setBadge(a, 0, 99); });
  });

  // 2a. Shown again from the back/forward cache: the DOM is the old one.
  window.addEventListener("pageshow", function (e) {
    if (!e.persisted) return;
    inflight = false; // a request cut off when the page was frozen never settles
    refresh(true);
  });

  // 2b. Restored from the HTTP cache by Back / Forward: also an old render.
  try {
    var nav = performance.getEntriesByType && performance.getEntriesByType("navigation")[0];
    if (nav && nav.type === "back_forward") refresh(true);
  } catch (err) { /* older browsers: the poll below still catches up */ }

  // 2c. Coming back to a tab that sat in the background.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refresh(false);
  });

  // 2d. Gentle poll while visible, so new orders etc. appear without a reload.
  setInterval(function () {
    if (document.visibilityState === "visible") refresh(false);
  }, POLL_MS);

  // The render is fresh at load, so the first poll can wait a full interval.
  lastFetch = Date.now();
})();
