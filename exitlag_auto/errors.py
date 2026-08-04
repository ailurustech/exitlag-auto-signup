"""Shared exception types.

Kept dependency-free so the CLI can import these without pulling in
DrissionPage (and therefore a browser stack) just to print an error.
"""
from __future__ import annotations


class BrowserNotFound(Exception):
    """Raised when no Chromium-based browser can be located.

    This is fatal and not worth retrying: no amount of backoff will make a
    browser appear on disk.
    """
