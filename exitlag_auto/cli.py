"""Command line interface for the ExitLag account provisioner."""
from __future__ import annotations

import argparse
import logging
import sys

from . import portable
from .addy import AddyClient, AddyError
from .config import ConfigError, load_config
from .errors import BrowserNotFound
from .signup import SignupError, SignupFlow
from .store import AccountStore

LOG = logging.getLogger("exitlag_auto.cli")


def setup_logging(log_path: str, verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    root.addHandler(console)
    if log_path:
        try:
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
            )
            root.addHandler(file_handler)
        except Exception as exc:  # pragma: no cover
            LOG.warning("Could not open log file '%s': %s", log_path, exc)


def _provision(cfg, store: AccountStore, addy: AddyClient):
    """Create one fresh account and store it."""
    result = SignupFlow(cfg, addy).run()
    return store.add(result, cfg.trial.days)


# ---------------------------------------------------------------- commands
def cmd_check(cfg, store, addy, args) -> int:
    print("Configuration loaded successfully.")
    try:
        details = addy.account_details()
        username = details.get("username", "<unknown>")
        print(f"addy.io token OK (username: {username})")
    except AddyError as exc:
        print(f"addy.io token FAILED: {exc}")
        return 1
    # Imported lazily so a broken DrissionPage install cannot break 'check'.
    from .browser import find_browser

    browser_path = find_browser(cfg.browser.path)
    if browser_path:
        print(f"Browser: {browser_path}")
    else:
        print("Browser: NOT FOUND -> install Chrome or set [browser].path in config.toml")

    accounts = store.list_all()
    active = [a for a in accounts if a.status == "active"]
    print(f"Alias domain: {cfg.addy.domain} (format: {cfg.addy.alias_format})")
    print(f"Store: {cfg.store_path} - {len(accounts)} account(s), {len(active)} active")
    print(f"Email verification: {'on' if cfg.signup.verify_email else 'off'}")
    print(f"HWID rotation: {'on' if cfg.integration.rotate_hwid else 'off'}")
    print(f"account.txt target: {cfg.integration.account_txt_path or '<not set>'}")
    return 0


def cmd_signup(cfg, store, addy, args) -> int:
    count = args.count or cfg.signup.count
    created = []
    for index in range(1, count + 1):
        LOG.info("=== Creating account %s/%s ===", index, count)
        try:
            created.append(_provision(cfg, store, addy))
        except SignupError as exc:
            LOG.error("Could not create account %s/%s: %s", index, count, exc)
    if not created:
        return 1
    print("\nCreated accounts:")
    for account in created:
        print(f"  {account.email}  {account.password}  (expires {account.trial_expires_at})")
    return 0


def cmd_list(cfg, store, addy, args) -> int:
    store.prune()
    accounts = store.list_all()
    if not accounts:
        print("No accounts stored yet. Run 'signup' or 'ensure'.")
        return 0
    print(f"{'EMAIL':42} {'STATUS':9} {'HOURS LEFT':>10}  CREATED")
    for account in accounts:
        hours = account.hours_left()
        left = f"{hours:.1f}" if account.status == "active" else "-"
        print(f"{account.email:42} {account.status:9} {left:>10}  {account.created_at}")
    return 0


def cmd_current(cfg, store, addy, args) -> int:
    store.prune()
    account = store.get_valid(cfg.trial.renew_before_hours)
    if not account:
        print("No usable account. Run 'ensure' to provision one.")
        return 1
    print(f"Email:    {account.email}")
    print(f"Password: {account.password}")
    print(f"Expires:  {account.trial_expires_at} ({account.hours_left():.1f}h left)")
    return 0


def cmd_prune(cfg, store, addy, args) -> int:
    count = store.prune()
    print(f"Marked {count} account(s) as expired.")
    return 0


def cmd_ensure(cfg, store, addy, args) -> int:
    """Guarantee a usable account, then hand it to the portable build."""
    store.prune()
    account = store.get_valid(cfg.trial.renew_before_hours)
    freshly_created = False
    if account is None:
        LOG.info("No account with a live trial; provisioning a new one.")
        try:
            account = _provision(cfg, store, addy)
        except SignupError as exc:
            LOG.error("Provisioning failed: %s", exc)
            return 1
        freshly_created = True
    else:
        LOG.info("Reusing %s (%.1fh of trial left).", account.email, account.hours_left())

    portable.apply_account(cfg, account, rotate=freshly_created)
    store.mark_used(account.email)

    if not args.no_launch:
        portable.launch(cfg.integration.launch_exe)
    print(f"\nActive account: {account.email}")
    print(f"Password:       {account.password}")
    print(f"Trial expires:  {account.trial_expires_at}")
    return 0


def cmd_expire(cfg, store, addy, args) -> int:
    """Manually mark an account as dead (e.g. banned or trial consumed)."""
    store.set_status(args.email, args.status)
    print(f"{args.email} -> {args.status}")
    return 0


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exitlag-auto",
        description="Provision and rotate ExitLag trial accounts using addy.io aliases.",
    )
    parser.add_argument("-c", "--config", default=None, help="Path to config.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("check", help="Validate config, addy.io token and browser").set_defaults(func=cmd_check)

    signup = sub.add_parser("signup", help="Create new account(s)")
    signup.add_argument("-n", "--count", type=int, default=None)
    signup.set_defaults(func=cmd_signup)

    sub.add_parser("list", help="List stored accounts").set_defaults(func=cmd_list)
    sub.add_parser("current", help="Show the account currently in use").set_defaults(func=cmd_current)
    sub.add_parser("prune", help="Flag expired trials").set_defaults(func=cmd_prune)

    ensure = sub.add_parser(
        "ensure", help="Guarantee a live account, write account.txt and launch ExitLag"
    )
    ensure.add_argument("--no-launch", action="store_true", help="Do not start the executable")
    ensure.set_defaults(func=cmd_ensure)

    expire = sub.add_parser("expire", help="Mark an account as expired/banned")
    expire.add_argument("email")
    expire.add_argument("--status", default="expired", choices=["expired", "banned", "active"])
    expire.set_defaults(func=cmd_expire)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    setup_logging(cfg.log_path, args.verbose)
    store = AccountStore(cfg.store_path)
    try:
        addy = AddyClient(cfg.addy.api_key)
        return args.func(cfg, store, addy, args)
    except KeyboardInterrupt:
        LOG.warning("Interrupted by user.")
        return 130
    except BrowserNotFound as exc:
        LOG.error("No usable browser found.")
        print(f"\n{exc}", file=sys.stderr)
        return 3
    except AddyError as exc:
        LOG.error("addy.io error: %s", exc)
        return 1
    finally:
        store.close()
