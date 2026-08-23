# OneWooz

A SaaS that builds and publishes a **working online store** for D2C brands and
B2B retailers. The owner signs up, answers a few questions, clicks once, and
gets a live site — products, checkout, payments, shipping — on a OneWooz
subdomain or their own domain. No developer, no page builder.

It is a clean rebuild of the `website` app inside the user's earlier 1matrix
monolith. **That reference is not in this repo:**
`C:\Users\hp5cd\OneDrive\Desktop\1matrix\1matrix\website` (+ its templates at
`1matrix\templates\website`, and a useful `1matrix\architecture.md`). Consult it
before implementing any integration — it is the only record of how payments,
shipping, custom-domain TLS and label generation behave in production. Do not
copy its structure: 841 KB of views in one module, and several defaults that
are outright defects (see the plan's "fix rather than port" table).

## Working agreement

- **Write files only.** Never run `makemigrations`, `migrate`, `runserver`,
  `check`, `collectstatic`, or any command that boots Django. The user runs
  those. Parse-only syntax checks are fine.
- **No manual setup steps.** Anything that would need hand-creating, generate
  automatically instead — as `settings/env.py:get_or_create_dev_secret` does
  for dev secrets.
- Migrations are not committed yet; the user generates them.

## Architecture

Django 5.2 LTS · Python 3.13 · MySQL (SQLite in dev) · Redis · Celery/SQS ·
server-rendered templates. **No front-end build step** — CSS is hand-authored
at `onewooz/static/css/onewooz.css` and committed.

### Four surfaces, one process

| Host (prod) | Dev path | URLconf | App |
|---|---|---|---|
| `onewooz.com` | `/` | `onewooz.urls.marketing` | `web` |
| `app.onewooz.com` | `/user/` | `onewooz.urls.app` | `ud` + `accounts` |
| `admin.onewooz.com` | `/admin/` | `onewooz.urls.admin` | `custom_admin` |
| `<slug>.onewooz.site` | `/s/<slug>/` | `onewooz.urls.storefront` | (phase 2) |

`core.middleware.HostRoutingMiddleware` resolves the Host and sets
`request.urlconf`. A storefront request structurally cannot reach a dashboard
route. On a loopback host in DEBUG it routes by **path prefix** instead, moving
the prefix into `SCRIPT_NAME` so `reverse()` and `{% url %}` reproduce it.

**Therefore: never hardcode a path.** Use `{% url %}` / `reverse()` within a
surface, and `{% surface_url 'app' '/sign-up/' %}` (or `core.links`) across
surfaces. A literal `/sign-in/` breaks local development.

Storefronts live on a *different registrable domain* on purpose — a
public-suffix boundary, so tenant script cannot reach the dashboard session.

## Conventions that are enforced, not suggested

- **UUIDv7 primary keys everywhere** (`core.ids`). No sequential integers, so
  nothing is enumerable. v7 not v4 because InnoDB clusters on the PK.
- **Tenant scoping is structural.** Models inherit `core.models.TenantScopedModel`;
  their queryset *refuses to evaluate* unless narrowed via `.for_site(site)`.
  Crossing tenants requires the explicit, greppable `.across_all_sites()`.
  System checks in `core/checks.py` fail startup if a model opts out.
- **Capability URLs carry purpose-salted signed tokens** (`core.tokens`), never
  raw ids. A token minted for one purpose cannot be replayed against another.
- **Third-party credentials use `core.fields.EncryptedTextField`** (HKDF from a
  dedicated key, `v1:` version prefix, raises on failure — the reference
  returned the ciphertext, so key rotation silently produced garbage).
- **Rate limits are declared in `settings.RATE_LIMITS`**, not invented at call
  sites. Enforce with `core.ratelimit`.
- **Never disclose whether an email has an account.** Sign-in, magic link and
  sign-up all respond identically.
- Every inline `<script>` needs `nonce="{{ csp_nonce }}"` or the CSP blocks it.
- Multi-line `{# #}` does not work in Django templates — use `{% comment %}`.

## Auth

Password (Argon2id) + TOTP, passkeys/WebAuthn, and email magic link / OTP.
No Google OAuth, deliberately. Admin accepts **passkeys only**, behind an IP
allowlist checked before authentication, with an idle timeout and a full audit
trail (`core.audit` → append-only `AuditEvent`).

## Design system

One accent (`#4b2bf5`), warm neutrals, hairline borders, two radii. Fluid
`clamp()` type so there is no breakpoint where the page changes character;
media queries live on layout primitives (`.grid--3`, `.split`), not on
individual sections. Direction: Mobbin restraint + Templafy section rhythm.

## Status

Phases 0–1 complete: foundation, host routing, security primitives, accounts,
marketing site. **Next: Phase 2** — `tenancy.Site`, the versioned Pydantic
content schema, and the industry content packs.

Those content packs are the product. The reference's "one click" produced
literal `<Insert FAQ question here>` placeholder text; OneWooz only keeps its
promise if a generated store is presentable without editing.

Full plan: `~/.claude/plans/before-you-implement-anything-encapsulated-candle.md`
