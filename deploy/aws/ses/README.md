# Store → customer email on `via.onewooz.website` (Amazon SES)

Every store mails its own customers (order updates, store credit, owner
notices, storefront shopper sign-in codes, **and the email-marketing / outreach
campaigns**) from a per-store branded address on one shared, SES-verified
sending domain:

    Jack & Jones   ->  jackandjones@via.onewooz.website
    Nike           ->  nike@via.onewooz.website

The `&` is spelled out as `and`; every other non-`a-z0-9` character is dropped
and the words merged. The From header shows the store name as display name.

This is separate from OneWooz's own transactional mail (account sign-in, billing
receipts), which keeps `DEFAULT_FROM_EMAIL` and its existing SMTP provider.

Code: `tenancy/email.py` (the address), `core/mail.py` (`SESEmailBackend` +
`store_mail_connection`), wired at `commerce/views/customers.py`,
`storefront/customer_accounts.py` and the outreach worker `outreach/tasks.py`.
Prod config lives in `onewooz/settings/prod.py`
(`STORE_EMAIL_BACKEND=core.mail.SESEmailBackend`, region defaults to
`ap-south-1`, domain `via.onewooz.website`). No model fields were added, so
there is no migration.

**Transport selection** (`core.mail.store_mail_connection`): if
`ONEWOOZ_STORE_SMTP_HOST` is set, mail goes through that SMTP relay (this was the
old Cloudflare path). With it unset  the current task definitions  mail goes
through the SES backend below, authenticated by the ECS task role, with no SMTP
password to store anywhere.

---

## One-time AWS setup

Region **ap-south-1**, account **521831014282**. Run these yourself (the agent
does not touch prod). Each block is one command.

### 1. Verify the `via.onewooz.website` domain in SES (Easy DKIM)

```bash
aws sesv2 create-email-identity --region ap-south-1 --email-identity via.onewooz.website
```

Read back the three DKIM tokens you must publish as CNAMEs:

```bash
aws sesv2 get-email-identity --region ap-south-1 --email-identity via.onewooz.website --query "DkimAttributes.Tokens" --output text
```

### 2. Set a custom MAIL FROM (better DMARC alignment + bounce handling)

```bash
aws sesv2 put-email-identity-mail-from-attributes --region ap-south-1 --email-identity via.onewooz.website --mail-from-domain bounce.via.onewooz.website --behavior-on-mx-failure USE_DEFAULT_VALUE
```

### 3. Publish the DNS records for `onewooz.website`

Publish these six records wherever `onewooz.website`'s DNS is hosted (your
registrar, Cloudflare DNS, or Route 53). Fill in the three DKIM tokens from
step 1  each token `T` becomes a CNAME `T._domainkey.via.onewooz.website`
pointing at `T.dkim.amazonses.com`:

| Name                                      | Type  | Value                                              |
|-------------------------------------------|-------|----------------------------------------------------|
| `TOKEN1._domainkey.via.onewooz.website`   | CNAME | `TOKEN1.dkim.amazonses.com`                        |
| `TOKEN2._domainkey.via.onewooz.website`   | CNAME | `TOKEN2.dkim.amazonses.com`                        |
| `TOKEN3._domainkey.via.onewooz.website`   | CNAME | `TOKEN3.dkim.amazonses.com`                        |
| `bounce.via.onewooz.website`              | MX    | `10 feedback-smtp.ap-south-1.amazonses.com`        |
| `bounce.via.onewooz.website`              | TXT   | `v=spf1 include:amazonses.com ~all`                |
| `_dmarc.via.onewooz.website`              | TXT   | `v=DMARC1; p=none; rua=mailto:dmarc@onewooz.website` |

**If `onewooz.website` is in Route 53**, find the hosted-zone id:

```bash
aws route53 list-hosted-zones-by-name --dns-name onewooz.website --query "HostedZones[0].Id" --output text
```

Then save the batch below as `records.json` (replace `TOKEN1/2/3`) and apply it,
replacing `ZONE_ID`:

```json
{
  "Comment": "SES sending domain via.onewooz.website",
  "Changes": [
    { "Action": "UPSERT", "ResourceRecordSet": { "Name": "TOKEN1._domainkey.via.onewooz.website", "Type": "CNAME", "TTL": 1800, "ResourceRecords": [ { "Value": "TOKEN1.dkim.amazonses.com" } ] } },
    { "Action": "UPSERT", "ResourceRecordSet": { "Name": "TOKEN2._domainkey.via.onewooz.website", "Type": "CNAME", "TTL": 1800, "ResourceRecords": [ { "Value": "TOKEN2.dkim.amazonses.com" } ] } },
    { "Action": "UPSERT", "ResourceRecordSet": { "Name": "TOKEN3._domainkey.via.onewooz.website", "Type": "CNAME", "TTL": 1800, "ResourceRecords": [ { "Value": "TOKEN3.dkim.amazonses.com" } ] } },
    { "Action": "UPSERT", "ResourceRecordSet": { "Name": "bounce.via.onewooz.website", "Type": "MX", "TTL": 1800, "ResourceRecords": [ { "Value": "10 feedback-smtp.ap-south-1.amazonses.com" } ] } },
    { "Action": "UPSERT", "ResourceRecordSet": { "Name": "bounce.via.onewooz.website", "Type": "TXT", "TTL": 1800, "ResourceRecords": [ { "Value": "\"v=spf1 include:amazonses.com ~all\"" } ] } },
    { "Action": "UPSERT", "ResourceRecordSet": { "Name": "_dmarc.via.onewooz.website", "Type": "TXT", "TTL": 1800, "ResourceRecords": [ { "Value": "\"v=DMARC1; p=none; rua=mailto:dmarc@onewooz.website\"" } ] } }
  ]
}
```

