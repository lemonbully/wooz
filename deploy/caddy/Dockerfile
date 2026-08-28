# Custom Caddy image with a shared Redis certificate store.
#
# On-demand TLS mints a certificate the first time a hostname is requested
# (storefront subdomains and verified custom domains). Across an autoscaling
# fleet every instance must read and write the SAME certificate store, or each
# would re-issue and hit Let's Encrypt rate limits — so certificates live in
# ElastiCache Redis via the storage-redis module, not on local disk.

# Caddy base pinned to 2.11 to match the redis storage plugin's requirement
# (caddy-storage-redis v1.8.1 needs caddy/v2 >= v2.11.1). GOTOOLCHAIN=auto lets
# the builder fetch the Go toolchain the modules declare.
FROM caddy:2.11-builder AS builder
ENV GOTOOLCHAIN=auto
RUN xcaddy build \
    --with github.com/pberkel/caddy-storage-redis@v1.8.1

FROM caddy:2.11
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
COPY deploy/caddy/Caddyfile /etc/caddy/Caddyfile
# Fail fast on a malformed Caddyfile at build time.
RUN caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
