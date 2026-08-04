"""addy.io API client.

Docs: https://app.addy.io/docs/
Only the endpoints we need: create, delete and inspect aliases.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

LOG = logging.getLogger(__name__)
BASE = "https://app.addy.io/api/v1"


class AddyError(Exception):
    """Raised when the addy.io API returns an unexpected response."""


@dataclass
class Alias:
    id: str
    email: str


class AddyClient:
    def __init__(self, api_key: str, timeout: int = 30):
        if not api_key:
            raise AddyError("addy.io API key is empty.")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

    def account_details(self):
        r = self.session.get(f"{BASE}/account-details", timeout=self.timeout)
        if r.status_code != 200:
            raise AddyError(f"account-details failed [{r.status_code}]: {r.text[:300]}")
        return r.json().get("data", {})

    def create_alias(self, domain: str, alias_format: str = "random_characters",
                     local_part: str = "", description: str = "exitlag") -> Alias:
        payload = {"domain": domain, "description": description, "format": alias_format}
        if alias_format == "custom":
            if not local_part:
                raise AddyError("alias_format='custom' requires a local_part.")
            payload["local_part"] = local_part
        r = self.session.post(f"{BASE}/aliases", json=payload, timeout=self.timeout)
        if r.status_code not in (200, 201):
            raise AddyError(f"create alias failed [{r.status_code}]: {r.text[:300]}")
        data = r.json().get("data", {})
        alias_id, email = data.get("id"), data.get("email")
        if not email:
            raise AddyError(f"create alias response missing email: {r.text[:300]}")
        LOG.info("Created alias %s", email)
        return Alias(id=alias_id, email=email)

    def delete_alias(self, alias_id: str) -> bool:
        """Delete an alias so a failed signup does not burn it."""
        if not alias_id:
            return False
        r = self.session.delete(f"{BASE}/aliases/{alias_id}", timeout=self.timeout)
        if r.status_code in (200, 204):
            LOG.info("Deleted unused alias %s", alias_id)
            return True
        LOG.warning("Could not delete alias %s [%s]: %s", alias_id, r.status_code, r.text[:200])
        return False