```bash
aws route53 change-resource-record-sets --hosted-zone-id ZONE_ID --change-batch file://records.json
```

Confirm SES sees the domain as verified (may take a few minutes):

```bash
aws sesv2 get-email-identity --region ap-south-1 --email-identity via.onewooz.website --query "{Verified:VerifiedForSendingStatus,Dkim:DkimAttributes.Status,MailFrom:MailFromAttributes.MailFromDomainStatus}"
```

### 4. Let the ECS tasks send through SES

Attach the send policy (in this folder) to the shared task role. Both `web` and
`worker` run as `onewooz-task-role`, so this one grant covers all senders. The
policy restricts the role to `From` addresses on `via.onewooz.website` (store →
customer mail) **and** `onewooz.com` (OneWooz's own transactional mail  see the
platform section below).

```bash
aws iam put-role-policy --role-name onewooz-task-role --policy-name onewooz-ses-send --policy-document file://deploy/aws/ses/task-role-ses-policy.json
```

### 5. Leave the SES sandbox (required for real delivery)

While the account is in the SES sandbox, mail only reaches verified addresses.
Request production access once (per region):

```bash
aws sesv2 put-account-details --region ap-south-1 --production-access-enabled --mail-type TRANSACTIONAL --website-url https://onewooz.com --use-case-description "Transactional store notifications and merchant email marketing (order updates, store credit, sign-in codes, campaigns) sent by merchant stores from <brand>@via.onewooz.website." --additional-contact-email-addresses ayushmantiwari2312@gmail.com --contact-language EN
```

Check the current state any time:

```bash
aws sesv2 get-account --region ap-south-1 --query "{SendingEnabled:SendingEnabled,ProductionAccess:ProductionAccessEnabled,Quota:SendQuota}"
```

### 6. (Optional) Open / click / bounce tracking for the marketing tool

To feed opens, clicks and bounces back for the email-marketing analytics, create
an SES configuration set, then put its name into
`ONEWOOZ_STORE_EMAIL_SES_CONFIGURATION_SET` on the web + worker task defs (it is
shipped empty). Nothing else changes  the backend picks it up automatically.

```bash
aws sesv2 create-configuration-set --region ap-south-1 --configuration-set-name onewooz-store
```

---

## Platform transactional mail on `onewooz.com` (OTP / sign-in / receipts)

OneWooz's *own* transactional mail (login OTP, magic links, sign-up
verification, billing receipts) is moved off Hostinger SMTP onto SES. It is sent
by the same `core.mail.SESEmailBackend` (task-role auth, no SMTP password) when
`ONEWOOZ_EMAIL_USE_SES=true`, from `no-reply@onewooz.com`. The web + worker task
defs already carry `ONEWOOZ_EMAIL_USE_SES=true`, `ONEWOOZ_FROM_EMAIL=no-reply@onewooz.com`
and `ONEWOOZ_FROM_NAME=OneWooz`. See the email block in `onewooz/settings/prod.py`.

Verify the `onewooz.com` domain the same way as the store domain (Easy DKIM +
MAIL FROM), then publish the DKIM CNAMEs in the `onewooz.com` zone:

```bash
aws sesv2 create-email-identity --region ap-south-1 --email-identity onewooz.com
```

```bash
aws sesv2 get-email-identity --region ap-south-1 --email-identity onewooz.com --query "DkimAttributes.Tokens" --output text
```

```bash
aws sesv2 put-email-identity-mail-from-attributes --region ap-south-1 --email-identity onewooz.com --mail-from-domain bounce.onewooz.com --behavior-on-mx-failure USE_DEFAULT_VALUE
```

Publish, in the `onewooz.com` DNS zone: the three `TOKEN._domainkey.onewooz.com`
CNAMEs to `TOKEN.dkim.amazonses.com`, plus for `bounce.onewooz.com` an MX
`10 feedback-smtp.ap-south-1.amazonses.com` and a TXT `v=spf1 include:amazonses.com ~all`.
If `onewooz.com` already publishes an SPF TXT for Hostinger, **merge** the SES
include into that one record rather than adding a second SPF record. A domain
`_dmarc.onewooz.com` TXT (`v=DMARC1; p=none; rua=mailto:dmarc@onewooz.com`) is
recommended. The IAM policy in step 4 already authorises `*@onewooz.com`.

