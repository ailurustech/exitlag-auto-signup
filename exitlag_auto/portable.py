"""Integration with the patched ExitLag portable build.

Responsibilities:
- write account.txt in the exact format the binary patch reads
  (email on line 1, password on line 2)
- rotate the machine fingerprint / clear ExitLag local trial state
- launch the portable executable

SAFETY: every filesystem and registry deletion target must contain "exitlag"
(case-insensitive). This makes it impossible for a typo or a bad config value to
wipe an unrelated directory or registry hive.
"""
from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys

LOG = logging.getLogger(__name__)


def generate_hwid() -> str:
    """Generate a random ExitLag-style hardware id (32 uppercase hex chars)."""
    return secrets.token_hex(16).upper()


def _expand(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def _is_safe_target(target: str) -> bool:
    """Refuse to touch anything that is not clearly ExitLag's own state."""
    if "exitlag" not in target.lower():
        LOG.error("REFUSING to delete '%s': target does not mention 'exitlag'.", target)
        return False
    return True


def write_account_txt(path: str, email: str, password: str) -> bool:
    if not path:
        LOG.warning("integration.account_txt_path is not set; skipping account.txt.")
        return False
    path = _expand(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(f"{email}\n{password}\n")
    LOG.info("Wrote credentials for %s to %s", email, path)
    return True


def write_hwid_txt(path: str, hwid: str) -> bool:
    if not path:
        return False
    path = _expand(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(hwid + "\n")
    LOG.info("Wrote HWID %s to %s", hwid, path)
    return True


def clean_paths(paths) -> int:
    removed = 0
    for raw in paths:
        target = _expand(raw)
        if not _is_safe_target(target):
            continue
        if not os.path.exists(target):
            LOG.debug("Nothing to clean at %s", target)
            continue
        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=False)
            else:
                os.remove(target)
            LOG.info("Cleared ExitLag state: %s", target)
            removed += 1
        except Exception as exc:
            LOG.warning("Could not remove %s: %s", target, exc)
    return removed


_ROOTS = {
    "HKCU": "HKEY_CURRENT_USER",
    "HKEY_CURRENT_USER": "HKEY_CURRENT_USER",
    "HKLM": "HKEY_LOCAL_MACHINE",
    "HKEY_LOCAL_MACHINE": "HKEY_LOCAL_MACHINE",
}


def _delete_key_tree(winreg, root, subkey: str):
    """Recursively delete a registry key (Windows has no built-in recursive delete)."""
    try:
        handle = winreg.OpenKey(root, subkey, 0, winreg.KEY_ALL_ACCESS)
    except FileNotFoundError:
        return False
    try:
        while True:
            try:
                child = winreg.EnumKey(handle, 0)
            except OSError:
                break
            _delete_key_tree(winreg, root, f"{subkey}\\{child}")
    finally:
        winreg.CloseKey(handle)
    winreg.DeleteKey(root, subkey)
    return True


def clean_registry(keys) -> int:
    if not keys:
        return 0
    if sys.platform != "win32":
        LOG.info("Not running on Windows; skipping registry cleanup.")
        return 0
    import winreg

    removed = 0
    for raw in keys:
        if not _is_safe_target(raw):
            continue
        parts = raw.replace("/", "\\").split("\\", 1)
        if len(parts) != 2 or parts[0].upper() not in _ROOTS:
            LOG.warning("Skipping malformed registry key '%s' (expected HKCU\\Path\\To\\Key).", raw)
            continue
        root = getattr(winreg, _ROOTS[parts[0].upper()])
        try:
            if _delete_key_tree(winreg, root, parts[1]):
                LOG.info("Deleted registry key %s", raw)
                removed += 1
        except Exception as exc:
            LOG.warning("Could not delete registry key %s: %s", raw, exc)
    return removed


def rotate_hwid(cfg) -> str:
    """Clear ExitLag's local trial state and publish a fresh HWID.

    Returns the new HWID (empty string when rotation is disabled).
    """
    integration = cfg.integration
    if not integration.rotate_hwid:
        LOG.debug("HWID rotation disabled.")
        return ""
    LOG.info("Rotating hardware fingerprint / clearing ExitLag trial state...")
    clean_paths(integration.clean_paths)
    clean_registry(integration.registry_keys)
    hwid = generate_hwid()
    write_hwid_txt(integration.hwid_txt_path, hwid)
    return hwid


def launch(exe_path: str) -> bool:
    if not exe_path:
        return False
    exe_path = _expand(exe_path)
    if not os.path.exists(exe_path):
        LOG.error("launch_exe '%s' does not exist.", exe_path)
        return False
    LOG.info("Launching %s", exe_path)
    try:
        subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path) or None, shell=False)
        return True
    except Exception as exc:
        LOG.error("Failed to launch %s: %s", exe_path, exc)
        return False


def apply_account(cfg, account, rotate: bool = False) -> None:
    """Make `account` the active one for the portable build."""
    if rotate:
        rotate_hwid(cfg)
    write_account_txt(cfg.integration.account_txt_path, account.email, account.password)
