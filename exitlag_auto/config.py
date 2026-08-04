"""Configuration loading and validation."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import tomllib

    def _load_toml(path):
        with open(path, "rb") as f:
            return tomllib.load(f)
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import toml

    def _load_toml(path):
        return toml.load(path)


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass
class AddyConfig:
    api_key: str
    domain: str
    alias_format: str = "random_characters"
    local_part: str = ""
    delete_alias_on_failure: bool = True


@dataclass
class SignupConfig:
    password: str = ""          # empty => generate a random one per account
    randomize_identity: bool = True
    first_name: str = ""
    last_name: str = ""
    count: int = 1
    verify_email: bool = False
    attempts: int = 3
    backoff_seconds: float = 5.0


@dataclass
class BrowserConfig:
    path: str = ""
    proxy: str = ""
    headless: bool = False
    page_timeout: int = 60
    overlay_timeout: int = 20


@dataclass
class TrialConfig:
    days: int = 3
    renew_before_hours: int = 2


@dataclass
class IntegrationConfig:
    account_txt_path: str = ""
    launch_exe: str = ""
    hwid_txt_path: str = ""
    rotate_hwid: bool = False
    clean_paths: List[str] = field(default_factory=list)
    registry_keys: List[str] = field(default_factory=list)


@dataclass
class ImapConfig:
    host: str = "imap.gmail.com"
    port: int = 993
    user: str = ""
    password: str = ""
    timeout: int = 180


@dataclass
class Config:
    addy: AddyConfig
    signup: SignupConfig
    browser: BrowserConfig
    trial: TrialConfig
    integration: IntegrationConfig
    imap: ImapConfig
    store_path: str = "accounts.db"
    log_path: str = "exitlag_auto.log"


VALID_ALIAS_FORMATS = ("random_characters", "uuid", "random_words", "custom")


def load_config(path: Optional[str] = None) -> Config:
    path = path or os.environ.get("EXITLAG_CONFIG", "config.toml")
    if not os.path.exists(path):
        raise ConfigError(
            f"Config file '{path}' not found. "
            "Copy config.example.toml to config.toml and fill it in."
        )
    raw = _load_toml(path)

    a = raw.get("addy", {}) or {}
    s = raw.get("signup", {}) or {}
    b = raw.get("browser", {}) or {}
    t = raw.get("trial", {}) or {}
    i = raw.get("integration", {}) or {}
    m = raw.get("imap", {}) or {}

    api_key = (os.environ.get("EXITLAG_ADDY_KEY") or a.get("api_key", "")).strip()
    if not api_key or api_key == "YOUR_ADDY_API_KEY":
        raise ConfigError(
            "addy.io API key missing. Set [addy].api_key in config.toml "
            "or the EXITLAG_ADDY_KEY environment variable."
        )

    domain = str(a.get("domain", "")).strip().lstrip("@")
    if not domain:
        raise ConfigError("[addy].domain is required (e.g. 'anonaddy.me' or your custom domain).")

    alias_format = str(a.get("alias_format", "random_characters")).strip()
    if alias_format not in VALID_ALIAS_FORMATS:
        raise ConfigError(
            f"[addy].alias_format must be one of {VALID_ALIAS_FORMATS}, got '{alias_format}'."
        )
    local_part = str(a.get("local_part", "")).strip()
    if alias_format == "custom" and not local_part:
        raise ConfigError("[addy].alias_format = 'custom' requires a non-empty local_part.")

    addy = AddyConfig(
        api_key=api_key,
        domain=domain,
        alias_format=alias_format,
        local_part=local_part,
        delete_alias_on_failure=bool(a.get("delete_alias_on_failure", True)),
    )

    password = str(s.get("password", "")).strip()
    if password:
        from .identity import password_problem

        problem = password_problem(password)
        if problem:
            raise ConfigError(f"[signup].password rejected: {problem}")

    signup = SignupConfig(
        password=password,
        randomize_identity=bool(s.get("randomize_identity", True)),
        first_name=str(s.get("first_name", "")).strip(),
        last_name=str(s.get("last_name", "")).strip(),
        count=max(1, int(s.get("count", 1))),
        verify_email=bool(s.get("verify_email", False)),
        attempts=max(1, int(s.get("attempts", 3))),
        backoff_seconds=float(s.get("backoff_seconds", 5.0)),
    )

    browser = BrowserConfig(
        path=str(b.get("path", "")).strip().strip('"').strip("'"),
        proxy=str(b.get("proxy", "")).strip(),
        headless=bool(b.get("headless", False)),
        page_timeout=int(b.get("page_timeout", 60)),
        overlay_timeout=int(b.get("overlay_timeout", 20)),
    )

    trial = TrialConfig(
        days=max(1, int(t.get("days", 3))),
        renew_before_hours=max(0, int(t.get("renew_before_hours", 2))),
    )

    integration = IntegrationConfig(
        account_txt_path=str(i.get("account_txt_path", "")).strip(),
        launch_exe=str(i.get("launch_exe", "")).strip(),
        hwid_txt_path=str(i.get("hwid_txt_path", "")).strip(),
        rotate_hwid=bool(i.get("rotate_hwid", False)),
        clean_paths=[str(p) for p in (i.get("clean_paths") or [])],
        registry_keys=[str(k) for k in (i.get("registry_keys") or [])],
    )

    imap = ImapConfig(
        host=str(m.get("host", "imap.gmail.com")).strip(),
        port=int(m.get("port", 993)),
        user=str(m.get("user", "")).strip(),
        password=str(m.get("password", "")),
        timeout=int(m.get("timeout", 180)),
    )
    if signup.verify_email and (not imap.user or not imap.password or imap.password == "APP_PASSWORD_HERE"):
        raise ConfigError(
            "[signup].verify_email is true but [imap].user/password are not configured."
        )

    return Config(
        addy=addy,
        signup=signup,
        browser=browser,
        trial=trial,
        integration=integration,
        imap=imap,
        store_path=str(raw.get("store_path", "accounts.db")),
        log_path=str(raw.get("log_path", "exitlag_auto.log")),
    )
