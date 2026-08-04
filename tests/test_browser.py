"""Browser detection tests.

DrissionPage is stubbed out so these run offline, with no browser installed.
"""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "DrissionPage" not in sys.modules:
    stub = types.ModuleType("DrissionPage")

    class _Chromium:
        def __init__(self, *a, **k):
            pass

    class _ChromiumOptions:
        def __init__(self):
            self.args = []
            self.browser_path = None
            self.proxy = None
            self.is_headless = False

        def incognito(self):
            return self

        def auto_port(self):
            return self

        def mute(self, *a):
            return self

        def set_browser_path(self, path):
            self.browser_path = path
            return self

        def set_proxy(self, proxy):
            self.proxy = proxy
            return self

        def headless(self, value=True):
            self.is_headless = value
            return self

        def set_argument(self, arg):
            self.args.append(arg)
            return self

    stub.Chromium = _Chromium
    stub.ChromiumOptions = _ChromiumOptions
    sys.modules["DrissionPage"] = stub

from exitlag_auto import browser
from exitlag_auto.errors import BrowserNotFound

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


print("browser detection")
with tempfile.TemporaryDirectory() as tmp:
    fake = os.path.join(tmp, "chrome.exe")
    open(fake, "w").close()

    check("explicit existing path is used", browser.find_browser(fake) == fake)
    check("require_browser returns it", browser.require_browser(fake) == fake)
    check("quoted path is cleaned", browser.find_browser(f'"{fake}"') == fake)

    cfg = types.SimpleNamespace(path=fake, proxy="", headless=False)
    opts = browser.build_options(cfg)
    check("build_options sets the browser path", opts.browser_path == fake)
    check(
        "automation flag is applied",
        "--disable-blink-features=AutomationControlled" in opts.args,
    )
    check("headless off by default", opts.is_headless is False)

    cfg_headless = types.SimpleNamespace(path=fake, proxy="", headless=True)
    check("headless honoured when requested", browser.build_options(cfg_headless).is_headless is True)

missing = os.path.join(tempfile.gettempdir(), "definitely-not-here-chrome.exe")
detected = browser.find_browser(missing)
check(
    "bad explicit path falls back to auto-detection",
    detected is None or os.path.exists(detected),
    f"got {detected}",
)

_original = browser.find_browser
browser.find_browser = lambda explicit_path="": None
try:
    browser.require_browser("")
    check("require_browser raises when nothing is found", False)
except BrowserNotFound as exc:
    message = str(exc)
    check("require_browser raises when nothing is found", True)
    check("error names the config key", "[browser]" in message and "path" in message)
    check("error is in English, not DrissionPage Chinese", "\u65e0\u6cd5" not in message)
    check("error suggests installing Chrome", "Chrome" in message)
finally:
    browser.find_browser = _original

print("candidate lists")
check("windows candidates populated", len(browser._WINDOWS_CANDIDATES) > 5)
check("posix candidates populated", len(browser._POSIX_CANDIDATES) > 5)
check("chrome is tried first", "chrome.exe" in browser._WINDOWS_CANDIDATES[0].lower())
check("edge included as last resort", any("msedge" in c for c in browser._WINDOWS_CANDIDATES))
check("brave supported", any("brave" in c.lower() for c in browser._WINDOWS_CANDIDATES))
check("unexpanded variables are skipped", browser._clean("%NOPE_NOT_SET%\\x.exe").startswith("%"))

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL BROWSER TESTS PASSED")
