# OneWooz — AWS deployment

Target: **good performance, autoscaling, background tasks, custom-domain
auto-SSL — under ₹10,000/month.** Region **ap-south-1 (Mumbai)**.

> Status: the **runtime layer** (this document, Docker images, Caddy edge,
> local compose, the Caddy ask endpoint) is built. The **Terraform** and
> **GitHub Actions** layers are the next step (see "Remaining work").

---

## Decisions (driven by the ₹10k/month budget)

| Question | Choice | Why |
|---|---|---|
| Package vs CI/CD | **Both**: Docker image + GitHub Actions | They're complementary, not either/or (see below) |
| Orchestration | **ECS on EC2**, Graviton `t4g` | Managed rolling deploys at no extra infra cost; **Fargate rejected** — costs more per unit and is harder to keep under budget |
| Environments | **Production only** now | Staging would ~double infra cost; Terraform is written as reusable modules so staging is one tfvars file later |
| Load balancer | **NLB** (L4 TCP passthrough) | Caddy must terminate TLS for arbitrary customer domains; an ALB can only serve certs for domains you own |
| TLS | **Caddy on-demand**, cert store in Redis | Per-domain certs minted on first hit, shared across the fleet via ElastiCache |
| Secrets | **SSM Parameter Store** (SecureString) | Free tier; Secrets Manager is $0.40/secret/mo |
| NAT | **None** — app tasks in public subnets, locked SGs, SSM for access | A NAT Gateway is ~₹2,700/mo; avoiding it is the single biggest saving |
| DB / cache | RDS `db.t4g.micro` single-AZ · ElastiCache `cache.t4g.micro` | Cheapest Graviton tiers that perform; Multi-AZ is a documented upgrade |

### Docker **or** GitHub for CI/CD? — both
- **Docker** = how the app is packaged and run (one image, role chosen at
  runtime: `web` / `worker` / `beat`). Plus a custom **Caddy** image with the
  Redis cert-storage plugin.
- **GitHub Actions** = the pipeline that tests, **builds those images, pushes to
  Amazon ECR**, then updates the ECS services and runs migrations.
- Flow: `git push` → Actions (test → build → push ECR → deploy → migrate).

---

## Architecture

```
customer DNS ─┐                          Route 53 (onewooz.com, onewooz.site)
              ▼
     Network Load Balancer  (TCP :443 passthrough + :80)
              ▼
     ECS service "web"  (Graviton EC2, autoscaling 1→N on CPU)
        ├─ Caddy      on-demand TLS · cert store in Redis · asks Django /internal/caddy/ask
        └─ Gunicorn   Django (same task → localhost)         X-Forwarded-Proto https
              │
     ┌────────┼─────────────────────────────┐
     ▼        ▼                              ▼
  RDS MySQL   ElastiCache Redis           SQS  ← Celery broker (IAM role, no creds)
 (t4g.micro) (cache/ratelimit/certs)        ▲
                                    ECS "worker" (Celery) + ECS "beat" (1 replica)
S3 (media, private) · SSM Parameter Store (secrets) · ECR (images) · CloudWatch (logs)
```

- **Autoscaling:** ECS service + EC2 capacity-provider ASG, target-tracking on
  CPU (~60%). Baseline 1× `t4g.medium`, burst to 2–3 under load.
- **Background tasks:** `worker` scales with queue depth; `beat` is pinned to a
  single replica (desired count 1) so schedules don't double-fire.
- **Custom domains + SSL:** customer CNAMEs/A-records their domain to the NLB →
  first HTTPS hit triggers Caddy on-demand issuance → Caddy calls the ask
  endpoint → issues once ownership is verified, and caches the cert in Redis.

---

## Estimated monthly cost (ap-south-1, ≈ ₹84/$)

