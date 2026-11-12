import json
import stat
import time

import pytest

from medcomms.front.users import (
    AccountError,
    UserStore,
    check_password,
    generate_password,
    hash_password,
)


@pytest.fixture
def store(tmp_path):
    return UserStore(tmp_path)


def test_passwords_are_stored_hashed(store, tmp_path):
    store.create("Tester01", "correct horse")
    saved = json.loads((tmp_path / "users.json").read_text())
    assert list(saved) == ["tester01"]  # usernames are lowercased
    assert "correct horse" not in (tmp_path / "users.json").read_text()
    assert saved["tester01"]["password"].startswith("scrypt$")
    for name in ("users.json", "secret.key"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600


def test_hashes_are_salted_and_checkable():
    a, b = hash_password("same password"), hash_password("same password")
    assert a != b
    assert check_password("same password", a)
    assert not check_password("other password", a)
    assert not check_password("x", "not-a-hash")
    assert len(generate_password()) == 12


@pytest.mark.parametrize(
    ("name", "password", "message"),
    [
        ("ab", "long enough", "Usernames"),
        ("has space", "long enough", "Usernames"),
        ("-starts-with-dash", "long enough", "Usernames"),
        ("fine", "short", "at least 8"),
    ],
)
def test_invalid_accounts_are_refused(store, name, password, message):
    with pytest.raises(AccountError, match=message):
        store.create(name, password)


def test_usernames_are_unique(store):
    store.create("tester01", "password1")
    with pytest.raises(AccountError, match="taken"):
        store.create("TESTER01", "password2")


def test_authenticate(store):
    store.create("tester01", "password1")
    assert store.authenticate(" Tester01 ", "password1") == "tester01"
    with pytest.raises(AccountError, match="Wrong"):
        store.authenticate("tester01", "nope")
    with pytest.raises(AccountError, match="Wrong"):
        store.authenticate("nobody", "password1")
    store.update("tester01", disabled=True)
    with pytest.raises(AccountError, match="disabled"):
        store.authenticate("tester01", "password1")


def test_cookies_identify_the_user_until_revoked(store, tmp_path):
    store.create("tester01", "password1")
    cookie = store.cookie_for("tester01")
    assert store.user_from_cookie(cookie) == "tester01"
    # The same key is reused after a restart.
    assert UserStore(tmp_path).user_from_cookie(cookie) == "tester01"

    tampered = cookie[:-1] + ("1" if cookie[-1] == "0" else "0")
    for forged in ("tester01", "tester01:x:y", tampered, "other" + cookie):
        assert store.user_from_cookie(forged) is None

    store.update("tester01", disabled=True)
    assert store.user_from_cookie(cookie) is None
    store.update("tester01", disabled=False)
    assert store.user_from_cookie(cookie) == "tester01"

    store.set_password("tester01", "password2")  # signs them out everywhere
    assert store.user_from_cookie(cookie) is None

    expired = store.cookie_for("tester01").split(":")
    expired[1] = str(int(time.time()) - 1)
    assert store.user_from_cookie(":".join(expired)) is None


def test_remove_and_touch(store):
    store.create("tester01", "password1")
    assert store.get("tester01")["last_seen"] is None
    store.touch("tester01")
    assert store.get("tester01")["last_seen"]
    assert store.remove("tester01")
    assert not store.remove("tester01")
    assert not store.update("tester01", disabled=True)
    assert not store.set_password("tester01", "password1")
