import asyncio
import re
import warnings
import time
import os
import sys
import subprocess

try:
    import tomllib  # Python 3.11+
    def _load_toml(path):
        with open(path, "rb") as f:
            return tomllib.load(f)
except ModuleNotFoundError:
    import toml
    def _load_toml(path):
        return toml.load(path)

from tqdm import TqdmExperimentalWarning
from tqdm.rich import tqdm
from DrissionPage import Chromium, ChromiumOptions
from lib.lib import Main, CloudflareBypasser, AddyClient, fetch_verification_link

warnings.filterwarnings("ignore", category=TqdmExperimentalWarning)

CONFIG_PATH = os.environ.get("EXITLAG_CONFIG", "config.toml")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        print(f"Config file '{CONFIG_PATH}' not found. Copy config.example.toml to config.toml and fill it in.")
        sys.exit(1)
    return _load_toml(CONFIG_PATH)


def write_integration(cfg, email, password):
    integ = cfg.get("integration", {})
    path = integ.get("account_txt_path", "").strip()
    if path:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"{email}\n{password}\n")
        print(f"Wrote account to portable account.txt: {path}")
    launch = integ.get("launch_exe", "").strip()
    if launch and os.path.exists(launch):
        print(f"Launching {launch} ...")
        try:
            subprocess.Popen([launch], shell=False)
        except Exception as e:
            print(f"Failed to launch exe: {e}")


async def main():
    cfg = load_config()
    addy_cfg = cfg["addy"]
    signup_cfg = cfg["signup"]

    lib = Main()
    print("Checking for updates...")
    await lib.checkUpdate()

    passw = signup_cfg.get("password", "Ex1tLag.Chy7!")
    result = await lib.checkPassword(passw)
    if "does not meet the requirements" in (result or ""):
        print(result)
        sys.exit(1)

    addy = AddyClient(addy_cfg["api_key"])
    try:
        if not addy.verify_token():
            print("WARNING: addy.io token check did not return 200; continuing anyway.")
    except Exception as e:
        print(f"addy.io token check error (continuing): {e}")

    co = ChromiumOptions()
    co.incognito().auto_port().mute(True)
    browserPath = signup_cfg.get("browser_path", "").strip().replace('"', "").replace("'", "")
    if browserPath:
        if os.path.exists(browserPath):
            co.set_browser_path(browserPath)
        else:
            print(f"browser_path '{browserPath}' does not exist; using default.")
    proxy = signup_cfg.get("proxy", "").strip()
    if proxy:
        if lib.testProxy(proxy)[0] is True:
            co.set_proxy(proxy)
        else:
            print(lib.testProxy(proxy)[1])

    executionCount = int(signup_cfg.get("count", 1))
    accounts = []

    for x in range(executionCount):
        bar = tqdm(total=100)
        bar.set_description(f"Creating addy.io alias [{x + 1}/{executionCount}]")
        bar.update(10)

        email = addy.create_alias(
            domain=addy_cfg["domain"],
            alias_format=addy_cfg.get("alias_format", "random_characters"),
            local_part=addy_cfg.get("local_part", ""),
            description="exitlag-auto-signup",
        )
        bar.set_description(f"Alias: {email} [{x + 1}/{executionCount}]")
        bar.update(15)

        chrome = Chromium(addr_or_opts=co)
        tab = chrome.new_tab("https://www.exitlag.com/register")
        try:
            CloudflareBypasser(tab).bypass()
        except Exception:
            pass
        bar.set_description(f"Cloudflare bypass [{x + 1}/{executionCount}]")
        bar.update(10)

        startTime = time.time()
        while True:
            if time.time() - startTime > 20:
                print("Failed to load registration page (overlay never cleared). Continuing anyway...")
                break
            try:
                if tab.ele("#:fullpage-overlay").style("display") == "none":
                    break
            except Exception:
                break

        tab.ele("#inputFirstName").input(signup_cfg.get("first_name", "Ariel"))
        tab.ele("#inputLastName").input(signup_cfg.get("last_name", "Segovia"))
        tab.ele("#inputEmail").input(email)
        tab.ele("#inputNewPassword1").input(passw)
        tab.ele("#inputNewPassword2").input(passw)
        try:
            tab.ele(".custom-checkbox--input checkbox").click()
        except Exception:
            pass
        try:
            tab.ele(".btn btn-primary btn-block btn-recaptcha btn-recaptcha-invisible").remove_attr("disabled")
        except Exception:
            pass
        tab.ele(".btn btn-primary btn-block btn-recaptcha btn-recaptcha-invisible").click()

        bar.set_description(f"Signing up [{x + 1}/{executionCount}]")
        bar.update(30)

        registered = tab.wait.url_change("https://www.exitlag.com/clientarea.php", timeout=60)
        if not registered:
            print("Registration did not reach clientarea.php. The account may still have been created; check manually.")

        if signup_cfg.get("verify_email", False):
            bar.set_description(f"Verifying email [{x + 1}/{executionCount}]")
            imap_cfg = cfg.get("imap", {})
            link = fetch_verification_link(imap_cfg, email, timeout=int(imap_cfg.get("timeout", 120)))
            if link:
                tab.get(link)
                print("Email verified.")
            else:
                print("Could not find verification email; you may need to verify manually.")
        else:
            print("Skipping email verification (signup.verify_email = false).")

        bar.update(25)
        try:
            tab.set.cookies.clear()
            chrome.set.cookies.clear()
            chrome.clear_cache()
            chrome.quit()
        except Exception:
            pass

        accounts.append({"email": email, "password": passw})
        bar.set_description(f"Done [{x + 1}/{executionCount}]")
        bar.update(10)
        bar.close()
        print()

    with open("accounts.txt", "a", encoding="utf-8") as f:
        for account in accounts:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"Email: {account['email']}, Password: {account['password']}, (Created at {timestamp})\n")

    if accounts:
        last = accounts[-1]
        write_integration(cfg, last["email"], last["password"])

    print("\nCredentials:")
    for account in accounts:
        print(f"Email: {account['email']}, Password: {account['password']}")
    print("\nCredentials saved to accounts.txt. Have fun using ExitLag!")


if __name__ == "__main__":
    asyncio.run(main())
