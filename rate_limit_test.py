#!/usr/bin/env python3
"""OneWooz rate-limit tester. Standard library only, no pip install needed.

Examples:
  # Local dev (path-prefix routing)
  python ratelimit_test.py --app http://localhost:8000/user --store http://localhost:8000/s/demo

  # Production
  python ratelimit_test.py --app https://app.onewooz.com --store https://demo.onewooz.site

  # Run only some tests
  python ratelimit_test.py --app ... --store ... --only login_identifier,xff_bypass

Tests (expected limits from settings.RATE_LIMITS):
  login_identifier  same fake email, wrong password    expect 429 after ~15   (login.identifier 15/900s)
  login_ip          random fake emails, same IP        expect 429 after ~50   (login.ip 50/300s)
  xff_bypass        random emails + spoofed X-Forwarded-For; if these still pass
                    after login_ip blocked you, the per-IP limit is bypassable
  analytics         storefront /api/track/ beacon      expect 429 after ~300  (analytics.ingest 300/60s)
  coupon            bogus codes on /cart/coupon/       currently NO limit, expect no 429
  pincode           /api/pincode/ (opt-in: --pincode, calls the store's courier API)

Side effects: login tests write LoginActivity rows and block YOUR IP from
sign-in for ~5 minutes. Fake emails are random @example.invalid addresses, so
no real account is ever touched.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import random
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "OneWooz-RateLimit-Test/1.0"
CSRF_COOKIES = ("__Host-ow_csrf", "ow_csrf")


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

class Client:
    """A cookie-keeping client that knows OneWooz's CSRF cookie names."""

    def __init__(self, timeout: float = 20.0):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.timeout = timeout

    def csrf(self) -> str:
        for c in self.jar:
            if c.name in CSRF_COOKIES:
                return c.value
        return ""

    def request(self, method, url, *, data=None, json_body=None, headers=None):
        hdrs = {"User-Agent": UA, **(headers or {})}
        body = None
        if json_body is not None:
            body = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        elif data is not None:
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers.get("Retry-After")
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Retry-After")
        except Exception as e:  # network error, timeout
            return f"ERR:{type(e).__name__}", None


def origin_of(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def fake_email() -> str:
    return f"rl-test-{secrets.token_hex(6)}@example.invalid"


def random_ip() -> str:
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def summarise(name, results, expect_block_after=None, expect_no_block=False):
    statuses = [s for s, _ in results]
    first_429 = next((i + 1 for i, s in enumerate(statuses) if s == 429), None)
    retry = next((r for s, r in results if s == 429 and r), None)
    counts = {}
    for s in statuses:
        counts[s] = counts.get(s, 0) + 1

    print(f"\n=== {name} ===")
    print(f"  requests sent : {len(statuses)}")
    print(f"  status counts : {counts}")
    print(f"  first 429 at  : {'#' + str(first_429) if first_429 else 'never'}")
    if retry:
        print(f"  Retry-After   : {retry}s")
    elif first_429:
        print("  Retry-After   : (missing - consider adding it)")

    if expect_no_block:
        verdict = "GAP CONFIRMED (no limit)" if not first_429 else "LIMITED (gap seems fixed)"
    elif expect_block_after is not None:
        if first_429 is None:
            verdict = "FAIL - never blocked"
        elif first_429 <= expect_block_after * 2 + 1:
            # Fixed windows allow up to 2x across a boundary, or the counter may
            # already hold hits from an earlier run.
            verdict = "PASS"
        else:
            verdict = "WARN - blocked much later than expected"
    else:
        verdict = ""
    if verdict:
        print(f"  verdict       : {verdict}")
    return first_429


def run_parallel(fn, n, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda i: fn(i), range(n)))


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def sign_in_client(app_base: str) -> tuple[Client, str]:
    url = app_base.rstrip("/") + "/sign-in/"
    c = Client()
    status, _ = c.request("GET", url)
    if not c.csrf():
        sys.exit(f"Could not get a CSRF cookie from {url} (status {status}). Check --app.")
    return c, url


def post_sign_in(c: Client, url: str, email: str, extra_headers=None):
    headers = {"Referer": url, "Origin": origin_of(url), **(extra_headers or {})}
    return c.request(
        "POST", url,
        data={"csrfmiddlewaretoken": c.csrf(), "email": email, "password": "wrong-" + secrets.token_hex(4)},
        headers=headers,
    )


def test_login_identifier(args):
    c, url = sign_in_client(args.app)
    email = fake_email()
    results = [post_sign_in(c, url, email) for _ in range(20)]
    summarise("login_identifier (same email)", results, expect_block_after=15)


