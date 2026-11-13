# CloudFront: cdn.onewooz.com + storefronts

Two distributions, created by `setup-cloudfront.ps1` (idempotent, safe to re-run).

| Distribution | Aliases | Origin | Caches |
|---|---|---|---|
| CDN | `cdn.onewooz.com` | `/static/*` → `onewooz-static-bucket`, `/*` → `onewooz-media-bucket` (OAC, private S3 access) | static: 1 year (content-hashed, immutable); uploads: 1 day min, 7 days default |
| Storefront | `onewooz.site`, `*.onewooz.site` | ALB `onewooz-alb` over HTTPS | only responses Django marks `public` (robots.txt, favicon, price history); cache key = Host + query |

## Run order

```powershell
# 1. CloudFront, certificates, OAC, bucket policies, DNS  (15-30 min, mostly waiting)
powershell -ExecutionPolicy Bypass -File deploy\aws\cdn\setup-cloudfront.ps1

# 2. Deploy the app: task defs already set ONEWOOZ_STATIC_CDN_DOMAIN / ONEWOOZ_MEDIA_CDN_DOMAIN
powershell -ExecutionPolicy Bypass -File deploy\aws\ha\deploy.ps1
```

Do step 1 before step 2. If you deploy first, every CSS/JS URL points at `cdn.onewooz.com` before it exists.

## Why storefront HTML is not edge-cached

Every storefront page is per visitor. It carries a CSRF token (footer newsletter, cart),
analytics visitor/session ids, the cart, and the signed-in customer. CloudFront ignores
`Vary: Cookie`, so caching that HTML would serve one shopper's cookies and cart to everyone else.
The storefront distribution still makes pages faster: TLS terminates at the nearest edge,
traffic rides the AWS backbone to Mumbai, responses are Brotli-compressed, and all CSS, JS,
fonts and images come from `cdn.onewooz.com`.

`core.middleware.EdgeDeliveryMiddleware` enforces this. Any response that sets a cookie or
varies on Cookie loses `public`/`s-maxage` and becomes `private`. Every HTML response also
gets a `Link: rel=preconnect` to the CDN.

Custom domains (`shop.brand.com`) still go through the Caddy edge (EIP `15.252.174.7`),
not CloudFront. Their CSS, JS and media still come from `cdn.onewooz.com`.

## Verify

```powershell
curl.exe -sI https://cdn.onewooz.com/static/css/onewooz.css   # expect: x-cache: Hit from cloudfront (2nd request)
curl.exe -sI https://<slug>.onewooz.site/                      # expect: via: ... cloudfront, x-cache: Miss from cloudfront
curl.exe -sI https://<slug>.onewooz.site/robots.txt
```

## Rollback

* CDN: remove `ONEWOOZ_STATIC_CDN_DOMAIN` / `ONEWOOZ_MEDIA_CDN_DOMAIN` from the three task
  defs and redeploy. URLs go back to S3 (the public-read grant on `static/*` is kept).
* Storefront: `aws route53 change-resource-record-sets --hosted-zone-id <onewooz.site zone> --change-batch file://deploy/aws/ha/route53-site-wildcard.json`
  points A back at the ALB. Then delete the two `AAAA` alias records (`onewooz.site`, `*.onewooz.site`) in Route53.
