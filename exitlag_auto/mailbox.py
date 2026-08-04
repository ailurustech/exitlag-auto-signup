"""Optional email verification.

addy.io is forward-only: it has no API to read message bodies. When ExitLag
requires a verified address, the confirmation mail lands in the real mailbox the
alias forwards to, so we read it over IMAP and follow the verification link.
"""
from __future__ import annotations

import email as emaillib
import imaplib
import logging
import re
import time
from email.header import decode_header

LOG = logging.getLogger(__name__)

SUBJECT_MARKER = "confirm your e-mail address"
VERIFY_PREFIX = "https://www.exitlag.com/user/verify"


def _decode_header(value) -> str:
    if not value:
        return ""
    out = ""
    for text, enc in decode_header(value):
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="ignore")
        else:
            out += text
    return out


def _message_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    body += payload.decode(errors="ignore")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(errors="ignore")
    return body


def extract_verify_link(body: str):
    for link in re.findall(r"https?://[^\s\"'<>]+", body):
        cleaned = link.rstrip(").,")
        if cleaned.startswith(VERIFY_PREFIX):
            return cleaned
    return None


def fetch_verification_link(cfg, alias_email: str, poll: int = 5):
    """Poll the mailbox for the ExitLag confirmation mail sent to alias_email."""
    deadline = time.time() + cfg.timeout
    LOG.info("Waiting for the confirmation email addressed to %s ...", alias_email)
    while time.time() < deadline:
        try:
            with imaplib.IMAP4_SSL(cfg.host, cfg.port) as box:
                box.login(cfg.user, cfg.password)
                box.select("INBOX")
                _typ, data = box.search(None, "TO", f'"{alias_email}"')
                ids = data[0].split() if data and data[0] else []
                for msg_id in reversed(ids):
                    _typ, raw = box.fetch(msg_id, "(RFC822)")
                    if not raw or not raw[0]:
                        continue
                    msg = emaillib.message_from_bytes(raw[0][1])
                    if SUBJECT_MARKER not in _decode_header(msg.get("Subject")).lower():
                        continue
                    link = extract_verify_link(_message_body(msg))
                    if link:
                        LOG.info("Found verification link.")
                        return link
        except Exception as exc:
            LOG.warning("IMAP poll error: %s", exc)
        time.sleep(poll)
    LOG.error("Timed out waiting for the confirmation email.")
    return None
