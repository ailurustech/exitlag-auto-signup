import time
import requests
import sys
import re
import imaplib
import email as emaillib
from email.header import decode_header
from DrissionPage import ChromiumPage


class Main:
    async def checkPassword(self, password):
        hasLowercase = re.search(r'[a-z]', password)
        hasUppercase = re.search(r'[A-Z]', password)
        hasSpecial = re.search(r'[!@#$%^&*(),.?":{}|<>]', password)
        hasNumber = re.search(r'[0-9]', password)

        if (len(password) >= 8 and
                hasLowercase and
                hasUppercase and
                hasSpecial and
                hasNumber):
            return "\nPassword is valid."
        else:
            if len(password) < 8:
                return "\nPassword does not meet the requirements. Please use at least 8 characters."
            if not hasLowercase:
                return "\nPassword does not meet the requirements. Please use a lowercase letter."
            if not hasUppercase:
                return "\nPassword does not meet the requirements. Please use an uppercase letter."
            if not hasSpecial:
                return "\nPassword does not meet the requirements. Please use at least 1 special character (!@#$%^&*(),.?\":{}|<>)."
            if not hasNumber:
                return "\nPassword does not meet the requirements. Please use at least 1 number."

    async def checkUpdate(self):
        try:
            resp = requests.get(
                "https://api.github.com/repos/qing762/exitlag-auto-signup/releases/latest"
            )
            latestVer = resp.json()["tag_name"]

            if getattr(sys, 'frozen', False):
                import version
                currentVer = version.__version__
            else:
                with open("version.txt", "r") as file:
                    currentVer = file.read().strip()

            if currentVer < latestVer:
                print(f"Update available: {latestVer} (Current version: {currentVer})")
            else:
                print(f"You are running the latest version: {currentVer}")
        except Exception as e:
            print(f"An error occurred while checking updates: {e}")

    def testProxy(self, proxy):
        try:
            response = requests.get("http://www.google.com", proxies={"http": proxy, "https": proxy}, timeout=5)
            return True, response.status_code
        except Exception:
            return False, "Proxy test failed! Please ensure that the proxy is working correctly. Skipping proxy usage..."


class AddyClient:
    """Minimal addy.io API client. Docs: https://app.addy.io/docs/"""
    BASE = "https://app.addy.io/api/v1"

    def __init__(self, api_key, timeout=30):
        if not api_key or api_key == "YOUR_ADDY_API_KEY":
            raise ValueError("addy.io api_key is missing. Fill it in config.toml [addy].api_key")
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        })

    def create_alias(self, domain, alias_format="random_characters", local_part="", description="exitlag"):
        payload = {"domain": domain, "description": description, "format": alias_format}
        if alias_format == "custom":
            if not local_part:
                raise ValueError("alias_format='custom' requires a non-empty local_part")
            payload["local_part"] = local_part
        r = self.session.post(f"{self.BASE}/aliases", json=payload, timeout=self.timeout)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"addy.io create_alias failed [{r.status_code}]: {r.text}")
        data = r.json().get("data", {})
        emailAddr = data.get("email")
        if not emailAddr:
            raise RuntimeError(f"addy.io response missing email: {r.text}")
        return emailAddr

    def verify_token(self):
        r = self.session.get(f"{self.BASE}/account-details", timeout=self.timeout)
        return r.status_code == 200


def _decode(s):
    if s is None:
        return ""
    parts = decode_header(s)
    out = ""
    for text, enc in parts:
        if isinstance(text, bytes):
            out += text.decode(enc or "utf-8", errors="ignore")
        else:
            out += text
    return out


def _extract_verify_link(body):
    links = re.findall(r"https?://[^\s\"'<>]+", body)
    for link in links:
        if link.startswith("https://www.exitlag.com/user/verify"):
            return re.sub(r"</?[^>]+>", "", link)
    return None


