"""Resilient ExitLag registration flow.

Design notes:
- The alias is created only AFTER the register page is confirmed usable, so a
  Cloudflare or navigation failure never burns an alias.
- If anything fails after the alias exists, it is deleted again via the API.
- Every field is located through a fallback chain, so a markup tweak on
  ExitLag's side degrades gracefully instead of hard-failing.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

from .browser import bypass_cloudflare, new_chromium
from .identity import make_identity

LOG = logging.getLogger(__name__)

REGISTER_URL = "https://www.exitlag.com/register"
SUCCESS_MARKER = "clientarea.php"

# Ordered fallback chains: first match wins.
FIELDS = {
    "first_name": ["#inputFirstName", "@name=firstname", "@name=first_name", "@placeholder:First"],
    "last_name": ["#inputLastName", "@name=lastname", "@name=last_name", "@placeholder:Last"],
    "email": ["#inputEmail", "@name=email", "@type=email", "@placeholder:mail"],
    "password1": ["#inputNewPassword1", "@name=password", "@type=password"],
    "password2": ["#inputNewPassword2", "@name=password2", "@name=confirm_password"],
}
CHECKBOX = [".custom-checkbox--input checkbox", "tag:input@type=checkbox", "@type=checkbox"]
SUBMIT = [
    ".btn btn-primary btn-block btn-recaptcha btn-recaptcha-invisible",
    "tag:button@type=submit",
    "@type=submit",
    "tag:button@text():Register",
]
ERROR_HINTS = [".alert-danger", ".alert", "#errorMessage", ".invalid-feedback"]


class SignupError(Exception):
    """Raised when a registration attempt fails."""


@dataclass
class SignupResult:
    email: str
    alias_id: str
    password: str
    first_name: str
    last_name: str
    verified: bool = False


class SignupFlow:
    def __init__(self, cfg, addy):
        self.cfg = cfg
        self.addy = addy

    # -- helpers ---------------------------------------------------------
    def _locate(self, tab, candidates, what: str, required: bool = True, timeout: int = 4):
        for selector in candidates:
            try:
                ele = tab.ele(selector, timeout=timeout)
                if ele:
                    return ele
            except Exception:
                continue
        if required:
            raise SignupError(f"Could not locate {what}. Tried: {candidates}")
        LOG.warning("Optional element '%s' not found; continuing.", what)
        return None

    def _wait_for_form(self, tab):
        """Wait for the fullpage overlay to clear, then confirm a field exists."""
        deadline = time.time() + self.cfg.browser.overlay_timeout
        while time.time() < deadline:
            try:
                overlay = tab.ele("#:fullpage-overlay", timeout=1)
                if not overlay or overlay.style("display") == "none":
                    break
            except Exception:
                break
            time.sleep(0.5)
        # The real readiness signal is the email field being present.
        self._locate(tab, FIELDS["email"], "the email field", timeout=8)

    def _page_error(self, tab) -> Optional[str]:
        for selector in ERROR_HINTS:
            try:
                ele = tab.ele(selector, timeout=1)
                if ele:
                    text = (ele.text or "").strip()
                    if text:
                        return text
            except Exception:
                continue
        return None

    # -- one attempt -----------------------------------------------------
    def _attempt(self) -> SignupResult:
        identity = make_identity(self.cfg.signup)
        chrome = None
        alias = None
        try:
            chrome = new_chromium(self.cfg.browser)
            tab = chrome.new_tab(REGISTER_URL)
            bypass_cloudflare(tab)
            self._wait_for_form(tab)

            # Page is usable -> only now do we spend an alias.
            alias = self.addy.create_alias(
                domain=self.cfg.addy.domain,
                alias_format=self.cfg.addy.alias_format,
                local_part=self.cfg.addy.local_part,
                description="exitlag-auto-signup",
            )

            self._locate(tab, FIELDS["first_name"], "first name").input(identity.first_name)
            self._locate(tab, FIELDS["last_name"], "last name").input(identity.last_name)
            self._locate(tab, FIELDS["email"], "email").input(alias.email)
            self._locate(tab, FIELDS["password1"], "password").input(identity.password)
            pw2 = self._locate(tab, FIELDS["password2"], "password confirmation", required=False)
            if pw2:
                pw2.input(identity.password)

            checkbox = self._locate(tab, CHECKBOX, "terms checkbox", required=False)
            if checkbox:
                try:
                    checkbox.click()
                except Exception as exc:
                    LOG.warning("Could not click the terms checkbox: %s", exc)

            submit = self._locate(tab, SUBMIT, "submit button")
            try:
                submit.remove_attr("disabled")
            except Exception:
                pass
            submit.click()
            LOG.info("Submitted registration for %s", alias.email)

            if not tab.wait.url_change(SUCCESS_MARKER, timeout=self.cfg.browser.page_timeout):
                detail = self._page_error(tab) or f"still at {tab.url}"
                raise SignupError(f"Registration did not reach the client area ({detail}).")

            LOG.info("Account created: %s", alias.email)

            verified = False
            if self.cfg.signup.verify_email:
                from .mailbox import fetch_verification_link

                link = fetch_verification_link(self.cfg.imap, alias.email)
                if link:
                    tab.get(link)
                    verified = True
                    LOG.info("Email verified for %s", alias.email)
                else:
                    LOG.warning("No verification link found for %s.", alias.email)

            return SignupResult(
                email=alias.email,
                alias_id=alias.id,
                password=identity.password,
                first_name=identity.first_name,
                last_name=identity.last_name,
                verified=verified,
            )
        except Exception:
            if alias and self.cfg.addy.delete_alias_on_failure:
                try:
                    self.addy.delete_alias(alias.id)
                except Exception as exc:
                    LOG.warning("Failed to clean up alias %s: %s", alias.email, exc)
            raise
        finally:
            if chrome is not None:
                try:
                    chrome.quit()
                except Exception:
                    pass

    # -- retries ---------------------------------------------------------
    def run(self) -> SignupResult:
        attempts = self.cfg.signup.attempts
        last_error = None
        for attempt in range(1, attempts + 1):
            LOG.info("Signup attempt %s/%s", attempt, attempts)
            try:
                return self._attempt()
            except Exception as exc:
                last_error = exc
                LOG.error("Attempt %s failed: %s", attempt, exc)
                if attempt < attempts:
                    delay = self.cfg.signup.backoff_seconds * attempt
                    LOG.info("Retrying in %.1fs with a fresh browser...", delay)
                    time.sleep(delay)
        raise SignupError(f"All {attempts} signup attempts failed. Last error: {last_error}")
