/* ==========================================================================
   OneWooz  Media library controller
   --------------------------------------------------------------------------
   One controller drives two surfaces that share a DOM contract:

     • the library PAGE grid (server-rendered `.app-medgrid` of `.app-medcell`)
     • the universal MODAL (`#ow-media`) mounted once in the store shell

   Every other surface (product galleries, category images, banners, section
   content) opens the same modal through `window.OneWoozMedia.open(...)` and
   receives the chosen asset(s) back  the file is uploaded and described once,
   then reused by reference, never re-uploaded per use.

   CSP-safe: no inline handlers, no eval; every listener is bound here. The CSRF
   token is read from the hidden field Django rendered inside the modal, and all
   requests are same-origin fetches.
   ========================================================================== */
(function () {
  "use strict";

  var modal = document.getElementById("ow-media");
  var pageGrid = document.querySelector("[data-medlib-grid]");
  if (!modal && !pageGrid) return; // Not a media surface.

  // ---- Config (from the modal's data-* ; the page grid carries its own) -----
  var cfgHost = modal || pageGrid;
  var CFG = {
    listUrl: cfgHost.getAttribute("data-list-url"),
    uploadUrl: cfgHost.getAttribute("data-upload-url"),
    // URL templates carry a placeholder id we substitute per asset.
    updateTmpl: cfgHost.getAttribute("data-update-tmpl"),
    deleteTmpl: cfgHost.getAttribute("data-delete-tmpl"),
    bulkDeleteUrl: cfgHost.getAttribute("data-bulk-delete-url"),
  };
  var PLACEHOLDER = "00000000-0000-0000-0000-000000000000";

  function urlFor(tmpl, id) {
    return tmpl ? tmpl.replace(PLACEHOLDER, id) : "";
  }

  function csrfToken() {
    var el = (modal || document).querySelector("input[name=csrfmiddlewaretoken]");
    if (el) return el.value;
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
  }

  // ---- Small DOM helpers ----------------------------------------------------
  function el(tag, cls, attrs) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === "text") n.textContent = attrs[k];
        else n.setAttribute(k, attrs[k]);
      });
    }
    return n;
  }
  function humanSize(bytes) {
    bytes = Number(bytes) || 0;
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    if (bytes >= 1024) return Math.round(bytes / 1024) + " KB";
    return bytes + " B";
  }

  // ---- Cell factory (identical markup to the server-rendered page cell) ------
  // `context` is "modal" (adds a select affordance) or "page".
  function buildCell(a, context) {
    var cell = el("figure", "app-medcell", {
      "data-id": a.id,
      "data-kind": a.kind,
      "data-url": a.url || "",
      "data-name": a.name || "",
      "data-alt": a.alt || "",
      "data-description": a.description || "",
      "data-placeholder": a.placeholder || "",
    });

    var thumb = el("div", "app-medcell__thumb");
    if (a.kind === "video") {
      var vid = el("video", "app-medcell__media", { src: a.url, muted: "", playsinline: "", preload: "metadata" });
      thumb.appendChild(vid);
      thumb.appendChild(el("span", "app-medcell__vbadge", { text: "VIDEO" }));
    } else {
      var img = el("img", "app-medcell__media", { src: a.url, alt: a.alt || a.name || "", loading: "lazy" });
      thumb.appendChild(img);
    }

    if (context === "modal") {
      var pick = el("button", "app-medcell__pick", { type: "button", "data-med-pick": a.id, "aria-label": "Select " + (a.name || "media") });
      pick.appendChild(el("span", "app-medcell__tick", { "aria-hidden": "true" }));
      thumb.appendChild(pick);
    }

    // Multi-select management tick  shown only while manage mode is on
    // (CSS gates it on `body.ow-managing`). Present in both contexts.
    thumb.appendChild(el("span", "app-medcell__mcheck", { "data-med-mcheck": "", "aria-hidden": "true" }));

    // Hover reveals a single Edit action; a plain click on the cell opens the
    // media in a full preview (see the delegated click handler). Deletion lives
    // inside the Edit panel and in bulk-management mode.
    var acts = el("div", "app-medcell__acts");
    var edit = el("button", "app-medcell__act", { type: "button", "data-med-edit": a.id, "aria-label": "Edit", title: "Edit", text: "Edit" });
    acts.appendChild(edit);
    thumb.appendChild(acts);

    cell.appendChild(thumb);
    var cap = el("figcaption", "app-medcell__cap");
    cap.appendChild(el("span", "app-medcell__name", { text: a.name || "Untitled" }));
    cap.appendChild(el("span", "app-medcell__meta", { text: humanSize(a.size) }));
    cell.appendChild(cap);
    return cell;
  }

  // Read the descriptor fields back off a cell's data-* attributes.
  function assetFromCell(cell) {
    return {
      id: cell.getAttribute("data-id"),
      kind: cell.getAttribute("data-kind"),
      url: cell.getAttribute("data-url"),
      name: cell.getAttribute("data-name"),
      alt: cell.getAttribute("data-alt"),
      description: cell.getAttribute("data-description"),
      placeholder: cell.getAttribute("data-placeholder"),
    };
  }

  function findCell(id) {
    return document.querySelector('.app-medcell[data-id="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
  }

  /* ========================================================================
     MODAL
     ===================================================================== */
  var M = null; // lazily-resolved modal element refs
  if (modal) {
    M = {
      root: modal,
      grid: modal.querySelector("[data-med-grid]"),
      empty: modal.querySelector("[data-med-empty]"),
      loading: modal.querySelector("[data-med-loading]"),
      search: modal.querySelector("[data-med-search]"),
      chips: modal.querySelectorAll("[data-med-kind]"),
      pagePrev: modal.querySelector("[data-med-prev]"),
      pageNext: modal.querySelector("[data-med-next]"),
      pageAt: modal.querySelector("[data-med-at]"),
      pager: modal.querySelector("[data-med-pager]"),
      file: modal.querySelector("[data-med-file]"),
      drop: modal.querySelector("[data-med-drop]"),
      selectBtn: modal.querySelector("[data-med-confirm]"),
      selCount: modal.querySelector("[data-med-selcount]"),
      title: modal.querySelector("[data-med-title]"),
      edit: modal.querySelector("[data-med-editpanel]"),
    };
  }

  var state = {
    open: false,
    kind: "",
    q: "",
    page: 1,
    numPages: 1,
    multiple: false,
    picking: false, // true when opened via open() with an onSelect callback
    noWebp: false,  // when true, uploads made through this open() keep their original format
    selected: [],
    onSelect: null,
    reqSeq: 0,
  };

  function setBodyLock(lock) {
    document.documentElement.style.overflow = lock ? "hidden" : "";
  }

  function openModal(opts) {
    opts = opts || {};
    state.open = true;
    state.multiple = !!opts.multiple;
    state.picking = typeof opts.onSelect === "function";
    state.noWebp = !!opts.noWebp;
    state.onSelect = opts.onSelect || null;
    state.kind = opts.kind === "image" || opts.kind === "video" ? opts.kind : "";
    state.q = "";
    state.selected = [];
    setManageMode(false); // Picking and bulk-management are mutually exclusive.
    if (M.search) M.search.value = "";
    syncKindChips();
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    setBodyLock(true);
    closeEdit();
    // Selection UI only matters when a caller is picking.
    modal.classList.toggle("is-picking", state.picking);
    if (M.title) M.title.textContent = state.picking ? (opts.title || "Select media") : "Media library";
    if (M.file) {
      var accept = opts.accept || (state.kind === "image" ? "image/*" : state.kind === "video" ? "video/*" : "image/*,video/*");
      M.file.setAttribute("accept", accept);
    }
    updateSelCount();
    loadList(1);
    // Focus the search box for keyboard users.
    if (M.search) { try { M.search.focus(); } catch (e) {} }
  }

  function closeModal() {
    state.open = false;
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    setBodyLock(false);
    closeEdit();
  }

  function syncKindChips() {
    if (!M.chips) return;
    Array.prototype.forEach.call(M.chips, function (c) {
      var on = (c.getAttribute("data-med-kind") || "") === state.kind;
      c.classList.toggle("is-active", on);
      c.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function loadList(page) {
    if (!M.grid) return;
    state.page = page || 1;
    var seq = ++state.reqSeq;
    if (M.loading) M.loading.hidden = false;
    if (M.empty) M.empty.hidden = true;
    var params = new URLSearchParams();
    if (state.kind) params.set("kind", state.kind);
    if (state.q) params.set("q", state.q);
    params.set("page", String(state.page));

    fetch(CFG.listUrl + "?" + params.toString(), {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (seq !== state.reqSeq) return; // A newer request superseded this one.
        if (M.loading) M.loading.hidden = true;
        renderList(data);
      })
      .catch(function () {
        if (seq !== state.reqSeq) return;
        if (M.loading) M.loading.hidden = true;
        if (M.empty) { M.empty.hidden = false; M.empty.textContent = "Couldn’t load media. Please try again."; }
      });
  }

  function renderList(data) {
    M.grid.innerHTML = "";
    var assets = data.assets || [];
    if (!assets.length) {
      if (M.empty) {
        M.empty.hidden = false;
        M.empty.textContent = state.q
          ? "No media matches “" + state.q + "”."
          : "No media yet. Upload a file to get started.";
      }
    } else if (M.empty) {
      M.empty.hidden = true;
    }
    assets.forEach(function (a) {
      var cell = buildCell(a, "modal");
      if (state.selected.indexOf(a.id) !== -1) cell.classList.add("is-selected");
      if (manage.ids.indexOf(a.id) !== -1) cell.classList.add("is-mselected");
      M.grid.appendChild(cell);
    });
    state.numPages = data.num_pages || 1;
    state.page = data.page || 1;
    if (M.pageAt) M.pageAt.textContent = "Page " + state.page + " of " + state.numPages;
    if (M.pagePrev) M.pagePrev.disabled = !data.has_prev;
    if (M.pageNext) M.pageNext.disabled = !data.has_next;
    if (M.pager) M.pager.hidden = state.numPages <= 1;
  }

  // ---- Selection ------------------------------------------------------------
  function togglePick(id) {
    if (!state.picking) return;
    var idx = state.selected.indexOf(id);
    if (idx === -1) {
      if (!state.multiple) state.selected = [];
      state.selected.push(id);
    } else {
      state.selected.splice(idx, 1);
    }
    // Reflect selection classes in the modal grid.
    Array.prototype.forEach.call(M.grid.querySelectorAll(".app-medcell"), function (c) {
      c.classList.toggle("is-selected", state.selected.indexOf(c.getAttribute("data-id")) !== -1);
    });
    updateSelCount();
  }

  function updateSelCount() {
    if (M.selCount) M.selCount.textContent = state.selected.length ? String(state.selected.length) : "";
    if (M.selectBtn) M.selectBtn.disabled = state.selected.length === 0;
  }

  function confirmSelection() {
    if (!state.picking || !state.selected.length) return;
    // Resolve selected ids to asset dicts from whatever cells are on screen,
    // then hand them back to the opener.
    var chosen = state.selected.map(function (id) {
      var cell = M.grid.querySelector('.app-medcell[data-id="' + id + '"]') || findCell(id);
      return cell ? assetFromCell(cell) : { id: id };
    });
    var cb = state.onSelect;
    closeModal();
    if (cb) cb(state.multiple ? chosen : chosen[0]);
  }

  /* ========================================================================
     UPLOAD  (shared by modal + page)
     ===================================================================== */
  function uploadFiles(files) {
    if (!files || !files.length) return;
    Array.prototype.forEach.call(files, function (f) { uploadOne(f); });
  }

  // A skeleton cell that lands in the grid immediately and shows its own upload
  // progress in place  so the media appears where it will live, no separate
  // loader strip. Returns handles to drive it. `context` is "modal" or "page".
  function buildUploadCell(file, context) {
    var isImg = /^image\//.test(file.type || "");
    var objectUrl = "";
    var cell = el("figure", "app-medcell app-medcell--uploading", { "data-uploading": "1" });
    var thumb = el("div", "app-medcell__thumb");

    if (isImg && window.URL && URL.createObjectURL) {
      try {
        objectUrl = URL.createObjectURL(file);
        thumb.appendChild(el("img", "app-medcell__media app-medcell__media--ghost", { src: objectUrl, alt: "" }));
      } catch (e) { objectUrl = ""; }
    }

    var box = el("div", "app-medupbox");
    var ring = el("span", "app-medupbox__ring", { "aria-hidden": "true" });
    var pct = el("span", "app-medupbox__pct", { text: "0%" });
    box.appendChild(ring);
    box.appendChild(pct);
    thumb.appendChild(box);

    var barTrack = el("span", "app-medupbox__bar");
    var fill = el("span", "app-medupbox__fill");
    barTrack.appendChild(fill);
    thumb.appendChild(barTrack);

    cell.appendChild(thumb);
    var cap = el("figcaption", "app-medcell__cap");
    cap.appendChild(el("span", "app-medcell__name", { text: file.name || "Uploading" }));
    var meta = el("span", "app-medcell__meta", { text: "Uploading…" });
    cap.appendChild(meta);
    cell.appendChild(cap);

    return {
      cell: cell, fill: fill, pct: pct, meta: meta, objectUrl: objectUrl,
      setPct: function (p) { fill.style.width = p + "%"; pct.textContent = p + "%"; },
      cleanup: function () { if (objectUrl) { try { URL.revokeObjectURL(objectUrl); } catch (e) {} } },
    };
  }

  function uploadOne(file) {
    // Drop a placeholder into every grid on screen, in the position the real
    // media will occupy (prepended, newest-first  matching onUploaded).
    var slots = [];
    if (state.open && M && M.grid) {
      if (M.empty) M.empty.hidden = true;
      var mslot = buildUploadCell(file, "modal");
      M.grid.insertBefore(mslot.cell, M.grid.firstChild);
      slots.push(mslot);
    }
    if (pageGrid) {
      var pempty = document.querySelector("[data-medlib-empty]");
      if (pempty) pempty.hidden = true;
      pageGrid.hidden = false;
      // Reveal the bulk bar that ships hidden in the empty state.
      var emptyBar = document.querySelector("[data-medlib-emptybar]");
      if (emptyBar) emptyBar.hidden = false;
      var pslot = buildUploadCell(file, "page");
      pageGrid.insertBefore(pslot.cell, pageGrid.firstChild);
      slots.push(pslot);
    }

    function eachSlot(fn) { slots.forEach(fn); }

    var fd = new FormData();
    fd.append("file", file);
    // When the opener asked to keep the original format (e.g. the Brand tab's
    // logo/favicon fields), tell the server not to transcode this upload to WebP.
    if (state.noWebp) fd.append("no_webp", "1");

    var xhr = new XMLHttpRequest();
    xhr.open("POST", CFG.uploadUrl, true);
    xhr.setRequestHeader("X-CSRFToken", csrfToken());
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");

    if (xhr.upload) {
      xhr.upload.onprogress = function (e) {
        if (!e.lengthComputable) return;
        var p = Math.round((e.loaded / e.total) * 100);
        // Hold at 99% until the server confirms  the last percent is the
        // server validating and storing the file, not bytes on the wire.
        if (p >= 100) p = 99;
        eachSlot(function (s) { s.setPct(p); });
      };
    }
    xhr.onload = function () {
      var data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch (e) {}
      if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
        eachSlot(function (s) {
          s.setPct(100);
          s.cleanup();
          if (s.cell.parentNode) s.cell.parentNode.removeChild(s.cell);
        });
        onUploaded(data.asset);
      } else {
        var msg = (data && data.error) || "Upload failed.";
        eachSlot(function (s) { failSlot(s, msg); });
      }
    };
    xhr.onerror = function () {
      eachSlot(function (s) { failSlot(s, "Upload failed. Check your connection and try again."); });
    };
    xhr.send(fd);
  }

  // Turn a placeholder cell into a dismissible error tile.
  function failSlot(slot, msg) {
    slot.cleanup();
    slot.cell.classList.add("is-error");
    if (slot.meta) slot.meta.textContent = "Failed";
    var thumb = slot.cell.querySelector(".app-medcell__thumb");
    if (thumb) {
      var box = thumb.querySelector(".app-medupbox");
      if (box) {
        box.innerHTML = "";
        box.appendChild(el("span", "app-medupbox__err", { text: msg }));
      }
      var bar = thumb.querySelector(".app-medupbox__bar");
      if (bar && bar.parentNode) bar.parentNode.removeChild(bar);
      // Click the failed tile to dismiss it.
      thumb.style.cursor = "pointer";
      thumb.addEventListener("click", function () {
        if (slot.cell.parentNode) slot.cell.parentNode.removeChild(slot.cell);
        refreshEmptyStates();
      });
    }
  }

  function onUploaded(asset) {
    if (!asset) return;
    // Prepend to the modal grid (if open) so it can be selected immediately…
    if (state.open && M && M.grid) {
      if (M.empty) M.empty.hidden = true;
      var mcell = buildCell(asset, "modal");
      M.grid.insertBefore(mcell, M.grid.firstChild);
      // A file uploaded while a picker is open is selected straight away, so
      // "Use selected" places it where the picker was opened from. A
      // single-select picker swaps its pick for the new file; a multi-select
      // picker adds it to whatever is already ticked. Only media of the kind
      // the picker takes is auto-selected (an image slot never picks a video).
      if (state.picking && (!state.kind || !asset.kind || asset.kind === state.kind)) {
        if (!state.multiple) state.selected = [];
        if (state.selected.indexOf(asset.id) === -1) state.selected.push(asset.id);
        Array.prototype.forEach.call(M.grid.querySelectorAll(".app-medcell"), function (c) {
          c.classList.toggle("is-selected", state.selected.indexOf(c.getAttribute("data-id")) !== -1);
        });
        updateSelCount();
        // Bring the newly selected file into view (it lands first in the grid).
        try { mcell.scrollIntoView({ block: "nearest" }); } catch (e) {}
      }
    }
    // …and to the library page grid (if present) so management stays live.
    if (pageGrid) {
      var pempty = document.querySelector("[data-medlib-empty]");
      if (pempty) pempty.hidden = true;
      pageGrid.hidden = false; // The empty-state ships a hidden grid to land in.
      pageGrid.insertBefore(buildCell(asset, "page"), pageGrid.firstChild);
    }
  }

  /* ========================================================================
     EDIT  (name, alt text, description, placeholder)
     ===================================================================== */
  var editState = { id: null };

  function openEdit(asset) {
    editState.id = asset.id;
    if (!M || !M.edit) return;
    M.edit.querySelector("[data-edit-name]").value = asset.name || "";
    M.edit.querySelector("[data-edit-alt]").value = asset.alt || "";
    M.edit.querySelector("[data-edit-description]").value = asset.description || "";
    M.edit.querySelector("[data-edit-placeholder]").value = asset.placeholder || "";
    var prev = M.edit.querySelector("[data-edit-preview]");
    if (prev) {
      prev.innerHTML = "";
      if (asset.kind === "video") prev.appendChild(el("video", null, { src: asset.url, muted: "", playsinline: "", preload: "metadata" }));
      else prev.appendChild(el("img", null, { src: asset.url, alt: asset.alt || "" }));
    }
    var err = M.edit.querySelector("[data-edit-error]");
    if (err) { err.hidden = true; err.textContent = ""; }
    modal.classList.add("is-editing");
    var first = M.edit.querySelector("[data-edit-name]");
    if (first) { try { first.focus(); } catch (e) {} }
  }

  function closeEdit() {
    editState.id = null;
    if (modal) modal.classList.remove("is-editing");
  }

  function saveEdit() {
    if (!editState.id || !M || !M.edit) return;
    var id = editState.id;
    var payload = {
      name: M.edit.querySelector("[data-edit-name]").value,
      alt_text: M.edit.querySelector("[data-edit-alt]").value,
      description: M.edit.querySelector("[data-edit-description]").value,
      placeholder: M.edit.querySelector("[data-edit-placeholder]").value,
    };
    var btn = M.edit.querySelector("[data-edit-save]");
    if (btn) btn.disabled = true;

    fetch(urlFor(CFG.updateTmpl, id), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (btn) btn.disabled = false;
        if (!data.ok) throw new Error(data.error || "Save failed.");
        patchCells(data.asset);
        closeEdit();
      })
      .catch(function (e) {
        if (btn) btn.disabled = false;
        var err = M.edit.querySelector("[data-edit-error]");
        if (err) { err.hidden = false; err.textContent = e.message || "Save failed."; }
      });
  }

  // Update every on-screen cell (modal + page) for an asset after an edit.
  function patchCells(asset) {
    var sel = '.app-medcell[data-id="' + asset.id + '"]';
    Array.prototype.forEach.call(document.querySelectorAll(sel), function (cell) {
      cell.setAttribute("data-name", asset.name || "");
      cell.setAttribute("data-alt", asset.alt || "");
      cell.setAttribute("data-description", asset.description || "");
      cell.setAttribute("data-placeholder", asset.placeholder || "");
      var nm = cell.querySelector(".app-medcell__name");
      if (nm) nm.textContent = asset.name || "Untitled";
      var media = cell.querySelector("img.app-medcell__media");
      if (media) media.setAttribute("alt", asset.alt || asset.name || "");
    });
  }

  /* ========================================================================
     DELETE
     ===================================================================== */
  function deleteAsset(id) {
    if (!id) return;
    if (!window.confirm("Delete this media permanently? Saved product and category images already using it keep their own copy.")) return;
    fetch(urlFor(CFG.deleteTmpl, id), {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken(), "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "Delete failed.");
        var sel = '.app-medcell[data-id="' + id + '"]';
        Array.prototype.forEach.call(document.querySelectorAll(sel), function (cell) {
          if (cell.parentNode) cell.parentNode.removeChild(cell);
        });
        var i = state.selected.indexOf(id);
        if (i !== -1) { state.selected.splice(i, 1); updateSelCount(); }
        var mi = manage.ids.indexOf(id);
        if (mi !== -1) { manage.ids.splice(mi, 1); updateManageUI(); }
        // If the deleted asset was open in the Edit panel, close it.
        if (editState.id === id) closeEdit();
        // Restore empty states if a grid is now empty.
        refreshEmptyStates();
      })
      .catch(function (e) { alert(e.message || "Delete failed."); });
  }

  /* ========================================================================
     PREVIEW  (full-size lightbox  a plain click on any cell)
     ---------------------------------------------------------------------------
     A self-contained overlay built once and appended to <body>, so it works on
     the library page and inside the modal alike. Showing the media at full size
     is the default click action; "Edit details" jumps to the edit panel.
     ===================================================================== */
  var mpreview = { el: null, asset: null };

  function buildPreviewOverlay() {
    var ov = el("div", "app-medpreview", { id: "ow-medpreview", "aria-hidden": "true", role: "dialog", "aria-modal": "true", "aria-label": "Media preview" });
    var backdrop = el("div", "app-medpreview__backdrop", { "data-mp-close": "" });
    var panel = el("div", "app-medpreview__panel", { role: "document" });
    var x = el("button", "app-medpreview__x", { type: "button", "data-mp-close": "", "aria-label": "Close preview", text: "×" });
    var stage = el("div", "app-medpreview__stage", { "data-mp-stage": "" });
    var bar = el("div", "app-medpreview__bar");
    var cap = el("span", "app-medpreview__cap", { "data-mp-cap": "" });
    var editBtn = el("button", "pal-btn pal-btn--ghost app-medpreview__edit", { type: "button", "data-mp-edit": "", text: "Edit details" });
    bar.appendChild(cap); bar.appendChild(editBtn);
    panel.appendChild(x); panel.appendChild(stage); panel.appendChild(bar);
    ov.appendChild(backdrop); ov.appendChild(panel);

    // Close on backdrop / × ; edit jumps to the edit panel.
    Array.prototype.forEach.call(ov.querySelectorAll("[data-mp-close]"), function (b) {
      b.addEventListener("click", function (e) { e.preventDefault(); closePreview(); });
    });
    editBtn.addEventListener("click", function (e) {
      e.preventDefault();
      var a = mpreview.asset;
      closePreview();
      if (a && modal) {
        if (!state.open) { openModalShell(); }
        openEdit(a);
      }
    });
    // Edit is meaningful only where the modal (its edit panel) is present.
    editBtn.style.display = modal ? "" : "none";
    return ov;
  }

  function openPreview(asset) {
    if (!asset || !asset.url) return;
    if (!mpreview.el) { mpreview.el = buildPreviewOverlay(); document.body.appendChild(mpreview.el); }
    mpreview.asset = asset;
    var ov = mpreview.el;
    var stage = ov.querySelector("[data-mp-stage]");
    stage.innerHTML = "";
    if (asset.kind === "video") {
      stage.appendChild(el("video", "app-medpreview__media", { src: asset.url, controls: "", playsinline: "", autoplay: "" }));
    } else {
      stage.appendChild(el("img", "app-medpreview__media", { src: asset.url, alt: asset.alt || asset.name || "" }));
    }
    var cap = ov.querySelector("[data-mp-cap]");
    if (cap) cap.textContent = asset.name || "";
    ov.classList.add("is-open");
    ov.setAttribute("aria-hidden", "false");
    setBodyLock(true);
  }

  function closePreview() {
    if (!mpreview.el) return;
    var stage = mpreview.el.querySelector("[data-mp-stage]");
    if (stage) stage.innerHTML = ""; // stops any playing video
    mpreview.el.classList.remove("is-open");
    mpreview.el.setAttribute("aria-hidden", "true");
    mpreview.asset = null;
    // Keep the scroll lock if the library modal is still open beneath.
    setBodyLock(state.open);
  }

  // Escape closes the preview first, ahead of the modal's own handler.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && mpreview.el && mpreview.el.classList.contains("is-open")) {
      e.stopPropagation();
      closePreview();
    }
  }, true);

  /* ========================================================================
     MULTI-SELECT MANAGEMENT  (bulk delete across page + modal)
     ---------------------------------------------------------------------------
     Independent of the picking `state.selected` above: this is management, not
     "use this media". Turning it on tags <body> with `ow-managing`, which
     reveals a tick on every cell; clicking a cell toggles it; the bulk bar then
     deletes every ticked asset in a single request.
     ===================================================================== */
  var manage = { on: false, ids: [] };

  function setManageMode(on) {
    manage.on = !!on;
    manage.ids = [];
    document.body.classList.toggle("ow-managing", manage.on);
    // Leaving manage mode clears every tick.
    Array.prototype.forEach.call(document.querySelectorAll(".app-medcell.is-mselected"), function (c) {
      c.classList.remove("is-mselected");
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-med-manage]"), function (b) {
      b.classList.toggle("is-active", manage.on);
      b.setAttribute("aria-pressed", manage.on ? "true" : "false");
    });
    updateManageUI();
  }

  function toggleManage(id) {
    if (!id) return;
    var i = manage.ids.indexOf(id);
    if (i === -1) manage.ids.push(id);
    else manage.ids.splice(i, 1);
    Array.prototype.forEach.call(document.querySelectorAll('.app-medcell[data-id="' + id + '"]'), function (c) {
      c.classList.toggle("is-mselected", manage.ids.indexOf(id) !== -1);
    });
    updateManageUI();
  }

  // Visibility rules for the bulk bar:
  //   • "Select multiple" toggle: always shown (far left).
  //   • Cancel / Delete selected: shown once manage mode is on; Delete is
  //     disabled until at least one item is ticked.
  //   • The "N selected" count shows only while managing AND ≥1 is ticked.
  function updateManageUI() {
    var n = manage.ids.length;
    Array.prototype.forEach.call(document.querySelectorAll("[data-med-bulk-count]"), function (c) {
      c.textContent = String(n);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-med-bulk-countwrap]"), function (c) {
      c.hidden = !(manage.on && n > 0);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-med-bulk-cancel]"), function (b) {
      b.hidden = !manage.on;
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-med-bulk-delete]"), function (b) {
      b.hidden = !manage.on;
      b.disabled = n === 0;
    });
  }

  function bulkDelete() {
    var ids = manage.ids.slice();
    if (!ids.length || !CFG.bulkDeleteUrl) return;
    var msg = ids.length === 1
      ? "Delete this media permanently?"
      : "Delete these " + ids.length + " items permanently?";
    if (!window.confirm(msg + " Saved product and category images already using them keep their own copy.")) return;

    Array.prototype.forEach.call(document.querySelectorAll("[data-med-bulk-delete]"), function (b) { b.disabled = true; });

    fetch(CFG.bulkDeleteUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "XMLHttpRequest",
      },
      credentials: "same-origin",
      body: JSON.stringify({ ids: ids }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "Delete failed.");
        (data.deleted || ids).forEach(function (id) {
          Array.prototype.forEach.call(document.querySelectorAll('.app-medcell[data-id="' + id + '"]'), function (cell) {
            if (cell.parentNode) cell.parentNode.removeChild(cell);
          });
          var pi = state.selected.indexOf(id);
          if (pi !== -1) state.selected.splice(pi, 1);
        });
        updateSelCount();
        refreshEmptyStates();
        setManageMode(false);
      })
      .catch(function (e) {
        updateManageUI();
        alert(e.message || "Delete failed.");
      });
  }

  // Restore the page/modal empty states if a grid has been emptied.
  function refreshEmptyStates() {
    if (pageGrid && !pageGrid.querySelector(".app-medcell")) {
      var pempty = document.querySelector("[data-medlib-empty]");
      if (pempty) pempty.hidden = false;
      pageGrid.hidden = true;
    }
    if (M && M.grid && !M.grid.querySelector(".app-medcell") && M.empty) M.empty.hidden = false;
  }

  /* ========================================================================
     EVENT WIRING
     ===================================================================== */
  // Delegated clicks for edit/delete/select across BOTH grids.
  document.addEventListener("click", function (e) {
    var t = e.target;

    // Multi-select management toggle (present on the page head and modal toolbar).
    var manageBtn = t.closest && t.closest("[data-med-manage]");
    if (manageBtn) {
      e.preventDefault();
      setManageMode(!manage.on);
      return;
    }
    var bulkCancel = t.closest && t.closest("[data-med-bulk-cancel]");
    if (bulkCancel) { e.preventDefault(); setManageMode(false); return; }
    var bulkDel = t.closest && t.closest("[data-med-bulk-delete]");
    if (bulkDel) { e.preventDefault(); bulkDelete(); return; }

    // While managing, a click anywhere on a cell toggles its selection 
    // never edits, deletes or picks.
    if (manage.on) {
      var mcell = t.closest && t.closest(".app-medcell");
      if (mcell && mcell.getAttribute("data-id")) {
        e.preventDefault();
        toggleManage(mcell.getAttribute("data-id"));
        return;
      }
    }

    var delBtn = t.closest && t.closest("[data-med-delete]");
    if (delBtn) {
      e.preventDefault();
      var dcell = delBtn.closest(".app-medcell");
      if (dcell) deleteAsset(dcell.getAttribute("data-id"));
      return;
    }
    // Hover "Edit" affordance  opens the details panel (needs the modal's panel).
    var editBtn = t.closest && t.closest("[data-med-edit]");
    if (editBtn) {
      e.preventDefault();
      var ecell = editBtn.closest(".app-medcell");
      if (ecell && !ecell.hasAttribute("data-uploading") && modal) {
        if (!state.open) { openModalShell(); }
        openEdit(assetFromCell(ecell));
      }
      return;
    }
    var pickBtn = t.closest && t.closest("[data-med-pick]");
    if (pickBtn) {
      e.preventDefault();
      var pcell = pickBtn.closest(".app-medcell");
      if (pcell) togglePick(pcell.getAttribute("data-id"));
      return;
    }
    // Plain click on a cell (not managing, not picking) opens the Edit details
    // panel directly. (It used to open a read-only full-screen preview; editing
    // is what owners actually want on a click, so the click now does that.)
    if (!state.picking) {
      var vcell = t.closest && t.closest(".app-medcell");
      if (vcell && vcell.getAttribute("data-id") && !vcell.hasAttribute("data-uploading") && modal) {
        e.preventDefault();
        if (!state.open) { openModalShell(); }
        openEdit(assetFromCell(vcell));
        return;
      }
    }
    // Opener buttons anywhere on the page (library "Upload", form triggers).
    var opener = t.closest && t.closest("[data-ow-media-open]");
    if (opener) {
      e.preventDefault();
      var idsName = opener.getAttribute("data-ow-ids");
      var fillSel = opener.getAttribute("data-ow-fill");
      var onSelect = null;
      // Preferred mode: submit the chosen library asset IDs (the backend copies
      // the files server-side). No cross-origin blob fetch, so it works even
      // when media lives on S3. Legacy `data-ow-fill` injects file bytes.
      if (idsName) onSelect = function (chosen) { fillMediaIds(opener, chosen); };
      else if (fillSel) onSelect = function (chosen) { fillFileInputs(opener, chosen); };
      openModal({
        kind: opener.getAttribute("data-ow-kind") || "",
        multiple: opener.getAttribute("data-ow-multiple") === "true",
        title: opener.getAttribute("data-ow-title") || "Media library",
        onSelect: onSelect,
      });
      return;
    }

    // Remove the currently-attached product video on an edit form: drop the
    // preview and blank every video_link input so saving detaches the video.
    // Any freshly-picked media_video hidden input is removed too.
    var clearVid = t.closest && t.closest("[data-clear-video]");
    if (clearVid) {
      e.preventDefault();
      var vform = clearVid.closest("form");
      var vitem = clearVid.closest(".app-medfield__item") || clearVid.parentNode;
      if (vform) {
        Array.prototype.forEach.call(vform.querySelectorAll('input[name="video_link"]'), function (i) { i.value = ""; });
        Array.prototype.forEach.call(vform.querySelectorAll('input[name="media_video"]'), function (i) {
          if (i.parentNode) i.parentNode.removeChild(i);
        });
      }
      if (vitem && vitem.parentNode) vitem.parentNode.removeChild(vitem);
      return;
    }

    // Remove an already-saved image on an edit form: flag it for deletion and
    // drop the thumbnail. A hidden input carries the id to the server.
    var rmExisting = t.closest && t.closest("[data-remove-existing]");
    if (rmExisting) {
      e.preventDefault();
      var item = rmExisting.closest(".app-medfield__item") || rmExisting.parentNode;
      var form = rmExisting.closest("form");
      var id = rmExisting.getAttribute("data-remove-existing");
      var fieldName = rmExisting.getAttribute("data-remove-name") || "remove_image";
      if (form && id) {
        var flag = document.createElement("input");
        flag.type = "hidden"; flag.name = fieldName; flag.value = id;
        form.appendChild(flag);
      }
      if (item && item.parentNode) item.parentNode.removeChild(item);
      return;
    }
  });

  /* ========================================================================
     MEDIA-ID TRIGGER  submit picked library asset IDs (backend copies files)
     ===================================================================== */
  function fillMediaIds(opener, chosen) {
    var assets = Array.isArray(chosen) ? chosen : [chosen];
    assets = assets.filter(Boolean);
    if (!assets.length) return;
    var name = opener.getAttribute("data-ow-ids");
    var multiple = opener.getAttribute("data-ow-multiple") === "true";
    var previewSel = opener.getAttribute("data-ow-preview");
    var preview = previewSel ? document.querySelector(previewSel) : null;
    var form = opener.closest("form");

    // Single-select replaces any previous pick  and the already-attached
    // preview (e.g. the product's current video on an edit form), so the field
    // shows only the newly-chosen media.
    if (!multiple && preview) {
      Array.prototype.forEach.call(preview.querySelectorAll(".app-medfield__item[data-picked], .app-medfield__item[data-existing-video]"), function (n) {
        if (n.parentNode) n.parentNode.removeChild(n);
      });
    }

    assets.forEach(function (a) {
      if (!a || !a.id) return;
      var host = preview || form;
      if (!host) return;
      var item = el("span", "app-medfield__item", { "data-picked": "1" });
      item.appendChild(el("input", null, { type: "hidden", name: name, value: a.id }));
      // A video asset gets a real <video> thumbnail (playable inline), not an
      // <img> pointed at the .mp4  otherwise the preview is a broken image and
      // the owner can't tell which video they picked.
      if (a.kind === "video") {
        item.appendChild(el("video", "app-medfield__thumb", { src: a.url, muted: "", playsinline: "", preload: "metadata", controls: "" }));
      } else {
        item.appendChild(el("img", "app-medfield__thumb", { src: a.url, alt: a.alt || a.name || "" }));
      }
      var rm = el("button", "app-medfield__rm", { type: "button", "aria-label": "Remove", title: "Remove" });
      rm.textContent = "×";
      rm.addEventListener("click", function () { if (item.parentNode) item.parentNode.removeChild(item); });
      item.appendChild(rm);
      host.appendChild(item);
    });
  }

  /* ========================================================================
     UNIVERSAL TRIGGER  inject picked library assets into existing file inputs
     ---------------------------------------------------------------------------
     A trigger carries:
       data-ow-fill="<css selector for the file input(s) to fill>"
       data-ow-preview="<css selector for a container to show thumbnails>" (opt)
     Selected assets are fetched from their own URL as Blobs, wrapped as File
     objects and assigned to the inputs through a DataTransfer, exactly as if the
     user had chosen them from disk. Falls back silently to the manual inputs if
     the browser cannot construct File objects.
     ===================================================================== */
  function fillFileInputs(opener, chosen) {
    var assets = Array.isArray(chosen) ? chosen : [chosen];
    assets = assets.filter(Boolean);
    if (!assets.length) return;

    var inputs = Array.prototype.slice.call(document.querySelectorAll(opener.getAttribute("data-ow-fill")));
    if (!inputs.length) return;
    var previewSel = opener.getAttribute("data-ow-preview");
    var preview = previewSel ? document.querySelector(previewSel) : null;

    // Find slots to fill: prefer currently-empty inputs, in document order.
    var slots = inputs.filter(function (i) { return !i.files || !i.files.length; });
    if (!slots.length) slots = inputs; // All full → overwrite from the first.

    assets.forEach(function (a, n) {
      var input = slots[n];
      if (!input || !a.url) return;
      fetch(a.url, { credentials: "same-origin" })
        .then(function (r) { return r.blob(); })
        .then(function (blob) {
          var fname = (a.name || "media") + guessExt(blob.type, a.url);
          var file;
          try { file = new File([blob], fname, { type: blob.type }); }
          catch (err) { return; } // Very old browser: leave the manual input.
          try {
            var dt = new DataTransfer();
            dt.items.add(file);
            input.files = dt.files;
            input.dispatchEvent(new Event("change", { bubbles: true }));
          } catch (err) { return; }
          if (preview) {
            // A removable chip: clearing it empties the slot input it filled, so
            // the form no longer submits that file.
            var item = el("span", "app-medfield__item");
            var img = el("img", "app-medfield__thumb", { src: a.url, alt: a.alt || a.name || "" });
            var rm = el("button", "app-medfield__rm", { type: "button", "aria-label": "Remove image", title: "Remove" });
            rm.textContent = "×";
            rm.addEventListener("click", function () {
              try {
                var empty = new DataTransfer();
                input.files = empty.files;
                input.dispatchEvent(new Event("change", { bubbles: true }));
              } catch (e) {}
              if (item.parentNode) item.parentNode.removeChild(item);
            });
            item.appendChild(img);
            item.appendChild(rm);
            preview.appendChild(item);
          }
        })
        .catch(function () { /* leave the manual input as the fallback */ });
    });
  }

  function guessExt(mime, url) {
    var m = { "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov" };
    if (m[mime]) return m[mime];
    var u = (url || "").split("?")[0];
    var dot = u.lastIndexOf(".");
    return dot !== -1 ? u.slice(dot) : "";
  }

  // When Edit is triggered from the library page with the modal closed, we open
  // the modal chrome (for its edit panel) but stay in management mode.
  function openModalShell() {
    state.open = true;
    state.picking = false;
    modal.classList.add("is-open");
    modal.classList.remove("is-picking");
    modal.setAttribute("aria-hidden", "false");
    setBodyLock(true);
    if (M.title) M.title.textContent = "Edit media";
    loadList(1);
  }

  if (modal) {
    // Close controls.
    Array.prototype.forEach.call(modal.querySelectorAll("[data-med-close]"), function (b) {
      b.addEventListener("click", function (e) { e.preventDefault(); closeModal(); });
    });
    // Backdrop click.
    var backdrop = modal.querySelector("[data-med-backdrop]");
    if (backdrop) backdrop.addEventListener("click", function () { closeModal(); });
    // Escape closes edit first, then the modal.
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || !state.open) return;
      if (modal.classList.contains("is-editing")) closeEdit();
      else closeModal();
    });

    // Kind chips.
    Array.prototype.forEach.call(M.chips, function (c) {
      c.addEventListener("click", function () {
        state.kind = c.getAttribute("data-med-kind") || "";
        syncKindChips();
        loadList(1);
      });
    });

    // Search (debounced).
    if (M.search) {
      var to = null;
      M.search.addEventListener("input", function () {
        clearTimeout(to);
        to = setTimeout(function () { state.q = M.search.value.trim(); loadList(1); }, 250);
      });
    }

    // Pager.
    if (M.pagePrev) M.pagePrev.addEventListener("click", function () { if (state.page > 1) loadList(state.page - 1); });
    if (M.pageNext) M.pageNext.addEventListener("click", function () { if (state.page < state.numPages) loadList(state.page + 1); });

    // File picker + drag & drop.
    if (M.file) M.file.addEventListener("change", function () { uploadFiles(M.file.files); M.file.value = ""; });
    if (M.drop) {
      ["dragenter", "dragover"].forEach(function (ev) {
        M.drop.addEventListener(ev, function (e) { e.preventDefault(); M.drop.classList.add("is-drag"); });
      });
      ["dragleave", "drop"].forEach(function (ev) {
        M.drop.addEventListener(ev, function (e) { e.preventDefault(); M.drop.classList.remove("is-drag"); });
      });
      M.drop.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files);
      });
    }

    // Confirm selection.
    if (M.selectBtn) M.selectBtn.addEventListener("click", function () { confirmSelection(); });

    // Edit panel controls.
    if (M.edit) {
      var saveBtn = M.edit.querySelector("[data-edit-save]");
      if (saveBtn) saveBtn.addEventListener("click", function (e) { e.preventDefault(); saveEdit(); });
      var delBtn = M.edit.querySelector("[data-edit-delete]");
      if (delBtn) delBtn.addEventListener("click", function (e) { e.preventDefault(); if (editState.id) deleteAsset(editState.id); });
      Array.prototype.forEach.call(M.edit.querySelectorAll("[data-edit-cancel]"), function (b) {
        b.addEventListener("click", function (e) { e.preventDefault(); closeEdit(); });
      });
      M.edit.addEventListener("submit", function (e) { e.preventDefault(); saveEdit(); });
    }
  }

  /* ========================================================================
     PUBLIC API
     ===================================================================== */
  window.OneWoozMedia = {
    // open({ kind, multiple, title, accept, onSelect })  onSelect receives one
    // asset (single) or an array (multiple). Omit onSelect to just browse/manage.
    open: function (opts) {
      if (!modal) { console.warn("OneWoozMedia: modal not mounted on this page."); return; }
      openModal(opts || {});
    },
    close: function () { if (modal) closeModal(); },
  };
})();
