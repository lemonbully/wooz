"""Rupee formatting for plan cards and bills: ``{% load billing_money %}``."""

from django import template

from billing.fee_bands import format_inr, range_label

register = template.Library()


@register.filter(name="inr")
def inr(value) -> str:
    """``{{ 1200|inr }}`` → ``₹1,200``; ``{{ 299.5|inr }}`` → ``₹299.50``."""
    return format_inr(value)


@register.simple_tag(name="inr_range")
def inr_range(min_value, max_value=None) -> str:
    """``{% inr_range b.min_price b.max_price %}`` → ``₹0 – ₹299`` (or ``₹2,000 and above``)."""
    return range_label(min_value, max_value)
