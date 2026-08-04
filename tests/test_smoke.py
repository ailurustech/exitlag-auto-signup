"""Offline tests for everything that does not need a browser or network."""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exitlag_auto import identity, portable
from exitlag_auto.config import ConfigError, load_config
from exitlag_auto.mailbox import extract_verify_link
from exitlag_auto.store import AccountStore

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


print("identity")
for _ in range(200):
    pw = identity.random_password()
    if identity.password_problem(pw) is not None:
        check("random_password always satisfies ExitLag rules", False, pw)
        break
else:
    check("random_password always satisfies ExitLag rules (200 samples)", True)
check("passwords are unique", len({identity.random_password() for _ in range(200)}) == 200)
check("short password rejected", identity.password_problem("Ab1!") is not None)
check("no-digit password rejected", identity.password_problem("Abcdefgh!") is not None)
check("no-special password rejected", identity.password_problem("Abcdefgh1") is not None)
check("good password accepted", identity.password_problem("Abcdefgh1!") is None)

cfg = SimpleNamespace(randomize_identity=True, first_name="", last_name="", password="")
ids = {("%s %s" % (identity.make_identity(cfg).first_name, identity.make_identity(cfg).last_name)) for _ in range(50)}
check("identities vary", len(ids) > 10, f"got {len(ids)} distinct")
fixed = SimpleNamespace(randomize_identity=False, first_name="Ariel", last_name="Segovia", password="Abcdefgh1!")
ident = identity.make_identity(fixed)
check("fixed identity respected", ident.first_name == "Ariel" and ident.password == "Abcdefgh1!")

print("mailbox link extraction")
body = 'Hi, click <a href="https://www.exitlag.com/user/verify?token=abc123">here</a> or https://www.exitlag.com/other'
check("extracts verify link", extract_verify_link(body) == "https://www.exitlag.com/user/verify?token=abc123")
check("ignores unrelated bodies", extract_verify_link("no links here") is None)
check("ignores non-verify exitlag links", extract_verify_link("https://www.exitlag.com/clientarea.php") is None)

print("portable safety guards")
check("refuses non-exitlag path", portable._is_safe_target("C:\\Windows\\System32") is False)
check("refuses home dir", portable._is_safe_target("/home/user") is False)
check("allows exitlag path", portable._is_safe_target("C:\\Users\\x\\AppData\\ExitLag") is True)
check("clean_paths refuses unsafe targets", portable.clean_paths(["/tmp", "/etc"]) == 0)
hwid = portable.generate_hwid()
check("hwid is 32 hex chars", len(hwid) == 32 and all(c in "0123456789ABCDEF" for c in hwid))
check("hwids differ", len({portable.generate_hwid() for _ in range(100)}) == 100)

with tempfile.TemporaryDirectory() as tmp:
    acct_path = os.path.join(tmp, "exitlag", "account.txt")
    portable.write_account_txt(acct_path, "a@b.co", "Pw1!abcd")
    with open(acct_path, encoding="utf-8") as fh:
        content = fh.read()
    check("account.txt format is email/password lines", content == "a@b.co\nPw1!abcd\n", repr(content))

print("store / trial lifecycle")
with tempfile.TemporaryDirectory() as tmp:
    store = AccountStore(os.path.join(tmp, "accounts.db"))
    res = SimpleNamespace(email="one@x.co", alias_id="a1", password="Pw1!abcd",
                          first_name="A", last_name="B", verified=False)
    a1 = store.add(res, trial_days=3)
    check("account stored", a1 is not None and a1.email == "one@x.co")
    check("trial ~72h out", 71 < a1.hours_left() < 73, f"{a1.hours_left():.2f}h")
    check("get_valid finds it", store.get_valid(2).email == "one@x.co")

    res2 = SimpleNamespace(email="two@x.co", alias_id="a2", password="Pw2!abcd",
                           first_name="C", last_name="D", verified=True)
    store.add(res2, trial_days=1)
    check("get_valid prefers most runway", store.get_valid(2).email == "one@x.co")

    # Force an expired trial and confirm prune + exclusion.
    past = (datetime.now(timezone.utc) - timedelta(hours=5)).replace(microsecond=0).isoformat()
    store.conn.execute("UPDATE accounts SET trial_expires_at = ? WHERE email = ?", (past, "one@x.co"))
    store.conn.commit()
    check("expired excluded from get_valid", store.get_valid(2).email == "two@x.co")
    check("prune flags expired", store.prune() == 1)
    check("pruned status is expired", store.get_by_email("one@x.co").status == "expired")

    # renew_before_hours must reject an account that is about to die.
    soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).replace(microsecond=0).isoformat()
    store.conn.execute("UPDATE accounts SET trial_expires_at = ? WHERE email = ?", (soon, "two@x.co"))
    store.conn.commit()
    check("account expiring soon is rejected", store.get_valid(2) is None)
    check("same account accepted with 0h margin", store.get_valid(0).email == "two@x.co")

    store.set_status("two@x.co", "banned")
    check("banned excluded", store.get_valid(0) is None)
    check("list_all sees both", len(store.list_all()) == 2)
    store.close()

print("config validation")
with tempfile.TemporaryDirectory() as tmp:
    good = os.path.join(tmp, "config.toml")
    with open(good, "w", encoding="utf-8") as fh:
        fh.write('[addy]\napi_key="k123"\ndomain="anonaddy.me"\n[signup]\ncount=2\n')
    conf = load_config(good)
    check("loads valid config", conf.addy.domain == "anonaddy.me" and conf.signup.count == 2)
    check("defaults applied", conf.trial.days == 3 and conf.signup.attempts == 3)

    missing_key = os.path.join(tmp, "c2.toml")
    with open(missing_key, "w", encoding="utf-8") as fh:
        fh.write('[addy]\napi_key="YOUR_ADDY_API_KEY"\ndomain="x.co"\n')
    try:
        load_config(missing_key)
        check("placeholder api_key rejected", False)
    except ConfigError:
        check("placeholder api_key rejected", True)

    bad_fmt = os.path.join(tmp, "c3.toml")
    with open(bad_fmt, "w", encoding="utf-8") as fh:
        fh.write('[addy]\napi_key="k"\ndomain="x.co"\nalias_format="nonsense"\n')
    try:
        load_config(bad_fmt)
        check("invalid alias_format rejected", False)
    except ConfigError:
        check("invalid alias_format rejected", True)

    weak_pw = os.path.join(tmp, "c4.toml")
    with open(weak_pw, "w", encoding="utf-8") as fh:
        fh.write('[addy]\napi_key="k"\ndomain="x.co"\n[signup]\npassword="abc"\n')
    try:
        load_config(weak_pw)
        check("weak password rejected", False)
    except ConfigError:
        check("weak password rejected", True)

    verify_no_imap = os.path.join(tmp, "c5.toml")
    with open(verify_no_imap, "w", encoding="utf-8") as fh:
        fh.write('[addy]\napi_key="k"\ndomain="x.co"\n[signup]\nverify_email=true\n')
    try:
        load_config(verify_no_imap)
        check("verify_email without imap rejected", False)
    except ConfigError:
        check("verify_email without imap rejected", True)

    try:
        load_config(os.path.join(tmp, "nope.toml"))
        check("missing file rejected", False)
    except ConfigError:
        check("missing file rejected", True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("ALL OFFLINE TESTS PASSED")