Confirm verification:

```bash
aws sesv2 get-email-identity --region ap-south-1 --email-identity onewooz.com --query "{Verified:VerifiedForSendingStatus,Dkim:DkimAttributes.Status,MailFrom:MailFromAttributes.MailFromDomainStatus}"
```

The SES sandbox exit (step 5) is account-wide, so once done it covers this
identity too. To keep Hostinger as a fallback, leave `ONEWOOZ_EMAIL_USE_SES`
unset/false  the SMTP path is untouched and still selected then.

## Collaboration invite mail on `myugcstore.com` (`no-reply@myugcstore.com`)

The mail that recognises a creator as a **referral partner** for a store  the
invite + expiring contract smart-link they use to go live at
`<subdomain>.myugcstore.com`  leaves from one fixed address on the referral
root domain, `no-reply@myugcstore.com`, with the store's name as the From
display name. It is sent by the same `core.mail.SESEmailBackend` (task-role auth,
no SMTP password) through `core.mail.store_mail_connection`, so `myugcstore.com`
must be a verified SES identity and admitted by the task-role policy.

Code: `commerce/emails.py` (`send_collaboration_invite`), templates
`templates/commerce/email/collaboration_invite.{txt,html}`, wired at
`commerce/views/growth.py` (invite create + resend). Config in
`onewooz/settings/base.py` (`REFERRAL_FROM_EMAIL`, defaults to
`no-reply@myugcstore.com`; `REFERRAL_EMAIL_DOMAIN` defaults to the referral root
`myugcstore.com`). No model fields, so no migration. `myugcstore.com` is already
in Route 53, so the DNS batch below applies straight into its hosted zone.

`mail.myugcstore.com` is the **custom MAIL FROM** (Return-Path / bounce) domain 
the same role `mail.onewooz.com` plays for the platform identity  not a separate
sending identity; the visible From stays `no-reply@myugcstore.com`.

### 1. Verify the `myugcstore.com` domain in SES (Easy DKIM)

```bash
aws sesv2 create-email-identity --region ap-south-1 --email-identity myugcstore.com
```

Read back the three DKIM tokens you must publish as CNAMEs:

```bash
aws sesv2 get-email-identity --region ap-south-1 --email-identity myugcstore.com --query "DkimAttributes.Tokens" --output text
```

### 2. Set the custom MAIL FROM `mail.myugcstore.com`

```bash
aws sesv2 put-email-identity-mail-from-attributes --region ap-south-1 --email-identity myugcstore.com --mail-from-domain mail.myugcstore.com --behavior-on-mx-failure USE_DEFAULT_VALUE
```

### 3. Publish the DNS records into the `myugcstore.com` hosted zone

Find the hosted-zone id:

```bash
aws route53 list-hosted-zones-by-name --dns-name myugcstore.com --query "HostedZones[0].Id" --output text
```

Edit `deploy/aws/route53-ses-myugcstore-records.json`  replace `TOKEN1/2/3`
with the three DKIM tokens from step 1 (each token `T` becomes a CNAME
`T._domainkey.myugcstore.com` → `T.dkim.amazonses.com`)  then apply it,
replacing `ZONE_ID`:

```bash
aws route53 change-resource-record-sets --hosted-zone-id ZONE_ID --change-batch file://deploy/aws/route53-ses-myugcstore-records.json
```

> If `myugcstore.com` already publishes an SPF TXT at the root or at
> `mail.myugcstore.com`, **merge** `include:amazonses.com` into that one record
> rather than adding a second SPF TXT.

Confirm SES sees it verified (may take a few minutes):

```bash
aws sesv2 get-email-identity --region ap-south-1 --email-identity myugcstore.com --query "{Verified:VerifiedForSendingStatus,Dkim:DkimAttributes.Status,MailFrom:MailFromAttributes.MailFromDomainStatus}"
```

### 4. Let the ECS tasks send from `myugcstore.com`

`deploy/aws/ses/task-role-ses-policy.json` now lists `*@myugcstore.com`
alongside the other two senders. Re-put the policy so the running role picks it
up:

```bash
aws iam put-role-policy --role-name onewooz-task-role --policy-name onewooz-ses-send --policy-document file://deploy/aws/ses/task-role-ses-policy.json
```

The SES sandbox exit (step 5 in the first section) is account-wide, so it
already covers this identity once done.

---

## Local testing

Dev keeps `STORE_EMAIL_BACKEND` on the console backend, so store→customer mail
**and collaboration invite mail** print to the runserver output  no SES needed.
To exercise the real path from dev, set
`ONEWOOZ_STORE_EMAIL_BACKEND=core.mail.SESEmailBackend` and
`ONEWOOZ_STORE_EMAIL_SES_REGION=ap-south-1` with credentials that can send.
