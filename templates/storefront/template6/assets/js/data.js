/* Demo content for the Template 6 preview (home tech and lighting). Every image is a stock photo. */
(function () {
  const U = (id, w = 900, h) =>
    `https://images.unsplash.com/photo-${id}?auto=format&fit=crop&w=${w}${h ? `&h=${h}` : ""}&q=75`;

  const IMG = {
    floorLamp: "1507473885765-e6ed057f782c", pendant: "1513506003901-1e6a229e2d15", pendants: "1615529182904-14819c35db37",
    chandelier: "1544457070-4cd773b4d71e", deskLamp: "1534105615256-13940a56ff44", headphones: "1546435770-a3e426bf472b",
    budsCase: "1590658268037-6bf12165a8df", budsRed: "1606220588913-b3aacb4d2f46", smartWatch: "1523275335684-37898b6baf30",
    wristWatch: "1524592094714-0f0654e20314", desk: "1518455027359-f3f8164ba6bd", deskChairs: "1611269154421-4e27233ac5c7",
    shelves: "1594026112284-02bb6f3352fe", vase: "1612196808214-b8e1d6145a8c", planter: "1485955900006-10f4d324d411",
    living4: "1618221195710-dd6b41faaea6", living3: "1616486338812-3dadae4b4ace", greyLiving: "1493663284031-b7e3aefcae8e",
    bedroom1: "1505693416388-ac5ce068fe85", bedroom2: "1540518614846-7eded433c457", bedroom3: "1616627561839-074385245ff6",
    dining1: "1617806118233-18e1de247200", kitchen: "1588854337236-6889d631faa8", yellowRoom: "1586023492125-27b2c045efd7",
    chairPlant: "1501045661006-fcebe0257c3f", living5: "1600210492486-724fe5c67fb0", livingPlants: "1513694203232-719a280e022f",
    darkLiving: "1550581190-9c1c48d21d6c", lounge: "1538688525198-9b88f6f53126",
    p1: "1508214751196-bcfd4ca60f91", p2: "1507003211169-0a1dd7228f2d", p3: "1517841905240-472988babdf9",
    p4: "1500648767791-00dcc994a43e", p5: "1529626455594-4ff0802cfb7e",
  };

  const categories = [
    { slug: "lighting", name: "Lighting", count: 24, img: IMG.pendants, desc: "Smart lamps and pendants that follow your day" },
    { slug: "audio", name: "Audio", count: 18, img: IMG.headphones, desc: "Headphones and earbuds tuned for long listening" },
    { slug: "wearables", name: "Wearables", count: 12, img: IMG.smartWatch, desc: "Watches that track sleep, steps and focus" },
    { slug: "workspace", name: "Workspace", count: 16, img: IMG.desk, desc: "Desks, chairs and storage for focused hours" },
    { slug: "home", name: "Home", count: 21, img: IMG.living3, desc: "Diffusers, planters and calm finishing touches" },
  ];

  const SKIN = ["Living room", "Bedroom", "Workspace", "Kitchen", "On the go"];
  const p = (o) => Object.assign({ rating: 4.7, reviews: 80, sizes: ["Standard"], out: [], colors: [], badges: [], skin: ["Living room"], tag: "App control" }, o);
  const C = { ink: "#2e2e2e", white: "#f2f2f2", sand: "#d9cbb6", sage: "#9db29a", rose: "#e8b7b7", blue: "#3e6fd8", red: "#e5322c", brass: "#c9a45c", walnut: "#6b4a33" };
  const products = [
    p({ id: "halo-floor-lamp", name: "Halo Smart Floor Lamp", cat: "lighting", price: 14900, mrp: 17900, img: [IMG.floorLamp, IMG.chairPlant], colors: [C.ink, C.white, C.brass], badges: ["sale"], rating: 4.8, reviews: 212, skin: ["Living room", "Bedroom"] }),
    p({ id: "orbit-pendant", name: "Orbit Pendant Light", cat: "lighting", price: 8900, img: [IMG.pendant, IMG.pendants], colors: [C.sage, C.white, C.ink], badges: ["best"], reviews: 167, skin: ["Kitchen", "Living room"] }),
    p({ id: "duo-pendant-set", name: "Duo Pendant Set", cat: "lighting", price: 15900, img: [IMG.pendants, IMG.living3], colors: [C.sand, C.ink], badges: ["new"], reviews: 58, skin: ["Living room"] }),
    p({ id: "arc-desk-lamp", name: "Arc Desk Lamp", cat: "lighting", price: 4900, img: [IMG.deskLamp, IMG.desk], colors: [C.ink, C.white], reviews: 301, skin: ["Workspace", "Bedroom"], tag: "Touch dimming" }),
    p({ id: "glow-chandelier", name: "Glow Chandelier", cat: "lighting", price: 32900, img: [IMG.chandelier, IMG.dining1], colors: [C.brass], reviews: 41, skin: ["Kitchen"] }),
    p({ id: "pulse-anc-headphones", name: "Pulse ANC Headphones", cat: "audio", price: 12900, mrp: 15900, img: [IMG.headphones, IMG.desk], colors: [C.ink, C.sand, C.blue], badges: ["sale", "best"], rating: 4.9, reviews: 488, skin: ["On the go", "Workspace"], tag: "40 hr battery" }),
    p({ id: "air-buds-pro", name: "Air Buds Pro", cat: "audio", price: 7900, img: [IMG.budsCase, IMG.headphones], colors: [C.white, C.ink], badges: ["best"], reviews: 352, skin: ["On the go"], tag: "Noise cancelling" }),
    p({ id: "sport-buds", name: "Sport Buds", cat: "audio", price: 4900, img: [IMG.budsRed, IMG.budsCase], colors: [C.ink, C.red, C.sage, C.blue], badges: ["new"], reviews: 129, skin: ["On the go"], tag: "Sweat proof" }),
    p({ id: "vita-smartwatch", name: "Vita Smartwatch", cat: "wearables", price: 18900, img: [IMG.smartWatch, IMG.wristWatch], colors: [C.white, C.ink, C.rose], badges: ["best"], rating: 4.8, reviews: 276, skin: ["On the go"], tag: "7 day battery" }),
    p({ id: "hybrid-classic-watch", name: "Hybrid Classic Watch", cat: "wearables", price: 14500, mrp: 16900, img: [IMG.wristWatch, IMG.smartWatch], colors: [C.walnut, C.ink], badges: ["sale"], reviews: 93, skin: ["On the go"] }),
    p({ id: "focus-standing-desk", name: "Focus Standing Desk", cat: "workspace", price: 38900, img: [IMG.desk, IMG.deskChairs], colors: [C.walnut, C.white], badges: ["new"], reviews: 64, sizes: ["120 cm", "140 cm", "160 cm"], skin: ["Workspace"], tag: "Dual motor" }),
    p({ id: "ergo-task-chair", name: "Ergo Task Chair", cat: "workspace", price: 21900, img: [IMG.deskChairs, IMG.desk], colors: [C.ink, C.sand], reviews: 188, skin: ["Workspace"], tag: "Lumbar support" }),
    p({ id: "float-wall-shelf", name: "Float Wall Shelf", cat: "workspace", price: 6900, img: [IMG.shelves, IMG.livingPlants], colors: [C.walnut, C.white], reviews: 87, sizes: ["90 cm", "120 cm"], skin: ["Workspace", "Living room"], tag: "Hidden cable tray" }),
    p({ id: "terra-diffuser", name: "Terra Ceramic Diffuser", cat: "home", price: 3900, img: [IMG.vase, IMG.livingPlants], colors: [C.white, C.sand], badges: ["new"], reviews: 219, skin: ["Bedroom", "Living room"], tag: "Ultrasonic" }),
    p({ id: "mesa-smart-planter", name: "Mesa Smart Planter", cat: "home", price: 2900, img: [IMG.planter, IMG.chairPlant], colors: [C.sage, C.white], reviews: 154, sizes: ["Small", "Medium"], skin: ["Kitchen", "Living room"], tag: "Self watering" }),
  ];
  products.forEach((x) => {
    x.thumb = U(x.img[0], 300, 300);
    x.img = x.img.map((id) => U(id, 900, 900));
    x.catName = categories.find((c) => c.slug === x.cat).name;
  });

  /* Product details in the same shape the live storefront serialises for
     Template 1's product page: trust badges, Features checklist, "About this
     item" bullets and the four information tabs, plus product FAQs. */
  const DESC = {
    lighting: "Warm, glare-free light that follows your day. Tunable from 2200K to 6500K, dimmable to 1%, and controlled by touch, voice or the lumo app with scenes that wake you gently and wind you down at night.",
    audio: "Studio-tuned sound with adaptive noise cancelling that adjusts to the room around you. Multipoint pairing switches between laptop and phone without missing a beat.",
    wearables: "Track sleep, steps, heart rate and focus sessions with a battery that lasts the week. Water resistant to 50 m and comfortable enough to forget it is there.",
    workspace: "Workspace furniture with the proportions of a home piece and the ergonomics of a studio one. Hidden cable management keeps every surface clear.",
    home: "Small home devices that quietly make rooms calmer: mist, light and green things, all running on schedules you set once.",
  };
  const FEAT = {
    lighting: ["Tunable white and colour", "Works with voice assistants", "Schedules and scenes", "No hub required"],
    audio: ["Adaptive noise cancelling", "Multipoint Bluetooth 5.3", "Fast charge, 10 min = 5 hrs", "Built-in mics for calls"],
    wearables: ["7 day battery", "Sleep and stress tracking", "Water resistant 5 ATM", "Swim and run modes"],
    workspace: ["Hidden cable management", "Solid wood top", "Memory height presets", "5 year warranty"],
    home: ["App schedules", "Whisper quiet", "Auto shut-off", "Works offline"],
  };
  const MAT = { lighting: "Aluminium and polycarbonate", audio: "Recycled plastic, protein leather", wearables: "Aluminium case, silicone strap", workspace: "Oak veneer, steel frame", home: "Stoneware" };
  products.forEach((x, n) => {
    x.detail = {
      desc: DESC[x.cat] + `\n\n${x.name} sets up in under two minutes over Wi-Fi or Bluetooth and receives free software updates for five years.`,
      bullets: [
        `${x.name} is designed for ${x.skin.join(", ").toLowerCase()} use and pairs with the free lumo app on Android and iOS.`,
        `Works with the voice assistants you already use, no extra hub needed.`,
        `Sets up in under two minutes and keeps working offline if your Wi-Fi drops.`,
        `Covered by a 2 year warranty, with free software updates for 5 years.`,
        `Ships in plastic-free packaging made from 100% recycled fibre.`,
      ],
      features: FEAT[x.cat],
      item: [
        { label: "Material", value: MAT[x.cat] },
        { label: "Colour", value: x.colors.length > 1 ? `${x.colors.length} colours` : "As shown" },
        { label: "Recommended Use", value: x.skin.join(", ") },
        { label: "Handling Time", value: "1 to 2 business days" },
        { label: "Includes", value: `1 x ${x.name}, USB-C cable, quick start guide` },
        { label: "Product Dimensions", value: x.cat === "workspace" ? "140 x 70 x 75 cm" : "18 x 12 x 24 cm" },
        { label: "Package Dimensions", value: x.cat === "workspace" ? "150 x 78 x 18 cm" : "24 x 18 x 30 cm" },
        { label: "Product Weight", value: x.cat === "workspace" ? "28 kg" : `${(0.3 + n * 0.1).toFixed(1)} kg` },
        { label: "Package Weight", value: x.cat === "workspace" ? "34 kg" : `${(0.6 + n * 0.1).toFixed(1)} kg` },
        { label: "HSN Code", value: x.cat === "lighting" ? "94054090" : x.cat === "audio" ? "85183000" : x.cat === "wearables" ? "85176290" : "94036000" },
        { label: "GST", value: "18%" },
        { label: "Return Type", value: "7 day replacement" },
        { label: "Connectivity", value: ["Wi-Fi + Bluetooth", "Bluetooth 5.3", "Wi-Fi 2.4 GHz", "Thread + Matter"][n % 4] },
        { label: "Power", value: x.cat === "audio" || x.cat === "wearables" ? "Rechargeable, USB-C" : "100 to 240 V AC" },
      ],
      warning: x.cat === "lighting" ? "Hard-wired fittings must be installed by a qualified electrician. Switch off mains before fitting." : "Contains a lithium battery. Do not expose to heat above 45°C or puncture.",
      origin: [
        { label: "Country of Origin", value: "India" },
        { label: "Manufacturer Contact", value: "Lumo Devices Pvt. Ltd., Plot 18, Electronic City Phase 2, Bengaluru, Karnataka 560100" },
        { label: "Packer Contact", value: "Lumo Devices Pvt. Ltd., Warehouse 5, Bhiwandi, Thane, Maharashtra 421302" },
        { label: "Importer Contact", value: "Not applicable, made in India" },
        { label: "Guarantee/Warranty", value: x.cat === "workspace" ? "5 year warranty" : "2 year warranty" },
        { label: "Customer Support Number", value: "+91 80 4000 3300" },
        { label: "Customer Support Email", value: "help@lumo.example" },
      ],
      extras: [
        { label: "App", value: "lumo app, Android 9+ and iOS 15+" },
        { label: "Certifications", value: "BIS, CE, RoHS" },
        { label: "Batch Number", value: `LUM-${2611 + n}-B` },
        { label: "Software Updates", value: "Free for 5 years" },
      ],
      badges: [
        { icon: "truck", label: "Free Delivery" },
        { icon: "return", label: "7 Day Replacement", url: "policy.html?type=refund" },
        { icon: "shield", label: "2 Year Warranty" },
        { icon: "card", label: "No Cost EMI" },
      ],
      faqs: [
        { q: `How do I set up ${x.name}?`, a: "Download the lumo app, tap Add device and follow three steps. Most people are done in under two minutes." },
        { q: "Does it work without Wi-Fi?", a: "Yes. Schedules and basic controls keep working locally. Remote control returns when Wi-Fi does." },
        { q: "Which voice assistants does it support?", a: "All major voice assistants and any platform that supports Matter." },
        { q: "Is EMI available?", a: "No cost EMI is available for 3 and 6 months on major cards." },
      ],
      video: n % 3 === 0,
    };
  });

  const heroSlides = [
    { eyebrow: "Smart lighting", title: "Light That Follows Your Day", cta: "Shop Now", img: U(IMG.living4, 2200), pos: "55%", card: { label: "Best Seller", product: "halo-floor-lamp" } },
    { eyebrow: "Focus mode", title: "A Desk That Works As Hard As You", cta: "Shop Workspace", img: U(IMG.desk, 2200), pos: "50%", card: { label: "New Arrival", product: "focus-standing-desk" } },
  ];

  const reels = [
    { img: U(IMG.yellowRoom, 700, 1100), views: "24.8K", product: "halo-floor-lamp" },
    { img: U(IMG.desk, 700, 1100), views: "18.2K", product: "arc-desk-lamp" },
    { img: U(IMG.bedroom3, 700, 1100), views: "41.0K", product: "terra-diffuser" },
    { img: U(IMG.pendants, 700, 1100), views: "12.6K", product: "duo-pendant-set" },
    { img: U(IMG.headphones, 700, 1100), views: "33.4K", product: "pulse-anc-headphones" },
    { img: U(IMG.chairPlant, 700, 1100), views: "9.7K", product: "mesa-smart-planter" },
  ];

  const reviews = [
    { title: "Set up in two minutes", text: "Everything connected quickly, the app is easy to understand and the lamp works reliably. I love that I can control my home even when I'm away.", name: "@riya.homes", city: "Mumbai", avatar: U(IMG.p1, 120, 120), photo: U(IMG.living3, 600, 800), product: "halo-floor-lamp" },
    { title: "Best headphones I have owned", text: "The noise cancelling is genuinely quiet on flights and the battery lasted a whole week of commuting.", name: "@arjun.m", city: "Bengaluru", avatar: U(IMG.p2, 120, 120), photo: U(IMG.desk, 600, 800), product: "pulse-anc-headphones" },
    { title: "Sleeping better already", text: "The sunrise scene on the pendant wakes me gently and the diffuser runs on schedule. Small things, big difference.", name: "@neha.k", city: "Delhi", avatar: U(IMG.p3, 120, 120), photo: U(IMG.bedroom1, 600, 800), product: "orbit-pendant" },
    { title: "My desk finally looks clean", text: "The cable tray hides everything and the height presets are so handy between calls.", name: "@kabir.r", city: "Pune", avatar: U(IMG.p4, 120, 120), photo: U(IMG.deskChairs, 600, 800), product: "focus-standing-desk" },
    { title: "Forget it is on my wrist", text: "Light, accurate and the battery really does last a week. The sleep insights changed my routine.", name: "@meera.i", city: "Chennai", avatar: U(IMG.p5, 120, 120), photo: U(IMG.smartWatch, 600, 800), product: "vita-smartwatch" },
  ];

  const blogs = [
    { slug: "starter-kit", title: "The starter kit for a connected home", tag: "Guide", date: "September 30, 2026", read: "6 min read", img: U(IMG.living3, 1200, 900), excerpt: "Not sure where to begin? These essentials, a lamp, a speaker and a sensor, make a solid first setup." },
    { slug: "room-by-room", title: "Save on lighting with room-by-room scenes", tag: "Guide", date: "September 22, 2026", read: "4 min read", img: U(IMG.yellowRoom, 1200, 900), excerpt: "Smart scenes turn on only the light you need, so rooms stay comfortable and bills stay low." },
    { slug: "blend-in", title: "Tech that blends into your decor", tag: "Design", date: "September 14, 2026", read: "5 min read", img: U(IMG.chairPlant, 1200, 900), excerpt: "Soft edges and calm tones mean your devices sit on a shelf like objects, not gadgets." },
    { slug: "better-sleep", title: "Five settings for better sleep", tag: "Wellness", date: "August 30, 2026", read: "5 min read", img: U(IMG.bedroom2, 1200, 900), excerpt: "Warm light after sunset, a quiet diffuser and a watch that knows when you dozed off." },
    { slug: "focus-desk", title: "Build a desk you want to sit at", tag: "Design", date: "August 18, 2026", read: "3 min read", img: U(IMG.desk, 1200, 900), excerpt: "Height, light and one rule about cables." },
    { slug: "matter-explained", title: "Matter, explained in plain words", tag: "Guide", date: "August 02, 2026", read: "4 min read", img: U(IMG.living5, 1200, 900), excerpt: "Why the new standard means your devices finally talk to each other." },
  ];

  const faqs = [
    { q: "How easy is it to set up the devices?", a: "Every device sets up from the lumo app in three steps. Most customers are done in under two minutes, no hub or tools needed." },
    { q: "Do smart devices really make daily life easier?", a: "Yes. Schedules and scenes take care of small routines, like lights that warm at sunset and a desk that rises when your first meeting starts." },
    { q: "What are the delivery and return policies?", a: "Delivery is free across India in 2 to 5 days. If anything arrives damaged or faulty, we replace it within 7 days." },
    { q: "Do you have a mobile app for device control?", a: "Yes. The free lumo app runs on Android and iOS and controls every device from anywhere." },
    { q: "Do your devices work with voice assistants?", a: "They work with all major voice assistants and any platform that supports Matter." },
    { q: "What happens if my Wi-Fi goes down?", a: "Schedules and local controls keep working. Remote access returns automatically when your connection does." },
  ];

  const why = [
    { title: "Connected System", text: "Every device speaks Matter, so lights, audio and sensors work together from one app.", icon: "shield", img: U(IMG.living4, 1100, 1000) },
    { title: "Seamless Design", text: "Clean lines, soft edges and calm tones that blend effortlessly into any room. Smart tech becomes an elegant part of your interior, not a distraction.", icon: "leaf", img: U(IMG.chairPlant, 1100, 1000) },
    { title: "Trusted Protection", text: "Local processing, encrypted connections and a 2 year warranty on every device.", icon: "heart", img: U(IMG.bedroom3, 1100, 1000) },
    { title: "Adaptive Intelligence", text: "Scenes learn your routine and adjust light, sound and climate before you think to.", icon: "truck", img: U(IMG.living5, 1100, 1000) },
  ];

  window.T3 = { U, IMG, categories, products, heroSlides, reels, reviews, blogs, faqs, why, brands: [], SKIN, brand: "lumo", freeShip: 2999 };
})();
