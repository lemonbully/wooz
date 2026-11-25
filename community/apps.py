from django.apps import AppConfig


class CommunityConfig(AppConfig):
    """Ask OneWooz: the public question-and-answer community at onewooz.com/ask.

    Anyone can read. Posting needs a :class:`~community.models.Member`, created
    by confirming an email address with a one-time code. Instant AI answers
    come from :mod:`prompts.ask`; a visitor can then post the question to the
    community, where people (and the OneWooz team) answer and vote.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "community"
    verbose_name = "Ask OneWooz community"
