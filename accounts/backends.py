"""Authentication backends."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class EmailPasswordBackend(ModelBackend):
    """Password authentication keyed on email.

    Inherits ``ModelBackend`` for two behaviours worth keeping:

    * when no account matches, it still runs the password hasher against a
      throwaway user, so a caller cannot tell "no such account" from "wrong
      password" by measuring how long the response took;
    * ``user_can_authenticate`` rejects inactive accounts, so deactivating a
      user takes effect on the next request rather than the next session
      expiry.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = username or kwargs.get("email") or kwargs.get(UserModel.USERNAME_FIELD)
        if email:
            email = email.strip().lower()
        return super().authenticate(request, username=email, password=password, **kwargs)
