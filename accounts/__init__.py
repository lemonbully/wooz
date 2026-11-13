"""Authentication and account management for OneWooz site owners.

Three ways in, all landing on the same account:

* password (Argon2id) with optional TOTP as a second factor
* passkeys / WebAuthn  phishing-resistant, usable as a second factor or,
  once enrolled, as the whole of sign-in
* email magic link / one-time code  for owners who never want a password

Internal staff use the same user records but reach the admin surface through
``custom_admin``, which requires a passkey and ignores the other two methods.
"""
