/* Paste-HTML editor for onewooz.com core pages and blog posts (Django admin).
 *
 * Turns a plain <textarea> into:
 *   - Write / Side by side / Preview modes. The preview is rendered by the
 *     server (custom_admin.marketing_content.preview): exactly the cleaned HTML
 *     that will be saved, styled by the real site stylesheets inside a shadow
 *     root so admin CSS cannot leak in.
 *   - Notes on what cleaning will change (inline styles removed, h1 to h2...).
 *   - Images: upload (button, drag and drop, or paste a file into the box),
 *     then copy the link, copy an <img> tag, or insert it at the cursor. Images
 *     already used in the HTML are listed too.
 *   - A formatting guide with copyable writing instructions for ChatGPT or
 *     Claude. Nothing is generated here: the content is always what you paste.
 *
 * The textarea stays the source of truth (the form or the tree editor saves
 * it); programmatic edits dispatch "input" so autosave sees them.
 *
 * Usage: OWHtmlEditor.mount(textarea, {previewUrl, uploadUrl, css, csrf,
 * titleInput}). Textareas rendered by HtmlContentWidget (data-ow-html) mount
 * themselves on load.
 */
(function () {
  "use strict";

  var FONTS = "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Inter:wght@400;500;600&display=swap";
  var MODE_KEY = "owh-mode";

  var INSTRUCTIONS = [
    "Write the content as clean HTML body content only, ready to paste into a web page.",
    "",
    "Rules:",
    "- Do not include <html>, <head>, <body>, <style>, <script>, CSS, class or style attributes. The website styles everything.",
    "- Do not include an <h1>. The page title is added automatically.",
    "- Start with one short paragraph that directly answers the reader's main question.",
    "- Use <h2> for main sections and <h3> for sub-sections. Phrase a heading as a question when the section answers one.",
    "- Use <p> for paragraphs, <ul><li> for lists and <ol><li> for step-by-step instructions. Use <strong> for key terms.",
    "- Use <table> with <thead> and <tbody> for comparisons.",
    "- Use <blockquote> for a single tip or important note.",
    "- For images use: <figure><img src=\"IMAGE URL\" alt=\"what the image shows\"><figcaption>Short caption</figcaption></figure>, using only the image URLs I give you.",
    "- Links: <a href=\"URL\">descriptive link text</a>.",
    "- Do not use em dashes.",
    "",
    "Topic: ",
    "Image URLs: "
  ].join("\n");

  var SKELETON = [
    "<p>One short paragraph that answers the main question directly.</p>",
    "",
    "<h2>First section</h2>",
    "<p>Explain the idea in plain language.</p>",
    "<ol>",
    "  <li><strong>Step one.</strong> What to do and why.</li>",
    "  <li><strong>Step two.</strong> What happens next.</li>",
    "</ol>",
    "",
    "<figure>",
    "  <img src=\"https://.../image.webp\" alt=\"What the image shows\">",
    "  <figcaption>A short caption.</figcaption>",
    "</figure>",
    "",
    "<h2>How long does it take?</h2>",
    "<p>A question heading followed by its answer becomes an FAQ for search engines.</p>",
    "",
    "<table>",
    "  <thead><tr><th>Option</th><th>Best for</th></tr></thead>",
    "  <tbody><tr><td>A</td><td>Shoppers</td></tr></tbody>",
    "</table>",
    "",
    "<blockquote>A tip or important note.</blockquote>"
  ].join("\n");

  var TAGS = ["h2", "h3", "h4", "p", "strong", "em", "a", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
    "blockquote", "figure", "img", "figcaption", "code", "pre", "hr", "details", "summary"];

  // ---------------------------------------------------------------- helpers
  function el(tag, cls, attrs, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }
  function escAttr(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }
  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { return null; }
    return null;
  }
  function copyText(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var t = el("textarea", null, { readonly: "" });
      t.value = text;
      t.style.position = "fixed"; t.style.opacity = "0";
      document.body.appendChild(t);
      t.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (e) { reject(e); }
      document.body.removeChild(t);
    });
  }
  function flash(btn, label) {
    var old = btn.getAttribute("data-label") || btn.textContent;
    btn.setAttribute("data-label", old);
    btn.textContent = label;
    btn.classList.add("is-done");
    setTimeout(function () { btn.textContent = old; btn.classList.remove("is-done"); }, 1500);
  }
  function csrfFrom(scope) {
    var input = (scope && scope.querySelector && scope.closest("form") && scope.closest("form").querySelector("[name=csrfmiddlewaretoken]")) ||
      document.querySelector("[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }
  function ensureFonts() {
    if (document.querySelector("link[data-owh-fonts]")) return;
    var link = el("link", null, { rel: "stylesheet", href: FONTS, "data-owh-fonts": "1" });
    document.head.appendChild(link);
  }
  function imgTag(img) {
    var size = img.width && img.height ? ' width="' + img.width + '" height="' + img.height + '"' : "";
    return '<img src="' + escAttr(img.url) + '" alt="' + escAttr(img.alt || "") + '"' + size + ">";
  }
  function figureTag(img) {
    return "<figure>\n  " + imgTag(img) + "\n  <figcaption>" + (img.alt || "") + "</figcaption>\n</figure>";
  }
  function insertAtCursor(ta, text) {
    var start = ta.selectionStart || 0, end = ta.selectionEnd || 0, value = ta.value;
    var before = value.slice(0, start), after = value.slice(end);
    var pre = before && !/\n\s*$/.test(before) ? "\n\n" : "";
    var post = after && !/^\s*\n/.test(after) ? "\n\n" : "";
    ta.value = before + pre + text + post + after;
    var caret = (before + pre + text).length;
    ta.focus();
    ta.setSelectionRange(caret, caret);
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  // ----------------------------------------------------------------- mount
  function mount(ta, opts) {
    if (!ta || ta.dataset.owhReady === "1") return null;
    ta.dataset.owhReady = "1";
    opts = opts || {};
    var previewUrl = opts.previewUrl || ta.getAttribute("data-preview-url");
    var uploadUrl = opts.uploadUrl || ta.getAttribute("data-upload-url");
    var cssList = String(opts.css || ta.getAttribute("data-css") || "").split(",").filter(Boolean);
    var csrf = opts.csrf || csrfFrom(ta);
    var titleOf = function () {
      var t = typeof opts.titleInput === "function" ? opts.titleInput() : opts.titleInput;
      return (t && t.value && t.value.trim()) || "Page title";
    };
    ensureFonts();

    // ---- skeleton
    var wrap = el("div", "owh");
    ta.parentNode.insertBefore(wrap, ta);

    var bar = el("div", "owh-bar");
    var tabs = el("div", "owh-tabs", { role: "tablist", "aria-label": "Editor view" });
    var modes = [["write", "HTML"], ["split", "Side by side"], ["preview", "Preview"]];
    var tabBtns = {};
    modes.forEach(function (m) {
      var b = el("button", "owh-tab", { type: "button", role: "tab", "data-mode": m[0] }, m[1]);
      tabBtns[m[0]] = b;
      tabs.appendChild(b);
    });
    var stats = el("span", "owh-stats", { "aria-live": "polite" });
    var actions = el("div", "owh-actions");
    var uploadBtn = el("button", "owh-btn", { type: "button" }, "Upload image");
    var guideBtn = el("button", "owh-btn owh-btn--ghost", { type: "button", "aria-expanded": "false" }, "Formatting guide");
    actions.appendChild(uploadBtn); actions.appendChild(guideBtn);
    bar.appendChild(tabs); bar.appendChild(stats); bar.appendChild(actions);

    var panes = el("div", "owh-panes");
    var write = el("div", "owh-write");
    var preview = el("div", "owh-preview", { "aria-label": "Preview", role: "region" });
    var host = el("div", "owh-preview__host");
    preview.appendChild(host);
    panes.appendChild(write); panes.appendChild(preview);
    write.appendChild(ta);
    ta.classList.add("owh-src");
    ta.setAttribute("placeholder", "Paste your HTML here, for example:\n\n" + SKELETON);

    var notes = el("div", "owh-notes", { "aria-live": "polite", hidden: "" });

    var guide = el("div", "owh-guide", { hidden: "" });
    guide.innerHTML =
      '<div class="owh-guide__col">' +
      '<p class="owh-guide__h">How it works</p>' +
      '<p>Paste HTML and the site styles it automatically in the onewooz.com theme. Inline styles, classes, scripts and an <code>&lt;h1&gt;</code> are removed or converted when you save, so pasted content always matches the rest of the site.</p>' +
      '<p class="owh-guide__h">Tags that are styled</p><p class="owh-guide__tags"></p>' +
      '<p class="owh-guide__h">Search and AI answers</p>' +
      '<p>The first paragraph becomes the page summary. Each <code>&lt;h2&gt;</code> gets an anchor and appears in "On this page". A heading ending in "?" followed by its answer is published as an FAQ, and the first numbered list as how-to steps.</p>' +
      "</div>" +
      '<div class="owh-guide__col">' +
      '<div class="owh-guide__copyhead"><p class="owh-guide__h">Instructions for ChatGPT or Claude</p><button type="button" class="owh-btn owh-btn--small" data-copy="instructions">Copy</button></div>' +
      '<pre class="owh-guide__pre" data-src="instructions"></pre>' +
      '<div class="owh-guide__copyhead"><p class="owh-guide__h">Example structure</p><button type="button" class="owh-btn owh-btn--small" data-copy="skeleton">Copy</button></div>' +
      '<pre class="owh-guide__pre" data-src="skeleton"></pre>' +
      "</div>";
    guide.querySelector(".owh-guide__tags").innerHTML = TAGS.map(function (t) { return "<code>&lt;" + t + "&gt;</code>"; }).join(" ");
    guide.querySelector('[data-src="instructions"]').textContent = INSTRUCTIONS;
    guide.querySelector('[data-src="skeleton"]').textContent = SKELETON;

    var images = el("section", "owh-images");
    var imagesHead = el("div", "owh-images__head");
    imagesHead.appendChild(el("p", "owh-images__title", null, "Images"));
    imagesHead.appendChild(el("p", "owh-images__hint", null,
      "Upload an image, then copy its link into your HTML or into your prompt. Images are converted to WebP and served from the CDN."));
    var fileInput = el("input", "owh-file", { type: "file", accept: "image/*", multiple: "", tabindex: "-1", "aria-hidden": "true" });
    var drop = el("div", "owh-drop", { tabindex: "0", role: "button" });
    drop.innerHTML = '<strong>Drop images here</strong> or <span class="owh-link">choose files</span><span class="owh-drop__sub">JPG, PNG, WebP, GIF, HEIC. Up to 10 MB each. You can also paste an image straight into the HTML box.</span>';
    var list = el("ul", "owh-imglist");
    images.appendChild(imagesHead); images.appendChild(drop); images.appendChild(fileInput); images.appendChild(list);

    wrap.appendChild(bar);
    wrap.appendChild(guide);
    wrap.appendChild(panes);
    wrap.appendChild(notes);
    wrap.appendChild(images);

    // ---- shadow-root preview, styled by the real site CSS
    var shadow = host.attachShadow ? host.attachShadow({ mode: "open" }) : host;
    var links = cssList.map(function (href) { return '<link rel="stylesheet" href="' + escAttr(href) + '">'; }).join("");
    shadow.innerHTML = links +
      "<style>" +
      ":host{display:block}" +
      ".pal{min-height:100%;min-height:24rem}" +
      ".owh-doc{padding:1.75rem clamp(1rem,3vw,2.25rem) 2.5rem;max-width:46rem}" +
      ".owh-doc .cp-article__title{font-size:clamp(1.7rem,1.3rem + 1.5vw,2.4rem);padding-bottom:1.1rem;border-bottom:1px solid var(--pal-line)}" +
      ".owh-empty{color:var(--pal-muted);font-size:.95rem;margin-top:1.5rem}" +
      "</style>" +
      '<div class="pal"><div class="owh-doc">' +
      '<p class="cp-eyebrow">Preview</p>' +
      '<h1 class="cp-article__title"></h1>' +
      '<div class="cpa-body cp-article__body"></div>' +
      "</div></div>";
    var docTitle = shadow.querySelector(".cp-article__title");
    var docBody = shadow.querySelector(".cpa-body");

    // ---- modes
    function setMode(mode, remember) {
      if (!tabBtns[mode]) mode = "split";
      panes.setAttribute("data-mode", mode);
      Object.keys(tabBtns).forEach(function (k) {
        tabBtns[k].setAttribute("aria-selected", String(k === mode));
        tabBtns[k].classList.toggle("is-active", k === mode);
      });
      if (remember) store(MODE_KEY, mode);
      if (mode !== "write") refresh(true);
    }
    Object.keys(tabBtns).forEach(function (k) {
      tabBtns[k].addEventListener("click", function () { setMode(k, true); });
    });

    guideBtn.addEventListener("click", function () {
      var open = guide.hasAttribute("hidden");
      if (open) guide.removeAttribute("hidden"); else guide.setAttribute("hidden", "");
      guideBtn.setAttribute("aria-expanded", String(open));
    });
    guide.addEventListener("click", function (e) {
      var b = e.target.closest("[data-copy]");
      if (!b) return;
      var src = guide.querySelector('[data-src="' + b.getAttribute("data-copy") + '"]');
      copyText(src.textContent).then(function () { flash(b, "Copied"); });
    });

    // ---- preview (server-cleaned, debounced, latest response wins)
    var seq = 0, timer = null, lastSent = null;
    function refresh(now) {
      clearTimeout(timer);
      timer = setTimeout(send, now ? 0 : 350);
    }
    function send() {
      docTitle.textContent = titleOf();
      if (panes.getAttribute("data-mode") === "write" && lastSent !== null) { updateStatsLocal(); return; }
      var value = ta.value;
      if (value === lastSent) return;
      lastSent = value;
      var mine = ++seq;
      if (!previewUrl) { docBody.innerHTML = ""; return; }
      fetch(previewUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify({ html: value })
      })
        .then(function (r) {
          return r.json().catch(function () {
            throw new Error("your admin session may have expired. Reload the page and sign in again.");
          }).then(function (d) { if (!r.ok) throw new Error(d.error || "Preview failed"); return d; });
        })
        .then(function (d) {
          if (mine !== seq) return;
          docBody.innerHTML = d.html || '<p class="owh-empty">Paste HTML on the left to see it here.</p>';
          renderNotes(d.notes || []);
          renderStats(d.stats || {});
        })
        .catch(function (err) {
          if (mine !== seq) return;
          lastSent = null;
          renderNotes(["Preview unavailable: " + (err && err.message ? err.message : "network error")], true);
        });
    }
    function renderNotes(items, isError) {
      notes.innerHTML = "";
      if (!items.length) { notes.setAttribute("hidden", ""); return; }
      notes.removeAttribute("hidden");
      notes.classList.toggle("is-error", !!isError);
      notes.appendChild(el("p", "owh-notes__title", null, isError ? "Preview" : "Cleaned when you save"));
      var ul = el("ul");
      items.forEach(function (n) { ul.appendChild(el("li", null, null, n)); });
      notes.appendChild(ul);
    }
    function renderStats(s) {
      var parts = [];
      parts.push((s.words || 0) + " words");
      parts.push((s.minutes || 1) + " min read");
      parts.push((s.headings || 0) + " section" + (s.headings === 1 ? "" : "s"));
      if (s.images) parts.push(s.images + " image" + (s.images === 1 ? "" : "s"));
      if (s.faqs) parts.push(s.faqs + " FAQ");
      stats.textContent = parts.join(" · ");
    }
    function updateStatsLocal() {
      var text = ta.value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      var words = text ? text.split(" ").length : 0;
      renderStats({
        words: words, minutes: Math.max(1, Math.round(words / 200)),
        headings: (ta.value.match(/<h2[\s>]/gi) || []).length,
        images: (ta.value.match(/<img[\s>]/gi) || []).length
      });
    }

    ta.addEventListener("input", function () { refresh(false); syncUsedImages(); });
    var titleInput = typeof opts.titleInput === "function" ? opts.titleInput() : opts.titleInput;
    if (titleInput && titleInput.addEventListener) {
      titleInput.addEventListener("input", function () { docTitle.textContent = titleOf(); });
    }

    // ---- images
    var imgs = []; // {url, name, width, height, alt, state, error}
    function findImg(url) { for (var i = 0; i < imgs.length; i++) if (imgs[i].url === url) return imgs[i]; return null; }
    function usedUrls() {
      var out = [], re = /<img\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']/gi, m;
      while ((m = re.exec(ta.value))) out.push(m[1]);
      return out;
    }
    var syncTimer = null;
    function syncUsedImages() {
      clearTimeout(syncTimer);
      syncTimer = setTimeout(function () {
        var used = usedUrls();
        used.forEach(function (u) {
          if (!findImg(u)) imgs.push({ url: u, name: u.split("/").pop().split("?")[0], state: "done", fromContent: true });
        });
        // Forget images that came only from the content and are gone from it.
        imgs = imgs.filter(function (i) { return !i.fromContent || used.indexOf(i.url) !== -1; });
        renderImages();
      }, 250);
    }
    function renderImages() {
      list.innerHTML = "";
      var used = usedUrls();
      imgs.forEach(function (img) {
        var li = el("li", "owh-img" + (img.state === "error" ? " is-error" : ""));
        var thumb = el("div", "owh-img__thumb");
        if (img.state === "uploading") thumb.appendChild(el("span", "owh-spin", { "aria-hidden": "true" }));
        else if (img.url) thumb.appendChild(el("img", null, { src: img.url, alt: "", loading: "lazy" }));
        var meta = el("div", "owh-img__meta");
        var top = el("p", "owh-img__name", null, img.name || "image");
        if (img.state === "done") {
          var inUse = used.indexOf(img.url) !== -1;
          top.appendChild(el("span", "owh-pill" + (inUse ? " is-on" : ""), null, inUse ? "In content" : "Not used yet"));
        }
        meta.appendChild(top);
        if (img.state === "uploading") meta.appendChild(el("p", "owh-img__sub", null, "Uploading…"));
        else if (img.state === "error") meta.appendChild(el("p", "owh-img__sub", null, img.error || "Upload failed"));
        else {
          if (img.width) meta.appendChild(el("p", "owh-img__sub", null, img.width + " × " + img.height + " px"));
          var url = el("input", "owh-img__url", { type: "text", readonly: "", "aria-label": "Image link" });
          url.value = img.url;
          url.addEventListener("focus", function () { url.select(); });
          meta.appendChild(url);
          var btns = el("div", "owh-img__btns");
          var b1 = el("button", "owh-btn owh-btn--small", { type: "button" }, "Copy link");
          var b2 = el("button", "owh-btn owh-btn--small owh-btn--ghost", { type: "button" }, "Copy <img> tag");
          var b3 = el("button", "owh-btn owh-btn--small owh-btn--ghost", { type: "button" }, "Insert at cursor");
          b1.addEventListener("click", function () { copyText(img.url).then(function () { flash(b1, "Copied"); }); });
          b2.addEventListener("click", function () { copyText(figureTag(img)).then(function () { flash(b2, "Copied"); }); });
          b3.addEventListener("click", function () { insertAtCursor(ta, figureTag(img)); });
          btns.appendChild(b1); btns.appendChild(b2); btns.appendChild(b3);
          meta.appendChild(btns);
        }
        li.appendChild(thumb); li.appendChild(meta);
        list.appendChild(li);
      });
      list.hidden = !imgs.length;
    }

    function upload(file, insert) {
      if (!file || !/^image\//.test(file.type || "image/")) return;
      var item = { name: file.name || "image", state: "uploading" };
      imgs.unshift(item);
      renderImages();
      if (!uploadUrl) { item.state = "error"; item.error = "Uploads are not available here."; renderImages(); return; }
      var fd = new FormData();
      fd.append("image", file, file.name || "image.png");
      fetch(uploadUrl, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf }, body: fd })
        .then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { if (!r.ok || !d.ok) throw new Error(d.error || "Upload failed"); return d; }); })
        .then(function (d) {
          item.state = "done"; item.url = d.url; item.width = d.width; item.height = d.height; item.alt = d.alt; item.name = d.name;
          renderImages();
          if (insert) insertAtCursor(ta, figureTag(item));
        })
        .catch(function (err) {
          item.state = "error"; item.error = err && err.message ? err.message : "Upload failed";
          renderImages();
        });
    }
    function uploadAll(files, insert) { Array.prototype.forEach.call(files || [], function (f) { upload(f, insert); }); }

    uploadBtn.addEventListener("click", function () { fileInput.click(); });
    drop.addEventListener("click", function () { fileInput.click(); });
    drop.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
    fileInput.addEventListener("change", function () { uploadAll(fileInput.files, false); fileInput.value = ""; });
    ["dragenter", "dragover"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("is-over"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove("is-over"); });
    });
    drop.addEventListener("drop", function (e) { uploadAll(e.dataTransfer && e.dataTransfer.files, false); });
    // An image pasted or dropped straight into the HTML box is uploaded and
    // inserted where the cursor is.
    ta.addEventListener("paste", function (e) {
      var files = e.clipboardData && e.clipboardData.files;
      if (files && files.length && /^image\//.test(files[0].type)) {
        e.preventDefault();
        uploadAll(files, true);
      }
    });
    ta.addEventListener("drop", function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length && /^image\//.test(files[0].type)) {
        e.preventDefault();
        uploadAll(files, true);
      }
    });

    // ---- start
    var initial = store(MODE_KEY) || (wrap.clientWidth >= 900 ? "split" : "write");
    setMode(initial, false);
    syncUsedImages();
    updateStatsLocal();
    return { refresh: function () { lastSent = null; refresh(true); } };
  }

  window.OWHtmlEditor = { mount: mount };

  function autoMount() {
    document.querySelectorAll("textarea[data-ow-html]").forEach(function (ta) {
      if (ta.id && ta.id.indexOf("__prefix__") !== -1) return;
      mount(ta, { titleInput: document.getElementById("id_title") });
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", autoMount);
  else autoMount();
})();
