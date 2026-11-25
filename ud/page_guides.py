"""Built-in page guides for the Business Hub "Help" tab.

One guide per dashboard page: every store page in the sidebar
(``commerce.context.NAV_GROUPS``, same keys) plus the account pages. Each page
of the dashboard links to its own guide (the "Page guide" button in the top
bar opens ``/business-hub/?tab=help&guide=<key>``), so an owner who is stuck
lands on exactly the right instructions.

A guide is plain data:

* ``key``       the sidebar key (``nav_active``) of the page it explains
* ``group``     the sidebar group it sits under
* ``title``     the page name as the sidebar shows it
* ``url_name``  where "Open page" goes (store pages take the store id)
* ``scope``     ``"store"`` (needs a store) or ``"account"``
* ``audience``  ``"all"``, ``"d2c"`` or ``"b2b"``
* ``summary``   one sentence: what the page is for
* ``tasks``     how-tos, each ``{"title", "steps": [...]}``
* ``tips``      short "good to know" points
* ``faqs``      ``{"q", "a"}`` for the questions owners actually ask
* ``related``   keys of related guides
* ``keywords``  extra words the search should match

Text is written for store owners: plain words, the labels they see on screen,
no em dashes.
"""

from __future__ import annotations

GROUPS = [
    ("start", "Getting started"),
    ("storefront", "Storefront"),
    ("operate", "Operate"),
    ("catalogue", "Catalogue"),
    ("grow", "Grow"),
    ("money", "Money & delivery"),
    ("store", "Store"),
    ("account", "Account"),
]

