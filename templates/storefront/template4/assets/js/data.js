/* Demo content for the Template 4 preview (skincare). Every image is a stock photo. */
(function () {
  const U = (id, w = 900, h) =>
    `https://images.unsplash.com/photo-${id}?auto=format&fit=crop&w=${w}${h ? `&h=${h}` : ""}&q=75`;

  const IMG = {
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
    pinkBottles: "1610705267928-1b9f2fa7f1c5", lipstick: "1586495777744-4413f21062fa", perfumeGold: "1588405748880-12d1d2a59f75",
    perfumeRose: "1592945403244-b3fbafd7f539", orangeTubes: "1615397349754-cfa2066a298e", soapStack: "1607006344380-b6775a0824a7",
    soaps: "1600857544200-b2f666a9a2ec", whiteBottle: "1576426863848-c21f53c60b19", amberBottle: "1532413992378-f169ac26fff0",
    powders: "1515688594390-b649af70d282", footSpa: "1519415510236-718bdfcd89c8", handOil: "1556760544-74068565f05c",
    monstera: "1545165375-1b744b9ed444",
    p1: "1508214751196-bcfd4ca60f91", p2: "1488426862026-3ee34a7d66df", p3: "1517841905240-472988babdf9",
    p4: "1534528741775-53994a69daeb", p5: "1529626455594-4ff0802cfb7e",
  };

  const categories = [
    { slug: "cleansers", name: "Cleansers", count: 18, img: IMG.creamTube, desc: "Gentle gels and balms that never strip", icon: "🫧" },
    { slug: "serums", name: "Serums", count: 24, img: IMG.amberStand, desc: "Concentrated actives for visible results", icon: "💧" },
    { slug: "moisturisers", name: "Moisturisers", count: 21, img: IMG.handJar, desc: "Barrier-loving creams for every skin type", icon: "🌿" },
    { slug: "masks", name: "Face Masks", count: 12, img: IMG.mask, desc: "Ten minute treatments, weekly rituals", icon: "🍃" },
    { slug: "body", name: "Body Care", count: 16, img: IMG.pumpTowel, desc: "Washes, oils and butters for head to toe", icon: "🛁" },
    { slug: "makeup", name: "Makeup", count: 29, img: IMG.palette, desc: "Skin-first colour that lets you shine through", icon: "💄" },
    { slug: "fragrance", name: "Fragrance", count: 9, img: IMG.perfumeRose, desc: "Botanical scents that linger softly", icon: "🌸" },
    { slug: "tools", name: "Tools", count: 11, img: IMG.roller, desc: "Rollers, brushes and gua sha stones", icon: "🪨" },
  ];

  const SKIN = ["Dry", "Oily", "Combination", "Sensitive", "Normal"];
  const p = (o) => Object.assign({ rating: 4.7, reviews: 120, sizes: ["30 ml", "50 ml"], out: [], colors: [], badges: [], skin: ["Normal", "Dry"], tag: "Cruelty-free" }, o);
  const products = [
    p({ id: "argan-milk-serum", name: "Argan Milk Serum", cat: "serums", price: 1890, mrp: 2290, img: [IMG.amberStand, IMG.handsSerum], badges: ["best"], rating: 4.9, reviews: 512, skin: ["Dry", "Normal", "Sensitive"] }),
    p({ id: "vitamin-c-glow-drops", name: "Vitamin C Glow Drops", cat: "serums", price: 1650, img: [IMG.dropperLeaf, IMG.orangeFace], badges: ["new"], reviews: 341, skin: ["Oily", "Combination", "Normal"], tag: "Vegan" }),
    p({ id: "cloud-cream-cleanser", name: "Cloud Cream Cleanser", cat: "cleansers", price: 990, img: [IMG.creamTube, IMG.facial1], sizes: ["100 ml", "200 ml"], rating: 4.8, reviews: 288, skin: SKIN }),
    p({ id: "barrier-repair-cream", name: "Barrier Repair Cream", cat: "moisturisers", price: 1450, mrp: 1790, img: [IMG.handJar, IMG.handJar2], badges: ["sale"], reviews: 406, skin: ["Dry", "Sensitive"] }),
    p({ id: "green-clay-mask", name: "Green Clay Detox Mask", cat: "masks", price: 1190, img: [IMG.mask, IMG.facial2], sizes: ["75 ml"], badges: ["best"], reviews: 197, skin: ["Oily", "Combination"] }),
    p({ id: "intense-shea-body-oil", name: "Intense Shea Body Oil", cat: "body", price: 1290, img: [IMG.amberBottle, IMG.handOil], sizes: ["100 ml", "250 ml"], reviews: 156, tag: "Plant-based" }),
    p({ id: "hydra-gel-moisturiser", name: "Hydra Gel Moisturiser", cat: "moisturisers", price: 1350, img: [IMG.blueTube, IMG.pastelTubes], badges: ["new"], rating: 4.8, reviews: 233, skin: ["Oily", "Combination", "Normal"] }),
    p({ id: "botanical-toner-trio", name: "Botanical Toner Trio", cat: "cleansers", price: 2490, mrp: 2990, img: [IMG.whiteRow, IMG.pastelRow], sizes: ["3 x 100 ml"], badges: ["sale"], reviews: 92 }),
    p({ id: "rose-quartz-roller", name: "Rose Quartz Face Roller", cat: "tools", price: 1490, img: [IMG.roller, IMG.stones], sizes: ["One size"], reviews: 174, tag: "Handmade" }),
    p({ id: "sunset-spf-fluid", name: "Sunset SPF 50 Fluid", cat: "moisturisers", price: 1150, img: [IMG.orangeTubes, IMG.amberOrange], sizes: ["50 ml"], badges: ["best"], rating: 4.9, reviews: 621, skin: SKIN }),
    p({ id: "velvet-matte-lip", name: "Velvet Matte Lip", cat: "makeup", price: 890, img: [IMG.palette, IMG.faceMakeup], colors: ["#b3342f", "#c46a5a", "#8c3b4a", "#d89a8a"], sizes: ["3.5 g"], badges: ["new"], reviews: 265, tag: "Vegan" }),
    p({ id: "nude-eye-palette", name: "Nude Eye Palette", cat: "makeup", price: 1990, mrp: 2490, img: [IMG.brushes, IMG.palette], sizes: ["12 shades"], badges: ["sale"], reviews: 188 }),
    p({ id: "golden-hour-eau", name: "Golden Hour Eau de Parfum", cat: "fragrance", price: 3490, img: [IMG.perfumeGold, IMG.perfumeRose], sizes: ["50 ml", "100 ml"], badges: ["best"], rating: 4.8, reviews: 143, tag: "Clean scent" }),
    p({ id: "petal-mist", name: "Petal Rose Mist", cat: "fragrance", price: 1290, img: [IMG.perfumeRose, IMG.perfumeGold], sizes: ["100 ml"], reviews: 87 }),
    p({ id: "oat-milk-soap-bar", name: "Oat Milk Soap Bar", cat: "body", price: 390, img: [IMG.soapStack, IMG.soaps], sizes: ["100 g", "3 pack"], reviews: 309, tag: "Handmade" }),
    p({ id: "lavender-body-wash", name: "Lavender Body Wash", cat: "body", price: 690, mrp: 850, img: [IMG.pumpTowel, IMG.footSpa], sizes: ["250 ml", "500 ml"], badges: ["sale"], reviews: 211 }),
    p({ id: "niacinamide-10", name: "Niacinamide 10% Serum", cat: "serums", price: 1090, img: [IMG.bottleTall, IMG.whiteBottle], badges: ["best"], rating: 4.8, reviews: 702, skin: ["Oily", "Combination"] }),
    p({ id: "overnight-recovery-balm", name: "Overnight Recovery Balm", cat: "moisturisers", price: 1790, img: [IMG.handJar2, IMG.handJar], sizes: ["50 ml"], reviews: 129, skin: ["Dry", "Sensitive"] }),
    p({ id: "silk-powder-veil", name: "Silk Powder Veil", cat: "makeup", price: 1390, img: [IMG.powders, IMG.brushes], colors: ["#f1d9c2", "#e2b994", "#c9946a"], sizes: ["10 g"], reviews: 96 }),
    p({ id: "pro-brush-set", name: "Pro Brush Set", cat: "tools", price: 2190, img: [IMG.brushes, IMG.flatlay2], sizes: ["7 pieces"], badges: ["new"], reviews: 64, tag: "Vegan" }),
    p({ id: "pink-clay-shampoo-duo", name: "Pink Clay Hair Duo", cat: "body", price: 1590, img: [IMG.pinkBottles, IMG.pastelTubes], sizes: ["2 x 250 ml"], reviews: 118 }),
    p({ id: "amber-ritual-oil", name: "Amber Ritual Face Oil", cat: "serums", price: 2290, mrp: 2690, img: [IMG.amberOrange, IMG.amberBottle], badges: ["sale"], reviews: 157, skin: ["Dry", "Normal"] }),
    p({ id: "purple-peptide-tube", name: "Peptide Eye Cream", cat: "moisturisers", price: 1590, img: [IMG.purpleTubes, IMG.tubesBW], sizes: ["15 ml"], reviews: 134, skin: SKIN }),
    p({ id: "monochrome-essentials", name: "Monochrome Essentials Kit", cat: "cleansers", price: 2890, img: [IMG.tubesBW, IMG.flatlay1], sizes: ["Kit"], badges: ["new"], reviews: 58 }),
  ];
  products.forEach((x) => {
    x.thumb = U(x.img[0], 300, 300);
    x.img = x.img.map((id) => U(id, 900, 1100));
    x.catName = categories.find((c) => c.slug === x.cat).name;
  });

  const heroSlides = [
    { eyebrow: "", title: "Your skin.", hl: "Glowing.", sub: "Simple formulas for everyday glow.", cta: "Shop All", cta2: "Find my Match", img: U(IMG.orangeFace, 2200), pos: "30%" },
    { eyebrow: "", title: "Smooth skin.", hl: "Effortlessly.", sub: "Plant-powered actives, clinically tested.", cta: "New Arrival", cta2: "Our Story", img: U(IMG.facial2, 2200), pos: "35%" },
    { eyebrow: "", title: "Rituals that", hl: "restore.", sub: "Ten minutes a day, softer skin for life.", cta: "Shop Rituals", cta2: "Take the Quiz", img: U(IMG.massage, 2200), pos: "40%" },
  ];

  const reels = [
    { img: U(IMG.handsSerum, 700, 1100), views: "24.8K", product: "argan-milk-serum" },
    { img: U(IMG.facial1, 700, 1100), views: "18.2K", product: "cloud-cream-cleanser" },
    { img: U(IMG.face2, 700, 1100), views: "41.0K", product: "vitamin-c-glow-drops" },
    { img: U(IMG.mask, 700, 1100), views: "12.6K", product: "green-clay-mask" },
    { img: U(IMG.faceMakeup, 700, 1100), views: "33.4K", product: "velvet-matte-lip" },
    { img: U(IMG.handOil, 700, 1100), views: "9.7K", product: "intense-shea-body-oil" },
    { img: U(IMG.face3, 700, 1100), views: "15.1K", product: "sunset-spf-fluid" },
  ];

  const reviews = [
    { title: "Perfect for sensitive skin!", text: "This is so nourishing on my sensitive, damaged skin. And the perfect size to take with me in any bag!", name: "Riya S.", city: "Mumbai", avatar: U(IMG.p1, 120, 120), photo: U(IMG.handsSerum, 600, 800), product: "argan-milk-serum" },
    { title: "My skin has never been calmer", text: "Two weeks in and the redness around my nose is almost gone. The texture sinks in instantly.", name: "Ananya P.", city: "Bengaluru", avatar: U(IMG.p2, 120, 120), photo: U(IMG.face2, 600, 800), product: "barrier-repair-cream" },
    { title: "The SPF I actually wear", text: "No white cast, no greasy shine. It sits beautifully under makeup on hot Delhi days.", name: "Neha K.", city: "Delhi", avatar: U(IMG.p3, 120, 120), photo: U(IMG.orangeFace, 600, 800), product: "sunset-spf-fluid" },
    { title: "A little goes a long way", text: "One pump of the cleanser is enough for my whole face. Skin feels soft, never tight.", name: "Kavya R.", city: "Pune", avatar: U(IMG.p4, 120, 120), photo: U(IMG.facial1, 600, 800), product: "cloud-cream-cleanser" },
    { title: "Glow in a bottle", text: "My dull winter skin looks awake again. I get asked what I changed at least once a week.", name: "Meera I.", city: "Chennai", avatar: U(IMG.p5, 120, 120), photo: U(IMG.faceMakeup, 600, 800), product: "vitamin-c-glow-drops" },
    { title: "Spa night, every night", text: "The clay mask plus roller combo is my favourite Sunday ritual. Pores look tiny afterwards.", name: "Isha T.", city: "Hyderabad", avatar: U(IMG.p1, 120, 120), photo: U(IMG.mask, 600, 800), product: "green-clay-mask" },
  ];

  const blogs = [
    { slug: "morning-routine", title: "The five minute morning routine dermatologists swear by", tag: "Routines", date: "Nov 08, 2026", read: "6 min read", img: U(IMG.flatlay1, 1200, 900), excerpt: "Cleanse, treat, protect. Here is the order, the amounts and the one step most people skip." },
    { slug: "niacinamide-guide", title: "Niacinamide: what it does and who it is for", tag: "Ingredients", date: "Oct 29, 2026", read: "4 min read", img: U(IMG.whiteBottle, 1200, 900), excerpt: "The quiet multitasker that calms redness, refines pores and plays well with almost everything." },
    { slug: "spf-myths", title: "Seven SPF myths to stop believing this winter", tag: "Sun care", date: "Oct 17, 2026", read: "5 min read", img: U(IMG.orangeTubes, 1200, 900), excerpt: "Yes, you need it indoors. No, makeup with SPF is not enough. Let us clear things up." },
    { slug: "behind-the-lab", title: "Inside our Kerala botanical lab", tag: "Our story", date: "Sep 30, 2026", read: "8 min read", img: U(IMG.stones, 1200, 900), excerpt: "Meet the chemists and growers behind every formula, from seed to serum." },
    { slug: "gua-sha-guide", title: "A beginner's guide to gua sha and face rollers", tag: "Rituals", date: "Sep 12, 2026", read: "5 min read", img: U(IMG.roller, 1200, 900), excerpt: "Strokes, pressure and the oils that make facial massage actually work." },
    { slug: "barrier-repair", title: "Is your skin barrier damaged? Five signs to look for", tag: "Ingredients", date: "Aug 28, 2026", read: "3 min read", img: U(IMG.handJar2, 1200, 900), excerpt: "Tightness, stinging and sudden breakouts all point to the same fix." },
  ];

  const faqs = [
    { q: "Are your products suitable for sensitive skin?", a: "Absolutely. Crafted with clean, skin-loving ingredients, our entire line is dermatologist-tested, non-comedogenic and free from parabens, sulfates and harsh irritants. It provides a soothing experience and a natural, radiant glow without compromising your skin barrier." },
    { q: "How do I choose the right product for my skin routine?", a: "Take our two minute skin quiz or filter any collection by skin type. Every product page also lists who it suits best and how to layer it." },
    { q: "What are your shipping times and free delivery conditions?", a: "Metro cities receive orders in 2 to 4 working days and the rest of India in 4 to 7. Shipping is free on orders above ₹1,499." },
    { q: "What is your return and exchange policy?", a: "Unopened products can be returned within 15 days. If a product does not suit your skin, we offer a one time exchange even if it has been opened." },
    { q: "Is Cash on Delivery available?", a: "Yes. COD is available across 19,000+ pin codes. You can also pay part online and the rest at delivery." },
    { q: "Are your products cruelty-free?", a: "Always. We never test on animals and 90% of our range is fully vegan. Look for the tags on each product card." },
  ];

  const why = [
    { title: "Dermatologist tested", text: "Every formula is patch tested on sensitive skin before launch.", icon: "shield" },
    { title: "Plant-powered actives", text: "Botanicals grown in Kerala, blended with proven science.", icon: "leaf" },
    { title: "Free shipping over ₹1,499", text: "Fast, carbon-neutral delivery to 19,000+ pin codes.", icon: "truck" },
    { title: "Skin match promise", text: "Not right for you? One free exchange, even if opened.", icon: "return" },
  ];

  const brands = ["SUNDARI", "Kaya Leaf", "MOHA", "terra.", "Aranya", "ojas", "NILA", "Vanam"];

  /* Product details in the same shape the live storefront serialises for
     Template 1's product page: trust badges, Features checklist, "About this
     item" bullets and the four information tabs (Description, Item Details,
     Origin/Manufacturer & Packer, Others), plus product FAQs. */
  const DESC = {
    cleansers: "A gentle, low-foam cleanser that lifts away sunscreen, oil and city grime without stripping the skin barrier. Kerala grown botanicals keep skin soft and comfortable after every wash.",
    serums: "A concentrated serum with clinically proven actives in a featherlight base. It absorbs in seconds, layers under any moisturiser and targets dullness, uneven tone and texture.",
    moisturisers: "A barrier-loving moisturiser that delivers 72 hours of hydration. Ceramides and botanical oils seal in moisture while the breathable texture sits well under makeup.",
    masks: "A weekly treatment mask that draws out impurities, refines the look of pores and leaves skin calm, smooth and glowing in ten minutes.",
    body: "A nourishing body ritual made with cold-pressed oils and gentle cleansers. Leaves skin soft, scented and comfortable all day.",
    makeup: "Skin-first colour in a lightweight, long-wear formula enriched with botanical oils. Buildable, breathable and kind to sensitive skin.",
    fragrance: "A clean botanical fragrance blended with natural essential oils. Soft on the skin, long lasting and free from phthalates.",
    tools: "A beauty tool crafted to make your routine feel like a spa ritual. Boosts circulation, helps products absorb and reduces morning puffiness.",
  };
  const FEAT = {
    cleansers: ["Sulphate free", "pH balanced 5.5", "Removes SPF and makeup", "Non-drying formula"],
    serums: ["Clinically proven actives", "Absorbs in seconds", "Fragrance free", "Suits all skin types"],
    moisturisers: ["72 hour hydration", "Ceramide complex", "Non-comedogenic", "Works under makeup"],
    masks: ["Ten minute treatment", "Kaolin and green clay", "Refines pores", "Weekly ritual"],
    body: ["Cold-pressed oils", "Fast absorbing", "Paraben free", "Recyclable packaging"],
    makeup: ["Long-wear formula", "Buildable colour", "Enriched with oils", "Vegan"],
    fragrance: ["Natural essential oils", "Phthalate free", "Lasts 6 to 8 hours", "Travel friendly"],
    tools: ["Natural stone", "Hand finished", "Cooling effect", "Lifetime guarantee"],
  };
  const MAT = { cleansers: "Gel cream", serums: "Lightweight serum", moisturisers: "Rich cream", masks: "Clay paste", body: "Oil and wash", makeup: "Cream to powder", fragrance: "Eau de parfum", tools: "Rose quartz" };
  products.forEach((x, n) => {
    const ml = parseInt(x.sizes[0], 10) || 50;
    x.detail = {
      desc: DESC[x.cat] + `\n\n${x.name} is dermatologist tested, cruelty-free and made in small batches at our Kochi lab.`,
      bullets: [
        `${x.name} is made for ${x.skin.join(", ").toLowerCase()} skin and is gentle enough for daily use, morning and night.`,
        `Formulated with Kerala grown botanicals and proven actives, free from parabens, sulphates and synthetic colour.`,
        `Dermatologist tested on sensitive skin, non-comedogenic and pH balanced so it never disturbs your skin barrier.`,
        `Recyclable glass and FSC certified carton, with a refill pouch available on subscription.`,
        `Every batch is quality checked twice and comes with a ${x.cat === "tools" ? "lifetime" : "12 month"} shelf life once opened.`,
      ],
      features: FEAT[x.cat],
      item: [
        { label: "Material", value: MAT[x.cat] },
        { label: "Colour", value: x.colors.length ? `${x.colors.length} shades` : "Natural" },
        { label: "Recommended Use", value: x.cat === "body" ? "Body" : x.cat === "tools" ? "Face and neck" : "Face" },
        { label: "Handling Time", value: "1 to 2 business days" },
        { label: "Includes", value: `1 x ${x.name}, care card` },
        { label: "Product Dimensions", value: "4 x 4 x 12 cm" },
        { label: "Package Dimensions", value: "6 x 6 x 14 cm" },
        { label: "Product Weight", value: `${ml + 60} g` },
        { label: "Package Weight", value: `${ml + 110} g` },
        { label: "HSN Code", value: x.cat === "fragrance" ? "33030010" : "33049990" },
        { label: "GST", value: "18%" },
        { label: "Return Type", value: "7 day replacement" },
        { label: "Skin Type", value: x.skin.join(", ") },
        { label: "Key Ingredient", value: ["Niacinamide", "Vitamin C", "Hyaluronic acid", "Ceramides", "Green clay", "Argan oil"][n % 6] },
      ],
      warning: x.cat === "fragrance" ? "Flammable. Keep away from heat and open flame. For external use only." : "For external use only. Patch test before first use. Avoid contact with eyes.",
      origin: [
        { label: "Country of Origin", value: "India" },
        { label: "Manufacturer Contact", value: "Velora Botanicals Pvt. Ltd., 21 Infopark Road, Kakkanad, Kochi, Kerala 682042" },
        { label: "Packer Contact", value: "Velora Botanicals Pvt. Ltd., Unit 4, SIDCO Industrial Estate, Kochi, Kerala 682030" },
        { label: "Importer Contact", value: "Not applicable, made in India" },
        { label: "Guarantee/Warranty", value: x.cat === "tools" ? "Lifetime guarantee against breakage" : "Skin match promise: one free exchange within 30 days" },
        { label: "Customer Support Number", value: "+91 484 400 1200" },
        { label: "Customer Support Email", value: "care@velora.example" },
      ],
      extras: [
        { label: "Shelf Life", value: "24 months unopened, 12 months after opening" },
        { label: "Certifications", value: "Cruelty-free, vegan, dermatologist tested" },
        { label: "Batch Number", value: `VLR-${2611 + n}-B` },
        { label: "Storage", value: "Store below 30°C, away from direct sunlight" },
      ],
      badges: [
        { icon: "leaf", label: "Cruelty Free" },
        { icon: "return", label: "7 Day Replacement", url: "policy.html?type=refund" },
        { icon: "truck", label: "Free Delivery" },
        { icon: "shield", label: "Secure Payment" },
      ],
      faqs: [
        { q: `Can I use ${x.name} every day?`, a: "Yes. It is gentle enough for twice daily use. Start once a day for the first week if your skin is sensitive." },
        { q: "Is it safe during pregnancy?", a: "The formula is free from retinoids and salicylic acid, but we always suggest checking with your doctor first." },
        { q: "How long does one pack last?", a: "Used as directed, one pack lasts about 8 weeks." },
        { q: "Is Cash on Delivery available?", a: "Yes, COD is available on this product across 19,000+ pin codes." },
      ],
      video: n % 3 === 0,
    };
  });

  window.T3 = { U, IMG, categories, products, heroSlides, reels, reviews, blogs, faqs, why, brands, SKIN, brand: "velora", freeShip: 1499 };
})();
