/* OneWooz Apps (marketplace.onewooz.com): progressive enhancements.
 *
 * Every page works without this file (search is a GET form, filters and sort
 * are links / a form). It adds:
 *   - search suggestions under the header box ([data-mk-suggest]), fed by
 *     #mk-palette-data; Ctrl/Cmd+K or "/" focuses the box
 *   - "common need" tabs ([data-mk-tabs])
 *   - sort select that applies on change ([data-mk-autosubmit])
 *   - filters collapsed by default on phones
 *   - the active tab on app pages follows the section in view ([data-mk-spy])
 */
(function () {
  "use strict";

  var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function norm(s) { return (s || "").toLowerCase().replace(/\s+/g, " ").trim(); }

  $$("[data-mk-mod]").forEach(function (k) { k.textContent = isMac ? "⌘ K" : "Ctrl K"; });

  // ---- Search suggestions ------------------------------------------------------
  var entries = [];
  try {
    var raw = document.getElementById("mk-palette-data");
    entries = raw ? JSON.parse(raw.textContent) || [] : [];
  } catch (err) { entries = []; }

  var form = $("[data-mk-suggest]");
  if (form) {
    var input = $("input[name=q]", form);
    var list = $(".mk-suggest", form);
    var shown = [], active = -1;

    var score = function (e, q) {
      var name = norm(e.n), text = norm(e.t + " " + e.c);
      if (name.indexOf(q) === 0) return 4;
      if (name.indexOf(q) > -1) return 3;
      var words = q.split(" "), hits = 0;
      words.forEach(function (w) { if (w && (name.indexOf(w) > -1 || text.indexOf(w) > -1)) hits++; });
      return hits === words.length ? 2 : hits ? 1 : 0;
    };
    var el = function (tag, cls, text) {
      var n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text != null) n.textContent = text;
      return n;
    };
    var close = function () {
      list.hidden = true;
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
      active = -1;
    };
    var select = function (i) {
      var items = $$(".mk-suggest__item", list);
      if (!items.length) return;
      active = (i + items.length) % items.length;
      items.forEach(function (li, n) { li.setAttribute("aria-selected", n === active ? "true" : "false"); });
      input.setAttribute("aria-activedescendant", items[active].id);
    };
    var render = function () {
      var q = norm(input.value);
      if (!q) {
        shown = entries.filter(function (e) { return e.k === "problem"; }).slice(0, 6);
      } else {
        shown = entries
          .map(function (e) { return { e: e, s: score(e, q) }; })
          .filter(function (x) { return x.s > 0; })
          .sort(function (a, b) { return b.s - a.s; })
          .slice(0, 8)
          .map(function (x) { return x.e; });
      }
      list.textContent = "";
      active = -1;
      if (!shown.length) {
        list.appendChild(el("li", "mk-suggest__none", "Press Enter to search every app for “" + input.value + "”"));
      }
      var group = "";
      shown.forEach(function (e, i) {
        var g = e.k === "problem" ? (q ? "Common needs" : "Popular needs") : "Apps";
        if (g !== group) {
          group = g;
          var h = el("li", "mk-suggest__group", g);
          h.setAttribute("role", "presentation");
          list.appendChild(h);
        }
        var li = el("li", "mk-suggest__item");
        li.setAttribute("role", "option");
        li.id = "mk-sg-" + i;
        li.setAttribute("aria-selected", "false");
        var a = el("a");
        a.href = e.u;
        a.tabIndex = -1;
        var tile = el("span", "mk-suggest__tile" + (e.k === "problem" ? " is-need" : ""), e.k === "problem" ? "?" : (e.n || "?").charAt(0));
        if (e.a) tile.style.setProperty("--tile", e.a);
        var body = el("span", "mk-suggest__body");
        body.appendChild(el("span", "mk-suggest__name", e.n));
        body.appendChild(el("span", "mk-suggest__desc", e.t));
        a.appendChild(tile);
        a.appendChild(body);
        a.appendChild(el("span", "mk-suggest__cat", e.c));
        a.addEventListener("mousemove", function () { select(i); });
        li.appendChild(a);
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute("aria-expanded", "true");
    };

    input.addEventListener("focus", render);
    input.addEventListener("input", render);
    input.addEventListener("keydown", function (e) {
      if (list.hidden) return;
      if (e.key === "ArrowDown") { e.preventDefault(); select(active + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); select(active - 1); }
      else if (e.key === "Escape") { close(); }
      else if (e.key === "Enter" && active > -1 && shown[active]) {
        e.preventDefault();
        window.location.href = shown[active].u;
      }
    });
    document.addEventListener("click", function (e) { if (!form.contains(e.target)) close(); });
    document.addEventListener("keydown", function (e) {
      var t = e.target, typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) { e.preventDefault(); input.focus(); input.select(); }
      else if (e.key === "/" && !typing) { e.preventDefault(); input.focus(); }
    });
  }

  // ---- Common-need tabs ------------------------------------------------------------
  $$("[data-mk-tabs]").forEach(function (root) {
    var tabs = $$("[role=tab]", root);
    var panels = tabs.map(function (t) { return document.getElementById(t.getAttribute("aria-controls")); });
    var show = function (i, focus) {
      tabs.forEach(function (t, n) {
        var on = n === i;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        if (panels[n]) panels[n].hidden = !on;
      });
      if (focus) tabs[i].focus();
    };
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { show(i, false); });
      t.addEventListener("keydown", function (e) {
        var k = e.key, n = null;
        if (k === "ArrowDown" || k === "ArrowRight") n = (i + 1) % tabs.length;
        else if (k === "ArrowUp" || k === "ArrowLeft") n = (i - 1 + tabs.length) % tabs.length;
        else if (k === "Home") n = 0;
        else if (k === "End") n = tabs.length - 1;
        if (n !== null) { e.preventDefault(); show(n, true); }
      });
    });
    root.setAttribute("data-ready", "");
    show(0, false);
  });

  // ---- Sort applies on change -------------------------------------------------------------
  $$("[data-mk-autosubmit]").forEach(function (s) {
    var go = s.form && $(".mk-sort__go", s.form);
    if (go) go.hidden = true;
    s.addEventListener("change", function () { if (s.form) s.form.submit(); });
  });

  // ---- Filters start collapsed on phones -----------------------------------------------------
  // (always open on wider screens, where the toggle is hidden).
  if (window.matchMedia) {
    var phone = window.matchMedia("(max-width: 56rem)");
    var wraps = $$(".mk-filters__wrap");
    wraps.forEach(function (d) { d.open = !phone.matches; });
    var sync = function () { if (!phone.matches) wraps.forEach(function (d) { d.open = true; }); };
    if (phone.addEventListener) phone.addEventListener("change", sync); else if (phone.addListener) phone.addListener(sync);
  }

  // ---- App page: active tab follows the section in view ----------------------------------------
  var spy = $("[data-mk-spy]");
  if (spy && "IntersectionObserver" in window) {
    var links = $$("a[href^='#']", spy);
    var byId = {};
    links.forEach(function (a) { byId[a.getAttribute("href").slice(1)] = a; });
    var io = new IntersectionObserver(function (seen) {
      seen.forEach(function (s) {
        if (!s.isIntersecting) return;
        links.forEach(function (a) { a.removeAttribute("aria-current"); });
        var a = byId[s.target.id];
        if (a) a.setAttribute("aria-current", "true");
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    Object.keys(byId).forEach(function (id) { var t = document.getElementById(id); if (t) io.observe(t); });
  }
})();
