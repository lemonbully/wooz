/* OneWooz resource centre (core pages) on onewooz.com.
 *
 * Progressive enhancement only: every page is complete without this file.
 *  - Hub: instant filter over topics and articles ("/" focuses the box).
 *  - Article: section tree starts folded on phones, "On this page" follows
 *    the heading in view, and "Copy link" copies the page address.
 * Loaded as an external file (script-src 'self'), so no inline handlers.
 */
(function () {
  "use strict";

  var each = function (list, fn) { Array.prototype.forEach.call(list || [], fn); };
  var norm = function (s) {
    return (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim();
  };

  // ---------------------------------------------------------------- hub search
  var search = document.querySelector("[data-cp-search]");
  if (search) {
    var input = search.querySelector("input");
    var sections = document.querySelectorAll("[data-cp-section]");
    var quick = document.querySelector("[data-cp-quick]");
    var none = document.querySelector("[data-cp-noresults]");
    var noneTerm = none && none.querySelector("[data-cp-term]");
    search.hidden = false;

    var apply = function () {
      var q = norm(input.value);
      var words = q ? q.split(" ") : [];
      var matches = function (text) {
        text = norm(text);
        for (var i = 0; i < words.length; i++) if (text.indexOf(words[i]) === -1) return false;
        return true;
      };
      var shown = 0;
      each(sections, function (sec) {
        var secHits = 0;
        var sectionMatch = q && matches(sec.getAttribute("data-search"));
        each(sec.querySelectorAll("[data-cp-card]"), function (card) {
          var cardMatch = !q || sectionMatch || matches(card.getAttribute("data-search"));
          var itemHits = 0;
          each(card.querySelectorAll("[data-cp-item]"), function (li) {
            var hit = !q || cardMatch || matches(li.getAttribute("data-search"));
            li.hidden = !hit;
            if (hit) itemHits++;
          });
          var visible = cardMatch || itemHits > 0;
          card.hidden = !visible;
          if (visible) secHits++;
        });
        sec.hidden = !(secHits > 0 || sectionMatch);
        shown += sec.hidden ? 0 : 1;
      });
      if (quick) quick.hidden = !!q;
      if (none) {
        none.hidden = !(q && shown === 0);
        if (noneTerm) noneTerm.textContent = input.value.trim();
      }
    };

    search.addEventListener("submit", function (e) { e.preventDefault(); apply(); });
    input.addEventListener("input", apply);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { input.value = ""; apply(); }
    });
    document.addEventListener("keydown", function (e) {
      var tag = (e.target && e.target.tagName) || "";
      if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag) && !e.target.isContentEditable) {
        e.preventDefault();
        input.focus();
      }
    });
    if (input.value) apply();
  }

  // ------------------------------------------------------- article: side tree
  var navBox = document.querySelector("[data-cp-nav]");
  if (navBox && window.matchMedia) {
    var wide = window.matchMedia("(min-width: 60rem)");
    var sync = function () { navBox.open = wide.matches; };
    sync();
    if (wide.addEventListener) wide.addEventListener("change", sync);
  }

  // --------------------------------------------- article: on-this-page spy
  var tocLinks = document.querySelectorAll("[data-cp-toc] a[href^='#']");
  if (tocLinks.length && "IntersectionObserver" in window) {
    var byId = {};
    var heads = [];
    each(tocLinks, function (a) {
      var id = decodeURIComponent(a.getAttribute("href").slice(1));
      var h = document.getElementById(id);
      if (h) { byId[id] = a; heads.push(h); }
    });
    var current = null;
    var setActive = function (id) {
      if (current === id) return;
      current = id;
      each(tocLinks, function (a) { a.classList.remove("is-active"); });
      if (byId[id]) byId[id].classList.add("is-active");
    };
    var visible = {};
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { visible[en.target.id] = en.isIntersecting; });
      for (var i = 0; i < heads.length; i++) {
        if (visible[heads[i].id]) { setActive(heads[i].id); return; }
      }
      // Nothing in the band: keep the last heading scrolled past.
      var past = null;
      for (var j = 0; j < heads.length; j++) {
        if (heads[j].getBoundingClientRect().top < 120) past = heads[j].id;
      }
      if (past) setActive(past);
    }, { rootMargin: "-72px 0px -60% 0px", threshold: 0 });
    heads.forEach(function (h) { io.observe(h); });
  }

  // ---------------------------------------------------------- copy link
  var copy = document.querySelector("[data-cp-copy]");
  if (copy) {
    copy.hidden = false;
    var label = copy.querySelector("[data-cp-copy-label]");
    var original = label ? label.textContent : "";
    copy.addEventListener("click", function () {
      var url = (document.querySelector("link[rel='canonical']") || {}).href || location.href;
      var done = function () {
        copy.classList.add("is-done");
        if (label) label.textContent = "Copied";
        setTimeout(function () {
          copy.classList.remove("is-done");
          if (label) label.textContent = original;
        }, 1800);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).then(done, function () {});
      } else {
        var t = document.createElement("textarea");
        t.value = url;
        t.setAttribute("readonly", "");
        t.className = "cp-sr";
        document.body.appendChild(t);
        t.select();
        try { document.execCommand("copy"); done(); } catch (e) {}
        document.body.removeChild(t);
      }
    });
  }
})();
