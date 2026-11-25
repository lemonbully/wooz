/* Template 3 live data. Replaces the demo data.js on a real store: the server
   prints the store's catalogue and content as JSON (#t3-data, built by
   storefront/template3.py) and this turns it into window.T3, the one object
   every section of the design reads.

   Images the owner has not uploaded yet arrive as "@key" tokens and resolve to
   the design's own reference photography below, so no slot is ever empty. */
(function () {
  var el = document.getElementById("t3-data");
  var data = {};
  try { data = JSON.parse(el ? el.textContent : "{}") || {}; } catch (e) { data = {}; }

  var IMG = {
    greenShirts: "1523381210434-271e8be1f52b", store: "1441986300917-64674bd600d8",
    rail: "1490481651871-ab68de25d43d", yellowSuit: "1515886657613-9f3515b0c78f",
    whiteTee: "1521572163474-6864f9cf17ab", bomber: "1591047139829-d91aecb6caea",
    jeansFold: "1542272604-787c3835535d", jeansLegs: "1541099649105-f69ad21f3246",
    denimShirt: "1596755094514-f87e34085b2c", knit: "1434389677669-e08b4cac3105",
    whiteDress: "1515372039744-b8f02a3ae446", redDress2: "1572804013309-59a88b7e92f1",
    roundShades: "1511499767150-a48a237f0083", yellowGirl: "1469334031218-e382a71b716b",
    pinkSuit: "1487222477894-8943e31ef7b2", tealGirl: "1509631179647-0177331693ae",
    redTop: "1529139574466-a303027c1d8b", pinkHair: "1500917293891-ef795e70e1f6",
    p1: "1494790108377-be9c29b29330", p2: "1507003211169-0a1dd7228f2d",
    p3: "1438761681033-6461ffad8d80", p4: "1500648767791-00dcc994a43e",
    p5: "1544005313-94ddf0286df2", flatlay: "1556905055-8f358a7a47b2",
    store2: "1445205170230-053b83016050", teeRack: "1489987707025-afc232f7ea0f",
    store3: "1567401893414-76b7b1e5a7a5", whiteKicks: "1608231387042-66d1773070a5",
    beach: "1475180098004-ca77a66827be", rack2: "1512436991641-6745cdb1723f",
    outfitFlat: "1516762689617-e1cffcef479d", streetDenim: "1539109136881-3be0616acf4b",
    leatherMan: "1520975954732-35dd22299614", teeStack: "1562157873-818bc0726f68",
    beardTee: "1586790170083-2f9ceadc732d", redBag: "1584917865442-de89df76afd3",
    shoesFlat: "1525507119028-ed4c629a60a3"
  };
  /* "@token" → reference photo id, per slot kind. */
  var FALLBACK = {
    hero: ["yellowSuit", "tealGirl", "pinkHair"],
    cat: ["teeStack", "denimShirt", "jeansFold", "bomber", "knit", "whiteDress", "whiteKicks", "redBag", "roundShades"],
    reel: ["yellowGirl", "streetDenim", "redTop", "leatherMan", "beach", "pinkSuit", "beardTee"],
    avatar: ["p1", "p2", "p3", "p4", "p5"],
    blog: ["rail", "jeansLegs", "shoesFlat", "store2", "leatherMan", "bomber"]
  };

  /* U(src, w, h): a real URL passes through; an IMG id or "@token" becomes the
     sized reference photo. */
  function U(src, w, h) {
    w = w || 900;
    if (!src) return "";
    var s = String(src);
    var m = /^@([a-z]+)(\d+)$/.exec(s);
    if (m) s = IMG[(FALLBACK[m[1]] || FALLBACK.cat)[+m[2] % (FALLBACK[m[1]] || FALLBACK.cat).length]];
    else if (IMG[s]) s = IMG[s];
    if (/^(https?:)?\/\//.test(s) || s.charAt(0) === "/" || s.indexOf("data:") === 0) return s;
    return "https://images.unsplash.com/photo-" + s + "?auto=format&fit=crop&w=" + w + (h ? "&h=" + h : "") + "&q=75";
  }

  data.U = U;
  data.IMG = IMG;
  data.urls = data.urls || {};
  data.categories = data.categories || [];
  data.products = data.products || [];
  data.heroSlides = data.heroSlides || [];
  data.reels = data.reels || [];
  data.reviews = data.reviews || [];
  data.blogs = data.blogs || [];
  data.faqs = data.faqs || [];
  data.why = data.why || [];
  data.page = data.page || {};
  data.home = data.home || {};
  data.contact = data.contact || {};
  data.socials = data.socials || {};
  data.brand = data.brand || "Store";
  data.freeShip = +data.freeShip || 0;
  /* Product images are real URLs (or the platform's demo photo); resolve any
     token so every consumer can use p.img[i] directly. */
  function fixProduct(p) { p.img = (p.img || []).map(function (s) { return U(s, 900, 1200) || U("@cat0", 900, 1200); }); return p; }
  data.products.forEach(fixProduct);
  if (data.page.products) data.page.products.forEach(fixProduct);
  if (data.page.wishlist) data.page.wishlist.forEach(fixProduct);
  if (data.page.product) { fixProduct(data.page.product); (data.page.product.related || []).forEach(fixProduct); }
  window.T3 = data;
})();
