/*
 * OneWooz custom select (ow-cs)
 * ------------------------------------------------------------------
 * Progressive enhancement for <select class="pal-select">. The native
 * <select> stays in the DOM as the form value and the no-JS fallback;
 * we hide it and build a squared, hairline, single-accent dropdown that
 * matches the  design system (see onewooz_design.md).
 *
 * Accessible listbox: button[aria-haspopup=listbox] + ul[role=listbox]
 * with roving aria-activedescendant, full keyboard control and type-ahead.
 * CSP-safe (external file, no inline handlers, no inline styles).
 *
 * Opt out per element with `data-no-enhance`. Multi-selects are skipped.
 */
(function () {
  "use strict";

  var OPEN = null; // the currently open instance, if any

  function enhance(select) {
    if (!select || select.dataset.owcs === "1" || select.multiple || select.hasAttribute("data-no-enhance")) return;
    // Never touch selects that belong to a third-party widget's own chrome
    // (e.g. TinyMCE's `.tox` UI in the content editor).
    if (select.closest && select.closest(".tox, [data-no-enhance]")) return;
    select.dataset.owcs = "1";

    var wrap = document.createElement("div");
    wrap.className = "ow-cs";
    // Carry the compact variant across so small inline selects stay small.
    if (select.classList.contains("pal-select--sm") || select.classList.contains("pal-input--sm")) wrap.classList.add("ow-cs--sm");
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("ow-cs__native");
    select.setAttribute("tabindex", "-1");
    select.setAttribute("aria-hidden", "true");

    var labelText = labelFor(select);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ow-cs__btn";
    btn.setAttribute("aria-haspopup", "listbox");
    btn.setAttribute("aria-expanded", "false");
    if (labelText) btn.setAttribute("aria-label", labelText);
    if (select.disabled) btn.disabled = true;

    var lbl = document.createElement("span");
    lbl.className = "ow-cs__label";
    var caret = document.createElement("span");
    caret.className = "ow-cs__caret";
    caret.setAttribute("aria-hidden", "true");
    btn.appendChild(lbl);
    btn.appendChild(caret);

    var panel = document.createElement("ul");
    panel.className = "ow-cs__panel";
    panel.setAttribute("role", "listbox");
    var pid = "owcs-" + Math.random().toString(36).slice(2, 8);
    panel.id = pid;
    if (labelText) panel.setAttribute("aria-label", labelText);
    btn.setAttribute("aria-controls", pid);

    wrap.appendChild(btn);
    wrap.appendChild(panel);

    var api = { select: select, wrap: wrap, btn: btn, panel: panel, lbl: lbl, opts: [], active: -1, typed: "", typedAt: 0 };
    wrap._owcs = api;
    buildOptions(api);
    syncFromNative(api);

    btn.addEventListener("click", function () { toggle(api); });
    btn.addEventListener("keydown", function (e) { onKey(api, e); });
    panel.addEventListener("click", function (e) {
      var li = e.target.closest(".ow-cs__opt");
      if (!li || li.getAttribute("aria-disabled") === "true") return;
      choose(api, parseInt(li.dataset.index, 10), true);
    });
    panel.addEventListener("mousemove", function (e) {
      var li = e.target.closest(".ow-cs__opt");
      if (li && li.getAttribute("aria-disabled") !== "true") setActive(api, parseInt(li.dataset.index, 10), false);
    });
    // Reflect any programmatic change back into the button/list.
    select.addEventListener("change", function () { syncFromNative(api); });
    // form.reset() restores the native value without firing "change", which
    // left the button showing the old choice. The "reset" event fires before
    // the values are restored, so resync on the next tick.
    if (select.form) {
      select.form.addEventListener("reset", function () {
        setTimeout(function () { syncFromNative(api); }, 0);
      });
    }
  }

  function labelFor(select) {
    if (select.getAttribute("aria-label")) return select.getAttribute("aria-label").trim();
    if (select.id) {
      var l = document.querySelector('label[for="' + select.id + '"]');
      if (l) return l.textContent.trim();
    }
    var wrapping = select.closest("label");
    if (wrapping) {
      var span = wrapping.querySelector(".pal-field__label");
      if (span) return span.textContent.trim();
    }
    return select.name || "";
  }

  // (Re)build the listbox <li>s from the native <select>'s current options.
  // Used at enhance time and again whenever the options change dynamically.
  function buildOptions(api) {
    var pid = api.panel.id;
    api.panel.innerHTML = "";
    api.opts = [];
    Array.prototype.forEach.call(api.select.options, function (o, i) {
      var li = document.createElement("li");
      li.className = "ow-cs__opt";
      li.setAttribute("role", "option");
      li.id = pid + "-o" + i;
      li.dataset.index = String(i);
      li.textContent = o.textContent;
      if (o.disabled) li.setAttribute("aria-disabled", "true");
      api.panel.appendChild(li);
      api.opts.push(li);
    });
    api.active = -1;
  }

  // Public refresh: re-read a native select whose <option>s changed after it was
  // enhanced (dependent dropdowns, injected forms). Enhances it if not yet done.
  function rebuild(select) {
    if (!select) return;
    var wrap = select.closest ? select.closest(".ow-cs") : null;
    var api = wrap && wrap._owcs;
    if (!api) { enhance(select); return; }
    buildOptions(api);
    syncFromNative(api);
  }

  function syncFromNative(api) {
    var sel = api.select;
    var i = sel.selectedIndex;
    var opt = i >= 0 ? sel.options[i] : null;
    api.lbl.textContent = opt ? opt.textContent : "";
    api.lbl.classList.toggle("ow-cs__label--placeholder", !!opt && opt.value === "");
    api.opts.forEach(function (li, idx) {
      li.setAttribute("aria-selected", idx === i ? "true" : "false");
    });
  }

  function choose(api, index, close) {
    if (index < 0 || index >= api.select.options.length) return;
    if (api.select.selectedIndex !== index) {
      api.select.selectedIndex = index;
      api.select.dispatchEvent(new Event("input", { bubbles: true }));
      api.select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    syncFromNative(api);
    if (close) closeMenu(api, true);
  }

  function setActive(api, index, scroll) {
    if (api.active === index) return;
    api.active = index;
    api.opts.forEach(function (li, idx) { li.classList.toggle("is-active", idx === index); });
    if (index >= 0) {
      api.panel.setAttribute("aria-activedescendant", api.opts[index].id);
      if (scroll) api.opts[index].scrollIntoView({ block: "nearest" });
    } else {
      api.panel.removeAttribute("aria-activedescendant");
    }
  }

  function openMenu(api) {
    if (OPEN && OPEN !== api) closeMenu(OPEN, false);
    OPEN = api;
    api.wrap.classList.add("is-open");
    api.btn.setAttribute("aria-expanded", "true");
    // Flip upward when there isn't room below.
    var r = api.btn.getBoundingClientRect();
    var below = window.innerHeight - r.bottom;
    api.wrap.classList.toggle("ow-cs--up", below < 260 && r.top > below);
    setActive(api, api.select.selectedIndex >= 0 ? api.select.selectedIndex : firstEnabled(api), true);
  }

  function closeMenu(api, focusBtn) {
    api.wrap.classList.remove("is-open", "ow-cs--up");
    api.btn.setAttribute("aria-expanded", "false");
    setActive(api, -1, false);
    if (OPEN === api) OPEN = null;
    if (focusBtn) api.btn.focus();
  }

  function toggle(api) {
    if (api.wrap.classList.contains("is-open")) closeMenu(api, true);
    else openMenu(api);
  }

  function firstEnabled(api) {
    for (var i = 0; i < api.opts.length; i++) if (api.opts[i].getAttribute("aria-disabled") !== "true") return i;
    return -1;
  }
  function lastEnabled(api) {
    for (var i = api.opts.length - 1; i >= 0; i--) if (api.opts[i].getAttribute("aria-disabled") !== "true") return i;
    return -1;
  }
  function move(api, delta) {
    var n = api.opts.length;
    if (!n) return;
    var i = api.active < 0 ? api.select.selectedIndex : api.active;
    if (i < 0) i = delta > 0 ? -1 : 0;
    for (var step = 0; step < n; step++) {
      i = (i + delta + n) % n;
      if (api.opts[i].getAttribute("aria-disabled") !== "true") { setActive(api, i, true); return; }
    }
  }

  function typeahead(api, ch) {
    var now = Date.now();
    api.typed = (now - api.typedAt < 700) ? api.typed + ch : ch;
    api.typedAt = now;
    var q = api.typed.toLowerCase();
    for (var i = 0; i < api.opts.length; i++) {
      if (api.opts[i].getAttribute("aria-disabled") === "true") continue;
      if (api.opts[i].textContent.trim().toLowerCase().indexOf(q) === 0) {
        if (api.wrap.classList.contains("is-open")) setActive(api, i, true);
        else choose(api, i, false);
        return;
      }
    }
  }

  function onKey(api, e) {
    var open = api.wrap.classList.contains("is-open");
    switch (e.key) {
      case "ArrowDown":
      case "ArrowUp":
        e.preventDefault();
        if (!open) openMenu(api);
        else move(api, e.key === "ArrowDown" ? 1 : -1);
        break;
      case "Home":
        if (open) { e.preventDefault(); setActive(api, firstEnabled(api), true); }
        break;
      case "End":
        if (open) { e.preventDefault(); setActive(api, lastEnabled(api), true); }
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (!open) openMenu(api);
        else if (api.active >= 0) choose(api, api.active, true);
        break;
      case "Escape":
        if (open) { e.preventDefault(); closeMenu(api, true); }
        break;
      case "Tab":
        if (open) closeMenu(api, false);
        break;
      default:
        if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); typeahead(api, e.key); }
    }
  }

  // Global dismissers.
  document.addEventListener("mousedown", function (e) {
    if (OPEN && !OPEN.wrap.contains(e.target)) closeMenu(OPEN, false);
  });
  window.addEventListener("resize", function () { if (OPEN) closeMenu(OPEN, false); });
  // Close on page/container scroll so the panel never floats detached from its
  // button  but NOT when the scroll happens inside the open dropdown's own
  // panel (a long, scrollable option list must stay open while you scroll it).
  window.addEventListener(
    "scroll",
    function (e) {
      if (!OPEN) return;
      var t = e.target;
      if (t && t.nodeType === 1 && OPEN.wrap.contains(t)) return;
      closeMenu(OPEN, false);
    },
    true
  );

  function init(root) {
    var scope = root && root.querySelectorAll ? root : document;
    Array.prototype.forEach.call(scope.querySelectorAll("select"), enhance);
  }

  // Enhance selects added to the DOM after load  the content editor rebuilds
  // its fields per tab, modals inject their forms, wizards add steps.
  function watch() {
    if (!window.MutationObserver) return;
    var obs = new MutationObserver(function (mutations) {
      for (var m = 0; m < mutations.length; m++) {
        var added = mutations[m].addedNodes;
        for (var i = 0; i < added.length; i++) {
          var node = added[i];
          if (!node || node.nodeType !== 1) continue;
          if (node.tagName === "SELECT") enhance(node);
          else if (node.querySelectorAll) Array.prototype.forEach.call(node.querySelectorAll("select"), enhance);
        }
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  function boot() { init(document); watch(); }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Expose for pages that inject selects dynamically.
  window.OwSelect = { enhance: enhance, init: init, rebuild: rebuild };
})();
