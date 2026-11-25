"""Example core page tree for onewooz.com: page → subpage → child page.

Three top-level pages (Help Centre, Selling Guides, Company), each with
subpages, most of which carry child pages. The order of each list is the order
shown on the site. See ``web.sample_content`` for the ``[[...]]`` link tokens.
"""

from __future__ import annotations

CORE_PAGES = [
    # =====================================================================
    # HELP CENTRE
    # =====================================================================
    {
        "title": "Help Centre",
        "slug": "help-centre",
        "show_in_footer": True,
        "content": """
            <p>Step-by-step answers for running a OneWooz store, from the first product you add to the
            hundredth order you ship. Every guide matches what you see in your dashboard today.</p>
            <h2>Start here</h2>
            <ul>
              <li><strong><a href="[[core:help-centre/getting-started]]">Getting started</a></strong>: create a store, pick a theme and go live.</li>
              <li><strong><a href="[[core:help-centre/products-and-catalogue]]">Products and catalogue</a></strong>: variations, categories and stock.</li>
              <li><strong><a href="[[core:help-centre/orders-and-shipping]]">Orders and shipping</a></strong>: accept, pack, ship and handle returns.</li>
              <li><strong><a href="[[core:help-centre/payments]]">Payments</a></strong>: gateways, cash on delivery and your bills.</li>
              <li><strong><a href="[[core:help-centre/domains]]">Domains</a></strong>: put your store on your own web address.</li>
            </ul>
            <h2>Still stuck?</h2>
            <p>Raise a ticket from <strong>Business Hub</strong> in your dashboard and attach a screenshot.
            Our team replies in the same thread and you get a notification on the bell when we do.
            You can also reach us through the <a href="[[web:contact]]">contact page</a>.</p>
        """,
        "subpages": [
            {
                "title": "Getting started",
                "slug": "getting-started",
                "content": """
                    <p>Most stores go from sign-up to a live address in under fifteen minutes. This section
                    walks through each step in the order you will meet it.</p>
                    <ol>
                      <li><a href="[[core:help-centre/getting-started/create-a-store]]">Create a store</a></li>
                      <li><a href="[[core:help-centre/getting-started/choose-a-theme]]">Choose a theme</a></li>
                      <li><a href="[[core:help-centre/getting-started/add-your-first-products]]">Add your first products</a></li>
                      <li><a href="[[core:help-centre/getting-started/go-live-checklist]]">Run the go-live checklist</a></li>
                    </ol>
                    <p>You can come back to any step later. Nothing you set up now is locked in.</p>
                """,
                "children": [
                    {
                        "title": "Create a store",
                        "slug": "create-a-store",
                        "content": """
                            <p>Creating a store takes four answers. OneWooz uses them to write your pages,
                            pick sensible defaults and build a storefront that already looks finished.</p>
                            <h2>What you will be asked</h2>
                            <ol>
                              <li><strong>Business category and subcategory.</strong> For example Apparel, then Ethnic wear. This decides the starter copy, FAQs and policies.</li>
                              <li><strong>Brand name.</strong> Shown in the header, emails and invoices. You can change it any time in <strong>Settings</strong>.</li>
                              <li><strong>Who you sell to.</strong> Shoppers (D2C) or businesses (B2B). This picks the theme family.</li>
                              <li><strong>Store address.</strong> Your public web address on OneWooz, such as <em>yourbrand.onewooz.site</em>.</li>
                            </ol>
                            <h2>What you get</h2>
                            <p>A working store with a home page, about and contact pages, refund, shipping and
                            privacy policies, FAQs and a checkout. Every section is filled with writing that
                            fits your category, so nothing reads "insert text here".</p>
                            <blockquote>Tip: you do not need product photos to start. Empty image slots show
                            neutral reference photos in the editor until you upload your own.</blockquote>
                            <p>Next: <a href="[[core:help-centre/getting-started/choose-a-theme]]">choose a theme</a>.</p>
                        """,
                    },
                    {
                        "title": "Choose a theme",
                        "slug": "choose-a-theme",
                        "content": """
                            <p>Your theme decides how the store looks and which buying flow it uses. There are two families.</p>
                            <table>
                              <thead><tr><th></th><th>Modern storefront (D2C)</th><th>Catalogue storefront (B2B)</th></tr></thead>
                              <tbody>
                                <tr><td>Best for</td><td>Brands selling to shoppers</td><td>Manufacturers, wholesalers, distributors</td></tr>
                                <tr><td>Buying flow</td><td>Cart, checkout and online payment</td><td>Enquiry and quote, with optional ordering</td></tr>
                                <tr><td>Product page</td><td>Variations, reviews, price history</td><td>Specifications, certificates, bulk enquiry</td></tr>
                                <tr><td>Extras</td><td>Collaborations with creators, wishlist</td><td>Certificates page, downloadable PDFs</td></tr>
                              </tbody>
                            </table>
                            <h2>Changing it later</h2>
                            <p>Your products, orders and written content stay put when you switch. Only the layout
                            changes, so it is safe to try both and keep the one that fits.</p>
                            <p>Next: <a href="[[core:help-centre/getting-started/add-your-first-products]]">add your first products</a>.</p>
                        """,
                    },
                    {
                        "title": "Add your first products",
                        "slug": "add-your-first-products",
                        "content": """
                            <p>Open <strong>Products</strong> and choose <strong>Add product</strong>. Only the name
                            and price are required; everything else improves how the product sells.</p>
                            <h2>The fields that matter most</h2>
                            <ul>
                              <li><strong>Images.</strong> Each image block is a fixed position, so block 1 is always the cover photo. Phone photos are converted to WebP automatically.</li>
                              <li><strong>Price and compare-at price.</strong> The compare-at price shows as a strike-through when it is higher.</li>
                              <li><strong>GST.</strong> Tick <em>Price includes GST</em> if the price you typed already contains tax.</li>
                              <li><strong>Stock.</strong> A blank stock box means zero, which shows the product as sold out.</li>
                              <li><strong>Category.</strong> Groups the product on the shop page and in the menu.</li>
                            </ul>
                            <p>Hover the <strong>!</strong> icon next to any field for a short explanation of what it does on the storefront.</p>
                            <p>Selling sizes or colours? Read <a href="[[core:help-centre/products-and-catalogue/variations-and-skus]]">Variations and SKUs</a> before adding stock.</p>
                        """,
                    },
                    {
                        "title": "Go-live checklist",
                        "slug": "go-live-checklist",
                        "content": """
                            <p>Run through this list before you share your store link. Each item takes a minute or two.</p>
                            <ol>
                              <li><strong>Logo and brand colours</strong> set in <strong>Edit content</strong>.</li>
                              <li><strong>At least three products</strong> with a cover image and stock.</li>
                              <li><strong>A payment method</strong> connected in <strong>Payments</strong>, or cash on delivery switched on.</li>
                              <li><strong>A shipping option</strong> set up in <strong>Shipping</strong>, with your pickup address.</li>
                              <li><strong>Business and tax details</strong> (legal name, GSTIN, PAN) filled in under <strong>Settings</strong>, so invoices are correct.</li>
                              <li><strong>Contact details</strong> and a grievance officer on the contact page.</li>
                              <li><strong>A test order</strong> placed from your phone, then cancelled from <strong>Orders</strong>.</li>
                            </ol>
                            <p>When everything is ticked, share your OneWooz address, or <a href="[[core:help-centre/domains/connect-your-own-domain]]">connect your own domain</a> first.</p>
                        """,
                    },
                ],
            },
            {
                "title": "Products and catalogue",
                "slug": "products-and-catalogue",
                "content": """
                    <p>How to organise what you sell so shoppers find it quickly and your stock counts stay accurate.</p>
                    <ul>
                      <li><a href="[[core:help-centre/products-and-catalogue/variations-and-skus]]">Variations and SKUs</a></li>
                      <li><a href="[[core:help-centre/products-and-catalogue/organise-with-categories]]">Organise with categories</a></li>
                      <li><a href="[[core:help-centre/products-and-catalogue/stock-and-inventory]]">Stock and inventory</a></li>
                    </ul>
                """,
                "children": [
                    {
                        "title": "Variations and SKUs",
                        "slug": "variations-and-skus",
                        "content": """
                            <p>Use variations when one product comes in several options, such as sizes or colours.
                            Shoppers pick an option on the product page and the price, stock and photos follow their choice.</p>
                            <h2>Setting up variations</h2>
                            <ol>
                              <li>On the product form, switch on <strong>Variations</strong>.</li>
                              <li>Pick a preset (Size, Colour, Material) or type your own option name.</li>
                              <li>Add the values, for example S, M, L and XL.</li>
                              <li>Fill in price, stock and SKU for each combination in the table that appears.</li>
                            </ol>
                            <h2>Things to know</h2>
                            <ul>
                              <li>Once variations are on, stock lives on each variation. The product's own stock box locks and shows the live total.</li>
                              <li>Each variation can have its own photo. The product page swaps to it when the shopper selects that option.</li>
                              <li>Use the numbered variation tabs to give one option its own details, such as a different fabric description.</li>
                              <li>The variation SKU prints on the shipping label, which makes packing mistakes rare.</li>
                            </ul>
                        """,
                    },
                    {
                        "title": "Organise with categories",
                        "slug": "organise-with-categories",
                        "content": """
                            <p>Categories group your products on the shop page, in the menu and in search results.
                            A good rule: a shopper should be able to guess which category a product is in without looking.</p>
                            <h2>Good category names</h2>
                            <ul>
                              <li>Describe what the product is (<em>Kurtas</em>, <em>Face serums</em>), not a campaign (<em>Summer sale</em>). Use discounts for campaigns.</li>
                              <li>Keep it to eight or fewer top-level categories so the menu fits on one line.</li>
                              <li>Give every category a cover image. It is used on the home page and when the link is shared.</li>
                            </ul>
                            <h2>Renaming is safe</h2>
                            <p>When you rename a category or product, its web address updates to match and the old
                            address redirects to the new one. Links you shared earlier, and search engine listings, keep working.</p>
                        """,
                    },
                    {
                        "title": "Stock and inventory",
                        "slug": "stock-and-inventory",
                        "content": """
                            <p>OneWooz keeps stock in step with your orders so you never sell something you do not have.</p>
                            <table>
                              <thead><tr><th>When this happens</th><th>Stock</th></tr></thead>
                              <tbody>
                                <tr><td>A cash on delivery order is placed</td><td>Reduced immediately</td></tr>
                                <tr><td>An online payment is confirmed</td><td>Reduced at confirmation</td></tr>
                                <tr><td>You cancel an order before dispatch</td><td>Returned to stock</td></tr>
                                <tr><td>A parcel comes back as RTO</td><td>Returned to stock</td></tr>
                              </tbody>
                            </table>
                            <p>A product, or a single size or colour, with zero stock shows as sold out and cannot be added to the cart.</p>
                            <h2>Keeping an eye on it</h2>
                            <p>The <strong>Out of stock</strong> page lists every sold-out item, down to the individual
                            variation, and the sidebar counter tells you when something new runs out. Update counts in bulk from <strong>Inventory</strong>.</p>
                        """,
                    },
                ],
            },
            {
                "title": "Orders and shipping",
                "slug": "orders-and-shipping",
                "content": """
                    <p>Every order moves through the same tabs on the <strong>Orders</strong> page:
                    Pending, Ready to Ship, Pickup Scheduled, Shipped and Delivered. These guides cover each step.</p>
                    <ul>
                      <li><a href="[[core:help-centre/orders-and-shipping/processing-an-order]]">Processing an order</a></li>
                      <li><a href="[[core:help-centre/orders-and-shipping/pickups-and-labels]]">Pickups and shipping labels</a></li>
                      <li><a href="[[core:help-centre/orders-and-shipping/cancellations-returns-replacements]]">Cancellations, returns and replacements</a></li>
                    </ul>
                """,
                "children": [
                    {
                        "title": "Processing an order",
                        "slug": "processing-an-order",
                        "content": """
                            <p>New orders arrive in <strong>Pending</strong>. You get a bell notification and an email for each one.</p>
                            <ol>
                              <li><strong>Check the order.</strong> Open it to see items, address and payment method.</li>
                              <li><strong>Edit if needed.</strong> Add or remove items before it ships; totals and stock recalculate and the shopper is emailed the change.</li>
                              <li><strong>Accept it.</strong> The order moves to <strong>Ready to Ship</strong> and, with a courier connected, a shipment is booked.</li>
                              <li><strong>Pack and request pickup.</strong> See <a href="[[core:help-centre/orders-and-shipping/pickups-and-labels]]">Pickups and shipping labels</a>.</li>
                            </ol>
                            <p>Online orders appear only once payment is confirmed, so an abandoned payment never clutters your list.</p>
                            <p>Need a record for your accountant? Use <strong>Export</strong> on the Orders page to download any month as a spreadsheet.</p>
                        """,
                    },
                    {
                        "title": "Pickups and shipping labels",
                        "slug": "pickups-and-labels",
                        "content": """
                            <p>With Shiprocket or Delhivery connected, one button does both jobs: <strong>Generate &amp; Request Pickup</strong>.</p>
                            <ol>
                              <li>Select the orders in <strong>Ready to Ship</strong>.</li>
                              <li>Choose a pickup date. The earliest is the next day (if you request after noon) and the latest is seven days ahead.</li>
                              <li>Pick a label size: 4×6 for thermal printers or A4 for office printers.</li>
                              <li>Confirm. The labels download as one PDF and the orders move to <strong>Pickup Scheduled</strong>.</li>
                            </ol>
                            <h2>What is on the label</h2>
                            <p>Courier barcode, delivery and return addresses, the order number, each item with its
                            variation SKU, and your tax invoice with your authorised signature.</p>
                            <h2>Tracking</h2>
                            <p>Status updates arrive from the courier automatically. The shopper gets an email with the
                            tracking link when the parcel ships and again when it is delivered.</p>
                            <p>Shipping yourself? Self-ship stores get a <strong>Generate Label</strong> button instead.</p>
                        """,
                    },
                    {
                        "title": "Cancellations, returns and replacements",
                        "slug": "cancellations-returns-replacements",
                        "content": """
                            <p>Shoppers can raise requests from their account page. They land on <strong>Requests</strong> in your dashboard.</p>
                            <table>
                              <thead><tr><th>Request</th><th>Allowed</th><th>What approving does</th></tr></thead>
                              <tbody>
                                <tr><td>Cancellation</td><td>Until the parcel ships</td><td>Cancels the order, restocks items, cancels the courier booking</td></tr>
                                <tr><td>Replacement</td><td>After delivery, within the product's replacement window</td><td>Creates a replacement order with the same order number, shipped prepaid at no charge</td></tr>
                              </tbody>
                            </table>
                            <h2>Returned to origin (RTO)</h2>
                            <p>If a courier cannot deliver and the parcel comes back, the order moves to the RTO tab and the stock is returned automatically.</p>
                            <p>Your refund and replacement terms live in your refund policy, which the product page links to.</p>
                        """,
                    },
                ],
            },
            {
                "title": "Payments",
                "slug": "payments",
                "content": """
                    <p>Choose how shoppers pay, and understand what OneWooz charges you.</p>
                    <ul>
                      <li><a href="[[core:help-centre/payments/connect-a-payment-gateway]]">Connect a payment gateway</a></li>
                      <li><a href="[[core:help-centre/payments/cod-and-part-payment]]">Cash on delivery and part payment</a></li>
                      <li><a href="[[core:help-centre/payments/commerce-fee-and-bills]]">Commerce fee and bills</a></li>
                    </ul>
                """,
                "children": [
                    {
                        "title": "Connect a payment gateway",
                        "slug": "connect-a-payment-gateway",
                        "content": """
                            <p>Online payments go straight from the shopper to your own Cashfree or Razorpay account.
                            OneWooz never holds your money.</p>
                            <ol>
                              <li>Open <strong>Payments</strong> and choose <strong>Edit</strong>.</li>
                              <li>Select your gateway and paste the App ID (or Key ID) and Secret from its dashboard.</li>
                              <li>Choose test or live mode and save.</li>
                              <li>Place a small test order to confirm the payment page opens.</li>
                            </ol>
                            <p>Your keys are encrypted before they are stored and are never shown in full again.</p>
                            <blockquote>Using your own domain? Whitelist it in your gateway dashboard, or payments may be declined.</blockquote>
                        """,
                    },
                    {
                        "title": "Cash on delivery and part payment",
                        "slug": "cod-and-part-payment",
                        "content": """
                            <p>Cash on delivery (COD) still accounts for a large share of orders in India. OneWooz offers it two ways.</p>
                            <ul>
                              <li><strong>Full COD:</strong> the shopper pays everything to the courier on delivery.</li>
                              <li><strong>Part payment:</strong> the shopper pays a small amount online now and the rest on delivery. The checkout shows it plainly, for example "Pay ₹199 now, rest ₹1,050 on COD".</li>
                            </ul>
                            <h2>Why part payment helps</h2>
                            <p>A shopper who has already paid something is far less likely to refuse the parcel at the door,
                            which cuts the cost of RTO without losing COD buyers.</p>
                            <p>Switch either option on, and set the part-payment amount, in <strong>Payments</strong>.</p>
                        """,
                    },
                    {
                        "title": "Commerce fee and bills",
                        "slug": "commerce-fee-and-bills",
                        "content": """
                            <p>Alongside your plan, OneWooz charges a small <strong>commerce fee</strong> on orders that are
                            delivered. Cancelled, returned and replacement orders are never charged.</p>
                            <h2>How it is worked out</h2>
                            <p>Your plan lists price ranges, each with a fixed fee per unit sold. A product priced in the
                            lowest range carries the smallest fee. GST at 18% is added on the bill.</p>
                            <h2>Paying your bill</h2>
                            <ol>
                              <li>Open <strong>Bills</strong> to see each invoice and the delivered orders it covers.</li>
                              <li>Download the Excel export if you want to check the orders line by line.</li>
                              <li>Choose <strong>Pay</strong>, pay by bank transfer or UPI QR, and upload the payment screenshot.</li>
                            </ol>
                            <p>See the fee ranges for each plan on the <a href="[[web:pricing]]">pricing page</a>.</p>
                        """,
                    },
                ],
            },
            {
                "title": "Domains",
                "slug": "domains",
                "content": """
                    <p>Every store gets a OneWooz address. You can also use a domain you own, such as
                    <em>yourbrand.com</em>, with HTTPS set up automatically.</p>
                    <ul>
                      <li><a href="[[core:help-centre/domains/connect-your-own-domain]]">Connect your own domain</a></li>
                      <li><a href="[[core:help-centre/domains/dns-records-explained]]">DNS records explained</a></li>
                    </ul>
                """,
                "children": [
                    {
                        "title": "Connect your own domain",
                        "slug": "connect-your-own-domain",
                        "content": """
                            <p>You will need access to the place you bought the domain (GoDaddy, Hostinger, Namecheap,
                            Cloudflare and so on). Allow about ten minutes, plus time for the records to spread.</p>
                            <ol>
                              <li>Open <strong>Custom domain</strong> and enter your domain, for example <em>yourbrand.com</em> or <em>shop.yourbrand.com</em>.</li>
                              <li>Add the <strong>TXT</strong> record shown, which proves you own the domain.</li>
                              <li>Add the <strong>A</strong> record shown, which points the domain at your store.</li>
                              <li>Optionally add the <strong>www</strong> CNAME so <em>www.yourbrand.com</em> also opens your store.</li>
                              <li>Choose <strong>Verify</strong>. Once it passes, HTTPS is issued automatically and your store opens on the new address.</li>
                            </ol>
                            <p>Use the <strong>Copy</strong> buttons next to each value. A single mistyped character is the most common reason verification fails.</p>
                        """,
                    },
                    {
                        "title": "DNS records explained",
                        "slug": "dns-records-explained",
                        "content": """
                            <p>DNS records tell the internet where your domain lives. You only need three.</p>
                            <table>
                              <thead><tr><th>Record</th><th>Host</th><th>Purpose</th><th>Required</th></tr></thead>
                              <tbody>
                                <tr><td>TXT</td><td>The verification host shown in your dashboard</td><td>Proves the domain is yours</td><td>Yes</td></tr>
                                <tr><td>A</td><td>@ for a root domain, or the subdomain such as shop</td><td>Sends visitors to your store</td><td>Yes</td></tr>
                                <tr><td>CNAME</td><td>www</td><td>Opens your store on www too</td><td>Recommended</td></tr>
                              </tbody>
                            </table>
                            <h2>How long does it take?</h2>
                            <p>Usually a few minutes, occasionally up to 24 hours. Setting the TTL to 300, or the lowest
                            value your provider allows, makes changes show up faster.</p>
                            <h2>Common mistakes</h2>
                            <ul>
                              <li>Typing the full domain in the Host box when your provider adds it automatically.</li>
                              <li>Leaving an old A record in place alongside the new one.</li>
                              <li>Adding the records at the registrar while the domain actually uses another DNS provider.</li>
                            </ul>
                        """,
                    },
                ],
            },
        ],
    },
    # =====================================================================
    # SELLING GUIDES
    # =====================================================================
    {
        "title": "Selling Guides",
        "slug": "selling-guides",
        "show_in_footer": True,
        "content": """
            <p>Practical playbooks from stores that sell on OneWooz every day. Where the Help Centre explains
            which button to press, these guides explain what to do with it.</p>
            <ul>
              <li><strong><a href="[[core:selling-guides/launching-a-d2c-brand]]">Launching a D2C brand</a></strong>: product pages, pricing and your first hundred orders.</li>
              <li><strong><a href="[[core:selling-guides/selling-to-businesses]]">Selling to businesses</a></strong>: enquiries, quotes and the trust B2B buyers look for.</li>
              <li><strong><a href="[[core:selling-guides/growing-your-store]]">Growing your store</a></strong>: discounts, email and Google Shopping.</li>
            </ul>
        """,
        "subpages": [
            {
                "title": "Launching a D2C brand",
                "slug": "launching-a-d2c-brand",
                "content": """
                    <p>Selling directly to shoppers means you own the customer, the margin and the brand.
                    It also means the product page has to do the job a shop assistant would. Start with these two guides.</p>
                """,
                "children": [
                    {
                        "title": "Product pages that sell",
                        "slug": "product-pages-that-sell",
                        "content": """
                            <p>A shopper decides in seconds. Answer their questions in the order they ask them.</p>
                            <ol>
                              <li><strong>What is it?</strong> A plain name: <em>Cotton straight kurta, indigo</em> beats <em>The Aria</em>.</li>
                              <li><strong>Will it suit me?</strong> Show it in use. The first image is the one shown in listings and shares.</li>
                              <li><strong>Is it worth the price?</strong> Lead the description with the one detail that justifies it: fabric, origin, ingredient.</li>
                              <li><strong>What if it does not work out?</strong> State your replacement window. The badge on the product page links to your refund policy.</li>
                              <li><strong>Can I trust this store?</strong> Reviews, trust badges and a real contact page.</li>
                            </ol>
                            <h2>Photos that work</h2>
                            <ul>
                              <li>Daylight, plain background, the same angle across the range.</li>
                              <li>One close-up of texture or finish.</li>
                              <li>One photo that shows scale: worn, held or next to a familiar object.</li>
                            </ul>
                        """,
                    },
                    {
                        "title": "Pricing with GST",
                        "slug": "pricing-with-gst",
                        "content": """
                            <p>Shoppers in India expect the price they see to be the price they pay. Most D2C brands
                            therefore price <strong>inclusive</strong> of GST.</p>
                            <h2>Inclusive or exclusive</h2>
                            <table>
                              <thead><tr><th></th><th>Inclusive (recommended for D2C)</th><th>Exclusive (common in B2B)</th></tr></thead>
                              <tbody>
                                <tr><td>Price shown</td><td>₹1,180</td><td>₹1,000 + GST</td></tr>
                                <tr><td>Shopper pays</td><td>₹1,180</td><td>₹1,180</td></tr>
                                <tr><td>Invoice shows</td><td>₹1,000 taxable + ₹180 GST</td><td>₹1,000 taxable + ₹180 GST</td></tr>
                              </tbody>
                            </table>
                            <p>On the product form, tick <strong>Price includes GST</strong> for inclusive pricing. The tax
                            invoice splits it into CGST and SGST, or IGST for other states, automatically.</p>
                            <blockquote>Fill in your GSTIN under Settings so it appears on every invoice your customers download.</blockquote>
                        """,
                    },
                ],
            },
            {
                "title": "Selling to businesses",
                "slug": "selling-to-businesses",
                "content": """
                    <p>B2B buyers rarely add to cart. They compare suppliers, ask for a quote and come back
                    when the paperwork checks out. The catalogue theme is built for that journey.</p>
                """,
                "children": [
                    {
                        "title": "Turning enquiries into orders",
                        "slug": "enquiries-into-orders",
                        "content": """
                            <p>Every enquiry form on your catalogue lands on the <strong>Enquiries</strong> page with the
                            product, quantity and the buyer's contact details.</p>
                            <h2>A reply routine that wins</h2>
                            <ol>
                              <li><strong>Reply the same day.</strong> The first supplier to send a clear quote often wins.</li>
                              <li><strong>Quote the full landed price:</strong> unit price, GST, freight and lead time in one message.</li>
                              <li><strong>Attach proof:</strong> a certificate or test report removes the next question before it is asked.</li>
                              <li><strong>Follow up once</strong> after three days with one new fact, such as stock availability.</li>
                            </ol>
                            <p>The sidebar counter on Enquiries shows how many are new, so nothing waits overnight by accident.</p>
                        """,
                    },
                    {
                        "title": "Certificates and trust",
                        "slug": "certificates-and-trust",
                        "content": """
                            <p>Procurement teams look for evidence before price. Publish yours where they look.</p>
                            <ul>
                              <li><strong>Certificates</strong>: add ISO, BIS, FSSAI or test reports in Edit content. Each gets a name, an image and a downloadable PDF, and is listed on its own certificates page.</li>
                              <li><strong>Business details</strong>: legal name, GSTIN and MSME number on the contact page and invoices.</li>
                              <li><strong>Specifications</strong>: units, tolerances and pack sizes on every product, not just the best-sellers.</li>
                            </ul>
                            <p>Certificates also appear in your store's search engine data, which helps buyers searching for certified suppliers find you.</p>
                        """,
                    },
                ],
            },
            {
                "title": "Growing your store",
                "slug": "growing-your-store",
                "content": """
                    <p>Once orders arrive steadily, these three levers bring in the next wave without paying for every visit.</p>
                """,
                "children": [
                    {
                        "title": "Discounts that protect your margin",
                        "slug": "discounts-that-protect-margin",
                        "content": """
                            <p>A discount should change behaviour, not just reduce the price for people who were buying anyway.</p>
                            <table>
                              <thead><tr><th>Goal</th><th>Use</th></tr></thead>
                              <tbody>
                                <tr><td>Raise order value</td><td>A discount on carts above a minimum value</td></tr>
                                <tr><td>Clear one product</td><td>A product discount with a minimum quantity</td></tr>
                                <tr><td>Create urgency</td><td>An instant discount that expires in 1 hour, 4 hours or 1 day</td></tr>
                                <tr><td>Win back a past buyer</td><td>Store credit for that customer</td></tr>
                              </tbody>
                            </table>
                            <p>Instant discounts generate their own code and switch themselves off, so a flash sale never runs longer than you meant it to.</p>
                        """,
                    },
                    {
                        "title": "Email campaigns customers open",
                        "slug": "email-campaigns",
                        "content": """
                            <p>Your subscribers and past customers are the audience you can reach without paying for ads.
                            <strong>Email marketing</strong> sends from your brand name, so it reads like a note from you.</p>
                            <h2>Four rules</h2>
                            <ol>
                              <li><strong>One message per email.</strong> A new launch, or a sale, not both.</li>
                              <li><strong>A subject line that says what is inside</strong>, such as "Indigo kurtas are back in all sizes".</li>
                              <li><strong>Send to the right list.</strong> Newsletter subscribers for launches, past buyers for restocks.</li>
                              <li><strong>Read the numbers.</strong> Opens and clicks are tracked per campaign; repeat what works.</li>
                            </ol>
                            <p>Grow the list with the newsletter popup in Edit content, optionally offering a discount code for signing up.</p>
                        """,
                    },
                    {
                        "title": "Listing on Google Shopping",
                        "slug": "google-shopping",
                        "content": """
                            <p>Google Shopping shows your products, with photo and price, right in search results, and its standard listings are not paid ads.</p>
                            <ol>
                              <li>Open <strong>Google Merchant</strong> and choose <strong>Connect</strong>.</li>
                              <li>Sign in with the Google account that should own the Merchant Center.</li>
                              <li>OneWooz verifies your store address and sends your products, including every variation.</li>
                              <li>Leave auto-sync on so price and stock changes reach Google without any extra work.</li>
                            </ol>
                            <h2>Before you connect</h2>
                            <ul>
                              <li>Every product needs an image, a price and stock.</li>
                              <li>Your refund, shipping and contact pages must be filled in; Google checks them.</li>
                              <li>A custom domain is recommended for a store you plan to run long term.</li>
                            </ul>
                        """,
                    },
                ],
            },
        ],
    },
    # =====================================================================
    # COMPANY
    # =====================================================================
    {
        "title": "Company",
        "slug": "company",
        "show_in_footer": True,
        "content": """
            <p><strong>Run your business, not just your website.</strong> OneWooz gives you your own online
            store in India, with built-in SEO, AEO, GEO and marketing so you're not stuck paying for ads. We
            build the store, the checkout, the payments and the shipping, so you can spend your day on the
            product and the customer.</p>
            <ul>
              <li><a href="[[core:company/about]]">About us</a></li>
              <li><a href="[[core:company/careers]]">Careers</a></li>
              <li><a href="[[core:company/press]]">Press and brand</a></li>
            </ul>
        """,
        "subpages": [
            {
                "title": "About us",
                "slug": "about",
                "content": """
                    <p>Most small brands in India sell on Instagram, WhatsApp and marketplaces, and give away a
                    slice of every order to do it. A store of their own was meant to fix that, but building one
                    still meant a developer, a page builder and weeks of setup.</p>
                    <p>OneWooz removes all of it. Answer a few questions and you get a live store with
                    product pages, checkout, payments and shipping already connected.</p>
                    <h2>What we believe</h2>
                    <ul>
                      <li><strong>Finished beats flexible.</strong> A store that looks right on day one is worth more than a thousand settings.</li>
                      <li><strong>Your customers are yours.</strong> Payments go to your account and your customer list is always exportable.</li>
                      <li><strong>Security is not a feature.</strong> Every store is isolated from every other, by design.</li>
                    </ul>
                """,
                "children": [],
            },
            {
                "title": "Careers",
                "slug": "careers",
                "content": """
                    <p>We are a small team that ships every week and talks to customers every day.
                    If you like work that is visible to real businesses within days, you will feel at home.</p>
                    <h2>How we work</h2>
                    <ul>
                      <li>Small teams that own a problem from idea to support ticket.</li>
                      <li>Written proposals before big changes, short meetings after.</li>
                      <li>Everyone spends time on support, because that is where the roadmap comes from.</li>
                    </ul>
                    <h2>Open roles</h2>
                    <p>We do not have open roles listed right now. If you think you would make OneWooz better,
                    write to us through the <a href="[[web:contact]]">contact page</a> with something you have built.</p>
                """,
                "children": [],
            },
            {
                "title": "Press and brand",
                "slug": "press",
                "content": """
                    <p>Writing about OneWooz? Here is what you need to describe us accurately.</p>
                    <h2>In one sentence</h2>
                    <blockquote>OneWooz builds and publishes a working online store, with checkout, payments and
                    shipping, for D2C brands and B2B suppliers in minutes, without a developer.</blockquote>
                """,
                "children": [
                    {
                        "title": "Brand guidelines",
                        "slug": "brand-guidelines",
                        "content": """
                            <ul>
                              <li>Write the name as <strong>OneWooz</strong>: one word, capital O and W.</li>
                              <li>Our accent colour is indigo, <strong>#4B2BF5</strong>. Use it sparingly, against warm neutrals.</li>
                              <li>Do not stretch, recolour or add effects to the logo.</li>
                              <li>Leave clear space around the logo at least the height of the letter O.</li>
                            </ul>
                        """,
                    },
                    {
                        "title": "Media enquiries",
                        "slug": "media-enquiries",
                        "content": """
                            <p>For interviews, data requests or customer stories, reach us through the
                            <a href="[[web:contact]]">contact page</a> and mention your publication and deadline.
                            We reply to press requests within one working day.</p>
                        """,
                    },
                ],
            },
        ],
    },
]
