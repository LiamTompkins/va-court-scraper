from __future__ import absolute_import
import os
import mechanize

# The Virginia courts site fingerprints the TLS handshake (JA3/JA4) and HTTP/2
# settings, not just the HTTP headers. A plain Python client - mechanize or
# requests, both on the stdlib `ssl` stack - presents a fingerprint that is
# obviously not a browser, so it gets flagged as a bot no matter how carefully
# the User-Agent and other headers are spoofed.
#
# curl_cffi talks through libcurl-impersonate, which reproduces a real Chrome
# TLS + HTTP/2 fingerprint (and a matching browser header set), so requests look
# like they come from Chrome at the network layer too. If it isn't installed we
# fall back to mechanize so the scraper still runs (just more likely to be
# challenged).
try:
    from curl_cffi import requests as curl_requests
    _HAVE_CURL = True
except Exception:
    _HAVE_CURL = False

# Chrome build to impersonate. curl_cffi manages the matching TLS fingerprint and
# browser headers (User-Agent, sec-ch-ua, Accept*, sec-fetch-*, etc.), so we must
# NOT layer the old hand-written headers on top or they'd contradict each other.
IMPERSONATE = 'chrome'
# Both court systems live on this host, so cookies default to it.
COURT_HOST = 'eapps.courts.state.va.us'

# Automatically solve Cloudflare managed challenges with a headless browser and
# reuse the resulting cf_clearance (see cf_solver). Toggle off via env for
# debugging or on machines without a browser.
CF_AUTOSOLVE = os.environ.get('CF_AUTOSOLVE', '1') != '0'
CF_SOLVE_HEADLESS = os.environ.get('CF_SOLVE_HEADLESS', '1') != '0'
CF_MAX_SOLVES = 2  # per request, so a bad cookie can't loop forever


def _is_cf_challenge(resp):
    """True if a curl_cffi response is a Cloudflare managed-challenge page rather
    than real content."""
    if str(resp.headers.get('cf-mitigated', '')).lower() == 'challenge':
        return True
    try:
        return b'just a moment' in resp.content[:2000].lower()
    except Exception:
        return False


class NoHistory(object):
    def add(self, *a, **k): pass
    def clear(self): pass


class DummyPage(object):
    """Minimal page object so callers can keep doing ``opener.open(...).read()``
    regardless of which backend produced the bytes."""
    def __init__(self, content):
        self.c = content

    def read(self):
        return self.c


