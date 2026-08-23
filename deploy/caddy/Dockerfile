# Custom Caddy image with a shared Redis certificate store.
#
# On-demand TLS mints a certificate the first time a hostname is requested
# (storefront subdomains and verified custom domains). Across an autoscaling
# fleet every instance must read and write the SAME certificate store, or each
# would re-issue and hit Let's Encrypt rate limits — so certificates live in
# ElastiCache Redis via the storage-redis module, not on local disk.

FROM caddy:2.8-builder AS builder
RUN xcaddy build \
    --with github.com/pberkel/caddy-storage-redis

FROM caddy:2.8
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY deploy/caddy/Caddyfile /etc/caddy/Caddyfile
# Fail fast on a malformed Caddyfile at build time.
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
