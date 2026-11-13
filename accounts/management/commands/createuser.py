"""Create a OneWooz account from the command line.

Public sign-up is currently disabled (see ``accounts.views.sign_up``), so this
is the supported way to add an account. It asks for a name, an email and a
password  prompting interactively for anything not supplied as a flag  and
creates a normal (non-staff) user through the same ``UserManager`` the app
uses, so email normalisation and Argon2id hashing happen exactly as they do
in production.

Examples::

    python manage.py createuser
    python manage.py createuser --email owner@brand.com --name "Owner"
    python manage.py createuser --email owner@brand.com --name Owner --password s3cret --no-input
"""

from __future__ import annotations

import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User


class Command(BaseCommand):
    help = "Create a OneWooz user account interactively (name, email, password)."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Email address for the new account.")
        parser.add_argument("--name", help="Full name for the new account.")
        parser.add_argument(
            "--password",
            help="Password. Prompted for (twice, hidden) when omitted.",
        )
        parser.add_argument(
            "--staff",
            action="store_true",
            help="Mark the account as internal staff (is_staff=True).",
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Create a superuser (implies --staff).",
        )
        parser.add_argument(
            "--no-input",
            "--noinput",
            action="store_false",
            dest="interactive",
            help="Do not prompt; require --email, --name and --password.",
        )

    def handle(self, *args, **options):
        interactive = options["interactive"]

        email = (options.get("email") or "").strip()
        name = options.get("name")
        password = options.get("password")
        make_super = options["superuser"]
        make_staff = options["staff"] or make_super

        # --- Email --------------------------------------------------------
        if not email:
            if not interactive:
                raise CommandError("--email is required with --no-input.")
            email = self._prompt_email()
        else:
            email = User.objects.normalize_email(email)
            self._reject_if_taken(email)

        # --- Name ---------------------------------------------------------
        if name is None:
            name = self._prompt("Full name") if interactive else ""
        name = (name or "").strip()[:150]

        # --- Password -----------------------------------------------------
        if password is None:
            if not interactive:
                raise CommandError("--password is required with --no-input.")
            password = self._prompt_password(email=email, name=name)
        else:
            self._validate_password(password, email=email, name=name)

        with transaction.atomic():
            if make_super:
                user = User.objects.create_superuser(
                    email=email, password=password, full_name=name
                )
            else:
                user = User.objects.create_user(
                    email=email,
                    password=password,
                    full_name=name,
                    is_staff=make_staff,
                    signup_source="cli",
                )

        label = "Superuser" if make_super else ("Staff user" if make_staff else "User")
        self.stdout.write(self.style.SUCCESS(f"{label} created: {user.email}"))

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def _prompt(self, label: str) -> str:
        return input(f"{label}: ").strip()

    def _prompt_email(self) -> str:
        while True:
            raw = input("Email: ").strip()
            if not raw or "@" not in raw:
                self.stderr.write(self.style.ERROR("Enter a valid email address."))
                continue
            email = User.objects.normalize_email(raw)
            try:
                self._reject_if_taken(email)
            except CommandError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                continue
            return email

    def _prompt_password(self, *, email: str, name: str) -> str:
        while True:
            first = getpass.getpass("Password: ")
            if not first:
                self.stderr.write(self.style.ERROR("Password cannot be blank."))
                continue
            second = getpass.getpass("Password (again): ")
            if first != second:
                self.stderr.write(self.style.ERROR("Passwords did not match. Try again."))
                continue
            try:
                self._validate_password(first, email=email, name=name)
            except CommandError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                continue
            return first

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _reject_if_taken(self, email: str) -> None:
        if User.objects.filter(email=email).exists():
            raise CommandError(f"An account already exists for {email}.")

    def _validate_password(self, password: str, *, email: str, name: str) -> None:
        # Validate against a throwaway, unsaved User so similarity checks can
        # see the email and name without hitting the database.
        probe = User(email=email, full_name=name)
        try:
            validate_password(password, user=probe)
        except ValidationError as exc:
            raise CommandError("\n".join(exc.messages)) from exc
