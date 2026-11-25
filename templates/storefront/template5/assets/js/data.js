/* Demo content for the Template 5 preview (furniture and interiors). Every image is a stock photo. */
(function () {
  const U = (id, w = 900, h) =>
    `https://images.unsplash.com/photo-${id}?auto=format&fit=crop&w=${w}${h ? `&h=${h}` : ""}&q=75`;

  const IMG = {
    greenSofa: "1555041469-a586c61ea9bc", yellowRoom: "1586023492125-27b2c045efd7", whiteChair: "1567538096630-e0c55bd6374c",
    blackChair: "1592078615290-033ee584e267", woodChair: "1506439773649-6e0eb8cfb237", stool: "1503602642458-232111445657",
    plantChair: "1519947486511-46149fa0a254", floorLamp: "1507473885765-e6ed057f782c", pendant: "1513506003901-1e6a229e2d15",
    greyLiving: "1493663284031-b7e3aefcae8e", bedroom1: "1505693416388-ac5ce068fe85", bedroom2: "1540518614846-7eded433c457",
    bohoLiving: "1556228453-efd6c1ff04f6", blueSofa: "1484101403633-562f891dc89a", lounge: "1538688525198-9b88f6f53126",
    darkLiving: "1550581190-9c1c48d21d6c", greyChair: "1598300042247-d088f8ab3a91", swivel: "1580480055273-228ff5388ef8",
    spotChair: "1549497538-303791108f95", living3: "1616486338812-3dadae4b4ace", living4: "1618221195710-dd6b41faaea6",
    bedroom3: "1616627561839-074385245ff6", bedroom4: "1615874959474-d609969a20ed", dining1: "1617806118233-18e1de247200",
    living5: "1600210492486-724fe5c67fb0", deskChairs: "1611269154421-4e27233ac5c7", wallShelves: "1594026112284-02bb6f3352fe",
    diningTable: "1577140917170-285929fb55b7", tablePlant: "1530018607912-eff2daa1bac4", darkBedroom: "1617104678098-de229db51175",
    whiteTable: "1533090481720-856c6e3c1fdc", desk: "1518455027359-f3f8164ba6bd", succulent: "1485955900006-10f4d324d411",
    brownSofa: "1578500494198-246f612d3b3d", vase: "1612196808214-b8e1d6145a8c", chandelier: "1544457070-4cd773b4d71e",
    orangeSofa: "1567016432779-094069958ea5", kitchen: "1588854337236-6889d631faa8", yellowArm: "1550226891-ef816aed4a98",
    pendants: "1615529182904-14819c35db37", shelves: "1513519245088-0e12902e5a38", bookshelf: "1594620302200-9a762244a156",
    chairPlant: "1501045661006-fcebe0257c3f", creamSofa: "1512212621149-107ffe572d2f", yellowChairs: "1551298370-9d3d53740c72",
    livingPlants: "1513694203232-719a280e022f",
    p1: "1508214751196-bcfd4ca60f91", p2: "1507003211169-0a1dd7228f2d", p3: "1517841905240-472988babdf9",
    p4: "1500648767791-00dcc994a43e", p5: "1529626455594-4ff0802cfb7e",
  };

  const categories = [
    { slug: "seating", name: "Seating", count: 24, img: IMG.yellowRoom, desc: "Lounge chairs, armchairs and stools built to be sat in" },
    { slug: "sofas", name: "Sofas", count: 16, img: IMG.greyLiving, desc: "Deep seats and honest frames for everyday living" },
    { slug: "tables", name: "Tables", count: 21, img: IMG.diningTable, desc: "Solid wood dining, coffee and side tables" },
    { slug: "lighting", name: "Lighting", count: 18, img: IMG.pendants, desc: "Pendants and floor lamps that set the mood" },
    { slug: "bedroom", name: "Bedroom", count: 12, img: IMG.bedroom1, desc: "Beds, side tables and soft linens for slower mornings" },
    { slug: "storage", name: "Storage", count: 14, img: IMG.wallShelves, desc: "Shelving and cabinets with careful joinery" },
    { slug: "decor", name: "Decor", count: 27, img: IMG.vase, desc: "Ceramics, planters and finishing touches" },
    { slug: "workspace", name: "Workspace", count: 9, img: IMG.desk, desc: "Desks and task chairs for focused hours" },
  ];

  const SKIN = ["Living room", "Bedroom", "Dining", "Workspace", "Outdoor"];
  const p = (o) => Object.assign({ rating: 4.7, reviews: 80, sizes: ["Standard"], out: [], colors: [], badges: [], skin: ["Living room"], tag: "Solid wood" }, o);
  const C = { oak: "#c8a27a", walnut: "#6b4a33", ink: "#1f1f1f", linen: "#e7e1d6", sage: "#9aab8c", rust: "#b5562f", stone: "#b9b4ab", mustard: "#d0a23a", teal: "#2f5f63" };
  const products = [
    p({ id: "harlow-swivel-chair", name: "Harlow Swivel Chair", cat: "seating", price: 42900, img: [IMG.swivel, IMG.living3], colors: [C.linen, C.stone], badges: ["best"], rating: 4.9, reviews: 212, skin: ["Living room", "Bedroom"], tag: "Boucle" }),
    p({ id: "reed-dining-chair", name: "Reed Dining Chair", cat: "seating", price: 12900, mrp: 15900, img: [IMG.woodChair, IMG.dining1], colors: [C.oak, C.walnut], badges: ["sale"], reviews: 146, skin: ["Dining"] }),
    p({ id: "sable-wingback", name: "Sable Wingback Chair", cat: "seating", price: 38500, img: [IMG.greyChair, IMG.darkLiving], colors: [C.stone, C.ink], reviews: 98, tag: "Wool blend" }),
    p({ id: "ember-lounge-chair", name: "Ember Lounge Chair", cat: "seating", price: 34900, img: [IMG.yellowArm, IMG.yellowRoom], colors: [C.mustard, C.rust], badges: ["new"], reviews: 64, tag: "Velvet" }),
    p({ id: "nova-shell-chair", name: "Nova Shell Chair", cat: "seating", price: 9900, img: [IMG.blackChair, IMG.whiteTable], colors: [C.ink, C.linen], reviews: 175, skin: ["Dining", "Workspace"], tag: "Moulded" }),
    p({ id: "tolo-bar-stool", name: "Tolo Bar Stool", cat: "seating", price: 6900, img: [IMG.stool, IMG.kitchen], colors: [C.linen, C.oak], reviews: 88, skin: ["Dining"] }),
    p({ id: "marlow-three-seater", name: "Marlow Three Seater", cat: "sofas", price: 129000, mrp: 149000, img: [IMG.greenSofa, IMG.lounge], colors: [C.teal, C.stone, C.rust], badges: ["sale", "best"], rating: 4.9, reviews: 301, sizes: ["2 seater", "3 seater", "4 seater"], tag: "Kiln-dried oak" }),
    p({ id: "cove-modular-sofa", name: "Cove Modular Sofa", cat: "sofas", price: 164000, img: [IMG.creamSofa, IMG.living4], colors: [C.linen, C.stone], badges: ["new"], reviews: 57, sizes: ["3 piece", "4 piece", "5 piece"], tag: "Performance fabric" }),
    p({ id: "sienna-loveseat", name: "Sienna Loveseat", cat: "sofas", price: 89000, img: [IMG.orangeSofa, IMG.bohoLiving], colors: [C.rust, C.mustard], reviews: 92, sizes: ["2 seater"], tag: "Velvet" }),
    p({ id: "atlas-leather-sofa", name: "Atlas Leather Sofa", cat: "sofas", price: 178000, img: [IMG.brownSofa, IMG.livingPlants], colors: [C.walnut], badges: ["best"], reviews: 134, sizes: ["3 seater", "4 seater"], tag: "Full grain leather" }),
    p({ id: "millbrook-dining-table", name: "Millbrook Dining Table", cat: "tables", price: 74900, img: [IMG.diningTable, IMG.dining1], colors: [C.oak, C.walnut], badges: ["best"], rating: 4.8, reviews: 188, sizes: ["4 seater", "6 seater", "8 seater"], skin: ["Dining"] }),
    p({ id: "foster-coffee-table", name: "Foster Coffee Table", cat: "tables", price: 24900, mrp: 29900, img: [IMG.tablePlant, IMG.greyLiving], colors: [C.oak], badges: ["sale"], reviews: 112 }),
    p({ id: "reya-side-table", name: "Reya Side Table", cat: "tables", price: 8900, img: [IMG.whiteTable, IMG.bedroom2], colors: [C.linen, C.ink], reviews: 76, skin: ["Living room", "Bedroom"] }),
    p({ id: "holloway-writing-desk", name: "Holloway Writing Desk", cat: "workspace", price: 45900, img: [IMG.desk, IMG.deskChairs], colors: [C.walnut, C.oak], badges: ["new"], reviews: 49, skin: ["Workspace"] }),
    p({ id: "kepler-task-chair", name: "Kepler Task Chair", cat: "workspace", price: 18900, img: [IMG.deskChairs, IMG.desk], colors: [C.ink, C.stone], reviews: 133, skin: ["Workspace"], tag: "Ergonomic" }),
    p({ id: "halsey-floor-lamp", name: "Halsey Floor Lamp", cat: "lighting", price: 14500, mrp: 17900, img: [IMG.floorLamp, IMG.chairPlant], colors: [C.ink], badges: ["sale"], reviews: 101, tag: "Powder coated" }),
    p({ id: "orbit-pendant", name: "Orbit Pendant Light", cat: "lighting", price: 11900, img: [IMG.pendant, IMG.pendants], colors: [C.linen, C.teal], badges: ["best"], reviews: 167, tag: "Spun metal" }),
    p({ id: "lumen-chandelier", name: "Lumen Chandelier", cat: "lighting", price: 32900, img: [IMG.chandelier, IMG.dining1], colors: [C.mustard], reviews: 38, skin: ["Dining"], tag: "Brass" }),
    p({ id: "loft-platform-bed", name: "Loft Platform Bed", cat: "bedroom", price: 98000, img: [IMG.bedroom1, IMG.bedroom3], colors: [C.oak, C.walnut], badges: ["best"], reviews: 141, sizes: ["Queen", "King"], skin: ["Bedroom"] }),
    p({ id: "dune-upholstered-bed", name: "Dune Upholstered Bed", cat: "bedroom", price: 112000, img: [IMG.darkBedroom, IMG.bedroom4], colors: [C.stone, C.ink], badges: ["new"], reviews: 44, sizes: ["Queen", "King"], skin: ["Bedroom"], tag: "Linen" }),
    p({ id: "ashby-wall-shelf", name: "Ashby Wall Shelf", cat: "storage", price: 15900, img: [IMG.wallShelves, IMG.shelves], colors: [C.oak, C.ink], reviews: 87, sizes: ["90 cm", "120 cm"] }),
    p({ id: "archive-bookcase", name: "Archive Bookcase", cat: "storage", price: 52900, mrp: 59900, img: [IMG.bookshelf, IMG.living5], colors: [C.walnut], badges: ["sale"], reviews: 65 }),
    p({ id: "terra-stoneware-vase", name: "Terra Stoneware Vase", cat: "decor", price: 3900, img: [IMG.vase, IMG.livingPlants], colors: [C.linen, C.stone], reviews: 219, sizes: ["Small", "Large"], tag: "Handmade" }),
    p({ id: "mesa-planter", name: "Mesa Ceramic Planter", cat: "decor", price: 2900, img: [IMG.succulent, IMG.plantChair], colors: [C.sage, C.linen], badges: ["new"], reviews: 154, sizes: ["Small", "Medium"], tag: "Handmade" }),
  ];
  products.forEach((x) => {
    x.thumb = U(x.img[0], 300, 300);
    x.img = x.img.map((id) => U(id, 900, 1100));
    x.catName = categories.find((c) => c.slug === x.cat).name;
  });

  /* Product details in the same shape the live storefront serialises for
     Template 1's product page: trust badges, Features checklist, "About this
     item" bullets and the four information tabs, plus product FAQs. */
  const DESC = {
    seating: "Designed to earn its place in your home, simple to live with, easy to care for, and built to stay useful. A kiln-dried hardwood frame, high resilience foam and hand-tied springs keep its shape for years.",
    sofas: "A sofa made for long evenings and lazy weekends. Deep seats, feather-wrapped cushions and a solid hardwood frame with corner-blocked joints that take the weight of everyday life.",
    tables: "Solid wood, careful joinery and a hand-rubbed oil finish that gets better with age. Built to gather around for decades, not seasons.",
    lighting: "Warm, glare-free light shaped by spun and powder-coated metal. Pairs with any LED bulb and sets the mood in living, dining and reading corners.",
    bedroom: "A calm, low-profile bed frame with a slatted base that needs no box spring. Quiet joinery means no creaks, and assembly takes under thirty minutes.",
    storage: "Shelving and cabinets made with dovetail and dowel joints, finished by hand. Room for books, ceramics and everything in between.",
    decor: "Finishing pieces thrown and glazed by hand in small batches, so every one is slightly different.",
    workspace: "Workspace furniture with the proportions of a home piece and the comfort of an office one.",
  };
  const FEAT = {
    seating: ["Kiln-dried hardwood frame", "High resilience foam", "Removable covers", "10 year frame warranty"],
    sofas: ["Corner-blocked joints", "Feather-wrapped cushions", "Stain resistant fabric", "Free white glove delivery"],
    tables: ["Solid FSC certified wood", "Hand-rubbed oil finish", "Mortise and tenon joints", "Seats up to 8"],
    lighting: ["Warm 2700K glow", "Works with LED bulbs", "Dimmable", "Adjustable cord length"],
    bedroom: ["No box spring needed", "Tool-free slats", "Under-bed clearance 18 cm", "Silent joinery"],
    storage: ["Dovetail joints", "Adjustable shelves", "Wall anchor included", "Holds 25 kg per shelf"],
    decor: ["Hand thrown", "Food safe glaze", "Drainage hole", "Small batch"],
    workspace: ["Cable management", "Ergonomic height", "Soft close drawers", "Easy assembly"],
  };
  const MAT = { seating: "Oak frame, boucle", sofas: "Hardwood frame, linen blend", tables: "Solid oak", lighting: "Steel and aluminium", bedroom: "Solid ash", storage: "Walnut veneer", decor: "Stoneware", workspace: "Oak and steel" };
  products.forEach((x, n) => {
    x.detail = {
      desc: DESC[x.cat] + `\n\n${x.name} is made in our partner workshops in Jodhpur and Bengaluru, then quality checked twice before it leaves for your home.`,
      bullets: [
        `${x.name} is built for ${x.skin.join(", ").toLowerCase()} spaces and sized to fit modern Indian homes.`,
        `Made from responsibly sourced materials with water-based, low VOC finishes.`,
        `Arrives fully assembled or with tool-free assembly in under thirty minutes.`,
        `Covered by our ${x.cat === "decor" ? "1 year" : "10 year"} warranty against manufacturing defects.`,
        `Free white glove delivery and packaging removal across 40 cities.`,
      ],
      features: FEAT[x.cat],
      item: [
        { label: "Material", value: MAT[x.cat] },
        { label: "Colour", value: x.colors.length > 1 ? `${x.colors.length} finishes` : "As shown" },
        { label: "Recommended Use", value: x.skin.join(", ") },
        { label: "Handling Time", value: "3 to 5 business days" },
        { label: "Includes", value: `1 x ${x.name}, care kit` },
        { label: "Product Dimensions", value: x.cat === "decor" ? "18 x 18 x 32 cm" : "84 x 78 x 92 cm" },
        { label: "Package Dimensions", value: x.cat === "decor" ? "24 x 24 x 38 cm" : "92 x 86 x 98 cm" },
        { label: "Product Weight", value: x.cat === "decor" ? "1.8 kg" : `${18 + n} kg` },
        { label: "Package Weight", value: x.cat === "decor" ? "2.4 kg" : `${24 + n} kg` },
        { label: "HSN Code", value: x.cat === "lighting" ? "94051090" : x.cat === "decor" ? "69120040" : "94036000" },
        { label: "GST", value: "18%" },
        { label: "Return Type", value: "7 day replacement" },
        { label: "Seat Height", value: ["seating", "sofas"].includes(x.cat) ? "44 cm" : "Not applicable" },
        { label: "Finish", value: ["Natural oil", "Matte lacquer", "Hand waxed", "Powder coat"][n % 4] },
      ],
      warning: x.cat === "lighting" ? "Electrical installation must be done by a qualified electrician. Switch off mains before fitting." : "Anchor tall units to the wall. Not suitable for children to climb.",
      origin: [
        { label: "Country of Origin", value: "India" },
        { label: "Manufacturer Contact", value: "Oakline Furniture Pvt. Ltd., Plot 22, Basni Industrial Area, Jodhpur, Rajasthan 342005" },
        { label: "Packer Contact", value: "Oakline Furniture Pvt. Ltd., Warehouse 3, Hoskote, Bengaluru, Karnataka 562114" },
        { label: "Importer Contact", value: "Not applicable, made in India" },
        { label: "Guarantee/Warranty", value: x.cat === "decor" ? "1 year warranty" : "10 year frame warranty" },
        { label: "Customer Support Number", value: "+91 80 4000 2200" },
        { label: "Customer Support Email", value: "care@oakline.example" },
      ],
      extras: [
        { label: "Care", value: "Dust with a dry cloth, re-oil wood every 12 months" },
        { label: "Certifications", value: "FSC certified wood, low VOC finish" },
        { label: "Batch Number", value: `OAK-${2611 + n}-J` },
        { label: "Assembly", value: x.cat === "decor" ? "None" : "Tool-free, under 30 minutes" },
      ],
      badges: [
        { icon: "box", label: "Free Delivery" },
        { icon: "return", label: "7 Day Replacement", url: "policy.html?type=refund" },
        { icon: "shield", label: "10 Year Warranty" },
        { icon: "card", label: "Easy EMI" },
      ],
      faqs: [
        { q: `How is ${x.name} delivered?`, a: "Our own white glove team delivers, places and assembles it, then takes the packaging away." },
        { q: "Can I see the fabric or finish before ordering?", a: "Yes. Order up to five free swatches, delivered in 3 days." },
        { q: "Is EMI available?", a: "No cost EMI is available for 3, 6 and 9 months on major cards." },
        { q: "What if it does not fit through my door?", a: "Every product page lists package dimensions. If it still does not fit, we arrange a free return." },
      ],
      video: n % 3 === 0,
    };
  });

  const heroSlides = [
    { title: "Rooms Worth Staying", sub: "Furniture made to be lived with, for years.", cta: "Shop Collection", img: U(IMG.living4, 2200), pos: "60%" },
    { title: "Built to Gather", sub: "Solid wood dining for long dinners.", cta: "Shop Tables", img: U(IMG.dining1, 2200), pos: "55%" },
  ];

  const reels = [
    { img: U(IMG.yellowRoom, 700, 1100), views: "24.8K", product: "ember-lounge-chair" },
    { img: U(IMG.lounge, 700, 1100), views: "18.2K", product: "marlow-three-seater" },
    { img: U(IMG.bedroom3, 700, 1100), views: "41.0K", product: "loft-platform-bed" },
    { img: U(IMG.pendants, 700, 1100), views: "12.6K", product: "orbit-pendant" },
    { img: U(IMG.dining1, 700, 1100), views: "33.4K", product: "millbrook-dining-table" },
    { img: U(IMG.chairPlant, 700, 1100), views: "9.7K", product: "halsey-floor-lamp" },
  ];

  const reviews = [
    { title: "Worth every rupee", text: "The swivel chair is the most comfortable seat in our home. The boucle is soft and it looks even better than the photos.", name: "Riya S.", city: "Mumbai", avatar: U(IMG.p1, 120, 120), photo: U(IMG.living3, 600, 800), product: "harlow-swivel-chair" },
    { title: "Delivered and assembled in an hour", text: "The team brought the sofa up four floors, assembled it and took every bit of packaging away.", name: "Arjun M.", city: "Bengaluru", avatar: U(IMG.p2, 120, 120), photo: U(IMG.greyLiving, 600, 800), product: "marlow-three-seater" },
    { title: "Heirloom quality table", text: "Solid, heavy and beautifully finished. Six of us had dinner on it the day it arrived.", name: "Neha K.", city: "Delhi", avatar: U(IMG.p3, 120, 120), photo: U(IMG.diningTable, 600, 800), product: "millbrook-dining-table" },
    { title: "Perfect reading corner", text: "The floor lamp gives a warm, even light. Exactly what our living room needed.", name: "Kabir R.", city: "Pune", avatar: U(IMG.p4, 120, 120), photo: U(IMG.chairPlant, 600, 800), product: "halsey-floor-lamp" },
    { title: "Sleeping better already", text: "No creaks at all, and the low profile makes the room feel bigger.", name: "Meera I.", city: "Chennai", avatar: U(IMG.p5, 120, 120), photo: U(IMG.bedroom1, 600, 800), product: "loft-platform-bed" },
  ];

  const blogs = [
    { slug: "solid-wood", title: "A look at where our solid wood comes from", tag: "Ideas", date: "Nov 08, 2026", read: "6 min read", img: U(IMG.living5, 1200, 900), excerpt: "A short look at the forests, mills and hands behind every solid wood piece we sell." },
    { slug: "dining-table", title: "Setting a dining table that brings people together", tag: "Interior", date: "Oct 29, 2026", read: "4 min read", img: U(IMG.dining1, 1200, 900), excerpt: "The proportions and materials that turn a dining table into the room everyone ends up in." },
    { slug: "natural-fabric", title: "The case for choosing natural fabric over synthetic", tag: "Care", date: "Oct 17, 2026", read: "5 min read", img: U(IMG.living3, 1200, 900), excerpt: "Linen, wool and cotton velvet age differently than synthetics, and mostly for the better." },
    { slug: "metal-finishes", title: "How to mix metal finishes with confidence", tag: "Guide", date: "Sep 30, 2026", read: "8 min read", img: U(IMG.chandelier, 1200, 900), excerpt: "Brass, black steel and chrome can share a room when one finish leads." },
    { slug: "small-spaces", title: "Big comfort for small living rooms", tag: "Ideas", date: "Sep 12, 2026", read: "5 min read", img: U(IMG.blueSofa, 1200, 900), excerpt: "Scale, legs and light: three rules for making a compact room feel generous." },
    { slug: "bedroom-calm", title: "Designing a bedroom for slower mornings", tag: "Interior", date: "Aug 28, 2026", read: "3 min read", img: U(IMG.bedroom2, 1200, 900), excerpt: "Low beds, warm light and fewer things on the nightstand." },
  ];

  const faqs = [
    { q: "How long does delivery take?", a: "In stock pieces arrive in 5 to 7 days across 40 cities. Made to order pieces take 3 to 4 weeks." },
    { q: "Is delivery and assembly free?", a: "Yes. White glove delivery, assembly and packaging removal are free on every order above ₹25,000." },
    { q: "Can I order fabric swatches?", a: "Yes. Order up to five free swatches and we deliver them in 3 days." },
    { q: "What is your return policy?", a: "If a piece arrives damaged or does not match the listing, we replace it within 7 days at no cost." },
    { q: "Do you offer EMI?", a: "No cost EMI is available for 3, 6 and 9 months on major credit cards." },
    { q: "Do you have a showroom?", a: "Visit our experience centres in Bengaluru and Mumbai, open every day from 11 AM to 8 PM." },
  ];

  const why = [
    { title: "Solid wood, honest joinery", text: "Kiln-dried hardwood and mortise and tenon joints in every frame.", icon: "shield" },
    { title: "Free white glove delivery", text: "Delivered, assembled and unpacked by our own team.", icon: "truck" },
    { title: "10 year warranty", text: "We stand behind every frame we build for a decade.", icon: "heart" },
    { title: "Made in India", text: "Crafted in partner workshops in Jodhpur and Bengaluru.", icon: "leaf" },
  ];

  window.T3 = { U, IMG, categories, products, heroSlides, reels, reviews, blogs, faqs, why, brands: [], SKIN, brand: "oakline", freeShip: 25000 };
})();
