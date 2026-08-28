# OneWooz — AWS deployment state

Account `521831014282` · region `ap-south-1` (Mumbai) · budget target < ₹10k/mo.
Architecture: **ECS on a single EC2 (Graviton) host**, Caddy at the edge,
self-hosted Redis, SQS as the Celery broker, RDS MySQL, S3 media. Images are
pushed to ECR by you (no CodeBuild).

## What is provisioned (done)

| Thing | Identifier / value |
|---|---|
| ECS cluster | `onewooz-prod` (1 registered `t4g.medium` container instance) |
| App EIP (stable IP for DNS) | **`13.204.135.123`** → instance `i-06291e99471306070` |
| RDS MySQL | `onewoozdb2` · `onewoozdb2.c72wgcao0a70.ap-south-1.rds.amazonaws.com:3306` · db `onewooz` · user `woozuser` |
| RDS password | reset & stored in SSM `/onewooz/prod/DB_PASSWORD` (RDS and SSM match) |
| S3 media bucket | `onewooz-media-bucket` |
| ECR repos | `onewooz-app` (has tag `v1`), `onewooz-caddy` (**empty**) |
| SQS | `onewooz-celery` (broker; prefix `onewooz-`) |
| CloudWatch logs | `onewooz-ecs` (30-day retention) |
| Exec role | `ecsTaskExecutionRole` (+ inline `onewooz-ssm-read`) |
| Task role | `onewooz-task-role` (S3 + inline `onewooz-sqs-ses`) |
| Task definitions | `onewooz-app:1`, `onewooz-edge:1` (see `taskdef-*.json`) |
| ECS services | `onewooz-app`, `onewooz-edge` — ACTIVE, **desiredCount 0** |
| Secrets (SSM SecureString) | `/onewooz/prod/{SECRET_KEY,FIELD_ENCRYPTION_KEY,CADDY_ASK_SECRET,DB_PASSWORD,EMAIL_HOST,EMAIL_USER,EMAIL_PASSWORD}` |

## Security fixes applied
- RDS SG `onewooz-rds`: removed `0.0.0.0/0:3306`; now allows 3306 **only** from
  app SG `onewooz-app` (`sg-0323ab11d67c244b2`).
- Deleted stray Aurora PostgreSQL `database-1` (final snapshot
  `database-1-final-20260824`) — was billing and unrelated to OneWooz.

## YOUR manual steps (in order)

1. **Build & push images to ECR** (from a machine with Docker). ARM64 (Graviton):
   ```sh
   aws ecr get-login-password --region ap-south-1 \
     | docker login --username AWS --password-stdin 521831014282.dkr.ecr.ap-south-1.amazonaws.com
   # app
   docker buildx build --platform linux/arm64 -f deploy/Dockerfile \
     -t 521831014282.dkr.ecr.ap-south-1.amazonaws.com/onewooz-app:latest --push .
   # caddy (repo is currently empty — required for the edge service)
   docker buildx build --platform linux/arm64 -f deploy/caddy/Dockerfile \
     -t 521831014282.dkr.ecr.ap-south-1.amazonaws.com/onewooz-caddy:latest --push .
   ```
   (The task defs pull `:latest`. The existing `onewooz-app:v1` is not used unless retagged.)

2. **Set up SES** (email is `required=True`; the app will NOT boot without it):
   - Verify your sending domain in SES; add the DKIM CNAMEs at your DNS host.
   - Request production access (exit the SES sandbox).
   - Create SMTP credentials, then fill the placeholders:
     ```sh
     aws ssm put-parameter --region ap-south-1 --name /onewooz/prod/EMAIL_USER \
       --type SecureString --overwrite --value 'SES_SMTP_USERNAME'
     aws ssm put-parameter --region ap-south-1 --name /onewooz/prod/EMAIL_PASSWORD \
       --type SecureString --overwrite --value 'SES_SMTP_PASSWORD'
     ```

3. **DNS** — point these A-records at **`13.204.135.123`**:
   `onewooz.com`, `www.onewooz.com`, `app.onewooz.com`, `admin.onewooz.com`
   (and later `*.onewooz.site` for storefronts). If your real domain differs
   from `onewooz.com`, also set `ONEWOOZ_MARKETING_HOST` / `ONEWOOZ_APP_HOST` /
   `ONEWOOZ_ADMIN_HOST` (env in `taskdef-app.json`) and re-register.

4. **Admin IP allowlist** — admin refuses all requests until set. Add
   `ONEWOOZ_ADMIN_IP_ALLOWLIST` (comma-separated) to the `web` container env in
   `taskdef-app.json`, then re-register + update the service.

5. **Start it** — once images are pushed and SES creds are set:
   ```sh
   aws ecs update-service --region ap-south-1 --cluster onewooz-prod --service onewooz-app  --desired-count 1
   aws ecs update-service --region ap-south-1 --cluster onewooz-prod --service onewooz-edge --desired-count 1
   ```
   Watch: `aws logs tail onewooz-ecs --follow --region ap-south-1`
   Migrations run automatically on the `web` container (`RUN_MIGRATIONS=1`).

## Recommended (not blocking)
- Set RDS `onewoozdb2` `PubliclyAccessible=false` (SG already restricts access).
- Replace the `AdministratorAccess` IAM **user** + long-lived keys with a scoped
  deploy role and rotate the keys before go-live.
- Optional cleanup (harmless, tidies the account): duplicate VPC
  `vpc-026372ad45306b23b` + its subnets/IGW; duplicate RDS subnet groups
  `onewooz-public` / `onewooz-public1`; unused CodeBuild project +
  `codebuild-onewooz-app-build-service-role`.

## Re-registering a task def after editing a `taskdef-*.json`
```sh
aws ecs register-task-definition --region ap-south-1 --cli-input-json file://deploy/aws/taskdef-app.json
aws ecs update-service --region ap-south-1 --cluster onewooz-prod --service onewooz-app --task-definition onewooz-app
```