def fetch_verification_link(imap_cfg, to_alias, timeout=120, poll=5):
    """Poll an IMAP mailbox for the ExitLag confirmation email addressed to a
    given alias, and return the verification URL. Used only when
    signup.verify_email = true (addy.io forwards to this real mailbox)."""
    deadline = time.time() + timeout
    subject_match = "confirm your e-mail address"
    while time.time() < deadline:
        try:
            M = imaplib.IMAP4_SSL(imap_cfg["host"], int(imap_cfg.get("port", 993)))
            M.login(imap_cfg["user"], imap_cfg["password"])
            M.select("INBOX")
            typ, data = M.search(None, '(TO "%s")' % to_alias)
            ids = data[0].split() if data and data[0] else []
            for msg_id in reversed(ids):
                typ, msg_data = M.fetch(msg_id, "(RFC822)")
                raw = msg_data[0][1]
                msg = emaillib.message_from_bytes(raw)
                subject = _decode(msg.get("Subject", "")).lower()
                if subject_match not in subject:
                    continue
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        ctype = part.get_content_type()
                        if ctype in ("text/html", "text/plain"):
                            payload = part.get_payload(decode=True)
                            if payload:
                                body += payload.decode(errors="ignore")
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode(errors="ignore")
                link = _extract_verify_link(body)
                if link:
                    M.logout()
                    return link
            M.logout()
        except Exception as e:
            print(f"IMAP poll error: {e}")
        time.sleep(poll)
    return None


class CloudflareBypasser:
    # SOURCE: https://github.com/sarperavci/CloudflareBypassForScraping
    def __init__(self, driver: ChromiumPage, max_retries=-1, log=True):
        self.driver = driver
        self.max_retries = max_retries
        self.log = log

    def search_recursively_shadow_root_with_iframe(self, ele):
        try:
            if ele.shadow_root:
                if ele.shadow_root.child().tag == "iframe":
                    return ele.shadow_root.child()
                else:
                    for child in ele.children():
                        result = self.search_recursively_shadow_root_with_iframe(child)
                        if result:
                            return result
        except Exception as e:
            self.log_message(f"Error searching shadow root with iframe: {e}")
        return None

    def search_recursively_shadow_root_with_cf_input(self, ele):
        try:
            if ele.shadow_root:
                if ele.shadow_root.ele("tag:input"):
                    return ele.shadow_root.ele("tag:input")
                else:
                    for child in ele.children():
                        result = self.search_recursively_shadow_root_with_cf_input(child)
                        if result:
                            return result
        except Exception as e:
            self.log_message(f"Error searching shadow root with CF input: {e}")
        return None

    def locate_cf_button(self):
        try:
            button = None
            eles = self.driver.eles("tag:input")
            for ele in eles:
                if "name" in ele.attrs.keys() and "type" in ele.attrs.keys():
                    if "turnstile" in ele.attrs["name"] and ele.attrs["type"] == "hidden":
                        button = ele.parent().shadow_root.child()("tag:body").shadow_root("tag:input")
                        break

            if button:
                return button
            else:
                self.log_message("Basic search failed. Searching for button recursively.")
                ele = self.driver.ele("tag:body")
                iframe = self.search_recursively_shadow_root_with_iframe(ele)
                if iframe:
                    button = self.search_recursively_shadow_root_with_cf_input(iframe("tag:body"))
                else:
                    self.log_message("Iframe not found. Button search failed.")
                return button
        except Exception as e:
            self.log_message(f"Error locating CF button: {e}")
            return None

    def log_message(self, message):
        if self.log:
            print(message)

    def click_verification_button(self):
        try:
            button = self.locate_cf_button()
            if button:
                self.log_message("Verification button found. Attempting to click.")
                button.click()
            else:
                self.log_message("Verification button not found.")
        except Exception as e:
            self.log_message(f"Error clicking verification button: {e}")

    def is_bypassed(self):
        try:
            title = self.driver.title.lower()
            return "just a moment" not in title
        except Exception as e:
            self.log_message(f"Error checking page title: {e}")
            return False

    def bypass(self):
        try_count = 0
        while not self.is_bypassed():
            if 0 < self.max_retries + 1 <= try_count:
                self.log_message("Exceeded maximum retries. Bypass failed.")
                break
            self.log_message(f"Attempt {try_count + 1}: Verification page detected. Trying to bypass...")
            self.click_verification_button()
            try_count += 1
            time.sleep(2)
        if self.is_bypassed():
            self.log_message("Bypass successful.")
        else:
            self.log_message("Bypass failed.")


if __name__ == "__main__":
    print("This is a library file. Please run main.py instead.")
