/* Template 3 shared behaviour: header, footer, drawers, cart, cards, sliders.
   Runs on the live store: window.T3 is the store's own data (boot.js), the
   cart and wishlist are the storefront's server endpoints, and every link is a
   real storefront URL from T3.urls. */
(function () {
  const D = window.T3;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const qs = (k) => new URLSearchParams(location.search).get(k);
  const SYM = { INR: "₹", USD: "$", EUR: "€", GBP: "£", AED: "AED ", SGD: "S$" };
  const sym = SYM[D.currency] || (D.currency ? D.currency + " " : "₹");
  const inr = (n) => sym + Math.round(+n || 0).toLocaleString(D.currency === "INR" || !D.currency ? "en-IN" : "en-US");
  const byId = (id) => D.products.find((p) => p.id === id) || (D.page.products || []).find((p) => p.id === id) || (D.page.wishlist || []).find((p) => p.id === id) || (D.page.product && D.page.product.id === id ? D.page.product : null);
  const L = D.urls;
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  /* ---------- Icons ---------- */
  const I = {
    search: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
    user: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6"/></svg>',
    bag: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4h2l2.4 11.2a1 1 0 0 0 1 .8h9.4a1 1 0 0 0 1-.8L21 7H6"/><circle cx="9" cy="20" r="1.3"/><circle cx="18" cy="20" r="1.3"/></svg>',
    heart: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M12 20s-7-4.4-9-9.2C1.7 7.4 4 4 7.4 4c2 0 3.6 1.1 4.6 2.7C13 5.1 14.6 4 16.6 4 20 4 22.3 7.4 21 10.8 19 15.6 12 20 12 20Z"/></svg>',
    chev: '<svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m2.5 4.5 3.5 3.5 3.5-3.5"/></svg>',
    left: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m15 5-7 7 7 7"/></svg>',
    right: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="m9 5 7 7-7 7"/></svg>',
    arrow: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    close: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    menu: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h10"/></svg>',
    minus: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M5 12h14"/></svg>',
    plus: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg>',
    play: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>',
    eye: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>',
    star: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="m12 2.8 2.8 5.9 6.4.8-4.7 4.4 1.2 6.4L12 17.2l-5.7 3.1 1.2-6.4-4.7-4.4 6.4-.8z"/></svg>',
    truck: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M2 6h12v10H2zM14 9h4l3 3.5V16h-7z"/><circle cx="6" cy="17.5" r="1.8"/><circle cx="17.5" cy="17.5" r="1.8"/></svg>',
    return: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/></svg>',
    card: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M2.5 10h19M6 15h4"/></svg>',
    chat: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/></svg>',
    leaf: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M5 19c0-8 5-13 15-14-1 10-6 15-14 15"/><path d="M5 19 13 11"/></svg>',
    box: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="m12 3 8.5 4.5v9L12 21l-8.5-4.5v-9z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9"/></svg>',
    shield: '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m8.5 12 2.5 2.5 4.5-5"/></svg>',
    pin: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 21s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12Z"/><circle cx="12" cy="9" r="2.5"/></svg>',
    mail: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>',
    phone: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2Z"/></svg>',
    clock: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
    filter: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 5h16l-6 7.5V19l-4 1.5v-8z"/></svg>',
    grid2: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><rect x="13" y="13" width="8" height="8" rx="2"/></svg>',
    grid3: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="5" r="2"/><circle cx="12" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="12" cy="19" r="2"/><circle cx="19" cy="19" r="2"/></svg>',
    share: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="m8.2 10.8 7.6-4.4M8.2 13.2l7.6 4.4"/></svg>',
    trend: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="m3 17 6-6 4 4 8-8M15 7h6v6"/></svg>',
    ig: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1" fill="currentColor"/></svg>',
    x: '<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><path d="M17.8 3h3.1l-6.8 7.7L22 21h-6.2l-4.9-6.3L5.3 21H2.2l7.2-8.3L2 3h6.3l4.4 5.8zm-1.1 16.2h1.7L7.4 4.7H5.6z"/></svg>',
    yt: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M22 8.2a3 3 0 0 0-2.1-2.1C18 5.6 12 5.6 12 5.6s-6 0-7.9.5A3 3 0 0 0 2 8.2 31 31 0 0 0 1.6 12 31 31 0 0 0 2 15.8a3 3 0 0 0 2.1 2.1c1.9.5 7.9.5 7.9.5s6 0 7.9-.5a3 3 0 0 0 2.1-2.1 31 31 0 0 0 .4-3.8 31 31 0 0 0-.4-3.8ZM10 15V9l5.2 3z"/></svg>',
    fb: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M14 8.5V6.8c0-.8.5-1 .9-1h2.4V2h-3.3C10.4 2 9.6 4.7 9.6 6.4v2.1H7.5v3.9h2.1V22H14v-9.6h2.9l.4-3.9z"/></svg>',
    pinterest: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 0 0-3.6 19.3c-.1-.8-.2-2 0-2.9l1.2-5s-.3-.6-.3-1.5c0-1.4.8-2.4 1.8-2.4.9 0 1.3.6 1.3 1.4 0 .9-.5 2.1-.8 3.3-.2 1 .5 1.8 1.5 1.8 1.8 0 3.1-1.9 3.1-4.6 0-2.4-1.7-4.1-4.2-4.1-2.9 0-4.5 2.1-4.5 4.4 0 .9.3 1.8.8 2.3l.1.4-.3 1.1c0 .2-.2.2-.4.1-1.3-.6-2-2.4-2-3.9 0-3.2 2.3-6.1 6.7-6.1 3.5 0 6.2 2.5 6.2 5.8 0 3.5-2.2 6.3-5.2 6.3-1 0-2-.5-2.3-1.1l-.6 2.4c-.2.9-.9 2-1.3 2.7A10 10 0 1 0 12 2Z"/></svg>',
    wa: '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.6 15l-1.4 5 5.1-1.3A10 10 0 1 0 12 2Zm5.8 14.1c-.2.7-1.4 1.3-2 1.4-.5.1-1.2.1-1.9-.1-.4-.1-1-.3-1.7-.6-3-1.3-5-4.3-5.1-4.5-.2-.2-1.2-1.6-1.2-3.1s.8-2.2 1.1-2.5c.3-.3.6-.4.8-.4h.6c.2 0 .4 0 .6.5l.9 2.1c.1.2.1.4 0 .5l-.3.5-.4.5c-.1.1-.3.3-.1.6.2.3.8 1.3 1.7 2.1 1.2 1 2.1 1.3 2.4 1.5.3.1.5.1.6-.1l.9-1c.2-.3.4-.2.6-.1l2 .9c.3.1.5.2.5.3.1.2.1.7-.1 1.3Z"/></svg>',
  };
  window.T3.I = I;
  window.T3.esc = esc;

  /* ---------- Storage (safe) ---------- */
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  };

  /* ---------- Server calls ---------- */
  function post(url, body) {
    const fd = new FormData();
    Object.entries(body || {}).forEach(([k, v]) => fd.append(k, v));
    return fetch(url, { method: "POST", body: fd, credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": D.csrf || "" } })
      .then((r) => r.json().catch(() => ({})).then((j) => ({ ok: r.ok, status: r.status, data: j })));
  }

  /* ---------- Cart (the storefront's server cart) ---------- */
  // Each line: { key, pid, name, img, url, meta, price, mrp, qty, line, inStock }
  let cart = [];
  let totals = { subtotal: 0, discount: 0, total: 0, credit: 0, coupon: "" };
  function readCart(j) {
    cart = (j.items || []).map((it) => {
      const unit = +it.unit_price || 0;
      return { key: it.product_id, pid: it.pid || "", name: it.title, img: it.image || it.image_fallback || D.U("@cat0", 300, 400), url: it.url || "#",
        meta: it.variant_label || "", price: unit, mrp: +it.mrp || 0, qty: +it.quantity || 1, line: +it.line_total || unit * (+it.quantity || 1), inStock: it.in_stock !== false, short: !!it.short };
    });
    const t = j.totals || {};
    totals = { subtotal: +t.subtotal || 0, discount: +t.discount || 0, total: +t.total || 0, credit: +t.credit_discount || 0, coupon: t.coupon_code || "", tax: +t.tax || 0,
      before: t.total_before_credit != null ? +t.total_before_credit : +t.total || 0, count: +j.count || 0, items: j.items || [] };
    renderCart();
  }
  function loadCart() {
    if (!L.cartData) return Promise.resolve();
    return fetch(L.cartData, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then((r) => r.json()).then(readCart).catch(() => {});
  }
  const cartCount = () => cart.reduce((n, l) => n + l.qty, 0);
  const cartTotal = () => totals.subtotal;
  const cartMrp = () => cart.reduce((n, l) => n + (l.mrp > l.price ? l.mrp : l.price) * l.qty, 0);

  /* opts: { qty, attrs: [{name, value}], key, silent, buy } */
  function addToCart(id, opts = {}) {
    const p = byId(id);
    if (p && !opts.attrs && !opts.key && p.needsPick) { location.href = p.url; return Promise.resolve(false); }
    const body = { quantity: opts.qty || 1 };
    if (opts.attrs && opts.attrs.length) body.variant_attrs = JSON.stringify(opts.attrs);
    if (opts.key) body.variant_key = opts.key;
    if (opts.buy) body.buy = "1";
    return post(L.cartAdd + encodeURIComponent(id) + "/", body).then((res) => {
      const j = res.data || {};
      if (!res.ok || j.ok === false) {
        if (j.select_variant && p) { location.href = p.url; return false; }
        toast(j.error || "Could not add this item right now.");
        return false;
      }
      if (opts.buy) { location.href = j.redirect_url || L.checkout; return true; }
      return loadCart().then(() => { if (!opts.silent) openDrawer("cart"); else toast("Added to your cart"); return true; });
    }).catch(() => { toast("Network error. Please try again."); return false; });
  }
  function setQty(key, qty) {
    return post(L.cartUpdate, qty > 0 ? { product_id: key, quantity: qty } : { product_id: key, action: "remove" })
      .then((res) => { if (res.data && res.data.items) readCart(res.data); else loadCart(); });
  }

  /* ---------- Wishlist (signed-in shoppers) ---------- */
  let wish = (D.wish || []).slice();
  function toggleWish(id) {
    const on = wish.includes(id);
    post((on ? L.wishRemove : L.wishAdd) + encodeURIComponent(id) + "/", {}).then((res) => {
      const j = res.data || {};
      if (j.login_required || res.status === 401) { toast("Sign in to save your wishlist"); setTimeout(() => (location.href = L.account), 900); return; }
      if (!res.ok) { toast("Could not update your wishlist."); return; }
      wish = on ? wish.filter((x) => x !== id) : [...wish, id];
      $$(`[data-wish="${id}"]`).forEach((b) => b.classList.toggle("is-on", wish.includes(id)));
      toast(wish.includes(id) ? "Saved to your wishlist" : "Removed from wishlist");
      $$("[data-wish-count]").forEach((e) => (e.textContent = wish.length));
      document.dispatchEvent(new CustomEvent("t3:wish"));
    });
  }
  Object.assign(window.T3, { $, $$, qs, inr, byId, addToCart, toggleWish, store, post, setQty, loadCart, getCart: () => cart, getTotals: () => totals, cartTotal, cartMrp, cartCount, getWish: () => wish, sym });

  /* ---------- Product card ---------- */
  const BADGE = { new: "New", hot: "Hot", popular: "Popular", sale: "Sale" };
  const HIDDEN_BADGES = ["sale", "popular"];  /* never shown as pills anywhere */
  window.T3.HIDDEN_BADGES = HIDDEN_BADGES;
  function priceHTML(p) {
    if (!D.showPrice) return "";
    const sale = p.mrp && p.mrp > p.price;
    return `<div class="price ${sale ? "price--sale" : ""}"><span class="price__now">${inr(p.price)}</span>${sale ? `<span class="price__was">MRP: ${inr(p.mrp)}</span><span class="price__off">${Math.round((1 - p.price / p.mrp) * 100)}% off</span>` : ""}</div>`;
  }
  function card(p, o = {}) {
    const href = p.url;
    const name = esc(p.name);
    const words = String(p.name || "").trim().split(/\s+/);
    const shortName = words.length > 6 ? esc(words.slice(0, 6).join(" ")) + "\u2026" : name;
    const quickLabel = !p.inStock ? "Sold Out" : (p.needsPick || p.sizes.length > 1 ? "Quick Add" : "Add To Cart");
    return `<article class="p-card ${o.reveal === false ? "" : "reveal"}">
      <div class="p-card__media">
        <a href="${href}" aria-label="${name}">
          <img src="${p.img[0]}" alt="${name}" loading="lazy">
          ${D.hoverSwap && p.img[1] ? `<img class="alt" src="${p.img[1]}" alt="" loading="lazy">` : ""}
        </a>
        <div class="p-card__badges">${p.badges.filter((b) => !HIDDEN_BADGES.includes(b)).slice(0, 2).map((b) => `<span class="badge badge--${b}">${BADGE[b]}</span>`).join("")}</div>
        <button class="p-card__wish ${wish.includes(p.id) ? "is-on" : ""}" data-wish="${p.id}" aria-label="Add to wishlist">${I.heart}</button>
        ${o.quick === false ? "" : `<button class="btn btn--white p-card__quick" ${p.inStock ? `data-add="${p.id}"` : "disabled"}>${quickLabel}</button>`}
      </div>
      <div class="p-card__body">
        ${o.vendor && p.catName ? `<div class="p-card__vendor">${esc(p.catName)}</div>` : ""}
        <a href="${href}" class="p-card__title" title="${name}">${shortName}</a>
        ${priceHTML(p)}
        ${o.swatch === false || !p.colors.length ? "" : `<div class="swatches">${p.colors.map((c, i) => `<i style="background:${c}" title="${esc((p.colorNames || [])[i] || "")}"></i>`).join("")}</div>`}
      </div>
    </article>`;
  }
  window.T3.card = card; window.T3.priceHTML = priceHTML;

  function stars(r) {
    return `<span class="stars">${Array.from({ length: 5 }, (_, i) => `<span style="opacity:${i < Math.round(r) ? 1 : .25}">${I.star}</span>`).join("")}</span>`;
  }
  window.T3.stars = stars;

  /* ---------- Logo ---------- */
  /* Edit Content → Brand logos. "header" renders the desktop logo above
     1024px and the phone logo below it (as Template 1); each is sized by the
     owner's width via --logo-w-d / --logo-w-m / --logo-w-f (set at boot). */
  function logo(kind) {
    const LG = D.logo || {}, alt = esc(LG.alt || D.brand);
    // No logo uploaded: the store's name alone (no decorative prefix).
    const word = esc(D.brand.toUpperCase());
    const img = (src, cls) => `<img class="logo__img ${cls}" src="${esc(src)}" alt="${alt}">`;
    if (kind === "header") {
      if (!LG.desktop && !LG.mobile) return word;
      return (LG.desktop ? img(LG.desktop, "logo__img--d") : `<span class="logo__txt logo__img--d">${word}</span>`) +
        (LG.mobile ? img(LG.mobile, "logo__img--m") : `<span class="logo__txt logo__img--m">${word}</span>`);
    }
    if (kind === "footer") return LG.footer ? img(LG.footer, "logo__img--f") : "";
    const src = LG[kind] || LG.desktop;
    return src ? img(src, kind === "mobile" ? "logo__img--drawer" : "logo__img--plain") : word;
  }
  (() => {
    const z = (D.logo || {}).sizes || {}, r = document.documentElement.style;
    if (z.desktop) r.setProperty("--logo-w-d", z.desktop);
    if (z.mobile) r.setProperty("--logo-w-m", z.mobile);
    if (z.footer) r.setProperty("--logo-w-f", z.footer);
  })();
  // Published as logoHTML: T3.logo is the store's logo DATA (urls, alt,
  // sizes) from boot.js, which the header, footer, drawer and pages read.
  // Assigning the function to T3.logo replaced that data, so no uploaded
  // logo ever rendered.
  window.T3.logoHTML = logo;

  /* ---------- Header ---------- */
  /* Edit Content → Brand: "static" shows statement 1, centred and still;
     "dynamic" loops every statement left to right (as Template 1), pausing on
     hover. Only the first copy of each statement is announced and focusable. */
  function announce() {
    const list = (D.announce || []).filter((a) => a.before || a.word || a.after);
    if (!list.length) return "";
    const one = (a, primary) => `<span class="announce__item"${primary ? "" : ' aria-hidden="true"'}>${esc(a.before)}${a.word ? `<a href="${esc(a.href)}" target="_blank" rel="noopener noreferrer"${primary ? "" : ' tabindex="-1"'}>${esc(a.word)}</a>` : ""}${esc(a.after)}</span>`;
    if (D.announceMode !== "dynamic") {
      return `<div class="announce announce--static" role="region" aria-label="Announcements"><div class="announce__static">${one(list[0], true)}</div></div>`;
    }
    const repeat = Math.max(1, Math.ceil(8 / list.length));
    const run = []; for (let r = 0; r < repeat; r++) run.push(...list);
    const chars = run.reduce((n, a) => n + (a.before + a.word + a.after).length, 0);
    const dur = Math.max(12, Math.round((chars * 7.5 + run.length * 56) / 45));
    const group = (g) => `<div class="announce__group"${g ? ' aria-hidden="true"' : ""}>${run.map((a, n) => one(a, !g && n < list.length)).join("")}</div>`;
    return `<div class="announce announce--dynamic" role="region" aria-label="Announcements"><div class="announce__track" style="animation-duration:${dur}s">${group(0)}${group(1)}</div></div>`;
  }
  /* The two featured categories: the owner's picks on the dashboard
     Categories page (in pick order), any empty slot filled by a homepage
     category, then by catalogue order. Used by the Shop mega menu and the
     top of the Categories page, so both always show the same pair. */
  function featuredPair() {
    const picked = (D.featuredPair || []).map((id) => D.categories.find((c) => c.id === id)).filter(Boolean);
    const rest = [...D.categories.filter((c) => c.featured), ...D.categories].filter((c) => !picked.includes(c));
    return [...new Set([...picked, ...rest])].slice(0, 2);
  }
  // Published under its own name: T3.featuredPair is the owner's DATA (the
  // picked ids from template3.py), which this must never overwrite.
  window.T3.featuredCategories = featuredPair;

  function header() {
    const float = document.body.dataset.float === "1";
    /* Shop mega menu cards: the two featured categories (the same pair the
       Categories page shows on top). "Categories" is always a plain link in
       Template 2: the dashboard's mega-menu toggle is a Template 1 setting. */
    const cards = featuredPair();
    const catsNav = `<div class="nav__item"><a class="nav__link" href="${L.categories}">Categories</a></div>`;
    const pages = [[L.about, "About Us"], [L.contact, "Contact"]];
    if (D.blogs.length) pages.push([L.blogs, "Blogs"]);
    if (D.hasCertificates && L.certificates) pages.push([L.certificates, "Certificates"]);
    pages.push([L.account, "My Account"], [L.policy.privacy, "Policies"]);
    return `
    ${announce()}
    <header class="header ${float ? "header--float" : ""}" id="site-header">
      <div class="header__bar">
        <div class="header__left"><button class="icon-btn menu-toggle" data-open="menu" aria-label="Menu">${I.menu}</button>
        <nav class="nav" aria-label="Main">
          <div class="nav__item"><a class="nav__link" href="${L.shop}">Shop ${I.chev}</a>
            <div class="mega-menu">
              <div><h4>Categories</h4><ul>${D.categories.slice(0, 6).map((c) => `<li><a href="${c.url}">${esc(c.name)}</a></li>`).join("") || `<li><a href="${L.shop}">All Products</a></li>`}</ul></div>
              <div><h4>Featured</h4><ul><li><a href="${L.shop}?sort=new">New Arrivals</a></li><li><a href="${L.shop}?sort=best">Best Sellers</a></li>${D.showPrice ? `<li><a href="${L.shop}?sale=1">Sale</a></li>` : ""}<li><a href="${L.shop}">Shop All</a></li><li><a href="${L.categories}">All Categories</a></li></ul></div>
              ${cards.length ? `<div class="mega-menu__cards">${cards.map((c) => `<a class="mega-card" href="${c.url}"><img src="${D.U(c.img, 500, 620)}" alt=""><em>&#9733; Featured</em><span>${esc(c.name)}</span></a>`).join("")}</div>` : ""}
            </div>
          </div>
          ${catsNav}
          <div class="nav__item"><a class="nav__link" href="${L.about}">Pages ${I.chev}</a>
            <div class="dropdown">
              ${pages.map(([h, t]) => `<a href="${h}">${esc(t)}</a>`).join("")}
            </div>
          </div>
          ${D.blogs.length ? `<div class="nav__item"><a class="nav__link" href="${L.blogs}">Blogs</a></div>` : `<div class="nav__item"><a class="nav__link" href="${L.contact}">Contact</a></div>`}
        </nav></div>
        <a href="${L.home}" class="logo" aria-label="${esc(D.brand)} home">${logo("header")}</a>
        <div class="header__tools">
          <button class="header__search" data-open="search"><span>What are you looking for?</span>${I.search}</button>
          <button class="icon-btn search-sm" data-open="search" aria-label="Search">${I.search}</button>
          <a class="icon-btn" href="${L.account}" aria-label="Account">${I.user}</a>
          <button class="icon-btn cart-btn" data-open="cart" aria-label="Cart">${I.bag}<span class="cart-bubble" data-cart-count></span></button>
        </div>
      </div>
    </header>`;
  }

  /* ---------- Footer ---------- */
  function footer() {
    const S = D.socials || {};
    const handle = S.instagram ? "@" + S.instagram.replace(/\/+$/, "").split("/").pop() : "";
    /* Shop the Look: only the store's own products and categories (never the
       design's demo reel photos), alternating product, category, product, and
       each tile links to that product or category page. Categories without
       an uploaded image are skipped rather than shown with a stock photo. */
    const prodShots = D.products.filter((p) => p.img && p.img[0]).map((p) => ({ img: p.img[0], url: p.url, name: p.name }));
    const catShots = D.categories.filter((c) => c.img && String(c.img).charAt(0) !== "@").map((c) => ({ img: D.U(c.img, 400, 400), url: c.url, name: c.name }));
    const shots = [];
    for (let i = 0; shots.length < 3 && (i < prodShots.length || i < catShots.length); i++) {
      if (prodShots[i]) shots.push(prodShots[i]);
      if (shots.length < 3 && catShots[i]) shots.push(catShots[i]);
    }
    const insta = shots.map((s) => `<a href="${esc(s.url)}" aria-label="${esc(s.name)}"><img src="${esc(s.img)}" alt="${esc(s.name)}" loading="lazy"></a>`).join("");
    const icons = [["x", "X", I.x], ["instagram", "Instagram", I.ig], ["youtube", "YouTube", I.yt], ["facebook", "Facebook", I.fb], ["pinterest", "Pinterest", I.pinterest]]
      .filter(([k]) => S[k]).map(([k, t, ic]) => `<a href="${esc(S[k])}" target="_blank" rel="noopener noreferrer" aria-label="${t}">${ic}</a>`).join("");
    const extraPol = (D.customPolicies || []).map((cp) => `<li><a href="${cp.url}">${esc(cp.title)}</a></li>`).join("");
    return `
    <footer class="footer">
      <div class="container">
        <div class="footer__top">
          <div>${(D.logo || {}).footer ? `<a href="${L.home}" class="logo footer__logo" aria-label="${esc(D.brand)} home">${logo("footer")}</a>` : ""}<h2>Subscribe for updates,<br>tips &amp; exclusive offers</h2></div>
          <form class="footer__form" data-newsletter data-source="footer">
            <div class="search-field"><input type="email" name="email" required placeholder="Enter your email" aria-label="Email"><button class="btn__dot" style="background:#000;color:#fff" aria-label="Subscribe">${I.arrow}</button></div>
            <small>By subscribing you agree to the <a href="${L.policy.terms}">Terms of Use</a> &amp; <a href="${L.policy.privacy}">Privacy Policy</a>.</small>
          </form>
        </div>
        <div class="footer__cols">
          <div><h3>Company</h3><ul><li><a href="${L.about}">Our Story</a></li><li><a href="${L.contact}">Contact</a></li><li><a href="${L.contact}#faq">FAQs</a></li>${D.blogs.length ? `<li><a href="${L.blogs}">Blogs</a></li>` : ""}${L.corePages ? `<li><a href="${L.corePages}">Help &amp; Guide</a></li>` : ""}${D.hasCertificates && L.certificates ? `<li><a href="${L.certificates}">Certificates</a></li>` : ""}<li><a href="${L.account}">My Account</a></li></ul></div>
          <div><h3>Collection</h3><ul>${D.categories.slice(0, 6).map((c) => `<li><a href="${c.url}">${esc(c.name)}</a></li>`).join("") || `<li><a href="${L.shop}">Shop All</a></li>`}</ul></div>
          <div><h3>Get Help</h3><ul><li><a href="${L.orders || L.account}">Track Order</a></li><li><a href="${L.policy.refund}">Return Policy</a></li><li><a href="${L.policy.shipping}">Shipping Info</a></li><li><a href="${L.policy.terms}">Terms &amp; Conditions</a></li><li><a href="${L.policy.privacy}">Privacy Policy</a></li>${extraPol}</ul></div>
          ${shots.length ? `<div><h3>Shop the Look</h3><div class="footer__insta">${insta}</div>${handle ? `<p class="small" style="margin-top:12px;opacity:.75"><a href="${esc(S.instagram)}" target="_blank" rel="noopener noreferrer">${esc(handle)}</a></p>` : ""}</div>` : ""}
        </div>
        <div class="footer__mid">
          <div class="socials">${icons}</div>
          <div class="footer__legal"><a href="${L.policy.shipping}">Shipping Policy</a><a href="${L.policy.terms}">Terms of Service</a><a href="${L.policy.privacy}">Privacy Policy</a></div>
        </div>
        <div class="footer__bottom">
          <span>© ${new Date().getFullYear()} ${esc(D.brand)}. All rights reserved.</span>
          <span class="footer__credit" data-nosnippet>Created at <span class="ow-credit"></span></span>
        </div>
      </div>
    </footer>`;
  }

  /* ---------- Drawers ---------- */
  function drawers() {
    const trend = D.categories.slice(0, 6).map((c) => c.name);
    const N = D.newsletter;
    return `
    <div class="overlay" data-overlay></div>
    <aside class="drawer" id="drawer-cart" aria-label="Cart">
      <div class="drawer__head"><h3 class="title-count">Your cart <sup data-cart-count></sup></h3><button class="icon-btn" data-close aria-label="Close cart">${I.close}</button></div>
      <div class="drawer__body" data-cart-lines></div>
      <div class="drawer__foot" data-cart-foot></div>
    </aside>
    <aside class="drawer drawer--left m-menu" id="drawer-menu" aria-label="Menu">
      <div class="drawer__head"><a href="${L.home}" class="logo">${logo("mobile")}</a><button class="icon-btn" data-close aria-label="Close menu">${I.close}</button></div>
      <div class="drawer__body">
        ${D.categories.length ? `<div class="m-menu__cats">${D.categories.slice(0, 6).map((c) => `<a href="${c.url}"><img src="${D.U(c.img, 300, 300)}" alt=""><span>${esc(c.name)}</span></a>`).join("")}</div>` : ""}
        <a class="m-link" href="${L.shop}">Shop All ${I.right}</a>
        <a class="m-link" href="${L.categories}">All Categories ${I.right}</a>
        <a class="m-link" href="${L.shop}?sort=new">New Arrivals ${I.right}</a>
        ${D.blogs.length ? `<a class="m-link" href="${L.blogs}">Blogs ${I.right}</a>` : ""}
        ${D.hasCertificates && L.certificates ? `<a class="m-link" href="${L.certificates}">Certificates ${I.right}</a>` : ""}
        <a class="m-link" href="${L.about}">About Us ${I.right}</a>
        <a class="m-link" href="${L.contact}">Contact ${I.right}</a>
        <a class="m-link" href="${L.account}">My Account ${I.right}</a>
      </div>
    </aside>
    <div class="search-pop" id="drawer-search" role="dialog" aria-label="Search">
      <div class="container">
        <form style="display:flex;gap:10px;align-items:center" action="${L.shop}" method="get" data-search-form>
          <div class="search-field" style="flex:1">${I.search}<input type="search" name="q" placeholder="Search for products…" data-search-input aria-label="Search"></div>
          <button type="button" class="icon-btn" data-close aria-label="Close search">${I.close}</button>
        </form>
        <div class="search-pop__grid">
          <div class="search-pop__trend"><p class="eyebrow muted" style="margin-bottom:12px">Trending searches</p>${trend.map((t) => `<a class="tag" href="#" data-trend="${esc(t)}">${esc(t)}</a>`).join("")}</div>
          <div><p class="eyebrow muted" style="margin-bottom:12px" data-search-label>Popular products</p><div class="search-results" data-search-results></div></div>
        </div>
      </div>
    </div>
    ${N ? `<div class="modal" id="modal-newsletter" role="dialog" aria-label="Newsletter">
      <div class="modal__backdrop" data-close></div>
      <div class="modal__box">
        <button class="modal__close" data-close aria-label="Close">${I.close}</button>
        <div class="modal__media"><picture>${N.mobileImage ? `<source media="(max-width: 640px)" srcset="${esc(N.mobileImage)}">` : ""}<img src="${esc(N.image || D.U("pinkHair", 700, 800))}" alt=""></picture></div>
        <div class="modal__content">
          <a class="logo" style="justify-content:center;font-size:20px">${logo("desktop")}</a>
          <h3 class="h2" style="font-size:30px">${esc(N.heading)}</h3>
          <p class="muted small">${esc(N.text)}${N.offer ? ` ${esc(N.offer)}` : ""}</p>
          <form data-newsletter data-source="popup" style="display:grid;gap:10px">
            <input class="input" type="email" name="email" required placeholder="Enter your email" aria-label="Email">
            <button class="btn btn--black btn--block">${esc(N.button)}</button>
          </form>
          <button class="link small" data-close style="color:var(--muted)">No thanks</button>
        </div>
      </div>
    </div>` : ""}
    <div class="toast" data-toast><i>${I.check}</i><span></span></div>`;
  }

  /* ---------- Cart rendering ---------- */
  function renderCart() {
    const n = cartCount();
    $$("[data-cart-count]").forEach((e) => { e.textContent = n || ""; e.dataset.count = n; });
    const lines = $("[data-cart-lines]"), foot = $("[data-cart-foot]");
    if (!lines) { document.dispatchEvent(new CustomEvent("t3:cart")); return; }
    const total = cartTotal(), left = D.freeShip ? Math.max(0, D.freeShip - total) : 0;
    if (!cart.length) {
      lines.innerHTML = `<div class="empty-state">${I.bag.replace(/20/g, "48")}<h3 class="h4">Your cart is empty</h3><p class="muted small" style="margin:6px 0 18px">Looks like you have not added anything yet.</p><a class="btn btn--black" href="${L.shop}">Start Shopping</a></div>`;
      foot.innerHTML = ""; document.dispatchEvent(new CustomEvent("t3:cart")); return;
    }
    const inCart = new Set(cart.map((l) => l.pid));
    const also = D.products.filter((p) => !inCart.has(p.id) && p.inStock).slice(0, 2);
    lines.innerHTML = (D.freeShip ? `<div class="ship-progress">${left ? `Spend ${inr(left)} more to unlock free shipping` : "You have unlocked free shipping!"}<div class="ship-progress__bar"><i style="width:${Math.min(100, (total / D.freeShip) * 100)}%"></i></div></div>` : "") +
      cart.map((l, i) => `<div class="line-item">
        <a class="line-item__img" href="${l.url}" aria-label="${esc(l.name)}"><img src="${l.img}" alt=""></a>
        <div><a class="line-item__title" href="${l.url}">${esc(l.name)}</a>
          ${l.meta ? `<div class="line-item__meta">${esc(l.meta)}</div>` : ""}
          ${l.short ? `<div class="line-item__meta" style="color:var(--badge-sale)">${l.inStock ? "Only a few left in stock" : "Out of stock"}</div>` : ""}
          ${priceHTML({ price: l.price, mrp: l.mrp })}
          <div class="line-item__row"><div class="qty"><button data-qty="${i}" data-d="-1" aria-label="Decrease">${I.minus}</button><input value="${l.qty}" readonly aria-label="Quantity"><button data-qty="${i}" data-d="1" aria-label="Increase">${I.plus}</button></div><button class="line-item__remove" data-remove="${i}">Remove</button></div>
        </div></div>`).join("") +
      (also.length ? `<div style="margin-top:22px"><p class="eyebrow" style="margin-bottom:12px">You may also like</p><div class="grid grid--2">${also.map((p) => card(p, { reveal: false, swatch: false })).join("")}</div></div>` : "");
    foot.innerHTML = `<div class="row"><span class="muted">Subtotal</span><b>${inr(total)}</b></div>
      ${totals.discount > 0 ? `<div class="row small" style="color:var(--ok)"><span>Discount${totals.coupon ? ` (${esc(totals.coupon)})` : ""}</span><b>−${inr(totals.discount)}</b></div>` : (cartMrp() > total ? `<div class="row small" style="color:var(--ok)"><span>You save</span><b>${inr(cartMrp() - total)}</b></div>` : "")}
      <p class="summary__note" style="margin:6px 0 14px">Taxes included. Shipping calculated at checkout.</p>
      <div style="display:grid;grid-template-columns:1fr 1.4fr;gap:10px"><a class="btn btn--grey" href="${L.cart}">View Cart</a><a class="btn btn--black" href="${L.checkout}">Check Out</a></div>`;
    document.dispatchEvent(new CustomEvent("t3:cart"));
  }

  /* ---------- Open/close ---------- */
  function openDrawer(name) {
    closeAll();
    const el = $(`#drawer-${name}`) || $(`#modal-${name}`); if (!el) return;
    el.classList.add("is-open");
    if (!name.startsWith("search") && !el.classList.contains("modal")) $("[data-overlay]").classList.add("is-open");
    if (name === "search") { $("[data-overlay]").classList.add("is-open"); setTimeout(() => $("[data-search-input]").focus(), 300); }
    document.body.classList.add("no-scroll");
  }
  function closeAll() {
    $$(".drawer.is-open, .search-pop.is-open, .modal.is-open, .filters.is-open").forEach((e) => e.classList.remove("is-open"));
    $("[data-overlay]")?.classList.remove("is-open");
    document.body.classList.remove("no-scroll");
  }
  Object.assign(window.T3, { openDrawer, closeAll });

  let toastT;
  function toast(msg) {
    const t = $("[data-toast]"); if (!t) return;
    t.querySelector("span").textContent = msg; t.classList.add("is-show");
    clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("is-show"), 2400);
  }
  window.T3.toast = toast;

  /* ---------- Search ---------- */
  function runSearch(q) {
    const res = $("[data-search-results]"); if (!res) return;
    // Every word must appear somewhere in the name, category or brand, so
    // "red cotton" finds "Cotton Shirt (Red)". All matches are listed (the
    // panel scrolls); the link at the end opens the full server-side search.
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const list = q ? D.products.filter((p) => { const hay = (p.name + " " + (p.catName || "") + " " + (p.brand || "")).toLowerCase(); return words.every((w) => hay.includes(w)); }) : D.products.slice(0, 8);
    const all = `${L.shop}?q=${encodeURIComponent(q)}`;
    $("[data-search-label]").textContent = q ? `${list.length} result${list.length === 1 ? "" : "s"} for “${q}”` : "Popular products";
    res.innerHTML = list.length
      ? list.map((p) => `<a class="search-hit" href="${p.url}"><img src="${(p.img || [])[0] || ""}" alt="" loading="lazy"><span><b>${esc(p.name)}</b>${priceHTML(p)}</span></a>`).join("") +
        (q ? `<a class="search-all link" href="${all}">View all results for “${esc(q)}”</a>` : "")
      : `<p class="muted">No matches. <a class="link" href="${all}">Search the full catalogue</a>.</p>`;
  }

  /* ---------- Sliders ---------- */
  function initSliders(root = document) {
    $$("[data-slider]", root).forEach((s) => {
      if (s.dataset.ready) return; s.dataset.ready = 1;
      const track = $(".slider__track", s), bar = $(".slider__bar i", s), prev = $("[data-prev]", s), next = $("[data-next]", s);
      if (!track) return;
      const step = () => (track.firstElementChild?.getBoundingClientRect().width || 300) + 16;
      const upd = () => {
        const max = track.scrollWidth - track.clientWidth;
        if (bar) { const w = Math.max(12, (track.clientWidth / (track.scrollWidth || 1)) * 100); bar.style.width = w + "%"; bar.style.left = (max ? (track.scrollLeft / max) * (100 - w) : 0) + "%"; }
        if (prev) prev.disabled = track.scrollLeft < 4; if (next) next.disabled = track.scrollLeft > max - 4;
      };
      prev?.addEventListener("click", () => track.scrollBy({ left: -step() }));
      next?.addEventListener("click", () => track.scrollBy({ left: step() }));
      track.addEventListener("scroll", upd, { passive: true }); addEventListener("resize", upd); setTimeout(upd, 60);
    });
  }
  window.T3.initSliders = initSliders;

  /* ---------- Accordions & reveal ---------- */
  function initAcc(root = document) {
    $$(".acc__btn", root).forEach((b) => {
      if (b.dataset.ready) return; b.dataset.ready = 1;
      b.addEventListener("click", () => {
        const acc = b.closest(".acc"), group = acc.closest("[data-acc-single]");
        if (group && !acc.classList.contains("is-open")) $$(".acc.is-open", group).forEach((a) => a.classList.remove("is-open"));
        acc.classList.toggle("is-open"); b.setAttribute("aria-expanded", acc.classList.contains("is-open"));
      });
    });
  }
  window.T3.initAcc = initAcc;
  window.T3.acc = (title, body, open) => `<div class="acc ${open ? "is-open" : ""}"><button class="acc__btn" aria-expanded="${!!open}">${title}<span class="chev">${I.chev}</span></button><div class="acc__panel"><div class="acc__inner"><div>${body}</div></div></div></div>`;
  /* Plain text with line breaks → paragraphs (owner FAQ answers, descriptions). */
  window.T3.paras = (t) => String(t || "").split(/\n{2,}/).map((x) => `<p>${esc(x).replace(/\n/g, "<br>")}</p>`).join("");

  const io = "IntersectionObserver" in window ? new IntersectionObserver((es) => es.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); } }), { rootMargin: "0px 0px -8% 0px" }) : null;
  function initReveal(root = document) { $$(".reveal:not(.is-in)", root).forEach((el, i) => { el.style.transitionDelay = (i % 6) * 60 + "ms"; io ? io.observe(el) : el.classList.add("is-in"); }); }
  window.T3.initReveal = initReveal;
  window.T3.refresh = (root) => { initSliders(root); initAcc(root); initReveal(root); };

  /* ---------- Service badges ----------
     Delivery, returns, payments and support, from the store's own settings
     where it has them (free-shipping threshold, office hours / phone / email,
     refund policy and contact links). Any page with a [data-services] slot
     gets them: the home page (between Best Sellers and About), Shop and Cart. */
  const SVC_ICONS = {
    truck: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6.5h11v9h-11z"/><path d="M13.5 9.5h4.2l3 3.2v2.8h-7.2"/><circle cx="6.5" cy="17.2" r="1.9"/><circle cx="17" cy="17.2" r="1.9"/><path d="M5.5 10h4"/></svg>',
    returns: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>',
    secure: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6z"/><path d="m8.8 12.2 2.2 2.2 4.3-4.6"/></svg>',
    support: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14v-2a8 8 0 0 1 16 0v2"/><rect x="2.8" y="13.5" width="4" height="6" rx="1.6"/><rect x="17.2" y="13.5" width="4" height="6" rx="1.6"/><path d="M19.2 19.5c0 1.4-1.6 2-4.2 2h-1.5"/></svg>',
  };
  function servicesHTML() {
    const C = D.contact || {};
    const items = [
      ["truck", "Fast delivery", D.freeShip ? `Free on orders above ${inr(D.freeShip)}` : "Tracked to your door", ""],
      ["returns", "Easy returns", "See our return policy", (L.policy || {}).refund || ""],
      ["secure", "100% secure payments", D.isD2C ? "UPI, cards, netbanking and COD" : "Trusted payment options", ""],
      ["support", "Customer service", C.hours || C.phone || C.email || "We are here to help", L.contact || ""],
    ];
    // pathLength="1" lets CSS draw every stroke from 0 to 1, whatever its size.
    const drawable = (svg) => svg.replace(/<(path|circle|rect)\b/g, '<$1 pathLength="1"');
    return `<div class="svc">${items.map(([ico, title, sub, href], i) => {
      const inner = `<span class="svc__ico" aria-hidden="true">${drawable(SVC_ICONS[ico])}</span><span class="svc__txt"><b>${esc(title)}</b><span>${esc(sub)}</span></span>`;
      const attrs = `class="svc__item" style="--i:${i}"`;
      return href ? `<a ${attrs} href="${esc(href)}">${inner}</a>` : `<div ${attrs}>${inner}</div>`;
    }).join("")}</div>`;
  }
  window.T3.servicesHTML = servicesHTML;

  /* Entrance: once the panel scrolls into view, each disc grows in, its icon
     draws itself and the text rises, one badge after another. Only armed
     when it can run (IntersectionObserver, motion allowed), so the badges are
     never left hidden. */
  function animateServices(root) {
    const svc = root.querySelector(".svc");
    if (!svc || !("IntersectionObserver" in window)) return;
    if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    svc.classList.add("is-armed");
    const io = new IntersectionObserver((entries) => entries.forEach((e) => {
      if (!e.isIntersecting) return;
      e.target.classList.add("is-in");
      io.unobserve(e.target);
      // Entrance over: drop the stagger delays so hover reacts instantly.
      setTimeout(() => e.target.classList.add("is-done"), 2000);
    }), { threshold: 0.3 });
    io.observe(svc);
  }

  /* ---------- Boot ---------- */
  document.addEventListener("DOMContentLoaded", () => {
    const h = $("[data-header]"); if (h) h.outerHTML = header();
    const f = $("[data-footer]"); if (f) f.outerHTML = footer();
    document.body.insertAdjacentHTML("beforeend", drawers());
    renderCart(); runSearch("");
    loadCart().then(() => { if (qs("open_cart") === "1") openDrawer("cart"); });

    const hdr = $("#site-header");
    if (hdr && hdr.classList.contains("header--float")) {
      const on = () => hdr.classList.toggle("is-stuck", scrollY > 60); on(); addEventListener("scroll", on, { passive: true });
    }

    document.addEventListener("click", (e) => {
      const t = e.target.closest("[data-open],[data-close],[data-overlay],[data-add],[data-wish],[data-qty],[data-remove],[data-trend]");
      if (!t) return;
      if (t.dataset.open) { e.preventDefault(); openDrawer(t.dataset.open); }
      else if ("close" in t.dataset || "overlay" in t.dataset) { closeAll(); }
      else if (t.dataset.add) { e.preventDefault(); addToCart(t.dataset.add); }
      else if (t.dataset.wish) { e.preventDefault(); toggleWish(t.dataset.wish); }
      else if (t.dataset.qty) { const l = cart[+t.dataset.qty]; if (l) setQty(l.key, Math.max(1, l.qty + +t.dataset.d)); }
      else if (t.dataset.remove) { const l = cart[+t.dataset.remove]; if (l) setQty(l.key, 0).then(() => toast("Item removed")); }
      else if (t.dataset.trend) { e.preventDefault(); const i = $("[data-search-input]"); i.value = t.dataset.trend; runSearch(i.value); }
    });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeAll(); });
    $("[data-search-input]")?.addEventListener("input", (e) => runSearch(e.target.value.trim()));
    $$("[data-newsletter]").forEach((frm) => frm.addEventListener("submit", (e) => {
      e.preventDefault();
      const email = (frm.querySelector("input[type=email]") || {}).value || "";
      post(L.newsletter, { email, source: frm.dataset.source || "footer" }).then((res) => {
        const j = res.data || {};
        if (!res.ok || j.ok === false) { toast(j.message || "Please enter a valid email."); return; }
        closeAll();
        const msg = frm.dataset.source === "popup" && D.newsletter ? D.newsletter.success : (j.message || "Thanks for subscribing!");
        toast(j.code ? `${msg} Your code: ${j.code}` : msg);
        frm.reset();
      });
    }));

    const N = D.newsletter;
    const popKey = "t3_popup_" + (N ? N.version : "");
    if (N && document.body.dataset.popup === "1" && !store.get(popKey, false)) {
      setTimeout(() => { openDrawer("newsletter"); store.set(popKey, true); }, N.delay || 6000);
    }
    $$("[data-services]").forEach((el) => { el.innerHTML = servicesHTML(); animateServices(el); });
    if (typeof window.T3Page === "function") window.T3Page();
    window.T3.refresh();
  });
})();