def test_login_ip(args):
    c, url = sign_in_client(args.app)
    results = run_parallel(lambda i: post_sign_in(c, url, fake_email()), 60, args.workers)
    return summarise("login_ip (random emails, real IP)", results, expect_block_after=50)


def test_xff_bypass(args):
    c, url = sign_in_client(args.app)
    # Make sure the real IP is over its limit first.
    pre = [post_sign_in(c, url, fake_email()) for _ in range(55)]
    blocked = any(s == 429 for s, _ in pre)
    if not blocked:
        print("\n=== xff_bypass ===\n  could not get the real IP blocked first; result below is inconclusive")

    results = run_parallel(
        lambda i: post_sign_in(c, url, fake_email(), {"X-Forwarded-For": random_ip()}),
        30, args.workers,
    )
    passed = sum(1 for s, _ in results if s != 429)
    print("\n=== xff_bypass (spoofed X-Forwarded-For) ===")
    print(f"  real IP blocked before test : {blocked}")
    print(f"  spoofed requests not blocked: {passed}/{len(results)}")
    if blocked and passed > 0:
        print("  verdict : VULNERABLE - a forged X-Forwarded-For header escapes the per-IP limit")
    elif blocked:
        print("  verdict : PASS - spoofed header ignored")


def test_analytics(args):
    url = args.store.rstrip("/") + "/api/track/"
    c = Client()
    body = {"type": "pageview", "path": "/rl-test/"}
    results = run_parallel(lambda i: c.request("POST", url, json_body=body), 330, args.workers)
    summarise("analytics (/api/track/)", results, expect_block_after=300)


def store_client(args) -> Client:
    c = Client()
    c.request("GET", args.store.rstrip("/") + "/cart/")
    if not c.csrf():
        print("  (no CSRF cookie from /cart/ - coupon test may get 403s)")
    return c


def test_coupon(args):
    url = args.store.rstrip("/") + "/cart/coupon/"
    c = store_client(args)
    hdrs = {"Referer": url, "Origin": origin_of(url), "X-Requested-With": "XMLHttpRequest"}

    def one(i):
        h = dict(hdrs, **{"X-CSRFToken": c.csrf()})
        return c.request("POST", url, data={"code": "RLTEST" + secrets.token_hex(3).upper()}, headers=h)

    results = run_parallel(one, 60, args.workers)
    summarise("coupon (/cart/coupon/, bogus codes)", results, expect_no_block=True)


def test_pincode(args):
    url = args.store.rstrip("/") + "/api/pincode/?pincode=110001"
    c = Client()
    results = run_parallel(lambda i: c.request("GET", url), 20, min(args.workers, 5))
    summarise("pincode (/api/pincode/) - calls courier API", results, expect_no_block=True)


TESTS = {
    "login_identifier": test_login_identifier,
    "login_ip": test_login_ip,
    "xff_bypass": test_xff_bypass,
    "analytics": test_analytics,
    "coupon": test_coupon,
}


def main():
    ap = argparse.ArgumentParser(description="OneWooz rate-limit tester")
    ap.add_argument("--app", required=True, help="app surface base, e.g. https://app.onewooz.com or http://localhost:8000/user")
    ap.add_argument("--store", help="storefront base, e.g. https://demo.onewooz.site")
    ap.add_argument("--only", help="comma-separated test names: " + ",".join(TESTS))
    ap.add_argument("--pincode", action="store_true", help="also run the pincode test (uses the store's courier API)")
    ap.add_argument("--workers", type=int, default=10, help="parallel requests (default 10)")
    args = ap.parse_args()

    names = args.only.split(",") if args.only else list(TESTS)
    if args.pincode:
        names.append("pincode")
    store_tests = {"analytics", "coupon", "pincode"}
    if any(n in store_tests for n in names) and not args.store:
        sys.exit("--store is required for: " + ", ".join(sorted(store_tests & set(names))))

    print(f"Target app  : {args.app}")
    print(f"Target store: {args.store or '-'}")
    print(f"Tests       : {', '.join(names)}")
    t0 = time.time()
    for n in names:
        fn = TESTS.get(n) or (test_pincode if n == "pincode" else None)
        if fn is None:
            print(f"\nunknown test {n!r}, skipping")
            continue
        fn(args)
    print(f"\nDone in {time.time() - t0:.1f}s. Note: your IP is now locked out of sign-in for ~5 minutes.")


if __name__ == "__main__":
    main()