class Opener:
    def __init__(self, name):
        self.use_curl = _HAVE_CURL
        if self.use_curl:
            # Session keeps its own cookie jar and follows redirects by default,
            # matching the old mechanize behaviour.
            self.session = curl_requests.Session(impersonate=IMPERSONATE)
            self.opener = None
        else:
            self.session = None
            self.opener = mechanize.Browser(history=NoHistory())
            self.opener.set_handle_robots(False)
            self.opener.set_handle_redirect(True)
            self.opener.addheaders = [
                ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'),
                ('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'),
                ('Accept-Language', 'en-US,en;q=0.9'),
                ('Sec-Ch-Ua', '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"'),
                ('Sec-Ch-Ua-Mobile', '?0'),
                ('Sec-Ch-Ua-Platform', '"Windows"'),
                ('Sec-Fetch-Dest', 'document'),
                ('Sec-Fetch-Mode', 'navigate'),
                ('Sec-Fetch-Site', 'none'),
                ('Sec-Fetch-User', '?1'),
                ('Upgrade-Insecure-Requests', '1')
            ]

    def set_cookie(self, name, value, domain=COURT_HOST):
        if self.use_curl:
            try:
                self.session.cookies.set(str(name), str(value),
                                         domain=domain or COURT_HOST, path='/')
            except Exception:
                # Fall back to a domainless cookie rather than dropping it.
                self.session.cookies.set(str(name), str(value))
        else:
            self.opener.set_cookie(str(name) + '=' + str(value))

    def save_cookies(self):
        return

    def can_prompt(self):
        """True only when running interactively (a real terminal), so unattended
        collectors never block waiting on a browser/keyboard."""
        import sys
        try:
            return bool(sys.stdin) and sys.stdin.isatty()
        except Exception:
            return False

    def solve_with_browser(self, url):
        """Open a real browser at ``url`` so the user can accept terms / solve a
        CAPTCHA, then copy the resulting cookies into this opener."""
        from selenium import webdriver
        from six.moves import input
        try:
            driver = webdriver.Chrome()
        except Exception as e:
            print('Could not open a browser (%s). Make sure Chrome and a matching '
                  'chromedriver are available.' % e)
            raise
        try:
            driver.get(url)
            input('A browser has opened. Accept any terms / solve the CAPTCHA there, '
                  'then press Enter here to continue...')
            for cookie in driver.get_cookies():
                try:
                    self.set_cookie(cookie['name'], cookie['value'],
                                    cookie.get('domain') or COURT_HOST)
                except Exception:
                    pass
        finally:
            try:
                driver.quit()
            except Exception:
                pass

    def refresh_cf_clearance(self, url):
        """Solve the Cloudflare challenge guarding ``url`` with a headless browser
        and inject the resulting cf_clearance/cookies (and matching User-Agent)
        into this session so subsequent curl_cffi requests are trusted."""
        from .cf_solver import solve_cloudflare
        cookies, ua = solve_cloudflare(url, headless=CF_SOLVE_HEADLESS)
        for name, value in cookies.items():
            self.set_cookie(name, value)
        # cf_clearance is bound to the User-Agent that solved it, so present that
        # same UA on the curl session from now on.
        if self.use_curl and ua:
            self.session.headers.update({'User-Agent': ua})
        return cookies

    def open(self, *args):
        if self.use_curl:
            return self._open_curl(*args)
        return self._open_mechanize(*args)

    def _open_curl(self, *args):
        import time

        url = args[0]
        data = args[1] if len(args) == 2 else None

        net_tries = 0
        solve_tries = 0
        max_net = 4
        while True:
            try:
                if data is not None:
                    # The callers hand us an already url-encoded form string, so
                    # send it verbatim and declare the form content type (curl,
                    # like requests, only sets it automatically for dict bodies).
                    resp = self.session.post(
                        url, data=data, timeout=120,
                        headers={'Content-Type': 'application/x-www-form-urlencoded'})
                else:
                    resp = self.session.get(url, timeout=120)
            except Exception as e:
                # Network-level failure (connection reset/closed, timeout, DNS,
                # TLS). Retry a few times so one blip doesn't cost the whole task
                # a long back-off.
                net_tries += 1
                if net_tries < max_net:
                    wait = 5 * net_tries
                    print('Network error in opener (%s). Retry %d/%d in %ds...' % (
                        e, net_tries, max_net - 1, wait))
                    time.sleep(wait)
                    continue
                raise

            # Cloudflare managed challenge: solve it with a browser (earning a
            # cf_clearance cookie) and retry the same request over curl_cffi.
            if resp.status_code == 403 and _is_cf_challenge(resp):
                if CF_AUTOSOLVE and solve_tries < CF_MAX_SOLVES:
                    solve_tries += 1
                    print('Cloudflare challenge on %s - solving with a browser '
                          '(attempt %d/%d)...' % (url, solve_tries, CF_MAX_SOLVES))
                    try:
                        self.refresh_cf_clearance(url)
                    except Exception as se:
                        raise IOError('HTTP error 403 for %s (Cloudflare '
                                      'challenge; solve failed: %s)' % (url, se))
                    continue
                raise IOError('HTTP error 403 for %s (Cloudflare challenge)' % url)

            # Preserve the old contract: an HTTP error surfaces as an exception so
            # callers (e.g. the circuit opener) can fall back to an interactive
            # browser. The 'http error 4/5' wording matches the checks in
            # court_bulk_collector.
            if resp.status_code >= 400:
                raise IOError('HTTP error %d for %s' % (resp.status_code, url))

            return DummyPage(resp.content)

    def _open_mechanize(self, *args):
        import time
        import socket
        from six.moves.urllib.error import URLError

        url = args[0]
        data = args[1] if len(args) == 2 else None

        max_attempts = 4
        for attempt in range(max_attempts):
            try:
                if data:
                    page = self.opener.open(url, data, timeout=120)
                else:
                    page = self.opener.open(url, timeout=120)

                content = page.read()
                page.close()

                return DummyPage(content)

            except Exception as e:
                # Retry transient network errors (dropped/reset connections,
                # timeouts) instead of letting one bubble up and cost the whole
                # task a 10-minute back-off. Only give up after max_attempts.
                msg = str(e).lower()
                # HTTP status errors (403, 404, 5xx) won't be fixed by retrying.
                code = getattr(e, 'code', None)
                if (isinstance(code, int) and code >= 400) or 'http error 4' in msg or 'http error 5' in msg:
                    raise
                transient = (
                    isinstance(e, (socket.timeout, socket.error, URLError, ConnectionError))
                    or 'timeout' in msg
                    or 'read operation' in msg
                    or 'forcibly closed' in msg
                    or 'connection reset' in msg
                    or '10054' in msg
                )
                if transient and attempt < max_attempts - 1:
                    wait = 5 * (attempt + 1)
                    print('Network error in opener (%s). Retry %d/%d in %ds...' % (
                        e, attempt + 1, max_attempts - 1, wait))
                    time.sleep(wait)
                    continue
                raise
