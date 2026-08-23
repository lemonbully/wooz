"""Account, credential and sign-in-history models."""

from __future__ import annotations

import hashlib

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from core.fields import EncryptedCharField
from core.ids import uuid7
from core.models import BaseModel

__all__ = [
    "User",
    "UserManager",
    "TOTPDevice",
    "WebAuthnCredential",
    "TrustedDevice",
    "LoginActivity",
]


def _hash_token(raw: str) -> str:
    """One-way hash for values we must compare but must not be able to read.

    Used for trusted-device cookies. If this table leaks, the hashes are not
    usable as cookies, so the leak does not become a set of live sessions.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class UserManager(BaseUserManager):
    use_in_migrations = True

    def normalize_email(self, email: str) -> str:  # type: ignore[override]
        """Lowercase the whole address, not just the domain.

        Django's default lowercases only the domain, treating ``A@x.com`` and
        ``a@x.com`` as different accounts. Every mail provider a customer of
        ours will use treats them as the same mailbox, so allowing both to
        register is an account-confusion bug waiting to happen.
        """
        email = super().normalize_email(email or "")
        return email.strip().lower()

    def _create(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            # A passwordless account is legitimate — magic link and passkey
            # users may never set one — but it must be unusable rather than
            # blank, so no empty-password login can ever succeed.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        if extra.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, BaseModel):
    """A OneWooz account.

    One account owns one or more sites. There is no organisation layer yet;
    when one arrives it goes between this model and ``tenancy.Site`` rather
    than replacing either.
    """

    email = models.EmailField(unique=True, max_length=254)
    full_name = models.CharField(max_length=150, blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")

    is_active = models.BooleanField(
        default=True,
        help_text="Unchecking this blocks every sign-in method immediately.",
    )
    is_staff = models.BooleanField(
        default=False,
        help_text="Internal OneWooz personnel. Required to reach the admin surface.",
    )

    email_verified_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)

    # When true, a second factor is demanded even if the account has none
    # enrolled — the user is sent to enrolment instead of the dashboard.
    require_mfa = models.BooleanField(default=False)

    signup_source = models.CharField(max_length=40, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        indexes = [models.Index(fields=("email",))]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        return super().save(*args, **kwargs)

    def set_password(self, raw_password):
        super().set_password(raw_password)
        self.password_changed_at = timezone.now()

    @property
    def display_name(self) -> str:
        return self.full_name or self.email.split("@", 1)[0]

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def has_totp(self) -> bool:
        return self.totp_devices.filter(confirmed_at__isnull=False).exists()

    @property
    def has_passkey(self) -> bool:
        return self.passkeys.exists()

    @property
    def has_second_factor(self) -> bool:
        return self.has_passkey or self.has_totp

    def mfa_is_required(self) -> bool:
        """Staff always need a second factor; everyone else opts in."""
        return self.is_staff or self.require_mfa or self.has_second_factor


class TOTPDevice(BaseModel):
    """An authenticator-app second factor.

    The shared secret is encrypted at rest: unlike a password it must be
    recoverable to verify a code, so hashing is not an option and encryption
    is the strongest available protection.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="totp_devices")
    label = models.CharField(max_length=60, default="Authenticator app")
    secret = EncryptedCharField(max_length=255)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # Highest time-step already accepted. A code is valid for 30 seconds and
    # would otherwise be replayable for the rest of its window; refusing any
    # step at or below this one makes each code single-use.
    last_used_step = models.BigIntegerField(default=0)

    class Meta:
        verbose_name = "TOTP device"

    def __str__(self) -> str:
        return f"{self.label} ({self.user.email})"


