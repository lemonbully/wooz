"""Turn a raw ``User-Agent`` string into something a person can read.

The security page shows an owner where and how they have signed in. A raw UA
string ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 …") is
noise to them, so this reduces it to a plain phrase like *"Chrome on Windows"* or
*"Safari on iPhone"*. It is deliberately small and dependency-free  a best-effort
label, never a security decision  and always returns *something* rather than
raising.
"""

from __future__ import annotations

__all__ = ["friendly", "browser_of", "os_of", "is_mobile"]


def _first(ua: str, pairs) -> str:
    """The label of the first ``(needle, label)`` whose needle is in ``ua``."""
    for needle, label in pairs:
        if needle in ua:
            return label
    return ""


# Order matters: more specific tokens must come before the generic ones they
# contain  Edge/Brave/Opera all also carry "Chrome"; Chrome carries "Safari".
_BROWSERS = [
    ("Edg/", "Microsoft Edge"),
    ("EdgA/", "Microsoft Edge"),
    ("OPR/", "Opera"),
    ("Opera", "Opera"),
    ("Brave", "Brave"),
    ("Vivaldi", "Vivaldi"),
    ("SamsungBrowser", "Samsung Internet"),
    ("YaBrowser", "Yandex"),
    ("CriOS", "Chrome"),
    ("Chrome", "Chrome"),
    ("Chromium", "Chromium"),
    ("FxiOS", "Firefox"),
    ("Firefox", "Firefox"),
    ("Version/", "Safari"),  # Apple's Safari carries "Version/x Safari/y"
    ("Safari", "Safari"),
]

_OSES = [
    ("Windows NT 10.0", "Windows"),
    ("Windows NT", "Windows"),
    ("Windows", "Windows"),
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("iPod", "iPhone"),
    ("Mac OS X", "macOS"),
    ("Macintosh", "macOS"),
    ("CrOS", "ChromeOS"),
    ("Android", "Android"),
    ("Linux", "Linux"),
]

_MOBILE_HINTS = ("Mobile", "iPhone", "iPad", "iPod", "Android")


def browser_of(ua: str) -> str:
    return _first(ua or "", _BROWSERS)


def os_of(ua: str) -> str:
    return _first(ua or "", _OSES)


def is_mobile(ua: str) -> bool:
    ua = ua or ""
    return any(h in ua for h in _MOBILE_HINTS)


def friendly(ua: str) -> str:
    """A short human label for a UA string, e.g. "Chrome on Windows".

    Falls back gracefully: browser-only, OS-only, or "Unknown device" when the
    string is empty or unrecognised (a bot, a script, a stripped header).
    """
    ua = (ua or "").strip()
    if not ua:
        return "Unknown device"
    browser = browser_of(ua)
    os_name = os_of(ua)
    if browser and os_name:
        return f"{browser} on {os_name}"
    if browser:
        return browser
    if os_name:
        return os_name
    return "Unknown device"