GUIDES: list[dict] = [
    # ==================================================================
    # GETTING STARTED (account level)
    # ==================================================================
    {
        "key": "dashboard",
        "group": "start",
        "title": "Dashboard",
        "url_name": "ud:dashboard",
        "scope": "account",
        "summary": "Your home screen: every store you own or help run, recent activity, and the next steps to get a store selling.",
        "tasks": [
            {
                "title": "Switch between stores",
                "steps": [
                    "Click your account menu at the top right.",
                    "Choose the store you want to work on. The sidebar and every page now follow that store.",
                    "Stores you were added to as staff appear in the same list, showing only the pages you were given.",
                ],
            },
            {
                "title": "Follow the getting-started checklist",
                "steps": [
                    "Find the Getting started card on the Dashboard. It shows six steps: create your account, build your store, add your products, connect your domain, configure payment and shipping, and start selling.",
                    "The next step to do carries a button that opens the right page.",
                    "Each step ticks itself off when it is really done, for example once a product exists or your domain is verified.",
                ],
            },
        ],
        "tips": [
            "Recent activity shows what changed in your store and who did it, so you can see edits made by staff.",
            "The bell at the top shows new orders, requests, replies to your tickets and messages from OneWooz.",
        ],
        "faqs": [
            {"q": "Why can't I see a store I created?", "a": "Check you are signed in with the same email you used to create it. If you were added as staff, the owner must keep your access active on their Team page."},
            {"q": "Where do I manage a store?", "a": "Pick the store from the account menu, then use the sidebar. Every store page is one click away."},
        ],
        "related": ["create", "overview", "business_hub"],
        "keywords": "home start switch store profile checklist activity",
    },
    {
        "key": "create",
        "group": "start",
        "title": "Create a store",
        "url_name": "ud:create_site",
        "scope": "account",
        "summary": "Answer a few questions and OneWooz builds a complete, working store with pages, policies and a checkout already written for your business.",
        "tasks": [
            {
                "title": "Create a new store",
                "steps": [
                    "Choose what you sell as: a brand selling to shoppers (D2C) or a business selling to businesses (B2B).",
                    "Pick your business category and subcategory. This decides the starter text, FAQs and policies.",
                    "Enter your brand name and choose your store address on OneWooz.",
                    "Create the store. It opens with its own sidebar, ready for products.",
                ],
            },
        ],
        "tips": [
            "D2C stores get a modern storefront with cart, checkout, reviews and creator collaborations. B2B stores get a catalogue built around enquiries, specifications and certificates.",
            "Everything written for you can be changed later in Edit content.",
            "Your plan sets how many stores you can create. At the limit, the page offers to add more to your plan.",
        ],
        "faqs": [
            {"q": "Can I change the store address later?", "a": "Your OneWooz address is fixed once created, but you can connect your own domain at any time from Custom domain."},
            {"q": "Can I switch between D2C and B2B?", "a": "The store type is chosen at creation. To sell both ways, create a second store."},
        ],
        "related": ["dashboard", "editor", "products", "domain"],
        "keywords": "new store setup d2c b2b category brand subdomain limit",
    },
    # ==================================================================
    # STOREFRONT
    # ==================================================================
    {
        "key": "editor",
        "group": "storefront",
        "title": "Edit content",
        "url_name": "store:editor",
        "scope": "store",
        "summary": "Change everything shoppers see on your storefront: brand, home sections, blogs, about and contact pages, policies, SEO and more.",
        "tasks": [
            {
                "title": "Set your brand",
                "steps": [
                    "Open the Brand tab.",
                    "Upload your logo, mobile logo, footer logo and favicon, and set their widths.",
                    "Add an announcement bar: one static message, or up to four rotating statements, each with an optional linked word.",
                    "Changes save as you go. Open your store in a new tab to see them live.",
                ],
            },
            {
                "title": "Edit the home page",
                "steps": [
                    "Open Home sections.",
                    "Edit each section's heading, text, images and buttons, and switch sections on or off.",
                    "Use the trash icon on an image to remove it. A removed image is not replaced with a demo picture.",
                ],
            },
            {
                "title": "Write a blog post",
                "steps": [
                    "Open the Blogs tab and add a post.",
                    "Give it a title, cover image and content, and set its status to Published.",
                    "Drafts stay hidden from your storefront until you publish them.",
                ],
            },
            {
                "title": "Set up search engine details (SEO)",
                "steps": [
                    "Open the SEO tab.",
                    "Choose Same for every page to fill titles and descriptions automatically, or Different to set each page yourself.",
                    "Write a clear title (about 60 characters) and description (about 155 characters) that say what you sell.",
                ],
            },
        ],
        "tips": [
            "Contact holds your address, map link, office hours, founder message and grievance officer. A Google Maps link becomes an embedded map automatically.",
            "Policies covers refund, shipping, privacy and terms, plus three custom policies of your own.",
            "Newsletter shows a sign-up popup to visitors and can offer one of your discount codes.",
            "B2B stores get a Certificates tab: each certificate has a name, image and downloadable PDF.",
            "Core pages lets you build extra pages with sub pages and child pages, such as a help section.",
        ],
        "faqs": [
            {"q": "My change isn't showing on the store.", "a": "Refresh the storefront tab. If you use your own domain, also try the OneWooz address to rule out browser caching."},
            {"q": "Some images show sample photos I didn't upload.", "a": "Empty image slots show reference photos so the store looks complete. Upload your own, or use the trash icon to remove the slot's image entirely."},
            {"q": "Where do I add Google Analytics or a Meta Pixel?", "a": "Use Tracking & ads. Custom scripts here are for anything else you have been asked to add."},
        ],
        "related": ["media", "domain", "tracking"],
        "keywords": "content editor theme home logo favicon announcement blog about contact footer social seo policies certificates core pages newsletter popup map",
    },
    {
        "key": "media",
        "group": "storefront",
        "title": "Media library",
        "url_name": "store:media",
        "scope": "store",
        "summary": "Every image and video you have uploaded for this store, in one place to search, reuse and replace.",
        "tasks": [
            {
                "title": "Upload images or videos",
                "steps": [
                    "Choose Upload media, or drag files onto the page.",
                    "Photos are converted to fast-loading WebP automatically, including iPhone HEIC photos.",
                    "Uploaded files are ready to pick anywhere: products, categories, banners and Edit content.",
                ],
            },
            {
                "title": "Reuse an image",
                "steps": [
                    "On any image field in the dashboard, open the picker.",
                    "Search by file name and select the image. The same file is reused, not uploaded again.",
                ],
            },
        ],
        "tips": [
            "Use clear file names like indigo-kurta-front.jpg. They make search easy and help search engines.",
            "Square or 4:5 photos look best on product pages; wide 16:9 images suit banners.",
        ],
        "faqs": [
            {"q": "Why does my photo look different after upload?", "a": "It was converted to WebP for speed and turned upright if your phone stored it sideways. Quality is kept high."},
        ],
        "related": ["editor", "products"],
        "keywords": "images photos videos upload webp heic gallery files",
    },
    {
        "key": "domain",
        "group": "storefront",
        "title": "Custom domain",
        "url_name": "store:domain",
        "scope": "store",
        "summary": "Put your store on your own web address, such as yourbrand.com, with a free security certificate (HTTPS).",
        "tasks": [
            {
                "title": "Connect your domain",
                "steps": [
                    "Enter your domain, for example yourbrand.com or shop.yourbrand.com.",
                    "At your domain provider (GoDaddy, Hostinger, Namecheap and so on), add the TXT record shown. It proves you own the domain.",
                    "Add the A record shown. It points the domain at your store.",
                    "Optionally add the www CNAME so www.yourbrand.com opens your store too.",
                    "Come back and choose Verify & go live. HTTPS is set up automatically once it passes.",
                ],
            },
        ],
        "tips": [
            "Use the Copy buttons. One wrong character is the most common reason verification fails.",
            "Set the TTL to 300, or the lowest your provider allows, so changes show up faster.",
            "Watch the tutorial video on the page for a walkthrough.",
        ],
        "faqs": [
            {"q": "Verification keeps failing.", "a": "Records can take from a few minutes up to 24 hours to spread. Check you added them at the provider that actually runs your DNS, that there is no old A record left beside the new one, and that you did not type the full domain in a Host box that adds it automatically. Then choose Re-check."},
            {"q": "Will my OneWooz address stop working?", "a": "No. Both work, and visitors are sent to your own domain."},
            {"q": "Payments fail after connecting my domain.", "a": "Add your new domain to the allowed (whitelisted) websites in your Cashfree or Razorpay dashboard."},
        ],
        "related": ["payments", "google_merchant", "settings"],
        "keywords": "domain dns txt a record cname www https ssl godaddy hostinger verify",
    },
    # ==================================================================
    # OPERATE
    # ==================================================================
    {
        "key": "overview",
        "group": "operate",
        "title": "Overview",
        "url_name": "store:overview",
        "scope": "store",
        "summary": "Today's snapshot of the store: revenue, orders, what needs your attention and the latest orders.",
        "tasks": [
            {
                "title": "Start your day here",
                "steps": [
                    "Check Needs attention for orders to accept, requests to answer and products running out.",
                    "Click any item to jump to the page where you can act on it.",
                    "Use Recent orders to open the newest orders directly.",
                ],
            },
        ],
        "tips": [
            "Revenue counts every placed order except cancelled ones, so cash on delivery orders are included before they are paid.",
            "For trends over weeks or months, use Analytics.",
        ],
        "faqs": [
            {"q": "Why doesn't revenue match my bank account?", "a": "Overview counts orders when they are placed. Cash on delivery money arrives after delivery, and gateway payouts follow your gateway's settlement schedule."},
        ],
        "related": ["orders", "analytics", "out_of_stock"],
        "keywords": "summary kpi revenue today attention snapshot",
    },
    {
        "key": "orders",
        "group": "operate",
        "title": "Orders",
        "url_name": "store:orders",
        "scope": "store",
        "summary": "Accept, pack, ship and track every order. Orders move through tabs from Pending to Delivered.",
        "tasks": [
            {
                "title": "Accept and ship an order",
                "steps": [
                    "New orders arrive in Pending. Open one to check the items, address and payment method.",
                    "Accept it (or select several and choose Accept Selected). It moves to Ready to Ship and, with a courier connected, a shipment is booked.",
                    "In Ready to Ship, select the orders and choose Generate & Request Pickup.",
                    "Pick a pickup date and label size (4x6 for thermal printers, A4 for office printers). The labels download and the orders move to Pickup Scheduled.",
                    "Tracking updates arrive from the courier automatically. The shopper is emailed when the parcel ships and when it is delivered.",
                ],
            },
            {
                "title": "Edit an order before it ships",
                "steps": [
                    "Open the order and choose Edit.",
                    "Add or remove items. Totals and stock recalculate.",
                    "Save. The shopper is emailed the old and new item list, and the courier booking is updated.",
                ],
            },
            {
                "title": "Cancel an order",
                "steps": [
                    "Select the order and choose Cancel (or Cancel Selected for several).",
                    "Items go back into stock and the courier booking is cancelled.",
                    "The shopper is emailed that the order was cancelled.",
                ],
            },
            {
                "title": "Export orders for your accountant",
                "steps": [
                    "Choose Export orders.",
                    "Pick a month and, optionally, a billing state.",
                    "The Excel file lists every line with GST split, discounts, store credit, Amount Payable and Amount Received, plus key figures at the top.",
                ],
            },
        ],
        "tips": [
            "Online orders appear only after the payment is confirmed, so abandoned payments never clutter the list.",
            "RTO lists parcels the courier returned to you. Their items are put back into stock automatically.",
            "Self-ship stores get Generate Label instead of a pickup request.",
            "Reprint a label from the printer icon in Pickup Scheduled, Shipped or later tabs.",
        ],
        "faqs": [
            {"q": "Generate & Request Pickup failed.", "a": "Open Shipping and check your courier is connected, a pickup location is chosen and your Shiprocket wallet has balance. The error message names the order that failed."},
            {"q": "An order is stuck in Pickup Scheduled.", "a": "The courier has not collected it yet. Use Sync now on the Shipping page to pull the latest status, or contact the courier with the AWB number."},
            {"q": "Why is an order missing from the list?", "a": "Check the date filter and the search box, and look in the Cancelled and RTO tabs."},
        ],
        "related": ["shipping", "requests", "payments", "customers"],
        "keywords": "accept ship pickup label awb courier shiprocket delhivery tracking cancel edit export excel rto pending delivered invoice",
    },
    {
        "key": "requests",
        "group": "operate",
        "title": "Requests",
        "url_name": "store:requests",
        "scope": "store",
        "summary": "Cancellations, returns, replacements, warranty claims and support questions that shoppers raise on their orders.",
        "tasks": [
            {
                "title": "Approve or reject a request",
                "steps": [
                    "Filter by type and status to find open requests.",
                    "Read the reason and any photos the shopper attached.",
                    "Approve to act on it, or Reject with a short reply explaining why.",
                ],
            },
            {
                "title": "Replace a delivered product",
                "steps": [
                    "Approve the replacement request.",
                    "A replacement order is created with the same order number, shipped prepaid at no charge, and it appears in Orders under Pending.",
                    "Ship it like any other order.",
                ],
            },
            {
                "title": "Answer a support question",
                "steps": [
                    "Open the question and type Your reply.",
                    "Choose Send reply. The shopper is emailed your answer.",
                    "Use Mark resolved without reply when it was handled another way.",
                ],
            },
        ],
        "tips": [
            "Shoppers can ask to cancel only until the parcel ships, and to replace only within the product's replacement window.",
            "Replacement orders carry no commerce fee and are not counted as revenue.",
        ],
        "faqs": [
            {"q": "How long is the replacement window?", "a": "It is set per product on the product form. The shopper can request within that many days of delivery."},
        ],
        "related": ["orders", "customers"],
        "keywords": "return replacement cancellation refund warranty support complaint",
    },
    {
        "key": "enquiries",
        "group": "operate",
        "title": "Enquiries",
        "url_name": "store:enquiries",
        "scope": "store",
        "summary": "Messages from your storefront's contact form and, for B2B stores, quote requests with product and quantity.",
        "tasks": [
            {
                "title": "Reply to an enquiry",
                "steps": [
                    "Open Unread to see new messages first.",
                    "Read the message and contact the person by phone or email from the details shown.",
                    "Choose Mark as read, or Delete spam.",
                ],
            },
        ],
        "tips": [
            "Reply the same day. The first supplier to send a clear quote often wins the order.",
            "For B2B quotes, send the full landed price in one message: unit price, GST, freight and lead time.",
        ],
        "faqs": [
            {"q": "I'm not receiving enquiries.", "a": "Check the Support email in Settings, then send a test from your own contact page. Shoppers must tick the privacy consent box before sending."},
        ],
        "related": ["customers", "settings"],
        "keywords": "contact form message quote rfq lead b2b",
    },
    {
        "key": "customers",
        "group": "operate",
        "title": "Customers",
        "url_name": "store:customers",
        "scope": "store",
        "summary": "Everyone who has ordered on your store, how much they have spent, their order history and store credit.",
        "tasks": [
            {
                "title": "See a customer's history",
                "steps": [
                    "Search by name, email or phone, or filter by category or product bought.",
                    "Choose View to see their details and every order in a list you can open one at a time.",
                ],
            },
            {
                "title": "Give store credit",
                "steps": [
                    "Open the customer and choose Add credit, with an amount and a note.",
                    "To reward everyone at once, use Bulk credits. The Given to all tab lists those grants and lets you remove one.",
                    "Credit applies automatically at checkout once the shopper verifies their email with a code.",
                ],
            },
            {
                "title": "Message all customers",
                "steps": [
                    "Choose Notify all to send a short update to every customer.",
                    "For designed campaigns with tracking, use Email marketing instead.",
                ],
            },
        ],
        "tips": [
            "A customer who deletes their account shows as Account deleted. Their orders stay in your records.",
            "Store credit used on a cancelled order is given back automatically.",
        ],
        "faqs": [
            {"q": "Why isn't a customer's credit applied?", "a": "Credit applies only after the shopper verifies their email at checkout, so nobody can spend someone else's credit."},
        ],
        "related": ["orders", "email_marketing", "subscribers"],
        "keywords": "buyers shoppers history spent store credit bulk credit notify",
    },
    {
        "key": "subscribers",
        "group": "operate",
        "title": "Subscribers",
        "url_name": "store:subscribers",
        "scope": "store",
        "summary": "Everyone who joined your newsletter from the storefront: the footer sign-up and the newsletter popup.",
        "tasks": [
            {
                "title": "Grow and use your list",
                "steps": [
                    "Switch on the newsletter popup in Edit content, Newsletter tab, optionally with a discount code as a thank-you.",
                    "Use the tabs to see who joined from the popup and who joined from other forms.",
                    "Send them a campaign from Email marketing, choosing Subscribers as the audience.",
                ],
            },
        ],
        "tips": [
            "Anyone who unsubscribes from an email is never emailed by your store again.",
            "Use the download button to get the list as a file if you need it elsewhere.",
        ],
        "faqs": [
            {"q": "Someone says they subscribed but isn't listed.", "a": "Ask them to try again and look for an error on the form. A browser that blocks cookies can stop the form from sending."},
        ],
        "related": ["email_marketing", "editor", "coupons"],
        "keywords": "newsletter email list signup popup audience",
    },
    # ==================================================================
    # CATALOGUE
    # ==================================================================
    {
        "key": "products",
        "group": "catalogue",
        "title": "Products",
        "url_name": "store:products",
        "scope": "store",
        "summary": "Add and edit what you sell: photos, prices, GST, stock, variations and the details shown on each product page.",
        "tasks": [
            {
                "title": "Add a product",
                "steps": [
                    "Choose + Create Product. Only the name and price are required.",
                    "Add images. Each image block is a fixed position, and block 1 is the cover photo used in listings and shares.",
                    "Set the price and, if you show a discount, the compare-at price.",
                    "Tick Price includes GST if the price you typed already contains tax.",
                    "Pick a category and enter stock. A blank stock box means zero, which shows as sold out.",
                    "Save. Hover the ! icon beside any field to see what it does on the storefront.",
                ],
            },
            {
                "title": "Sell sizes or colours (variations)",
                "steps": [
                    "Switch on Variations and pick a preset such as Size or Colour, or type your own.",
                    "Add the values, then fill in price, stock and SKU for each combination.",
                    "Give a variation its own photos so the page swaps to them when a shopper picks it.",
                    "Use the numbered variation tabs to give one variation its own details, such as a different material.",
                ],
            },
        ],
        "tips": [
            "Renaming a product updates its web address, and the old address keeps working by redirecting.",
            "Set the replacement window per product. Shoppers can request a replacement within that many days of delivery.",
            "Out-of-stock products cannot be added to the cart or ordered.",
            "D2C product pages show a 30-day price history so shoppers can trust your discounts.",
        ],
        "faqs": [
            {"q": "The stock box is locked.", "a": "Variations are on, so stock lives on each variation. The locked box shows the total."},
            {"q": "Why does my product show as sold out?", "a": "Its stock, or the selected variation's stock, is zero or blank. Update it here or in Inventory."},
            {"q": "How do I show the variation SKU on labels?", "a": "Enter a SKU for each variation. It prints on the shipping label automatically."},
        ],
        "related": ["categories", "inventory", "media", "google_merchant"],
        "keywords": "add product price mrp compare gst sku variation size colour images stock tooltip replacement",
    },
    {
        "key": "categories",
        "group": "catalogue",
        "title": "Categories",
        "url_name": "store:categories",
        "scope": "store",
        "summary": "Group your products for the menu, the shop page and the home page.",
        "tasks": [
            {
                "title": "Create a category",
                "steps": [
                    "Choose + Add Category and give it a clear name, such as Kurtas or Face serums.",
                    "Add a cover image. It is used on the home page and when the link is shared.",
                    "Save, then assign products to it from the product form.",
                ],
            },
        ],
        "tips": [
            "Name categories by what the product is, not by a campaign. Use Discounts for sales.",
            "Keep top-level categories to about eight so the menu fits on one line.",
            "Renaming is safe: the old web address redirects to the new one.",
        ],
        "faqs": [
            {"q": "A category isn't showing on the store.", "a": "It needs at least one product that is in stock and visible."},
        ],
        "related": ["products", "editor"],
        "keywords": "collection group menu shop category image",
    },
    {
        "key": "inventory",
        "group": "catalogue",
        "title": "Inventory",
        "url_name": "store:inventory",
        "scope": "store",
        "summary": "See and update stock for every product and variation, alongside how many sold this month.",
        "tasks": [
            {
                "title": "Update stock",
                "steps": [
                    "Find the product or variation in the list.",
                    "Edit Available Qty and choose Save.",
                    "Use Sold This Month to decide what to reorder.",
                ],
            },
        ],
        "tips": [
            "Stock goes down when an order is placed (cash on delivery) or paid (online), and comes back when an order is cancelled before dispatch or returned (RTO).",
        ],
        "faqs": [
            {"q": "Stock went down but I didn't sell anything.", "a": "Check Orders for new orders, including ones still in Pending. Stock is reserved as soon as an order is placed."},
        ],
        "related": ["out_of_stock", "products"],
        "keywords": "stock quantity warehouse reorder sold month",
    },
    {
        "key": "out_of_stock",
        "group": "catalogue",
        "title": "Out of stock",
        "url_name": "store:out_of_stock",
        "scope": "store",
        "summary": "Products and individual sizes or colours that need restocking before shoppers stop seeing them.",
        "tasks": [
            {
                "title": "Restock quickly",
                "steps": [
                    "Review the list. A sold-out size or colour appears even when other variations still have stock.",
                    "Open the product or go to Inventory and enter the new quantity.",
                    "The item disappears from this list once it has stock again.",
                ],
            },
        ],
        "tips": [
            "The sidebar counter tells you when something new runs out.",
        ],
        "faqs": [],
        "related": ["inventory", "products"],
        "keywords": "sold out restock zero stock threshold",
    },
    {
        "key": "reviews",
        "group": "catalogue",
        "title": "Reviews",
        "url_name": "store:reviews",
        "scope": "store",
        "audience": "d2c",
        "summary": "Approve, hide and reply to the product reviews shoppers leave on your D2C store.",
        "tasks": [
            {
                "title": "Moderate reviews",
                "steps": [
                    "Open Pending to see new reviews.",
                    "Approve genuine reviews so they appear on the product page.",
                    "Check Reported for reviews shoppers flagged, and remove anything abusive.",
                ],
            },
        ],
        "tips": [
            "Ask your first buyers for a review and a photo. Reviews persuade new shoppers more than any ad.",
            "Keep honest critical reviews. A few make the positive ones more believable.",
        ],
        "faqs": [],
        "related": ["products", "customers"],
        "keywords": "ratings stars feedback moderate approve report",
    },
    # ==================================================================
    # GROW
    # ==================================================================
    {
        "key": "analytics",
        "group": "grow",
        "title": "Analytics",
        "url_name": "store:analytics",
        "scope": "store",
        "summary": "How shoppers find and use your store: traffic, conversion, revenue, products, devices and page speed.",
        "tasks": [
            {
                "title": "Find out what is working",
                "steps": [
                    "Choose a Date range at the top.",
                    "Acquisition channels shows where visitors came from; Campaigns shows your tagged links.",
                    "The Conversion funnel shows where shoppers drop off between viewing, carting and buying.",
                    "Revenue by category and Discount performance show what earns the most.",
                ],
            },
        ],
        "tips": [
            "Core web vitals and Load-speed spread show how fast your store loads for real visitors.",
            "Tag the links you share (for example with utm_source=instagram) so they appear under Campaigns.",
        ],
        "faqs": [
            {"q": "Analytics says no activity yet.", "a": "Data appears as visitors arrive. Widen the date range, and check that your store is live and not in maintenance mode."},
        ],
        "related": ["overview", "tracking", "coupons"],
        "keywords": "traffic visitors conversion funnel revenue reports devices speed web vitals campaigns utm",
    },
    {
        "key": "email_marketing",
        "group": "grow",
        "title": "Email marketing",
        "url_name": "store:email_marketing",
        "scope": "store",
        "summary": "Send designed emails to your customers and subscribers from your brand name, and see who opened and clicked.",
        "tasks": [
            {
                "title": "Send a campaign",
                "steps": [
                    "Create a campaign and give it a subject line that says what's inside.",
                    "Choose the audience: subscribers, customers, or a list.",
                    "Fill in the promo email design (heading, image, text and button), or pick a template.",
                    "Preview it, then choose Send / schedule to send now or at a set time.",
                ],
            },
            {
                "title": "Set up an automatic series (drip)",
                "steps": [
                    "Create a drip campaign.",
                    "Add each email with the number of days after joining to send it.",
                    "Switch it on. New audience members receive the series automatically.",
                ],
            },
            {
                "title": "Read the results",
                "steps": [
                    "Open a campaign to see Sent, Delivery, Opened and Clicked.",
                    "The Engagement funnel shows where readers drop off. Reuse the subject lines and offers that work.",
                ],
            },
        ],
        "tips": [
            "One message per email works best: a launch or a sale, not both.",
            "Every email includes an unsubscribe link. People who unsubscribe are never emailed again.",
            "Your plan sets how many emails you can send. The page shows what's left.",
        ],
        "faqs": [
            {"q": "My campaign hasn't sent yet.", "a": "Campaigns go out in batches within a few minutes of their scheduled time. Large lists take longer."},
        ],
        "related": ["subscribers", "customers", "coupons"],
        "keywords": "newsletter campaign broadcast drip automation template open rate click unsubscribe",
    },
    {
        "key": "coupons",
        "group": "grow",
        "title": "Discounts",
        "url_name": "store:coupons",
        "scope": "store",
        "summary": "Create discount codes and short flash deals, and see how often each one is used.",
        "tasks": [
            {
                "title": "Create a discount code",
                "steps": [
                    "Choose New discount.",
                    "Set the code, the value (fixed amount or percentage) and any minimum order or product it applies to.",
                    "Set start and end dates, then save. Shoppers enter the code at checkout.",
                ],
            },
            {
                "title": "Run a flash deal",
                "steps": [
                    "Choose Instant discount.",
                    "Choose a cart-value or single-product deal and a duration: 1 hour, 4 hours or 1 day.",
                    "A unique code is generated. Copy it and share it. It switches itself off when time runs out.",
                ],
            },
        ],
        "tips": [
            "Use a minimum cart value to raise order size rather than just lowering prices.",
            "Pick one of your codes as the newsletter popup offer to grow your list.",
        ],
        "faqs": [
            {"q": "A shopper says the code doesn't work.", "a": "Check the dates, the minimum order value and which products it applies to. Expired instant discounts cannot be extended; create a new one."},
        ],
        "related": ["email_marketing", "analytics", "subscribers"],
        "keywords": "coupon code offer sale flash deal instant percentage fixed promo",
    },
    {
        "key": "collaborations",
        "group": "grow",
        "title": "Collaborations",
        "url_name": "store:collaborations",
        "scope": "store",
        "audience": "d2c",
        "summary": "Work with creators who sell your products from their own storefront and earn a commission on each order.",
        "tasks": [
            {
                "title": "Invite a creator",
                "steps": [
                    "Choose Add Collaboration and enter their name, email and social handle.",
                    "Set the commission as a percentage or fixed amount per order.",
                    "Choose Send Invite. They receive an email with a contract link to accept.",
                ],
            },
            {
                "title": "Pay a creator",
                "steps": [
                    "Payout requests from creators appear here and on the bell.",
                    "Check their earnings with View earnings.",
                    "Pay them, then choose Mark paid (or Record payout). The creator is emailed.",
                ],
            },
        ],
        "tips": [
            "Creators with a few thousand engaged followers in your niche often sell better than big accounts.",
            "Orders through a creator's storefront are paid on your own store, so the money comes to you.",
        ],
        "faqs": [
            {"q": "A creator didn't get the invite.", "a": "Ask them to check spam, then use Resend invite or Copy contract link and send it yourself."},
        ],
        "related": ["orders", "coupons"],
        "keywords": "creator influencer ugc affiliate commission payout contract invite",
    },
    {
        "key": "tracking",
        "group": "grow",
        "title": "Tracking & ads",
        "url_name": "store:tracking",
        "scope": "store",
        "summary": "Add your analytics and advertising tags so your ad campaigns can measure sales and retarget visitors.",
        "tasks": [
            {
                "title": "Add your tags",
                "steps": [
                    "Paste the IDs you have: Google Analytics (G-...), Google Tag Manager (GTM-...), Google Ads, Meta Pixel, TikTok, Pinterest or Microsoft (UET).",
                    "For Meta, add the Conversions API token as well so purchases are counted even when browsers block the pixel.",
                    "Choose Save tags. They load on every page of your storefront.",
                ],
            },
        ],
        "tips": [
            "Paste just the ID, not the whole code snippet, unless the field asks for HTML.",
            "Use Custom head HTML only for a tag that isn't listed.",
            "Check the tag is firing with the provider's own helper, such as Meta Pixel Helper or Google Tag Assistant.",
        ],
        "faqs": [
            {"q": "Meta shows no purchases.", "a": "Make sure the Pixel ID and Conversions API token belong to the same pixel, then place a test order and check Events Manager after a few minutes."},
        ],
        "related": ["analytics", "google_merchant", "editor"],
        "keywords": "google analytics ga4 gtm tag manager meta facebook pixel capi tiktok pinterest bing uet ads conversion",
    },
    {
        "key": "google_merchant",
        "group": "grow",
        "title": "Google Merchant",
        "url_name": "store:google_merchant",
        "scope": "store",
        "summary": "Show your products on Google Shopping, free listings and Search, kept in sync with your store automatically.",
        "tasks": [
            {
                "title": "Connect and list your products",
                "steps": [
                    "Read Before you connect and make sure every product has an image, a price and stock.",
                    "Choose Connect Google Merchant Center and sign in with the Google account that should own it.",
                    "Choose your Merchant Center account. OneWooz verifies your store address and sends your products, including every variation.",
                    "Set the target country and language, and keep Keep Google in sync automatically switched on.",
                ],
            },
        ],
        "tips": [
            "Google checks your refund, shipping and contact pages. Fill them in Edit content before connecting.",
            "Use Refresh statuses to see which products Google approved and why any were rejected.",
            "Use Re-send all after big changes to your catalogue.",
        ],
        "faqs": [
            {"q": "Products are disapproved.", "a": "Open the product status for Google's reason. Common causes: missing image, price mismatch, or missing shipping and return information."},
        ],
        "related": ["products", "domain", "tracking"],
        "keywords": "google shopping merchant center free listings feed sync products search",
    },
    # ==================================================================
    # MONEY & DELIVERY
    # ==================================================================
    {
        "key": "payments",
        "group": "money",
        "title": "Payments",
        "url_name": "store:payments",
        "scope": "store",
        "summary": "Choose how shoppers pay: online through your own gateway, cash on delivery, or part now and the rest on delivery.",
        "tasks": [
            {
                "title": "Connect Razorpay or Cashfree",
                "steps": [
                    "Choose Edit, then pick your gateway under Payment gateway.",
                    "Paste the Key ID (or App ID) and Secret from your gateway's dashboard.",
                    "Switch it on and choose Save payment settings.",
                    "Place a small test order to confirm the payment page opens.",
                ],
            },
            {
                "title": "Offer cash on delivery and part payment",
                "steps": [
                    "Under Checkout options, switch on cash on delivery.",
                    "Switch on part payment and set a fixed amount or a percentage paid online now.",
                    "Checkout then shows, for example, Pay 199 now, rest on COD.",
                ],
            },
            {
                "title": "Set your invoice details",
                "steps": [
                    "Fill in Invoice identity: company name, GSTIN, state, pincode, mobile, email and address.",
                    "These print on every tax invoice your customers download.",
                ],
            },
        ],
        "tips": [
            "Money goes straight from the shopper to your gateway account. OneWooz never holds it.",
            "Your keys are encrypted when saved and never shown in full again.",
            "Part payment reduces refused cash on delivery parcels without losing COD buyers.",
        ],
        "faqs": [
            {"q": "Payments fail on my own domain.", "a": "Add the domain to the whitelisted websites in your gateway dashboard."},
            {"q": "An online order is missing.", "a": "Online orders appear only after the gateway confirms payment. If the shopper's money was taken but there's no order, check the payment in your gateway dashboard and contact us with the payment ID."},
        ],
        "related": ["orders", "settings", "domain"],
        "keywords": "razorpay cashfree gateway cod cash on delivery partial payment keys invoice gstin",
    },
    {
        "key": "shipping",
        "group": "money",
        "title": "Shipping",
        "url_name": "store:shipping",
        "scope": "store",
        "summary": "Connect your courier, set your pickup warehouses and keep tracking in sync.",
        "tasks": [
            {
                "title": "Connect Shiprocket",
                "steps": [
                    "Under Carrier, choose Shiprocket.",
                    "Enter your Shiprocket API email and password (create an API user in Shiprocket under Settings, API).",
                    "Choose Load pickup locations and pick your pickup location from the list.",
                    "Leave courier selection on Automatic to let Shiprocket pick the recommended courier, then Save carrier.",
                ],
            },
            {
                "title": "Turn on live tracking",
                "steps": [
                    "Copy the URL and token from the Tracking webhook card.",
                    "Paste them into Shiprocket's webhook settings.",
                    "Status updates now arrive automatically. Use Sync now from Shiprocket to pull them on demand.",
                ],
            },
            {
                "title": "Add a warehouse",
                "steps": [
                    "Choose Add warehouse and enter the address, contact name and phone.",
                    "Mark one warehouse as the default for pickups.",
                ],
            },
        ],
        "tips": [
            "Keep your Shiprocket wallet topped up. You get an email and a notification when it drops below 100.",
            "Choose Manual / self-ship if you deliver yourself. You still get printable labels.",
            "API passwords and tokens are encrypted when saved.",
        ],
        "faqs": [
            {"q": "Pickup locations won't load.", "a": "Check the API email and password are for an API user, not your normal Shiprocket login, and that a pickup address exists in Shiprocket."},
            {"q": "Tracking isn't updating.", "a": "Check the webhook URL and token in Shiprocket match this page, or regenerate the token and paste it again."},
        ],
        "related": ["orders", "payments"],
        "keywords": "courier shiprocket delhivery self ship warehouse pickup location webhook tracking wallet awb",
    },
    {
        "key": "bills",
        "group": "money",
        "title": "Bills",
        "url_name": "store:bills",
        "scope": "store",
        "summary": "What your store owes OneWooz: the commerce fee on delivered orders, rolled into a bill you pay each period.",
        "tasks": [
            {
                "title": "Pay a bill",
                "steps": [
                    "Open the bill to see the delivered orders it covers and the amount including GST.",
                    "Download the Excel export to check orders line by line.",
                    "Choose Pay, pay by bank transfer or UPI QR, and upload the payment screenshot.",
                ],
            },
        ],
        "tips": [
            "The commerce fee is charged only on delivered orders. Cancelled, returned and replacement orders are never charged.",
            "The fee per unit depends on the product's price range in your plan. GST at 18% is added.",
        ],
        "faqs": [
            {"q": "Why is an order on my bill?", "a": "It was delivered in that period. Open Delivered orders on the bill to see each one and its fee."},
        ],
        "related": ["upgrade", "orders"],
        "keywords": "commerce fee invoice bill pay gst upi bank screenshot",
    },
    # ==================================================================
    # STORE
    # ==================================================================
    {
        "key": "settings",
        "group": "store",
        "title": "Settings",
        "url_name": "store:settings",
        "scope": "store",
        "summary": "Your store's identity, how customers reach you, your business and tax details, and store-wide switches.",
        "tasks": [
            {
                "title": "Fill in business and tax details",
                "steps": [
                    "Choose Edit to unlock the form.",
                    "Under Business & tax details, enter your legal business name, GSTIN, PAN and MSME (Udyam) number.",
                    "Upload your authorised signature. It prints on shipping labels and invoices.",
                    "Choose Save settings.",
                ],
            },
            {
                "title": "Pause the store for maintenance",
                "steps": [
                    "Under Store controls, switch on maintenance mode.",
                    "Shoppers see a short notice instead of the store. Switch it off to reopen.",
                ],
            },
        ],
        "tips": [
            "Support contact is the email and phone shown to shoppers and used for enquiries.",
            "Send abandoned cart emails (Store controls) reminds signed-in shoppers who left items in their cart: 1, 6, 24, 48 and 72 hours after their last cart change. Reminders stop when they click one, place an order, or after 72 hours.",
            "Settings are owner-only. Staff cannot open this page.",
        ],
        "faqs": [
            {"q": "My GSTIN is rejected.", "a": "A GSTIN has 15 characters and contains your PAN. Check for a mistyped letter or number."},
        ],
        "related": ["payments", "staff", "domain"],
        "keywords": "store name tagline currency country support email phone legal name gstin pan msme udyam signature maintenance abandoned cart",
    },
    # ==================================================================
    # ACCOUNT
    # ==================================================================
    {
        "key": "staff",
        "group": "account",
        "title": "Staff & admins",
        "url_name": "store:staff",
        "scope": "store",
        "summary": "Add people to help run your store, each with access to only the pages and actions you choose.",
        "tasks": [
            {
                "title": "Add a team member",
                "steps": [
                    "Open your account menu at the top right and choose Staff & admins.",
                    "Choose Add a team member and enter their name and email.",
                    "Tick the pages they may use, and for each page whether they can view, create, edit or delete.",
                    "Choose Save access. They sign in with their own account using that email.",
                ],
            },
        ],
        "tips": [
            "Staff never see Settings or the Team page.",
            "Your plan sets how many team members you can add. The page shows how many you are using.",
            "Remove member takes away access immediately.",
        ],
        "faqs": [
            {"q": "My team member can't see the store.", "a": "They must sign in with exactly the email you added. Then they pick your store from their account menu."},
        ],
        "related": ["settings", "security"],
        "keywords": "staff team member admin access permissions roles employee",
    },
    {
        "key": "security",
        "group": "account",
        "title": "Security",
        "url_name": "accounts:security",
        "scope": "account",
        "summary": "Protect your account: two-step verification, passkeys, trusted devices, active sessions and sign-in history.",
        "tasks": [
            {
                "title": "Turn on two-step verification",
                "steps": [
                    "Under Two-step verification, scan the QR code with an authenticator app such as Google Authenticator.",
                    "Enter the 6-digit code and choose Confirm and turn on.",
                    "No authenticator app? Choose Use email instead to receive codes by email.",
                ],
            },
            {
                "title": "Sign in with a passkey",
                "steps": [
                    "Choose Add passkey and follow your device's prompt (fingerprint, face or screen lock).",
                    "Next time, sign in with the passkey instead of a password.",
                ],
            },
            {
                "title": "Sign out other devices",
                "steps": [
                    "Check Active sessions for any device you don't recognise.",
                    "Choose Sign out everywhere else, then change your password if something looked wrong.",
                ],
            },
        ],
        "tips": [
            "Login activity lists recent sign-ins with approximate location, so you can spot anything unusual.",
            "Your plan may limit how many devices can be signed in at the same time.",
        ],
        "faqs": [
            {"q": "I lost my phone with the authenticator app.", "a": "Sign in with a passkey or email code if you set one up, then set two-step verification up again. Otherwise raise a ticket from the Business Hub."},
        ],
        "related": ["staff", "dashboard"],
        "keywords": "password two factor 2fa totp authenticator passkey sessions devices login history",
    },
    {
        "key": "upgrade",
        "group": "account",
        "title": "Upgrade plan",
        "url_name": "ud:upgrade",
        "scope": "account",
        "summary": "See your current plan, compare plans and unlock more stores, team members, email sends and features.",
        "tasks": [
            {
                "title": "Change your plan",
                "steps": [
                    "Review Your current plan and what it includes.",
                    "Pick a plan and duration. Enter a discount code if you have one.",
                    "Read the user agreement, choose Confirm your plan and pay.",
                ],
            },
        ],
        "tips": [
            "Pages marked Upgrade in the sidebar are not in your current plan.",
            "Your plan's terms are fixed when you buy it, so later price changes don't affect you until you renew.",
            "Your subscription bill is on your Profile page, in the account menu.",
        ],
        "faqs": [
            {"q": "What happens when my plan expires?", "a": "The dashboard asks you to renew before you continue. Your store data is kept."},
        ],
        "related": ["bills", "dashboard"],
        "keywords": "plan pricing subscription renew upgrade downgrade free locked features",
    },
    {
        "key": "business_hub",
        "group": "account",
        "title": "Business hub",
        "url_name": "ud:business_hub",
        "scope": "account",
        "summary": "News, help and important updates from OneWooz, these page guides, and support tickets with our team.",
        "tasks": [
            {
                "title": "Raise a support ticket",
                "steps": [
                    "Fill in Raise a ticket: subject, category, priority, store and a clear description.",
                    "Attach a screenshot if it helps (up to 10 MB) and choose Submit ticket.",
                    "Our reply arrives under View tickets, on the bell and by email. Reply on the ticket to follow up.",
                ],
            },
        ],
        "tips": [
            "The Page guide button at the top of every dashboard page opens that page's guide here.",
            "Important posts are announcements that may need action, such as policy or pricing changes.",
        ],
        "faqs": [],
        "related": ["dashboard", "security"],
        "keywords": "support ticket help news announcement contact onewooz team",
    },
]