| Item | ~USD | ~INR |
|---|--:|--:|
| NLB (+ modest LCU) | 18 | 1,500 |
| EC2 1× t4g.medium baseline (on-demand) | 24 | 2,000 |
| RDS db.t4g.micro single-AZ + 20 GB gp3 | 15 | 1,250 |
| ElastiCache cache.t4g.micro | 12 | 1,000 |
| Data transfer (NLB + egress, low traffic) | 8 | 670 |
| CloudWatch logs (7-day retention) | 3 | 250 |
| S3 + Route 53 (2 zones) + SQS + ECR + SSM | 4.5 | 380 |
| **Baseline total** | **≈ 84** | **≈ ₹7,050** |

Headroom to ₹10k covers autoscaling bursts and traffic growth. **Levers if you
approach the cap:** a 1-yr Compute Savings Plan on the steady instance (~40%
off EC2), drop baseline to `t4g.small`, Spot for the worker ASG, and the free
S3 gateway VPC endpoint to cut S3 transfer. **Upgrade when revenue allows:**
Multi-AZ RDS (+~₹1,250/mo) for database HA.

---

## What's built now (this repo)

| File | Purpose |
|---|---|
| `deploy/Dockerfile` | App image (multi-stage, non-root, Graviton-ready); role via entrypoint |
| `deploy/entrypoint.sh` | `web` / `worker` / `beat` / `migrate` roles; migrations gated, not automatic |
| `deploy/caddy/Dockerfile` | Custom Caddy with the Redis cert-storage plugin |
| `deploy/caddy/Caddyfile` | Production edge: on-demand TLS, ask-gated, security headers |
| `deploy/caddy/Caddyfile.local` | Local edge with Caddy's internal CA |
| `docker-compose.yml` | Local production-parity stack (MySQL+Redis+web+worker+beat+Caddy) |
| `deploy/.env.example` | Every environment variable, documented |
| `core.middleware.CaddyAskMiddleware` | The on-demand-TLS ask endpoint, before host routing |

App-code changes made: added `CaddyAskMiddleware` (registered before
`HostRoutingMiddleware`); made the Celery broker overridable
(`ONEWOOZ_CELERY_BROKER_URL`) so local/staging can use Redis instead of SQS.
No models, no migrations.

### Try it locally
```bash
cp deploy/.env.example .env          # then set the three secrets
docker compose up --build            # web migrates itself locally (RUN_MIGRATIONS=1)
# open https://app.onewooz.localhost  (accept the local Caddy CA once)
```

---

## Remaining work (next steps I'll build)

1. **`infra/terraform/`** — VPC (public app subnets, private data subnets, S3
   gateway endpoint), ECS cluster + EC2 capacity provider (t4g ASG), NLB +
   target group, RDS, ElastiCache, SQS queues, S3 bucket, ECR repo, IAM
   (task/exec/instance roles, least-privilege for SQS+S3+SSM), SSM parameters,
   Route 53 records, CloudWatch log groups, autoscaling policies.
2. **ECS task definitions** — `web` (Caddy + Gunicorn containers, shared
   network), `worker`, `beat`.
3. **`.github/workflows/`** — `ci.yml` (lint + `pytest`); `deploy.yml` (build →
   push ECR → register task defs → update services → run one-off `migrate` task).
4. **Runbook** — bootstrap order, DNS cutover, first deploy, custom-domain flow.

## Prerequisites you own
- Register **`onewooz.com`** and **`onewooz.site`**; create Route 53 hosted
  zones. Point the platform hosts + `*.onewooz.site` at the NLB.
- An email sender (SES in ap-south-1, or SMTP) for `ONEWOOZ_EMAIL_*`.
- Set a real `ACME_EMAIL` for Let's Encrypt.

## Honest caveats
- **Custom-domain ownership verification** (the `domains` app that populates the
  verified-domain set) is **Phase 6 and not built**. The edge is fully wired and
  the ask endpoint authorises platform + `*.onewooz.site` hosts today; arbitrary
  custom domains are denied until that app lands (by design — it prevents ACME
  abuse). Wiring is complete; only the verification logic remains.
- The Caddy Redis-storage plugin (`pberkel/caddy-storage-redis`) directive
  syntax can vary by version — pin it in `deploy/caddy/Dockerfile` and adjust
  the `storage redis { … }` block to the pinned version if `caddy validate`
  complains.
