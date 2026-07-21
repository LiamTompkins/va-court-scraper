from __future__ import absolute_import
import os
import time

# Solving Cloudflare's managed JS challenge ("Just a moment...", served with the
# `cf-mitigated: challenge` header) can't be done by spoofing TLS/headers alone -
# the challenge JavaScript has to actually run. We drive a real Chrome to execute
# it, which earns a `cf_clearance` cookie, then hand that cookie (plus the exact
# User-Agent) back to the caller so a fast curl_cffi session - impersonating the
# same Chrome fingerprint - can reuse it for the bulk of the scraping.
#
# Headless Chrome advertises a "HeadlessChrome" User-Agent that Cloudflare flags,
# so when running headless we override the UA to a normal Chrome string (and the
# solved cf_clearance is bound to that overridden UA, which is what curl_cffi then
# presents).

# A plain desktop-Chrome UA used for headless runs. Keep this in sync with the
# Chrome major version installed on the collectors if challenges start failing.
CHROME_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
             '(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36')


def _chromedriver_path():
    """Prefer the chromedriver.exe kept in the repo root (its documented home);
    return None so Selenium Manager can auto-provision one if it isn't there."""
    for name in ('chromedriver.exe', 'chromedriver'):
        if os.path.exists(name):
            return os.path.abspath(name)
    return None


def _build_driver(headless, ua):
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument('--headless=new')
        # Hide the give-away HeadlessChrome token.
        opts.add_argument('--user-agent=' + ua)
    # Reduce the most obvious automation signals.
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument('--window-size=1280,900')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--no-sandbox')

    driver_path = _chromedriver_path()
    try:
        service = Service(driver_path) if driver_path else Service()
        driver = webdriver.Chrome(service=service, options=opts)
    except Exception:
        # Fall back to Selenium Manager (auto-downloads a matching driver) if the
        # bundled chromedriver is missing or version-mismatched.
        driver = webdriver.Chrome(options=opts)

    try:
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
            'source': "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"})
    except Exception:
        pass
    return driver


def solve_cloudflare(url, headless=True, timeout=35, ua=CHROME_UA):
    """Drive a real Chrome to pass the Cloudflare challenge guarding ``url`` and
    return ``(cookies_dict, user_agent)``. Raises if no cf_clearance is obtained."""
    driver = _build_driver(headless, ua)
    try:
        driver.get(url)
        real_ua = driver.execute_script('return navigator.userAgent')
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.5)
            src = driver.page_source.lower()
            # The interstitial says "Just a moment..." and carries Cloudflare
            # branding; a real page is larger and has neither.
            if ('just a moment' not in src and 'cloudflare' not in src
                    and len(src) > 3000):
                break
        cookies = {}
        for c in driver.get_cookies():
            cookies[c['name']] = c['value']
        if 'cf_clearance' not in cookies:
            raise RuntimeError('Cloudflare challenge not solved (no cf_clearance)')
        return cookies, real_ua
    finally:
        try:
            driver.quit()
        except Exception:
            pass
