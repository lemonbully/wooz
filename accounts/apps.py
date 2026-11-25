from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    verbose_name = "Accounts"

    def ready(self):
        # Browser push: queue a push for every new bell notification, and drop
        # a browser's subscription when its login session ends.
        #
        # How it works: accounts.signals listens for (1) a new Notification
        # row, which queues accounts.tasks.send_web_push after the commit, and
        # (2) a deleted Session row (sign-out, ended session, expiry), which
        # deletes that browser's PushSubscription.
        #
        # BROWSER PUSH: DISABLED (commented out, not removed). With this import
        # off, no notification is pushed and no push code runs on save.
        # Uncomment to re-enable.
        # from . import signals  # noqa: F401
        pass