_BY_KEY = {g["key"]: g for g in GUIDES}
_GROUP_LABEL = dict(GROUPS)


def get(key: str | None) -> dict | None:
    return _BY_KEY.get(key or "")


def _search_text(guide: dict) -> str:
    parts = [guide["title"], guide["summary"], guide.get("keywords", ""), _GROUP_LABEL.get(guide["group"], "")]
    for task in guide.get("tasks", []):
        parts.append(task["title"])
        parts.extend(task["steps"])
    parts.extend(guide.get("tips", []))
    for faq in guide.get("faqs", []):
        parts.extend((faq["q"], faq["a"]))
    return " ".join(parts).lower()


_SEARCH = {g["key"]: _search_text(g) for g in GUIDES}


def matches(guide: dict, query: str) -> bool:
    """Every word of ``query`` appears somewhere in the guide."""
    text = _SEARCH[guide["key"]]
    return all(word in text for word in query.lower().split())


def grouped(query: str = "") -> list[dict]:
    """Guides in sidebar order, grouped, filtered by ``query``. Each guide gets
    ``related_guides`` (key + title) for its "Related" links."""
    out = []
    for group_key, label in GROUPS:
        items = [
            {
                **g,
                "group_label": label,
                "related_guides": [
                    {"key": k, "title": _BY_KEY[k]["title"]} for k in g.get("related", []) if k in _BY_KEY
                ],
                "audience": g.get("audience", "all"),
                "search_text": _SEARCH[g["key"]],
            }
            for g in GUIDES
            if g["group"] == group_key and (not query or matches(g, query))
        ]
        if items:
            out.append({"key": group_key, "label": label, "guides": items})
    return out


def guide_key_for(nav_active: str | None, *, account: bool) -> str:
    """The guide for a dashboard page's sidebar key. The account dashboard's
    key is "overview", which on store pages means the store Overview."""
    key = nav_active or ""
    if account and key == "overview":
        key = "dashboard"
    return key if key in _BY_KEY else ""
