/* Single-page marketing core-page tree editor.
 *
 * Renders the page -> subpage -> child tree as nested accordions (one open at a
 * time per level). Opening a node reveals its fields plus, for pages and
 * subpages, a nested accordion where the next level is created inline. Every
 * change autosaves the whole tree to the tree/save/ endpoint, which returns the
 * saved tree so freshly created rows pick up their server id (and de-duplicated
 * slug) for subsequent in-place updates.
 *
 * Rich text uses the same free TinyMCE 6 build as the rest of the admin, mounted
 * lazily when a node opens and torn down when it closes.
 */
(function () {
  "use strict";

  var root = document.getElementById("mkt-tree");
  if (!root) return;

  var pagesEl = document.getElementById("mkt-pages");
  var emptyEl = document.getElementById("mkt-empty");
  var saveEl = document.getElementById("mkt-save");
  var saveText = document.getElementById("mkt-save__text");
  var saveUrl = root.getAttribute("data-save-url");
  var csrf = (root.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";

  var DATA = [];
  try { DATA = JSON.parse(document.getElementById("mkt-data").textContent) || []; } catch (e) { DATA = []; }

  var UID = 0;

  // Per-level configuration: which fields it has and what it nests.
  var LEVELS = {
    page: {
      fields: ["title", "slug", "flags", "content"],
      flags: [
        { name: "is_published", label: "Published" },
        { name: "show_in_footer", label: "Show in footer" }
      ],
      childKey: "subpages", childLevel: "sub", addLabel: "Add subpage", nestedLabel: "Subpages"
    },
    sub: {
      fields: ["title", "slug", "flags", "content"],
      flags: [{ name: "is_published", label: "Published" }],
      childKey: "children", childLevel: "child", addLabel: "Add child page", nestedLabel: "Child pages"
    },
    child: {
      fields: ["title", "slug", "flags", "content"],
      flags: [{ name: "is_published", label: "Published" }],
      childKey: null
    }
  };

  // ---- TinyMCE ---------------------------------------------------------
  var TINY = {
    menubar: "edit view insert format tools table",
    plugins: "advlist autolink lists link image charmap preview anchor searchreplace visualblocks visualchars code fullscreen insertdatetime media table emoticons help wordcount",
    toolbar: "undo redo | blocks fontsize | bold italic underline strikethrough | forecolor backcolor | alignleft aligncenter alignright alignjustify | bullist numlist outdent indent | link image media table | blockquote hr | removeformat code fullscreen preview",
    height: 340, menubar_sticky: true, branding: false, promotion: false, license_key: "gpl",
    content_style: "body{font-family:Inter,system-ui,sans-serif;font-size:14px;line-height:1.6}",
    paste_data_images: true, automatic_uploads: false, file_picker_types: "image",
    file_picker_callback: function (callback, value, meta) {
      if (meta.filetype !== "image") return;
      var input = document.createElement("input");
      input.type = "file"; input.accept = "image/*";
      input.addEventListener("change", function () {
        var file = input.files && input.files[0]; if (!file) return;
        var reader = new FileReader();
        reader.onload = function () { callback(reader.result, { title: file.name }); };
        reader.readAsDataURL(file);
      });
      input.click();
    },
    setup: function (editor) {
      editor.on("change input undo redo keyup SetContent", function () {
        editor.save();
        var ta = editor.getElement();
        if (ta) ta.dispatchEvent(new Event("input", { bubbles: true }));
      });
    }
  };
  function ownContent(item) {
    // The node's OWN richtext textarea (never a nested node's).
    var fields = item.querySelector(":scope > .mkt-item__body > .mkt-fields");
    return fields ? fields.querySelector(":scope > .mkt-field--content > textarea.admin-richtext") : null;
  }
  function initRich(item) {
    var ta = ownContent(item);
    if (!ta || !window.tinymce || ta.dataset.tmceReady === "1") return;
    ta.dataset.tmceReady = "1";
    window.tinymce.init(Object.assign({ target: ta }, TINY));
  }
  function destroyRich(item) {
    var ta = ownContent(item);
    if (!ta || !window.tinymce) return;
    var ed = ta.id ? window.tinymce.get(ta.id) : null;
    if (ed) ed.remove();
    delete ta.dataset.tmceReady;
  }
  function flushRich() { if (window.tinymce) window.tinymce.triggerSave(); }

  // ---- Small DOM helpers ----------------------------------------------
  function el(tag, cls, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function slugify(s) {
    return String(s || "").toLowerCase().trim()
      .replace(/[^a-z0-9\s-]/g, "").replace(/[\s_-]+/g, "-").replace(/^-+|-+$/g, "");
  }

  // ---- Build a field --------------------------------------------------
  function textField(name, label, value, help) {
    var wrap = el("div", "mkt-field mkt-field--" + name);
    var lab = el("label", "mkt-field__label"); lab.textContent = label;
    var inp = el("input", null, { type: "text", "data-field": name });
    inp.value = value == null ? "" : value;
    wrap.appendChild(lab); wrap.appendChild(inp);
    if (help) { var h = el("p", "mkt-field__help"); h.textContent = help; wrap.appendChild(h); }
    return wrap;
  }
  function contentField(value) {
    var wrap = el("div", "mkt-field mkt-field--content");
    var lab = el("label", "mkt-field__label"); lab.textContent = "Content";
    var ta = el("textarea", "admin-richtext", { "data-field": "content", id: "mkt-rt-" + (++UID) });
    ta.value = value == null ? "" : value;
    wrap.appendChild(lab); wrap.appendChild(ta);
    return wrap;
  }
  function flagsField(flags, data) {
    var wrap = el("div", "mkt-field mkt-field--flags");
    var row = el("div", "mkt-checks");
    flags.forEach(function (f) {
      var lab = el("label", "mkt-check");
      var cb = el("input", null, { type: "checkbox", "data-field": f.name });
      cb.checked = data[f.name] !== false; // default on for new rows
      var span = el("span"); span.textContent = f.label;
      lab.appendChild(cb); lab.appendChild(span); row.appendChild(lab);
    });
    wrap.appendChild(row);
    return wrap;
  }

  // ---- Build one node -------------------------------------------------
  function buildItem(level, data) {
    data = data || {};
    var cfg = LEVELS[level];
    var item = el("div", "mkt-item", { "data-level": level, "data-id": data.id || "" });

    var hdr = el("div", "mkt-item__hdr");
    var toggle = el("button", "mkt-item__toggle", { type: "button" });
    var flag = el("span", "mkt-item__flag", { "data-on": String(data.is_published !== false) });
    var title = el("span", "mkt-item__title");
    var path = el("span", "mkt-item__path");
    var chev = el("span", "mkt-item__chev", { "aria-hidden": "true" });
    toggle.appendChild(flag); toggle.appendChild(title); toggle.appendChild(path); toggle.appendChild(chev);
    var del = el("button", "mkt-item__del", { type: "button", title: "Delete", "aria-label": "Delete" });
    del.innerHTML = "&times;";
    hdr.appendChild(toggle); hdr.appendChild(del);

    var body = el("div", "mkt-item__body");
    var fields = el("div", "mkt-fields");
    cfg.fields.forEach(function (f) {
      if (f === "title") fields.appendChild(textField("title", "Title", data.title));
      else if (f === "slug") fields.appendChild(textField("slug", "Slug (URL)", data.slug, "Lowercase words joined by hyphens. Leave blank to generate from the title."));
      else if (f === "flags") fields.appendChild(flagsField(cfg.flags, data));
      else if (f === "content") fields.appendChild(contentField(data.content));
    });
    body.appendChild(fields);

    if (cfg.childKey) {
      var nested = el("div", "mkt-nested");
      var nlab = el("p", "mkt-nested__label"); nlab.textContent = cfg.nestedLabel;
      var acc = el("div", "mkt-acc", { "data-child-acc": cfg.childKey });
      (data[cfg.childKey] || []).forEach(function (c) { acc.appendChild(buildItem(cfg.childLevel, c)); });
      var add = el("button", "mkt-add mkt-add--nested", { type: "button" });
      add.innerHTML = '<span aria-hidden="true">+</span> ' + cfg.addLabel;
      add.addEventListener("click", function () {
        var row = buildItem(cfg.childLevel, {});
        acc.appendChild(row);
        openItem(row, false);
        refreshHeaders(); queueSave();
      });
      nested.appendChild(nlab); nested.appendChild(acc); nested.appendChild(add);
      body.appendChild(nested);
    }

    item.appendChild(hdr); item.appendChild(body);

    toggle.addEventListener("click", function () { openItem(item, true); });
    del.addEventListener("click", function () {
      var what = level === "page" ? "core page" : (level === "sub" ? "subpage" : "child page");
      if (!window.confirm("Delete this " + what + " and everything inside it?")) return;
      destroyRich(item);
      item.querySelectorAll(".mkt-item").forEach(destroyRich);
      item.remove();
      refreshHeaders(); toggleEmpty(); queueSave();
    });

    updateHeader(item);
    return item;
  }

  // ---- Accordion (one open per level) ---------------------------------
  function openItem(item, toggle) {
    var acc = item.parentNode; // the .mkt-acc this item belongs to
    var isOpen = item.classList.contains("is-open");
    Array.prototype.forEach.call(acc.children, function (sib) {
      if (sib !== item && sib.classList.contains("is-open")) {
        sib.classList.remove("is-open");
        destroyRich(sib);
      }
    });
    if (toggle && isOpen) {
      item.classList.remove("is-open");
      destroyRich(item);
    } else {
      item.classList.add("is-open");
      initRich(item);
    }
  }

  // ---- Header title / path / flag -------------------------------------
  function fieldEl(item, name) {
    var fields = item.querySelector(":scope > .mkt-item__body > .mkt-fields");
    return fields ? fields.querySelector(":scope > [class*='mkt-field'] [data-field='" + name + "']") : null;
  }
  function slugOf(item) {
    var s = fieldEl(item, "slug"), t = fieldEl(item, "title");
    return (s && slugify(s.value)) || (t && slugify(t.value)) || "…";
  }
  function chainOf(item) {
    var chain = [], cur = item;
    while (cur && cur.classList && cur.classList.contains("mkt-item")) {
      chain.unshift(cur);
      cur = cur.parentNode ? cur.parentNode.closest(".mkt-item") : null;
    }
    return chain;
  }
  function updateHeader(item) {
    var t = fieldEl(item, "title"), pub = fieldEl(item, "is_published");
    var titleEl = item.querySelector(":scope > .mkt-item__hdr .mkt-item__title");
    var pathEl = item.querySelector(":scope > .mkt-item__hdr .mkt-item__path");
    var flagEl = item.querySelector(":scope > .mkt-item__hdr .mkt-item__flag");
    var name = (t && t.value.trim()) || "Untitled";
    titleEl.textContent = name;
    titleEl.setAttribute("data-empty", String(!(t && t.value.trim())));
    pathEl.textContent = "/pages/" + chainOf(item).map(slugOf).join("/") + "/";
    if (flagEl && pub) flagEl.setAttribute("data-on", String(!!pub.checked));
  }
  function refreshHeaders() {
    pagesEl.querySelectorAll(".mkt-item").forEach(updateHeader);
  }

  // ---- Gather the whole tree ------------------------------------------
  function gatherItem(item) {
    var level = item.dataset.level, cfg = LEVELS[level];
    var out = { id: item.dataset.id || "" };
    cfg.fields.forEach(function (f) {
      if (f === "flags") {
        cfg.flags.forEach(function (fl) { var e = fieldEl(item, fl.name); out[fl.name] = !!(e && e.checked); });
      } else {
        var e = fieldEl(item, f); out[f] = e ? e.value : "";
      }
    });
    if (cfg.childKey) {
      var acc = item.querySelector(":scope > .mkt-item__body > .mkt-nested > .mkt-acc");
      out[cfg.childKey] = acc ? gatherAcc(acc) : [];
    }
    return out;
  }
  function gatherAcc(acc) {
    return Array.prototype.map.call(acc.querySelectorAll(":scope > .mkt-item"), gatherItem);
  }
  function gatherTree() { return { pages: gatherAcc(pagesEl) }; }

  // ---- Reconcile server ids/slugs back into the DOM -------------------
  function reconcile(acc, arr) {
    var items = acc.querySelectorAll(":scope > .mkt-item");
    if (items.length !== arr.length) return; // structure changed mid-save; skip
    arr.forEach(function (d, i) {
      var item = items[i];
      item.dataset.id = d.id;
      var slug = fieldEl(item, "slug");
      if (slug && document.activeElement !== slug) slug.value = d.slug || "";
      var cfg = LEVELS[item.dataset.level];
      if (cfg.childKey && d[cfg.childKey]) {
        var childAcc = item.querySelector(":scope > .mkt-item__body > .mkt-nested > .mkt-acc");
        if (childAcc) reconcile(childAcc, d[cfg.childKey]);
      }
    });
    refreshHeaders();
  }

  // ---- Save (debounced, serialised) -----------------------------------
  var timer = null, inflight = false, pending = false;
  function setStatus(state) {
    saveEl.setAttribute("data-state", state === "saved" ? "idle" : state);
    saveText.textContent = state === "saving" ? "Saving…" : state === "error" ? "Couldn't save" : "Saved";
  }
  function queueSave() {
    setStatus("saving");
    clearTimeout(timer);
    timer = setTimeout(doSave, 700);
  }
  function doSave() {
    if (inflight) { pending = true; return; }
    inflight = true; setStatus("saving");
    flushRich();
    var payload = gatherTree();
    fetch(saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(payload)
    })
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function (d) {
        if (d && d.pages) reconcile(pagesEl, d.pages);
        setStatus("saved");
      })
      .catch(function () { setStatus("error"); })
      .then(function () {
        inflight = false;
        if (pending) { pending = false; doSave(); }
      });
  }

  function toggleEmpty() {
    var has = pagesEl.querySelector(":scope > .mkt-item");
    if (emptyEl) emptyEl.hidden = !!has;
  }

  // ---- Wire it up -----------------------------------------------------
  DATA.forEach(function (p) { pagesEl.appendChild(buildItem("page", p)); });
  toggleEmpty();
  refreshHeaders();  // fix nested paths now that the tree is attached

  document.getElementById("mkt-add-page").addEventListener("click", function () {
    var row = buildItem("page", {});
    pagesEl.appendChild(row);
    openItem(row, false);
    toggleEmpty(); refreshHeaders(); queueSave();
  });

  // Any edit within the tree updates its header and schedules a save.
  root.addEventListener("input", function (e) {
    var item = e.target.closest(".mkt-item");
    if (item) updateHeader(item);
    if (e.target.closest("#mkt-pages")) queueSave();
  });
  root.addEventListener("change", function (e) {
    if (e.target.type === "checkbox") {
      var item = e.target.closest(".mkt-item");
      if (item) updateHeader(item);
      queueSave();
    }
  });
})();
