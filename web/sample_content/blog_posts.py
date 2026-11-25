"""Example blog posts for onewooz.com.

Each post is a complete article: a real excerpt, sections with headings,
lists, a table or quote where it helps, and links into the Help Centre.
``days_ago`` spreads the publish dates so the blog index reads like a living
blog; ``cover`` is the two-colour palette for the generated cover image.
"""

from __future__ import annotations

BLOG_POSTS = [
    {
        "title": "Welcome to OneWooz",
        "slug": "welcome-to-onewooz",
        "author": "OneWooz Team",
        "days_ago": 62,
        "cover": ("#4b2bf5", "#a99bff"),
        "excerpt": (
            "What OneWooz is, who it is for, and how it turns a few answers about your "
            "business into a live store with checkout, payments and shipping."
        ),
        "content": """
            <p>Running a small brand already means being the buyer, the photographer, the packer and the
            customer service desk. Building a website should not be another full-time job. That is the
            idea behind OneWooz.</p>
            <h2>What OneWooz does</h2>
            <p>You answer a few questions about your business: what you sell, who you sell to and what
            you are called. OneWooz builds a working online store from those answers. Not a template with
            empty boxes, but a store with pages written for your category, policies, FAQs, a checkout and
            payments ready to connect.</p>
            <ul>
              <li><strong>Payments</strong> go straight to your own Cashfree or Razorpay account, with cash on delivery and part payment built in.</li>
              <li><strong>Shipping</strong> connects to Shiprocket or Delhivery, so labels and pickups take one click.</li>
              <li><strong>Your domain</strong> can be connected in minutes, with HTTPS set up for you.</li>
              <li><strong>Marketing tools</strong> such as discounts, email campaigns and Google Shopping are in the same dashboard.</li>
            </ul>
            <h2>Who it is for</h2>
            <p>Two kinds of business, each with its own theme. <strong>D2C brands</strong> selling to shoppers
            get a modern storefront with variations, reviews and creator collaborations. <strong>B2B
            suppliers</strong> get a catalogue built around enquiries, specifications and certificates.</p>
            <h2>What this blog is for</h2>
            <p>We will use it for product updates and for practical guides drawn from what we see working
            across stores. If you are just starting, the <a href="[[core:help-centre/getting-started]]">getting
            started guide</a> is the best next step.</p>
        """,
    },
    {
        "title": "How to offer cash on delivery without the RTO headache",
        "slug": "cash-on-delivery-without-rto-headache",
        "author": "OneWooz Team",
        "days_ago": 48,
        "cover": ("#0f766e", "#7dd3c0"),
        "excerpt": (
            "COD wins orders, and refused parcels eat the profit. Part payment, clear "
            "expectations and a few checks keep the first without the second."
        ),
        "content": """
            <p>Turn off cash on delivery and you lose a big share of first-time buyers. Leave it on with no
            safeguards and a good part of your parcels come back unopened, each one costing you shipping
            both ways. The answer is not to choose, but to make COD orders more committed.</p>
            <h2>Why parcels come back</h2>
            <ul>
              <li><strong>Impulse orders.</strong> Ordering costs the shopper nothing, so second thoughts cost them nothing either.</li>
              <li><strong>Surprises at the door.</strong> A total higher than expected, or a delivery that took longer than promised.</li>
              <li><strong>Unreachable shoppers.</strong> Wrong phone numbers or incomplete addresses.</li>
            </ul>
            <h2>1. Ask for a small amount upfront</h2>
            <p>Part payment lets the shopper pay a small amount online and the rest on delivery. The checkout
            states it plainly, for example "Pay ₹199 now, rest ₹1,050 on COD". A shopper who has already paid
            something is far more likely to accept the parcel. You keep the COD buyer and lose the casual
            orderer.</p>
            <h2>2. Show the full price early</h2>
            <p>Price inclusive of GST and show shipping charges on the product page or in the cart, not at
            the last step. The amount the courier asks for should never surprise anyone.</p>
            <h2>3. Ship fast and say so</h2>
            <p>Refusals rise with every day a parcel spends in transit. Accept orders daily and request
            pickup the next morning. With a courier connected, the shopper receives the tracking link by
            email as soon as the parcel ships, which keeps the order on their mind.</p>
            <h2>4. Watch the numbers</h2>
            <table>
              <thead><tr><th>Measure</th><th>Where to find it</th></tr></thead>
              <tbody>
                <tr><td>Share of orders paid by COD</td><td>Analytics</td></tr>
                <tr><td>Parcels returned to origin</td><td>Orders, RTO tab</td></tr>
                <tr><td>Products most often refused</td><td>Monthly orders export</td></tr>
              </tbody>
            </table>
            <p>If one product is refused far more than the rest, the problem is usually its photos or
            description, not COD itself.</p>
            <p>Set up both payment options in a few minutes with the
            <a href="[[core:help-centre/payments/cod-and-part-payment]]">COD and part payment guide</a>.</p>
        """,
    },
    {
        "title": "Your first 100 orders: a launch plan for new D2C brands",
        "slug": "first-100-orders-launch-plan",
        "author": "OneWooz Team",
        "days_ago": 36,
        "cover": ("#c2410c", "#fbbf77"),
        "excerpt": (
            "Forget going viral. The first hundred orders come from people who already "
            "know you, a store that earns trust, and a habit of asking for the next sale."
        ),
        "content": """
            <p>New brands often wait for a big launch moment. In practice the first hundred orders arrive in
            small, unglamorous steps. Here is a plan we see work again and again.</p>
            <h2>Week 1: make the store trustworthy</h2>
            <p>Before you share the link with anyone, run the
            <a href="[[core:help-centre/getting-started/go-live-checklist]]">go-live checklist</a>. A shopper
            who has never heard of you checks three things: real photos, a clear refund policy and a way to
            contact a human. Make sure all three are obvious.</p>
            <h2>Weeks 2 and 3: sell to the people who know you</h2>
            <ul>
              <li>Message friends, family and past customers personally. A one-to-one note beats a broadcast.</li>
              <li>Offer a launch code with a short expiry. An instant discount that lasts one day creates a reason to order now.</li>
              <li>Ask every early buyer for a review and a photo. Reviews on the product page do more for the next stranger than any ad.</li>
            </ul>
            <h2>Weeks 4 to 8: borrow other people's audiences</h2>
            <p>Creators with a few thousand engaged followers in your niche often convert better than large
            accounts. Collaborations let a creator sell your products from their own storefront and earn a
            commission on each order, so you pay only for results.</p>
            <h2>All along: build the list</h2>
            <p>Switch on the newsletter popup from day one. Every visitor who leaves an email is someone you
            can reach again without paying for ads when you launch a new colour or restock a best-seller.</p>
            <h2>What to measure</h2>
            <table>
              <thead><tr><th>Number</th><th>Why it matters</th></tr></thead>
              <tbody>
                <tr><td>Visitors to orders</td><td>Tells you whether the store, not the traffic, is the problem</td></tr>
                <tr><td>Repeat customers</td><td>The clearest sign that the product itself works</td></tr>
                <tr><td>Average order value</td><td>Shows whether bundles and minimum-cart discounts are working</td></tr>
              </tbody>
            </table>
            <blockquote>A hundred orders is enough to know what sells, who buys it and why. That is worth
            more than the revenue.</blockquote>
        """,
    },
    {
        "title": "Product photos that sell, shot on a phone",
        "slug": "product-photos-on-a-phone",
        "author": "OneWooz Team",
        "days_ago": 27,
        "cover": ("#1f2937", "#9ca3af"),
        "excerpt": (
            "You do not need a studio. Daylight, a plain background and a consistent "
            "angle will make a phone camera look professional."
        ),
        "content": """
            <p>The photo is the product online. Shoppers cannot touch the fabric or smell the candle, so the
            image has to do it for them. The good news: a recent phone is all the camera you need.</p>
            <h2>Light: the only thing that really matters</h2>
            <ul>
              <li>Shoot next to a large window in daylight, with the window to one side of the product.</li>
              <li>Turn off the room lights. Mixed light gives colours a yellow or blue cast.</li>
              <li>Hold a sheet of white card opposite the window to soften the shadows.</li>
            </ul>
            <h2>Background and angle</h2>
            <p>A plain wall, a sheet of chart paper or a clean bedsheet works. Choose one and keep it for the
            whole range, along with the same angle and distance. Consistency is what makes a catalogue look
            professional, far more than any single great shot.</p>
            <h2>The five shots every product needs</h2>
            <ol>
              <li><strong>Cover:</strong> the whole product, straight on, filling most of the frame.</li>
              <li><strong>Detail:</strong> texture, stitching or finish, up close.</li>
              <li><strong>Scale:</strong> worn, held or next to something familiar.</li>
              <li><strong>Variants:</strong> one image per colour, so the page can switch when a shopper picks one.</li>
              <li><strong>In use:</strong> the product where it lives, on a shelf, a desk or a person.</li>
            </ol>
            <h2>Uploading</h2>
            <p>Upload straight from your phone. OneWooz converts photos, including iPhone HEIC files, to
            fast-loading WebP automatically. The first image block is the cover used in listings and when
            the link is shared, so put your best shot there.</p>
            <p>Next: write a description that does the photos justice with
            <a href="[[core:selling-guides/launching-a-d2c-brand/product-pages-that-sell]]">product pages that sell</a>.</p>
        """,
    },
    {
        "title": "Your own store or a marketplace? Why the answer is both",
        "slug": "own-store-vs-marketplace",
        "author": "OneWooz Team",
        "days_ago": 20,
        "cover": ("#4b2bf5", "#f0abfc"),
        "excerpt": (
            "Marketplaces bring buyers, but they keep the customer. A store of your own "
            "is where repeat orders, margin and the brand itself are built."
        ),
        "content": """
            <p>Marketplaces are a fine place to be found. They are a hard place to build a business, because
            the customer relationship belongs to the marketplace, not to you.</p>
            <h2>What a marketplace gives you</h2>
            <ul>
              <li>Shoppers who are already searching.</li>
              <li>Trust borrowed from a famous name.</li>
              <li>Logistics handled for you, in some programmes.</li>
            </ul>
            <h2>What it keeps</h2>
            <table>
              <thead><tr><th></th><th>Marketplace</th><th>Your own store</th></tr></thead>
              <tbody>
                <tr><td>Customer's email and phone</td><td>Hidden</td><td>Yours</td></tr>
                <tr><td>Commission per order</td><td>Often 15% to 30%</td><td>A small percentage of each order as a commerce fee</td></tr>
                <tr><td>Brand presentation</td><td>Their layout</td><td>Your layout, your story</td></tr>
                <tr><td>Repeat order</td><td>Competes with rivals on the same page</td><td>Comes straight back to you</td></tr>
              </tbody>
            </table>
            <h2>Use each for what it does best</h2>
            <p>Let marketplaces introduce you to new buyers. Then give those buyers a reason to come to your
            own store for the second order: a thank-you card with your web address, a newsletter discount,
            a product line that is only sold there.</p>
            <p>Over time, more of your revenue comes from customers you own, at a margin you set. That is the
            business you can grow, and eventually the business you can sell.</p>
            <p>See what a store of your own costs on the <a href="[[web:pricing]]">pricing page</a>.</p>
        """,
    },
    {
        "title": "GST invoices for online sellers, explained simply",
        "slug": "gst-invoices-for-online-sellers",
        "author": "OneWooz Team",
        "days_ago": 13,
        "cover": ("#047857", "#bef264"),
        "excerpt": (
            "Inclusive or exclusive pricing, CGST and SGST or IGST, and what must be on "
            "every invoice: the essentials without the jargon."
        ),
        "content": """
            <p>If you are registered for GST, every order needs a proper tax invoice. Here is what that means
            for an online store, in plain language. It is a starting point, not tax advice, so check anything
            specific to your business with your accountant.</p>
            <h2>Which tax applies</h2>
            <table>
              <thead><tr><th>Shipping to</th><th>Tax charged</th><th>Example on ₹1,000 at 18%</th></tr></thead>
              <tbody>
                <tr><td>Your own state</td><td>CGST + SGST</td><td>₹90 + ₹90</td></tr>
                <tr><td>Another state</td><td>IGST</td><td>₹180</td></tr>
              </tbody>
            </table>
            <p>OneWooz works this out from your business address and the shopper's delivery address.</p>
            <h2>What every invoice must show</h2>
            <ul>
              <li>Your legal name, address and GSTIN.</li>
              <li>A unique invoice number and date.</li>
              <li>The buyer's name and address, and their GSTIN for a B2B sale.</li>
              <li>Each item with its taxable value, rate and tax amount.</li>
              <li>Your signature, physical or digital.</li>
            </ul>
            <h2>Setting it up once</h2>
            <ol>
              <li>Add your legal name, GSTIN and PAN under <strong>Settings</strong>.</li>
              <li>Upload your authorised signature so it prints on labels and invoices.</li>
              <li>On each product, tick <strong>Price includes GST</strong> if the price already contains the tax.</li>
            </ol>
            <p>From then on, the invoice prints with the shipping label and customers can download it from
            their account page once the order is delivered.</p>
            <p>More on choosing a pricing style in <a href="[[core:selling-guides/launching-a-d2c-brand/pricing-with-gst]]">pricing with GST</a>.</p>
        """,
    },
    {
        "title": "Building a B2B catalogue that gets enquiries",
        "slug": "b2b-catalogue-that-gets-enquiries",
        "author": "OneWooz Team",
        "days_ago": 7,
        "cover": ("#1e3a8a", "#93c5fd"),
        "excerpt": (
            "Business buyers shortlist suppliers on evidence. Specifications, certificates "
            "and a fast reply turn a catalogue visit into a quote request."
        ),
        "content": """
            <p>A procurement manager visiting your site is usually comparing you with three or four other
            suppliers. They are not looking to be inspired. They are looking for a reason to shortlist you,
            and a reason not to.</p>
            <h2>Answer the checklist before they ask</h2>
            <ul>
              <li><strong>Can you make what I need?</strong> Complete specifications on every product: dimensions, grades, tolerances, pack sizes.</li>
              <li><strong>Can I trust the quality?</strong> Certificates and test reports, downloadable as PDFs.</li>
              <li><strong>Are you a real, registered business?</strong> Legal name, GSTIN and MSME number, visible on the contact page.</li>
              <li><strong>Can you deliver at my volume?</strong> Minimum order quantities and lead times, stated plainly.</li>
            </ul>
            <h2>Make the enquiry effortless</h2>
            <p>Every product in the catalogue theme carries an enquiry button that captures the product and
            quantity with the buyer's details. Keep the form short; you can ask for the rest when you reply.</p>
            <h2>Reply like the supplier you want to be</h2>
            <p>Speed is a signal. A same-day quote with the full landed price, including GST, freight and lead
            time, tells the buyer how you will behave once they place an order. The sidebar counter on
            <strong>Enquiries</strong> shows what is waiting.</p>
            <blockquote>The supplier who replies first, with everything in one message, wins more often than
            the supplier with the lowest price.</blockquote>
            <p>For the full routine, read <a href="[[core:selling-guides/selling-to-businesses/enquiries-into-orders]]">turning
            enquiries into orders</a>.</p>
        """,
    },
    {
        "title": "Growing an email list from zero",
        "slug": "growing-an-email-list-from-zero",
        "author": "OneWooz Team",
        "days_ago": 2,
        "cover": ("#be185d", "#fda4af"),
        "excerpt": (
            "Social reach rises and falls with the algorithm. An email list is the "
            "audience you own. Here is how to start one with no subscribers at all."
        ),
        "content": """
            <p>Every brand eventually learns the same lesson: followers on a social platform are rented. Reach
            can halve overnight. An email address, on the other hand, keeps working for as long as the
            subscriber wants to hear from you.</p>
            <h2>Give people a reason to sign up</h2>
            <p>"Subscribe to our newsletter" is not a reason. Try one of these instead:</p>
            <ul>
              <li><strong>A welcome discount</strong> on the first order, shown in the newsletter popup.</li>
              <li><strong>Early access</strong> to new collections or restocks of sold-out sizes.</li>
              <li><strong>Something useful</strong>: a size guide, a care guide or a recipe card.</li>
            </ul>
            <h2>Ask in the right places</h2>
            <ol>
              <li>The newsletter popup, shown once to new visitors.</li>
              <li>The footer sign-up on every page.</li>
              <li>Checkout, where buyers are already sharing their email.</li>
            </ol>
            <h2>Send something worth opening</h2>
            <p>Start with one email a fortnight. Each should do one job: announce a launch, share a story about
            how a product is made, or offer a time-limited deal. Email marketing tracks opens and clicks for each
            campaign, so after a few sends you will know what your list responds to.</p>
            <h2>A realistic first target</h2>
            <p>Most new stores can collect their first 250 subscribers within three months from the popup and
            checkout alone. At that size, a single launch email can already bring in a noticeable wave of orders.</p>
            <p>Ready to write your first campaign? Start with <a href="[[core:selling-guides/growing-your-store/email-campaigns]]">email
            campaigns customers open</a>.</p>
        """,
    },
]
