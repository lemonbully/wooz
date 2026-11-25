/* Product listing: filters, sort, grid density, pagination. Used by shop +
   category. Eurus layout (a row of dropdown filter pills above the grid) over
   Template 3's live logic: every filter reads the store's own products
   (availability, sale, category, colour and size values they carry), "Best
   selling" follows the owner's best-seller ranking and prices hide when the
   store hides them. */
window.T3Collection = function (opts) {
  const D = T3, I = T3.I, $ = T3.$, $$ = T3.$$;
  const base = opts.products || [];
  const root = $(opts.root);
  const esc = T3.esc;
  const prices = base.map((p) => p.price);
  const MIN = prices.length ? Math.floor(Math.min(...prices) / 100) * 100 : 0;
  const MAX = prices.length ? Math.max(MIN + 100, Math.ceil(Math.max(...prices) / 100) * 100) : 100;
  // Colour filter: every colour value the listed products carry, keyed by name.
  const COLORS = {};
  base.forEach((p) => (p.colorNames || []).forEach((n, i) => { if (!COLORS[n]) COLORS[n] = p.colors[i]; }));
  const pColors = (p) => p.colorNames || [];
  const q0 = T3.qs("sort");
  const state = { stock: false, cats: [], colors: [], sizes: [], min: MIN, max: MAX, sort: ({ new: "new", newest: "new", best: "best", price_asc: "low", price_desc: "high", low: "low", high: "high" })[q0] || (opts.sort || "best"), sale: T3.qs("sale") === "1", page: 1 };
  const count = (fn) => base.filter(fn).length;
  const sizesAll = [...new Set(base.flatMap((p) => p.sizes))];
  const catsAll = [...new Set(base.map((p) => p.cat).filter(Boolean))].map((s) => D.categories.find((c) => c.slug === s) || { slug: s, name: (base.find((p) => p.cat === s) || {}).catName || s });

  const group = (key, title, items) => `<div class="filter-group" data-group="${key}">
    <button class="filter-group__head" type="button" data-fold>${title}<span>${I.chev}</span></button>
    <div class="filter-group__panel"><ul>${items}</ul></div></div>`;

  root.innerHTML = `
    <div class="toolbar">
      <div class="toolbar__left"><button class="btn--outline-light" data-toggle-filters>${I.filter} <span data-ftxt>Hide filter</span></button><span class="muted small" data-count></span></div>
      <div class="toolbar__right">
        <label class="small hide-sm" style="white-space:nowrap">Sort by:</label>
        <select class="select" data-sort aria-label="Sort products">
          <option value="best">Best selling</option><option value="new">Newest</option>${D.showPrice ? `<option value="low">Price, low to high</option><option value="high">Price, high to low</option>` : ""}<option value="az">Alphabetically, A to Z</option><option value="rating">Top rated</option>
        </select>
        <div class="view-toggle hide-sm"><button data-cols="4" class="is-active" aria-label="4 columns">${I.grid2}</button><button data-cols="5" aria-label="5 columns">${I.grid3}</button></div>
      </div>
    </div>
    <div class="collection">
      <aside class="filters" data-filters>
        <div class="filters__head"></div>
        ${group("stock", "Availability", `<li><label class="check"><input type="checkbox" value="in">In stock</label><em>${count((p) => p.inStock)}</em></li>${D.showPrice ? `<li><label class="check"><input type="checkbox" value="sale" ${state.sale ? "checked" : ""}>On sale</label><em>${count((p) => p.mrp)}</em></li>` : ""}`)}
        ${opts.showCats === false || !catsAll.length ? "" : group("cats", "Category", catsAll.map((c) => `<li><label class="check"><input type="checkbox" value="${esc(c.slug)}">${esc(c.name)}</label><em>${count((p) => p.cat === c.slug)}</em></li>`).join(""))}
        ${D.showPrice && base.length ? `<div class="filter-group" data-group="price"><button class="filter-group__head" type="button" data-fold>Price<span>${I.chev}</span></button><div class="filter-group__panel" style="min-width:300px">
          <p class="small muted" style="margin-bottom:6px">The highest price is ${T3.inr(MAX)}</p>
          <div class="range"><div class="range__rail"></div><div class="range__fill" data-fill></div>
            <input type="range" min="${MIN}" max="${MAX}" step="100" value="${MIN}" data-rmin aria-label="Minimum price"><input type="range" min="${MIN}" max="${MAX}" step="100" value="${MAX}" data-rmax aria-label="Maximum price"></div>
          <div class="price-inputs"><input class="input" data-pmin value="${MIN}" aria-label="From"><input class="input" data-pmax value="${MAX}" aria-label="To"></div>
        </div></div>` : ""}
        ${Object.keys(COLORS).length ? group("colors", "Colour", Object.entries(COLORS).map(([n, c]) => `<li><label class="check"><input type="checkbox" value="${esc(n)}"><span class="color-dot" style="background:${c}"></span>${esc(n)}</label><em>${count((p) => pColors(p).includes(n))}</em></li>`).join("")) : ""}
        ${sizesAll.length ? group("sizes", "Size", `<li style="display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-start" class="opt-sizes">${sizesAll.slice(0, 14).map((s) => `<button type="button" data-size="${esc(s)}">${esc(s)}</button>`).join("")}</li>`) : ""}
        <button class="btn btn--black btn--block" data-apply style="margin-top:6px">Show results</button>
      </aside>
      <div>
        <div class="active-filters" data-chips></div>
        <div class="grid grid--4" data-grid></div>
        <div class="pagination" data-pages></div>
      </div>
    </div>`;

  const grid = $("[data-grid]", root);
  const fill = () => { const f = $("[data-fill]", root); if (!f) return; f.style.left = ((state.min - MIN) / (MAX - MIN)) * 100 + "%"; f.style.right = (100 - ((state.max - MIN) / (MAX - MIN)) * 100) + "%"; };
  const setRange = (a, b) => { [["[data-rmin]", a], ["[data-rmax]", b], ["[data-pmin]", a], ["[data-pmax]", b]].forEach(([s, v]) => { const el = $(s, root); if (el) el.value = v; }); };
  /* "Best selling": the owner's best-seller ranking first (rank 1 on top),
     then everything else by review count. */
  const bestRank = {};
  ((window.T3 && T3.bestOrder) || []).forEach((id, i) => { bestRank[id] = i; });
  const rankOf = (p) => (p.id in bestRank ? bestRank[p.id] : Infinity);
  const sorters = { best: (a, b) => (rankOf(a) - rankOf(b)) || (b.reviews - a.reviews), new: (a, b) => (b.created || 0) - (a.created || 0), low: (a, b) => a.price - b.price, high: (a, b) => b.price - a.price, az: (a, b) => a.name.localeCompare(b.name), rating: (a, b) => b.rating - a.rating };
  const PER = 12;

  function render() {
    let list = base.filter((p) =>
      (!state.stock || p.inStock) && (!state.sale || p.mrp) && (!state.cats.length || state.cats.includes(p.cat)) && (!state.colors.length || pColors(p).some((c) => state.colors.includes(c))) &&
      (!state.sizes.length || p.sizes.some((s) => state.sizes.includes(s))) && (!D.showPrice || (p.price >= state.min && p.price <= state.max)));
    list = [...list].sort(sorters[state.sort] || sorters.best);
    $("[data-count]", root).textContent = `${list.length} products`;
    const pages = Math.max(1, Math.ceil(list.length / PER)); state.page = Math.min(state.page, pages);
    const slice = list.slice((state.page - 1) * PER, state.page * PER);
    const promo = opts.promo && state.page === 1 && slice.length > 5 ? opts.promo : "";
    grid.innerHTML = slice.map((p, i) => (i === 5 && promo ? promo : "") + T3.card(p)).join("") ||
      `<div class="empty-state" style="grid-column:1/-1"><h3 class="h4">No products match these filters</h3><p class="muted small" style="margin:6px 0 16px">Try removing a filter or widening the price range.</p><button class="btn btn--black" data-clear>Clear all filters</button></div>`;
    $("[data-pages]", root).innerHTML = pages > 1 ? Array.from({ length: pages }, (_, i) => `<a href="#" data-page="${i + 1}" class="${i + 1 === state.page ? "is-active" : ""}">${i + 1}</a>`).join("") + `<a href="#" data-page="${Math.min(pages, state.page + 1)}" aria-label="Next page">${I.right}</a>` : "";
    const chips = [];
    if (state.stock) chips.push(["stock", "", "In stock"]);
    if (state.sale) chips.push(["sale", "", "On sale"]);
    state.cats.forEach((c) => chips.push(["cats", c, (catsAll.find((x) => x.slug === c) || { name: c }).name]));
    state.colors.forEach((c) => chips.push(["colors", c, c]));
    state.sizes.forEach((s) => chips.push(["sizes", s, "Size " + s]));
    if (state.min > MIN || state.max < MAX) chips.push(["price", "", `${T3.inr(state.min)} to ${T3.inr(state.max)}`]);
    $("[data-chips]", root).innerHTML = chips.map(([k, v, t]) => `<span class="chip">${esc(t)}<button data-chip="${k}" data-v="${esc(v)}" aria-label="Remove">${I.close}</button></span>`).join("") + (chips.length ? `<button class="link small" data-clear>Clear all</button>` : "");
    T3.initReveal(grid);
  }

  root.addEventListener("change", (e) => {
    const t = e.target, g = t.closest("[data-group]")?.dataset.group;
    if (t.matches("[data-sort]")) state.sort = t.value;
    else if (g === "stock" && t.value === "sale") state.sale = t.checked;
    else if (g === "stock" && t.value === "in") state.stock = t.checked;
    else if (t.matches("[data-pmin],[data-pmax]")) { state.min = +$("[data-pmin]", root).value || MIN; state.max = +$("[data-pmax]", root).value || MAX; $("[data-rmin]", root).value = state.min; $("[data-rmax]", root).value = state.max; fill(); }
    else if (g && g !== "stock" && g !== "price") state[g] = $$(`[data-group="${g}"] input:checked`, root).map((i) => i.value);
    state.page = 1; render();
  });
  root.addEventListener("input", (e) => {
    if (!e.target.matches("[data-rmin],[data-rmax]")) return;
    let a = +$("[data-rmin]", root).value, b = +$("[data-rmax]", root).value; if (a > b - 100) { if (e.target.matches("[data-rmin]")) a = b - 100; else b = a + 100; }
    state.min = a; state.max = b; setRange(a, b); fill(); render();
  });
  root.addEventListener("click", (e) => {
    const t = e.target.closest("button,a"); if (!t) return;
    if (t.dataset.cols) { $$("[data-cols]", root).forEach((b) => b.classList.toggle("is-active", b === t)); grid.className = `grid grid--${t.dataset.cols}`; }
    else if ("toggleFilters" in t.dataset) { const h = $(".collection", root).classList.toggle("no-filters"); $("[data-ftxt]", root).textContent = h ? "Show filter" : "Hide filter"; }
    else if ("closeFilters" in t.dataset || "apply" in t.dataset) { if (innerWidth <= 1024) T3.closeAll(); }
    else if (t.dataset.size) { t.classList.toggle("is-active"); state.sizes = $$("[data-size].is-active", root).map((b) => b.dataset.size); state.page = 1; render(); }
    else if (t.dataset.page) { e.preventDefault(); state.page = +t.dataset.page; render(); root.scrollIntoView({ behavior: "smooth" }); }
    else if (t.dataset.chip) {
      const k = t.dataset.chip, v = t.dataset.v;
      if (k === "sale") { state.sale = false; const i = $('[data-group="stock"] input[value="sale"]', root); if (i) i.checked = false; }
      else if (k === "stock") { state.stock = false; const i = $('[data-group="stock"] input[value="in"]', root); if (i) i.checked = false; }
      else if (k === "price") { state.min = MIN; state.max = MAX; setRange(MIN, MAX); fill(); }
      else if (k === "sizes") { state.sizes = state.sizes.filter((x) => x !== v); $$("[data-size]", root).filter((b) => b.dataset.size === v).forEach((b) => b.classList.remove("is-active")); }
      else { state[k] = state[k].filter((x) => x !== v); const i = $$(`[data-group="${k}"] input`, root).find((x) => x.value === v); if (i) i.checked = false; }
      render();
    } else if ("clear" in t.dataset) {
      Object.assign(state, { cats: [], colors: [], sizes: [], sale: false, stock: false, min: MIN, max: MAX, page: 1 });
      $$("[data-filters] input[type=checkbox]", root).forEach((i) => (i.checked = false)); $$("[data-size]", root).forEach((b) => b.classList.remove("is-active"));
      setRange(MIN, MAX); fill(); render();
    } else if ("fold" in t.dataset) { const g = t.closest(".filter-group"), was = g.classList.contains("is-open"); $$(".filter-group.is-open", root).forEach((x) => x.classList.remove("is-open")); if (!was) g.classList.add("is-open"); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".filter-group")) $$(".filter-group.is-open", root).forEach((x) => x.classList.remove("is-open")); });
  $("[data-sort]", root).value = state.sort in sorters && (D.showPrice || !["low", "high"].includes(state.sort)) ? state.sort : "best";
  if (!(D.showPrice || !["low", "high"].includes(state.sort))) state.sort = "best";
  fill(); render();
};