class WebAuthnCredential(BaseModel):
    """A registered passkey.

    Phishing-resistant because the browser binds the signature to the origin:
    a credential registered for onewooz.com cannot be used against a lookalike
    domain, no matter how convincing the page.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="passkeys")
    label = models.CharField(max_length=60, default="Passkey")

    credential_id = models.CharField(max_length=512, unique=True, db_index=True)
    public_key = models.TextField()

    # Authenticators that keep a counter increment it on every assertion. A
    # value at or below the stored one signals a cloned authenticator; the
    # assertion is rejected and the event recorded. Many modern passkeys always
    # report zero, which is permitted by the specification and skips the check.
    sign_count = models.BigIntegerField(default=0)

    transports = models.JSONField(default=list, blank=True)
    aaguid = models.CharField(max_length=64, blank=True, default="")
    is_backed_up = models.BooleanField(default=False)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "passkey"
        verbose_name_plural = "passkeys"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.label} ({self.user.email})"


class TrustedDevice(BaseModel):
    """A browser the user has told us to stop challenging for a second factor.

    Stores only a hash of the cookie value, so this table cannot be turned back
    into working cookies.
    """

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="trusted_devices"
    )
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    label = models.CharField(max_length=120, blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    @classmethod
    def issue(cls, user, *, raw_token: str, ttl_days: int, request=None):
        return cls.objects.create(
            user=user,
            token_hash=_hash_token(raw_token),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:1000] if request else ""),
            ip_address=(request.META.get("REMOTE_ADDR") if request else None),
            expires_at=timezone.now() + timezone.timedelta(days=ttl_days),
        )

    @classmethod
    def lookup(cls, user, raw_token: str):
        if not raw_token:
            return None
        return cls.objects.filter(
            user=user,
            token_hash=_hash_token(raw_token),
            expires_at__gt=timezone.now(),
        ).first()

    def __str__(self) -> str:
        return f"{self.label or 'device'} ({self.user.email})"


class LoginActivity(BaseModel):
    """Every sign-in attempt, successful or not.

    Kept for the account owner to review, and as the evidence base for
    detecting credential stuffing. The attempted email is stored even when no
    matching account exists, which is what makes an attack against many
    accounts from one address visible.
    """

    class Outcome(models.TextChoices):
        SUCCESS = "success", "Success"
        BAD_PASSWORD = "bad_password", "Wrong password"
        NO_ACCOUNT = "no_account", "No such account"
        INACTIVE = "inactive", "Account disabled"
        MFA_REQUIRED = "mfa_required", "Second factor requested"
        MFA_FAILED = "mfa_failed", "Second factor failed"
        RATE_LIMITED = "rate_limited", "Rate limited"
        TOKEN_INVALID = "token_invalid", "Link invalid or expired"

    class Method(models.TextChoices):
        PASSWORD = "password", "Password"
        TOTP = "totp", "Authenticator app"
        PASSKEY = "passkey", "Passkey"
        MAGIC_LINK = "magic_link", "Magic link"
        EMAIL_OTP = "email_otp", "Email code"

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="login_activity",
    )
    email_attempted = models.CharField(max_length=254, blank=True, default="", db_index=True)
    method = models.CharField(max_length=20, choices=Method.choices)
    outcome = models.CharField(max_length=20, choices=Outcome.choices, db_index=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.TextField(blank=True, default="")
    device = models.CharField(max_length=120, blank=True, default="")
    country_code = models.CharField(max_length=2, blank=True, default="")

    #: Set when the attempt looks unlike the account's history — new country,
    #: new device, or a burst of failures preceding it.
    is_suspicious = models.BooleanField(default=False, db_index=True)
    surface = models.CharField(max_length=20, blank=True, default="")
    request_id = models.CharField(max_length=36, blank=True, default="")

    class Meta:
        verbose_name = "login activity"
        verbose_name_plural = "login activity"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("email_attempted", "-created_at")),
            models.Index(fields=("ip_address", "-created_at")),
            models.Index(fields=("outcome", "-created_at")),
        ]

    def __str__(self) -> str:
        return f"{self.email_attempted or '-'} {self.method} {self.outcome}"
