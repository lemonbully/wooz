from django.apps import AppConfig


class PromptsConfig(AppConfig):
    """Admin-managed AI prompts, and what OneWooz generates from them.

    The first generation type is a store's ``llms.txt`` (see
    :mod:`prompts.llms_txt`): an admin writes the prompt once in Django admin
    (Prompts → type "LLMs.txt generation") and every qualifying store's file is
    generated from it with the OpenAI API.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "prompts"
    verbose_name = "Prompts"

    def ready(self):
        # Catalogue changes regenerate the store's llms.txt (prompts.signals).
        from django.db.models.signals import m2m_changed, post_delete, post_save

        from commerce.models import Category, Product

        from . import signals

        post_save.connect(signals.product_saved, sender=Product, dispatch_uid="prompts.llms_txt.product_saved")
        post_delete.connect(signals.catalogue_deleted, sender=Product, dispatch_uid="prompts.llms_txt.product_deleted")
        post_save.connect(signals.category_saved, sender=Category, dispatch_uid="prompts.llms_txt.category_saved")
        post_delete.connect(signals.catalogue_deleted, sender=Category, dispatch_uid="prompts.llms_txt.category_deleted")
        m2m_changed.connect(
            signals.product_categories_changed, sender=Product.categories.through,
            dispatch_uid="prompts.llms_txt.product_categories_changed",
        )
