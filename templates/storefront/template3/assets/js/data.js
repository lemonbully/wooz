/* Demo content for the Template 3 preview. Every image is a stock photo. */
(function () {
  const U = (id, w = 900, h) =>
    `https://images.unsplash.com/photo-${id}?auto=format&fit=crop&w=${w}${h ? `&h=${h}` : ""}&q=75`;

  const IMG = {
    greenShirts: "1523381210434-271e8be1f52b", store: "1441986300917-64674bd600d8",
    rail: "1490481651871-ab68de25d43d", yellowSuit: "1515886657613-9f3515b0c78f",
    peaceTee: "1503342217505-b0a15ec3261c", whiteTee: "1521572163474-6864f9cf17ab",
    graphicTee: "1576566588028-4147f3842f27", redRunner: "1542291026-7eec264c27ff",
    brownSneaker: "1549298916-b41d501d3772", colorSneaker: "1560769629-975ec94e6a86",
    greyRunner: "1491553895911-0055eca6402d", navyPack: "1553062407-98eeb64c6a62",
    quiltBag: "1548036328-c9fa89d128fa", redBag: "1584917865442-de89df76afd3",
    whiteCap: "1588850561407-ed78c282e89b", hoodieBack: "1556821840-3a63f95609a7",
    whiteSweat: "1620799140408-edc6dcb6d633", pinkJogger: "1594633312681-425c7b97ccd1",
    leather: "1551028719-00167b16eac5", bomber: "1591047139829-d91aecb6caea",
    jeansFold: "1542272604-787c3835535d", jeansLegs: "1541099649105-f69ad21f3246",
    denimShirt: "1596755094514-f87e34085b2c", shirtsFold: "1602810318383-e386cc2a3ccf",
    knit: "1434389677669-e08b4cac3105", blackKnits: "1576871337622-98d48d1cf531",
    whiteDress: "1515372039744-b8f02a3ae446", redDress: "1595777457583-95e059d581b8",
    redDress2: "1572804013309-59a88b7e92f1", smartWatch: "1523275335684-37898b6baf30",
    wristWatch: "1524592094714-0f0654e20314", roundShades: "1511499767150-a48a237f0083",
    blackShades: "1572635196237-14b3f281503f", shopping: "1483985988355-763728e1935b",
    yellowGirl: "1469334031218-e382a71b716b", pinkSuit: "1487222477894-8943e31ef7b2",
    tealGirl: "1509631179647-0177331693ae", redTop: "1529139574466-a303027c1d8b",
    floral: "1496747611176-843222e1e57c", suit: "1507679799987-c73779587ccf",
    suitWalk: "1617137968427-85924c800a22", pinkHair: "1500917293891-ef795e70e1f6",
    p1: "1494790108377-be9c29b29330", p2: "1507003211169-0a1dd7228f2d",
    p3: "1438761681033-6461ffad8d80", p4: "1500648767791-00dcc994a43e",
    p5: "1544005313-94ddf0286df2", flatlay: "1556905055-8f358a7a47b2",
    store2: "1445205170230-053b83016050", teeRack: "1489987707025-afc232f7ea0f",
    store3: "1567401893414-76b7b1e5a7a5", heel: "1543163521-1bf539c55dd2",
    vans: "1525966222134-fcfa99b8ae77", neon: "1606107557195-0e29a4b5b4aa",
    whiteKicks: "1608231387042-66d1773070a5", denimJacket: "1611312449408-fcece27cdbb7",
    beach: "1475180098004-ca77a66827be", rack2: "1512436991641-6745cdb1723f",
    boxes: "1490114538077-0a7f8cb49891", outfitFlat: "1516762689617-e1cffcef479d",
    streetDenim: "1539109136881-3be0616acf4b", leatherMan: "1520975954732-35dd22299614",
    friends: "1503944583220-79d8926ad5e2", kidLook: "1519238263530-99bdd11df2ea",
    teeMan: "1622445275463-afa2ab738c34", blackTee: "1618354691373-d851c5c3a990",
    blackTee2: "1583743814966-8936f5b7be1a", blueTee: "1564584217132-2271feaeb3c5",
    shoesFlat: "1525507119028-ed4c629a60a3", feetKicks: "1460353581641-37baddab0fa2",
    teeStack: "1562157873-818bc0726f68", darkTee: "1581655353564-df123a1eb820",
    beardTee: "1586790170083-2f9ceadc732d", greyBag: "1559563458-527698bf5295",
    rings: "1617038220319-276d3cfab638",
  };

  const categories = [
    { slug: "t-shirts", name: "T-Shirts", count: 42, img: IMG.teeStack, desc: "Heavyweight cotton for everyday wear", icon: "👕" },
    { slug: "shirts", name: "Shirts", count: 28, img: IMG.denimShirt, desc: "Crisp weaves, relaxed collars", icon: "👔" },
    { slug: "denim", name: "Denim", count: 24, img: IMG.jeansFold, desc: "Selvedge and stretch denim that ages well", icon: "👖" },
    { slug: "outerwear", name: "Outerwear", count: 19, img: IMG.bomber, desc: "Layers for wind, rain and late nights", icon: "🧥" },
    { slug: "knitwear", name: "Knitwear", count: 21, img: IMG.knit, desc: "Soft knits in breathable yarns", icon: "🧶" },
    { slug: "dresses", name: "Dresses", count: 33, img: IMG.whiteDress, desc: "Easy silhouettes for every plan", icon: "👗" },
    { slug: "sneakers", name: "Sneakers", count: 26, img: IMG.whiteKicks, desc: "Cushioned soles, clean lines", icon: "👟" },
    { slug: "bags", name: "Bags", count: 17, img: IMG.redBag, desc: "Carry-alls, crossbodies and totes", icon: "👜" },
    { slug: "accessories", name: "Accessories", count: 38, img: IMG.roundShades, desc: "Watches, shades and caps to finish", icon: "🕶️" },
  ];

  const C = { black: "#111", white: "#f4f4f2", cream: "#ece3d2", sage: "#9aab8c", navy: "#1f2b4a", rust: "#b5562f", red: "#c4301c", pink: "#f3b7c2", blue: "#7da2c9", grey: "#9a9a9a", olive: "#6d7246", tan: "#c49a6c", brown: "#6b4a33", denim: "#46607e", yellow: "#e9c53a", green: "#2f7a4f" };

  const p = (o) => Object.assign({ rating: 4.7, reviews: 120, sizes: ["XS", "S", "M", "L", "XL"], out: [], colors: [], badges: [] }, o);
  const products = [
    p({ id: "essential-heavy-tee", name: "Essential Heavy Tee", cat: "t-shirts", price: 1299, mrp: 1799, img: [IMG.whiteTee, IMG.teeMan, IMG.darkTee, IMG.teeStack], colors: [C.white, C.black, C.sage], badges: ["new", "popular"], rating: 4.8, reviews: 412 }),
    p({ id: "retro-graphic-tee", name: "Retro Graphic Tee", cat: "t-shirts", price: 1499, img: [IMG.graphicTee, IMG.peaceTee], colors: [C.cream, C.black], badges: ["hot"], reviews: 238 }),
    p({ id: "relaxed-denim-shirt", name: "Relaxed Denim Shirt", cat: "shirts", price: 2499, mrp: 3299, img: [IMG.denimShirt, IMG.shirtsFold], colors: [C.denim, C.blue], badges: ["sale"], out: ["XS"] }),
    p({ id: "straight-fit-jeans", name: "Straight Fit Jeans", cat: "denim", price: 2899, img: [IMG.jeansFold, IMG.jeansLegs], colors: [C.denim, C.navy], sizes: ["28", "30", "32", "34", "36"], badges: ["popular"], rating: 4.9, reviews: 356 }),
    p({ id: "rust-bomber-jacket", name: "Rust Bomber Jacket", cat: "outerwear", price: 4999, mrp: 6499, img: [IMG.bomber, IMG.leatherMan], colors: [C.rust, C.olive], badges: ["sale", "hot"], reviews: 98 }),
    p({ id: "cloud-knit-poncho", name: "Cloud Knit Poncho", cat: "knitwear", price: 2199, img: [IMG.knit, IMG.blackKnits], colors: [C.cream, C.black], sizes: ["Free size"], badges: ["new"] }),
    p({ id: "street-runner-red", name: "Street Runner Red", cat: "sneakers", price: 5499, img: [IMG.redRunner, IMG.shoesFlat], colors: [C.red, C.black], sizes: ["UK 6", "UK 7", "UK 8", "UK 9", "UK 10"], out: ["UK 10"], badges: ["hot"], rating: 4.8, reviews: 211 }),
    p({ id: "quilted-mini-bag", name: "Quilted Mini Bag", cat: "bags", price: 3299, mrp: 3999, img: [IMG.quiltBag, IMG.greyBag], colors: [C.black, C.grey], sizes: ["One size"], badges: ["sale"] }),
    p({ id: "linen-midi-dress", name: "Linen Midi Dress", cat: "dresses", price: 3499, img: [IMG.whiteDress, IMG.beach], colors: [C.white, C.sage], badges: ["new"], rating: 4.9, reviews: 167 }),
    p({ id: "cloud-pullover-hoodie", name: "Cloud Pullover Hoodie", cat: "knitwear", price: 2799, img: [IMG.hoodieBack, IMG.whiteSweat], colors: [C.grey, C.white, C.navy], badges: ["popular"], reviews: 302 }),
    p({ id: "tan-court-sneaker", name: "Tan Court Sneaker", cat: "sneakers", price: 4299, img: [IMG.brownSneaker, IMG.feetKicks], colors: [C.tan, C.white], sizes: ["UK 6", "UK 7", "UK 8", "UK 9", "UK 10"] }),
    p({ id: "round-metal-shades", name: "Round Metal Shades", cat: "accessories", price: 1899, mrp: 2499, img: [IMG.roundShades, IMG.yellowGirl], colors: [C.yellow, C.black], sizes: ["One size"], badges: ["sale"] }),
    p({ id: "scarlet-wrap-dress", name: "Scarlet Wrap Dress", cat: "dresses", price: 3899, img: [IMG.redDress, IMG.redDress2], colors: [C.red], badges: ["hot"], rating: 4.9, reviews: 189 }),
    p({ id: "biker-leather-jacket", name: "Biker Leather Jacket", cat: "outerwear", price: 8999, img: [IMG.leather, IMG.leatherMan], colors: [C.black, C.brown], badges: ["popular"], reviews: 76 }),
    p({ id: "everyday-sweatshirt", name: "Everyday Sweatshirt", cat: "knitwear", price: 1999, img: [IMG.whiteSweat, IMG.hoodieBack], colors: [C.white, C.grey] }),
    p({ id: "lounge-jogger-blush", name: "Lounge Jogger Blush", cat: "denim", price: 1799, mrp: 2299, img: [IMG.pinkJogger, IMG.outfitFlat], colors: [C.pink, C.grey], badges: ["sale"] }),
    p({ id: "classic-cap-white", name: "Classic Cap White", cat: "accessories", price: 899, img: [IMG.whiteCap, IMG.flatlay], colors: [C.white, C.black, C.navy], sizes: ["One size"], badges: ["new"] }),
    p({ id: "commuter-backpack", name: "Commuter Backpack", cat: "bags", price: 3799, img: [IMG.navyPack, IMG.boxes], colors: [C.navy, C.black], sizes: ["22 L"], rating: 4.8, reviews: 143 }),
    p({ id: "minimal-leather-watch", name: "Minimal Leather Watch", cat: "accessories", price: 6499, mrp: 7999, img: [IMG.wristWatch, IMG.smartWatch], colors: [C.tan, C.black], sizes: ["One size"], badges: ["sale", "popular"] }),
    p({ id: "trucker-denim-jacket", name: "Trucker Denim Jacket", cat: "outerwear", price: 4499, img: [IMG.denimJacket, IMG.streetDenim], colors: [C.denim], badges: ["new"] }),
    p({ id: "oxford-shirt-pack", name: "Oxford Shirt Pack", cat: "shirts", price: 3999, img: [IMG.shirtsFold, IMG.denimShirt], colors: [C.white, C.blue, C.navy] }),
    p({ id: "midnight-pocket-tee", name: "Midnight Pocket Tee", cat: "t-shirts", price: 1199, img: [IMG.blackTee, IMG.blackTee2], colors: [C.black, C.navy], badges: ["popular"] }),
    p({ id: "sky-cotton-tee", name: "Sky Cotton Tee", cat: "t-shirts", price: 1099, img: [IMG.blueTee, IMG.teeRack], colors: [C.blue, C.white] }),
    p({ id: "scarlet-top-handle", name: "Scarlet Top Handle", cat: "bags", price: 4599, img: [IMG.redBag, IMG.quiltBag], colors: [C.red, C.black], sizes: ["One size"], badges: ["hot"] }),
    p({ id: "volt-trainer", name: "Volt Trainer", cat: "sneakers", price: 5999, img: [IMG.neon, IMG.colorSneaker], colors: [C.yellow, C.white], sizes: ["UK 6", "UK 7", "UK 8", "UK 9", "UK 10"], badges: ["new"] }),
    p({ id: "noir-wayfarer", name: "Noir Wayfarer", cat: "accessories", price: 1699, img: [IMG.blackShades, IMG.roundShades], colors: [C.black], sizes: ["One size"] }),
    p({ id: "garden-floral-dress", name: "Garden Floral Dress", cat: "dresses", price: 2999, mrp: 3799, img: [IMG.floral, IMG.whiteDress], colors: [C.pink, C.white], badges: ["sale"] }),
    p({ id: "maroon-skate-low", name: "Maroon Skate Low", cat: "sneakers", price: 3499, img: [IMG.vans, IMG.shoesFlat], colors: [C.red, C.black], sizes: ["UK 6", "UK 7", "UK 8", "UK 9"], out: ["UK 6"] }),
  ];
  products.forEach((x) => (x.img = x.img.map((id) => U(id, 900, 1200))));
  products.forEach((x) => (x.catName = categories.find((c) => c.slug === x.cat).name));

  const heroSlides = [
    { eyebrow: "New campaign", title: "Softness in Comfort", cta: "Shop Collection", img: U(IMG.yellowSuit, 2000), bg: "#d9b27a", pos: "12%" },
    { eyebrow: "Summer 26 edit", title: "Made to Move Freely", cta: "Explore New In", img: U(IMG.tealGirl, 2000), bg: "#3fa59a", pos: "20%" },
    { eyebrow: "Little moments", title: "Wrapped in Warmth", cta: "Shop Knitwear", img: U(IMG.pinkHair, 2000), bg: "#e7b9b2", pos: "25%" },
  ];

  const reels = [
    { img: U(IMG.yellowGirl, 700, 1100), views: "24.8K", product: "round-metal-shades" },
    { img: U(IMG.streetDenim, 700, 1100), views: "18.2K", product: "trucker-denim-jacket" },
    { img: U(IMG.redTop, 700, 1100), views: "41.0K", product: "scarlet-wrap-dress" },
    { img: U(IMG.leatherMan, 700, 1100), views: "12.6K", product: "biker-leather-jacket" },
    { img: U(IMG.beach, 700, 1100), views: "33.4K", product: "linen-midi-dress" },
    { img: U(IMG.pinkSuit, 700, 1100), views: "9.7K", product: "noir-wayfarer" },
    { img: U(IMG.beardTee, 700, 1100), views: "15.1K", product: "essential-heavy-tee" },
  ];

  const reviews = [
    { title: "Amazing look and quality!", text: "The tee is heavy, soft and keeps its shape after a dozen washes. Fits exactly like the size chart says.", name: "Riya S.", city: "Mumbai", avatar: U(IMG.p1, 120, 120), product: "essential-heavy-tee" },
    { title: "My new favourite jacket", text: "The bomber feels premium and the rust colour is even better in person. Got so many compliments.", name: "Arjun M.", city: "Bengaluru", avatar: U(IMG.p2, 120, 120), product: "rust-bomber-jacket" },
    { title: "Delivered in two days", text: "Packaging was beautiful and the dress fits perfectly. The linen is breathable and very soft.", name: "Neha K.", city: "Delhi", avatar: U(IMG.p3, 120, 120), product: "linen-midi-dress" },
    { title: "Worth every rupee", text: "Comfortable straight out of the box. I walk 10k steps a day in these and my feet thank me.", name: "Kabir R.", city: "Pune", avatar: U(IMG.p4, 120, 120), product: "street-runner-red" },
    { title: "Easy exchange too", text: "Ordered the wrong size, the exchange was picked up the next day. Customer care was lovely.", name: "Ananya P.", city: "Hyderabad", avatar: U(IMG.p5, 120, 120), product: "quilted-mini-bag" },
    { title: "Jeans that actually fit", text: "Finally a straight fit that sits right at the waist. The denim is sturdy but not stiff.", name: "Vikram T.", city: "Chennai", avatar: U(IMG.p2, 120, 120), product: "straight-fit-jeans" },
  ];

  const blogs = [
    { slug: "capsule-wardrobe", title: "How to build a 12 piece capsule wardrobe", tag: "Style guide", date: "Nov 08, 2026", read: "6 min read", img: U(IMG.rail, 1200, 900), excerpt: "Twelve pieces, endless outfits. Here is the exact mix of basics and statement layers we swear by." },
    { slug: "denim-care", title: "Denim care: wash less, wear longer", tag: "Care", date: "Oct 29, 2026", read: "4 min read", img: U(IMG.jeansLegs, 1200, 900), excerpt: "Raw or washed, your jeans last years longer with three simple habits. No freezer required." },
    { slug: "sneaker-rotation", title: "The three sneaker rotation", tag: "Footwear", date: "Oct 17, 2026", read: "5 min read", img: U(IMG.shoesFlat, 1200, 900), excerpt: "A clean white, a runner and something loud. Why three pairs cover every plan you make." },
    { slug: "behind-the-seams", title: "Behind the seams at our Tiruppur studio", tag: "Our story", date: "Sep 30, 2026", read: "8 min read", img: U(IMG.store2, 1200, 900), excerpt: "Meet the people who cut, stitch and check every piece before it reaches you." },
    { slug: "layering-101", title: "Layering 101 for Indian winters", tag: "Style guide", date: "Sep 12, 2026", read: "5 min read", img: U(IMG.leatherMan, 1200, 900), excerpt: "Light layers that work from a cold morning to a warm afternoon commute." },
    { slug: "colour-of-the-season", title: "Rust is the colour of the season", tag: "Trends", date: "Aug 28, 2026", read: "3 min read", img: U(IMG.bomber, 1200, 900), excerpt: "Warm, earthy and easy to pair. How to wear rust without overthinking it." },
  ];

  const faqs = [
    { q: "How long does delivery take?", a: "Metro cities receive orders in 2 to 4 working days and the rest of India in 4 to 7 working days. You will get a tracking link on email and WhatsApp as soon as your order ships." },
    { q: "Is Cash on Delivery available?", a: "Yes. COD is available on orders up to ₹10,000 across 19,000+ pin codes. You can also pay part online and the rest in cash at delivery." },
    { q: "What is your return and exchange policy?", a: "Returns and size exchanges are free within 15 days of delivery. Items must be unworn with original tags. Raise a request from your account and we will arrange a pickup." },
    { q: "How do I find my size?", a: "Every product page has a size guide with garment measurements. If you are between sizes, our fits run true, so pick your usual size for a regular fit or size up for a relaxed look." },
    { q: "Are your fabrics sustainable?", a: "Over 80% of our range uses organic cotton, recycled polyester or linen. Every piece is made in our partner studios in Tiruppur and Jaipur." },
    { q: "Can I change or cancel my order?", a: "You can edit or cancel an order from your account until it is packed, usually within 6 hours of placing it." },
  ];

  const why = [
    { title: "Organic cotton", text: "Soft, breathable fabrics grown without harsh chemicals.", icon: "leaf" },
    { title: "Free 2-day shipping", text: "On every order above ₹999 across metro cities.", icon: "truck" },
    { title: "15 day easy returns", text: "Doorstep pickup and instant refunds to source.", icon: "return" },
    { title: "Made in India", text: "Cut and stitched by artisans in Tiruppur and Jaipur.", icon: "heart" },
  ];

  window.T3 = { U, IMG, categories, products, heroSlides, reels, reviews, blogs, faqs, why, brand: "Mellow", freeShip: 2999 };
})();
