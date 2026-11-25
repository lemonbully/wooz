/* Template 4 (owner-facing "Template 3", Eurus look) live data. The server
   prints the store's catalogue and content as JSON (#t3-data, built by
   storefront/template3.py, the feed Template 3 also reads) and this turns it
   into window.T3, the one object every section of the design reads.

   Images the owner has not uploaded yet arrive as "@key" tokens and resolve to
   the design's own botanical reference photography below, so no slot is ever
   empty. */
(function () {
  var el = document.getElementById("t3-data");
  var data = {};
  try { data = JSON.parse(el ? el.textContent : "{}") || {}; } catch (e) { data = {}; }

  var IMG = {
    stones: "1556228578-8c89e6adf883", tubesBW: "1608248543803-ba4f8c70ae0b", creamTube: "1620916566398-39f1143ab7be",
    flatlay1: "1612817288484-6f916006741a", flatlay2: "1598440947619-2c35fc9aa908", purpleTubes: "1571781926291-c477ebfd024b",
    dropperLeaf: "1617897903246-719242758050", handJar: "1601049541289-9b1b7bbbfe19", bottleTall: "1611930022073-b7a4ba5fcccd",
    pastelRow: "1629198688000-71f23e745b6e", brushes: "1522335789203-aabd1fc54bc9", cosmetics: "1512496015851-a90fb38ba796",
    faceMakeup: "1487412947147-5cebf100ffc2", handsSerum: "1515377905703-c4788e51af15", facial1: "1552693673-1bf958298935",
    facial2: "1570172619644-dfd03ed5d881", mask: "1616394584738-fc6e612e71b9", amberStand: "1608571423902-eed4a5ad8108",
    whiteRow: "1631729371254-42c2892f0e6e", handJar2: "1619451334792-150fd785ee74", roller: "1600428877878-1a0fd85beda8",
    pumpTowel: "1540555700478-4be289fbecef", massage: "1544161515-4ab6ce6db874", orangeFace: "1505944270255-72b8c68c6a70",
    face2: "1531746020798-e6953c6e8e04", face3: "1524504388940-b1c1722653e1", blueTube: "1556228720-195a672e8a03",
    pastelTubes: "1556229010-6c3f2c9ca5f8", palette: "1583241800698-e8ab01830a07", amberOrange: "1627384113743-6bd5a479fffd",
    pinkBottles: "1610705267928-1b9f2fa7f1c5", perfumeGold: "1588405748880-12d1d2a59f75", perfumeRose: "1592945403244-b3fbafd7f539",
    orangeTubes: "1615397349754-cfa2066a298e", soapStack: "1607006344380-b6775a0824a7", whiteBottle: "1576426863848-c21f53c60b19",
    amberBottle: "1532413992378-f169ac26fff0", handOil: "1556760544-74068565f05c", monstera: "1545165375-1b744b9ed444",
    p1: "1508214751196-bcfd4ca60f91", p2: "1488426862026-3ee34a7d66df", p3: "1517841905240-472988babdf9",
    p4: "1534528741775-53994a69daeb", p5: "1529626455594-4ff0802cfb7e",
    /* Template 3 page names kept so shared page code finds a photo too. */
    beach: "1544161515-4ab6ce6db874", pinkHair: "1531746020798-e6953c6e8e04"
  };
  /* "@token" → reference photo id, per slot kind. */
  var FALLBACK = {
    hero: ["orangeFace", "facial2", "massage"],
    cat: ["creamTube", "amberStand", "handJar", "mask", "pumpTowel", "palette", "perfumeRose", "roller", "bottleTall"],
    reel: ["handsSerum", "facial1", "face2", "mask", "faceMakeup", "handOil", "face3"],
    avatar: ["p1", "p2", "p3", "p4", "p5"],
    blog: ["flatlay1", "whiteBottle", "orangeTubes", "stones", "roller", "handJar2"]
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
  data.announce = data.announce || [];
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
