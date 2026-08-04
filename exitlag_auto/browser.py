"""Chromium factory and Cloudflare Turnstile bypass."""
from __future__ import annotations

import logging
import time

import requests
from DrissionPage import Chromium, ChromiumOptions

LOG = logging.getLogger(__name__)


def test_proxy(proxy: str) -> bool:
    try:
        requests.get("http://www.google.com", proxies={"http": proxy, "https": proxy}, timeout=5)
        return True
    except Exception as exc:
        LOG.warning("Proxy '%s' failed (%s); continuing without it.", proxy, exc)
        return False


def build_options(cfg) -> ChromiumOptions:
    """Build ChromiumOptions from a BrowserConfig."""
    import os

    co = ChromiumOptions()
    co.incognito().auto_port().mute(True)
    if cfg.path:
        if os.path.exists(cfg.path):
            co.set_browser_path(cfg.path)
        else:
            LOG.warning("browser.path '%s' does not exist; using the default browser.", cfg.path)
    if cfg.proxy and test_proxy(cfg.proxy):
        co.set_proxy(cfg.proxy)
    if cfg.headless:
        # Cloudflare Turnstile is much harder to pass headless; opt-in only.
        co.headless(True)
    # Reduce the most obvious automation signals.
    co.set_argument("--disable-blink-features=AutomationControlled")
    return co


def new_chromium(cfg) -> Chromium:
    return Chromium(addr_or_opts=build_options(cfg))


class CloudflareBypasser:
    """Clicks the Cloudflare Turnstile checkbox hidden inside nested shadow roots.

    Adapted from https://github.com/sarperavci/CloudflareBypassForScraping
    """

    def __init__(self, driver, max_retries: int = 5, log: bool = True):
        self.driver = driver
        self.max_retries = max_retries
        self.log = log

    def _log(self, message):
        if self.log:
            LOG.info(message)

    def _shadow_iframe(self, ele):
        try:
            if ele.shadow_root:
                if ele.shadow_root.child().tag == "iframe":
                    return ele.shadow_root.child()
                for child in ele.children():
                    found = self._shadow_iframe(child)
                    if found:
                        return found
        except Exception:
            pass
        return None

    def _shadow_input(self, ele):
        try:
            if ele.shadow_root:
                found = ele.shadow_root.ele("tag:input", timeout=1)
                if found:
                    return found
                for child in ele.children():
                    found = self._shadow_input(child)
                    if found:
                        return found
        except Exception:
            pass
        return None

    def locate_button(self):
        try:
            for ele in self.driver.eles("tag:input"):
                attrs = ele.attrs
                if "turnstile" in attrs.get("name", "") and attrs.get("type") == "hidden":
                    return ele.parent().shadow_root.child()("tag:body").shadow_root("tag:input")
        except Exception as exc:
            self._log(f"Turnstile direct lookup failed: {exc}")
        try:
            body = self.driver.ele("tag:body", timeout=2)
            iframe = self._shadow_iframe(body)
            if iframe:
                return self._shadow_input(iframe("tag:body"))
        except Exception as exc:
            self._log(f"Turnstile recursive lookup failed: {exc}")
        return None

    def is_bypassed(self) -> bool:
        try:
            return "just a moment" not in self.driver.title.lower()
        except Exception:
            return False

    def bypass(self) -> bool:
        attempts = 0
        while not self.is_bypassed() and attempts < self.max_retries:
            attempts += 1
            self._log(f"Cloudflare challenge detected (attempt {attempts}/{self.max_retries}).")
            button = self.locate_button()
            if button:
                try:
                    button.click()
                except Exception as exc:
                    self._log(f"Could not click Turnstile checkbox: {exc}")
            time.sleep(2)
        ok = self.is_bypassed()
        self._log("Cloudflare bypassed." if ok else "Cloudflare bypass FAILED.")
        return ok


def bypass_cloudflare(tab) -> bool:
    try:
        return CloudflareBypasser(tab).bypass()
    except Exception as exc:
        LOG.warning("Cloudflare bypass raised: %s", exc)
        return False
