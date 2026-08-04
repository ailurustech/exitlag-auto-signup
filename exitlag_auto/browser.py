"""Chromium factory, browser auto-detection and Cloudflare Turnstile bypass."""
from __future__ import annotations

import logging
import os
import shutil
import sys
import time

import requests
from DrissionPage import Chromium, ChromiumOptions

from .errors import BrowserNotFound  # noqa: F401  (re-exported for convenience)

LOG = logging.getLogger(__name__)


# Ordered by preference. Chrome first (best Turnstile track record), then the
# Chromium forks, then Edge -- which is preinstalled on every modern Windows,
# so it is the reliable last resort.
_WINDOWS_CANDIDATES = [
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles%\Google\Chrome Beta\Application\chrome.exe",
    r"%ProgramFiles%\Chromium\Application\chrome.exe",
    r"%LOCALAPPDATA%\Chromium\Application\chrome.exe",
    r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%ProgramFiles%\Vivaldi\Application\vivaldi.exe",
    r"%LOCALAPPDATA%\Vivaldi\Application\vivaldi.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
]

_POSIX_CANDIDATES = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/brave-browser",
    "/snap/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]

_WHICH_NAMES = [
    "chrome", "google-chrome", "google-chrome-stable",
    "chromium", "chromium-browser", "brave-browser", "msedge",
]

_REGISTRY_APP_PATHS = ["chrome.exe", "msedge.exe", "brave.exe"]


def _clean(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path.strip().strip('"').strip("'")))


def _from_registry():
    """Ask Windows where the browser is installed (survives custom install dirs)."""
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    subkey_root = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for exe in _REGISTRY_APP_PATHS:
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(root, f"{subkey_root}\\{exe}") as key:
                    value, _ = winreg.QueryValueEx(key, None)
                candidate = _clean(value)
                if candidate and os.path.exists(candidate):
                    return candidate
            except (FileNotFoundError, OSError):
                continue
    return None


def find_browser(explicit_path: str = ""):
    """Locate a Chromium-based browser.

    Order: explicit config value -> known install locations -> Windows registry
    -> PATH lookup. Returns the path, or None if nothing was found.
    """
    if explicit_path:
        candidate = _clean(explicit_path)
        if os.path.exists(candidate):
            LOG.info("Using configured browser: %s", candidate)
            return candidate
        LOG.warning("browser.path '%s' does not exist; auto-detecting instead.", candidate)

    candidates = _WINDOWS_CANDIDATES if sys.platform == "win32" else _POSIX_CANDIDATES
    for raw in candidates:
        candidate = _clean(raw)
        # Unexpanded variables (e.g. %ProgramFiles(x86)% on some shells) are skipped.
        if "%" in candidate or not candidate:
            continue
        if os.path.exists(candidate):
            LOG.info("Detected browser: %s", candidate)
            return candidate

    from_reg = _from_registry()
    if from_reg:
        LOG.info("Detected browser via registry: %s", from_reg)
        return from_reg

    for name in _WHICH_NAMES:
        found = shutil.which(name)
        if found:
            LOG.info("Detected browser on PATH: %s", found)
            return found

    return None


def require_browser(explicit_path: str = "") -> str:
    path = find_browser(explicit_path)
    if path:
        return path
    hint = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if sys.platform == "win32"
        else "/usr/bin/google-chrome"
    )
    raise BrowserNotFound(
        "No Chromium-based browser found. Install Google Chrome, or set the full "
        "path in config.toml:\n\n"
        f"    [browser]\n    path = \"{hint}\"\n\n"
        "Chrome, Chromium, Brave, Vivaldi and Microsoft Edge are all supported."
    )


def test_proxy(proxy: str) -> bool:
    try:
        requests.get("http://www.google.com", proxies={"http": proxy, "https": proxy}, timeout=5)
        return True
    except Exception as exc:
        LOG.warning("Proxy '%s' failed (%s); continuing without it.", proxy, exc)
        return False


def build_options(cfg) -> ChromiumOptions:
    """Build ChromiumOptions from a BrowserConfig."""
    co = ChromiumOptions()
    co.incognito().auto_port().mute(True)
    co.set_browser_path(require_browser(cfg.path))
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
