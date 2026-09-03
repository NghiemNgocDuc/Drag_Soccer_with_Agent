"""Tests for account features: profile photo (Storage), password reset, email change.

Supabase is faked at the client layer (db.supabase_client / supabase.create_client)
so the route logic — validation, session scoping, re-auth gating, error mapping —
is exercised without network access.
"""
import io
import os
import sys
from types import SimpleNamespace

import pytest

os.environ["DEV_MODE"] = "1"
os.environ["SECRET_KEY"] = "test-secret-key"
sys.path.insert(0, os.path.dirname(__file__))

import app as appmod
from app import app  # noqa: E402


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(appmod, "_check_rate_limit", lambda path: True)


#  Fakes 

class FakeStorageFile:
    def __init__(self, parent):
        self.parent = parent

    def upload(self, path, data, options=None):
        self.parent.objects.append(path)
        return {"Key": path}

    def remove(self, paths):
        self.parent.objects = [p for p in self.parent.objects if p not in paths]

    def list(self, folder):
        return [{"name": o.split("/")[-1]} for o in self.parent.objects
                if o.startswith(folder + "/")]

    def get_public_url(self, path):
        return f"https://fake.supabase/storage/v1/object/public/avatars/{path}"


class FakeStorage:
    def __init__(self):
        self.objects = []
        self.created = []

    def create_bucket(self, id_, public=False):
        self.created.append((id_, public))

    def from_(self, name):
        return FakeStorageFile(self)


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self._eq = None
        self._data = None

    def select(self, *cols):
        return self

    def eq(self, k, v):
        self._eq = (k, v)
        return self

    def maybe_single(self):
        return self

    def in_(self, k, vals):
        return self

    def update(self, data):
        self._data = data
        return self

    def execute(self):
        if self._data is not None:
            rid = self._eq[1]
            self.rows[rid].update(self._data)
            return SimpleNamespace(data=[self.rows[rid]])
        matched = [r for k, r in self.rows.items()
                   if not self._eq or r.get(self._eq[0]) == self._eq[1]]
        if not matched:
            return SimpleNamespace(data=None)
        return SimpleNamespace(data=matched[0] if len(matched) == 1 else matched)


class FakeAdmin:
    def __init__(self, users):
        self.users = users      # id -> email
        self.updated = []

    def get_user_by_id(self, uid):
        if uid not in self.users:
            raise Exception("User not found")
        return SimpleNamespace(user=SimpleNamespace(email=self.users[uid]))

    def update_user_by_id(self, uid, attrs):
        self.updated.append((uid, attrs))


class FakeAuth:
    def __init__(self):
        self.session_ok = True
        self.pw_ok = True
        self.pw_calls = []
        self.updates = []
        self.reset_calls = []

    def reset_password_for_email(self, email, options=None):
        self.reset_calls.append((email, options))

    def set_session(self, at, rt):
        if not self.session_ok:
            raise Exception("Invalid token")

    def update_user(self, attrs):
        self.updates.append(attrs)

    def sign_in_with_password(self, creds):
        self.pw_calls.append(creds)
        if not self.pw_ok:
            raise Exception("Invalid credentials")
        return SimpleNamespace(user=object(), session=object())


class FakeClient:
    """A fake Supabase client: `.auth` exposes the auth operations."""

    def __init__(self):
        self.auth = FakeAuth()


class FakeService:
    def __init__(self):
        self.storage = FakeStorage()
        self.auth = SimpleNamespace(admin=FakeAdmin({"u1": "old@example.com"}))
        self.tables = {}

    def table(self, name):
        if name not in self.tables:
            self.tables[name] = FakeTable({})
        return self.tables[name]


def make_client(user_id="u1", username="tester"):
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = user_id
        s["username"] = username
    return client


def enable_supabase_env(monkeypatch):
    import config
    monkeypatch.setattr(config, "SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "fake-anon-key")


def _patch_supabase_create_client(monkeypatch, client):
    monkeypatch.setattr("supabase.create_client", lambda url, key: client)


#  Photo upload 

def _valid_png_bytes():
    try:
        from PIL import Image as _PILImage
        import io as _io2
        im = _PILImage.new("RGB", (64, 64), color=(120, 180, 220))
        b = _io2.BytesIO(); im.save(b, format="PNG"); return b.getvalue()
    except Exception:
        import base64
        return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC")

def test_photo_upload_success(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.service", svc)
    svc.table("profiles").rows["u1"] = {"id": "u1", "username": "tester"}
    r = make_client().post("/api/profile/photo",
                           data={"photo": (io.BytesIO(_valid_png_bytes()), "me.png")},
                           content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "/u1/photo_" in body["avatar_url"]
    assert svc.storage.objects and svc.storage.objects[0].startswith("u1/photo_")
    assert svc.table("profiles").rows["u1"]["avatar_url"] == body["avatar_url"]
    assert ("avatars", True) in svc.storage.created


def test_photo_upload_replaces_old(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.service", svc)
    svc.table("profiles").rows["u1"] = {"id": "u1", "username": "tester"}
    svc.storage.objects = ["u1/photo_111.jpg"]
    r = make_client().post("/api/profile/photo",
                           data={"photo": (io.BytesIO(_valid_png_bytes()), "new.webp")},
                           content_type="multipart/form-data")
    assert r.status_code == 200
    assert "u1/photo_111.jpg" not in svc.storage.objects
    assert len(svc.storage.objects) == 1
    assert svc.storage.objects[0].endswith(".webp")


def test_photo_upload_rejects_bad_type(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.service", svc)
    r = make_client().post("/api/profile/photo",
                           data={"photo": (io.BytesIO(b"gif"), "me.gif")},
                           content_type="multipart/form-data")
    assert r.status_code == 400
    assert "JPG, PNG or WebP" in r.get_json()["error"]


def test_photo_upload_rejects_oversize(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.service", svc)
    big = b"x" * (5 * 1024 * 1024 + 10)
    r = make_client().post("/api/profile/photo",
                           data={"photo": (io.BytesIO(big), "me.png")},
                           content_type="multipart/form-data")
    assert r.status_code == 400
    assert "5 MB" in r.get_json()["error"]


def test_photo_upload_no_file(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.service", svc)
    r = make_client().post("/api/profile/photo", data={},
                           content_type="multipart/form-data")
    assert r.status_code == 400


def test_photo_upload_denied_for_dev_and_clerk_users(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.service", svc)
    for uid in ("dev:someone@x.com", "clerk:abc"):
        r = make_client(user_id=uid).post("/api/profile/photo",
                                          data={"photo": (io.BytesIO(b"i"), "a.png")},
                                          content_type="multipart/form-data")
        assert r.status_code == 400
        assert "not available for this account type" in r.get_json()["error"]


def test_photo_upload_without_storage(monkeypatch):
    monkeypatch.setattr("db.supabase_client.service", None)
    r = make_client().post("/api/profile/photo",
                           data={"photo": (io.BytesIO(_valid_png_bytes()), "a.png")},
                           content_type="multipart/form-data")
    assert r.status_code == 503
    assert "Storage may not be configured" in r.get_json()["error"]


def test_photo_remove(monkeypatch):
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.service", svc)
    svc.storage.objects = ["u1/photo_111.jpg"]
    svc.table("profiles").rows["u1"] = {"id": "u1", "username": "tester",
                                        "avatar_url": "https://x/u1/photo_111.jpg"}
    r = make_client().delete("/api/profile/photo")
    assert r.status_code == 200
    assert r.get_json()["avatar_url"] is None
    assert svc.storage.objects == []
    assert svc.table("profiles").rows["u1"]["avatar_url"] is None


def test_photo_routes_require_login():
    r = app.test_client().post("/api/profile/photo",
                               data={"photo": (io.BytesIO(b"i"), "a.png")},
                               content_type="multipart/form-data")
    assert r.status_code in (302, 401)


#  Forgot password 

def test_forgot_password_calls_supabase(monkeypatch):
    anon = FakeClient()
    monkeypatch.setattr("db.supabase_client.anon", anon)
    r = make_client().post("/api/auth/forgot-password", json={"email": " A@X.com "})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert anon.auth.reset_calls[0][0] == "a@x.com"
    redirect_to = anon.auth.reset_calls[0][1]["redirect_to"]
    assert redirect_to.endswith("/reset-password")


def test_forgot_password_invalid_email(monkeypatch):
    anon = FakeClient()
    monkeypatch.setattr("db.supabase_client.anon", anon)
    r = make_client().post("/api/auth/forgot-password", json={"email": "not-an-email"})
    assert r.status_code == 400
    assert anon.auth.reset_calls == []


def test_forgot_password_dev_mode(monkeypatch):
    monkeypatch.setattr("db.supabase_client.anon", None)
    r = make_client().post("/api/auth/forgot-password", json={"email": "a@x.com"})
    assert r.status_code == 503


def test_forgot_password_does_not_leak_existence(monkeypatch):
    class BoomAuth(FakeAuth):
        def reset_password_for_email(self, email, options=None):
            raise Exception("Email not found")
    monkeypatch.setattr("db.supabase_client.anon", SimpleNamespace(auth=BoomAuth()))
    r = make_client().post("/api/auth/forgot-password", json={"email": "a@x.com"})
    assert r.status_code == 502


#  Reset password 

def test_reset_password_success(monkeypatch):
    enable_supabase_env(monkeypatch)
    anon = FakeClient()
    _patch_supabase_create_client(monkeypatch, anon)
    r = make_client().post("/api/auth/reset-password",
                           json={"access_token": "at", "refresh_token": "rt", "password": "newpass123"})
    assert r.status_code == 200
    assert anon.auth.updates == [{"password": "newpass123"}]


def test_reset_password_expired_token(monkeypatch):
    enable_supabase_env(monkeypatch)
    anon = FakeClient()
    anon.auth.session_ok = False
    _patch_supabase_create_client(monkeypatch, anon)
    r = make_client().post("/api/auth/reset-password",
                           json={"access_token": "at", "refresh_token": "rt", "password": "newpass123"})
    assert r.status_code == 400
    assert "expired" in r.get_json()["error"]
    assert anon.auth.updates == []


def test_reset_password_missing_tokens():
    r = make_client().post("/api/auth/reset-password",
                           json={"access_token": "", "refresh_token": "", "password": "newpass123"})
    assert r.status_code == 400
    assert "expired" in r.get_json()["error"]


def test_reset_password_too_short():
    r = make_client().post("/api/auth/reset-password",
                           json={"access_token": "at", "refresh_token": "rt", "password": "12345"})
    assert r.status_code == 400
    assert "6 characters" in r.get_json()["error"]


def test_reset_password_dev_mode(monkeypatch):
    import config
    monkeypatch.setattr(config, "SUPABASE_URL", "")
    monkeypatch.setattr(config, "SUPABASE_ANON_KEY", "")
    r = make_client().post("/api/auth/reset-password",
                           json={"access_token": "at", "refresh_token": "rt", "password": "newpass123"})
    assert r.status_code == 503


#  Change email 

def test_change_email_success(monkeypatch):
    anon = FakeClient()
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.anon", anon)
    monkeypatch.setattr("db.supabase_client.service", svc)
    r = make_client().post("/api/account/change-email",
                           json={"current_password": "oldpw", "new_email": "NEW@x.com"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["pending_email"] == "new@x.com"
    uid_updated, attrs = svc.auth.admin.updated[0]
    assert uid_updated == "u1"
    assert attrs == {"email": "new@x.com"}          # no email_confirm: confirmation required
    assert "email_confirm" not in attrs
    assert anon.auth.pw_calls == [{"email": "old@example.com", "password": "oldpw"}]


def test_change_email_wrong_password(monkeypatch):
    anon = FakeClient()
    anon.auth.pw_ok = False
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.anon", anon)
    monkeypatch.setattr("db.supabase_client.service", svc)
    r = make_client().post("/api/account/change-email",
                           json={"current_password": "wrong", "new_email": "new@x.com"})
    assert r.status_code == 401
    assert "incorrect" in r.get_json()["error"]
    assert svc.auth.admin.updated == []


def test_change_email_same_address(monkeypatch):
    anon = FakeClient()
    svc = FakeService()
    monkeypatch.setattr("db.supabase_client.anon", anon)
    monkeypatch.setattr("db.supabase_client.service", svc)
    r = make_client().post("/api/account/change-email",
                           json={"current_password": "oldpw", "new_email": "OLD@example.com"})
    assert r.status_code == 400
    assert "already" in r.get_json()["error"]
    assert svc.auth.admin.updated == []


def test_change_email_validation(monkeypatch):
    monkeypatch.setattr("db.supabase_client.anon", FakeClient())
    monkeypatch.setattr("db.supabase_client.service", FakeService())
    for payload in ({"current_password": "", "new_email": "new@x.com"},
                    {"current_password": "oldpw", "new_email": "nope"}):
        r = make_client().post("/api/account/change-email", json=payload)
        assert r.status_code == 400


def test_change_email_denied_for_dev_and_clerk(monkeypatch):
    monkeypatch.setattr("db.supabase_client.anon", FakeClient())
    monkeypatch.setattr("db.supabase_client.service", FakeService())
    for uid in ("dev:someone@x.com", "clerk:abc"):
        r = make_client(user_id=uid).post("/api/account/change-email",
                                          json={"current_password": "x", "new_email": "new@x.com"})
        assert r.status_code == 400
        assert "not available for this account type" in r.get_json()["error"]


def test_change_email_dev_mode(monkeypatch):
    monkeypatch.setattr("db.supabase_client.anon", None)
    monkeypatch.setattr("db.supabase_client.service", None)
    r = make_client().post("/api/account/change-email",
                           json={"current_password": "x", "new_email": "new@x.com"})
    assert r.status_code == 503


def test_pages_render():
    assert make_client().get("/profile").status_code == 200
    assert app.test_client().get("/forgot-password").status_code == 200
    assert app.test_client().get("/reset-password").status_code == 200
    assert "Forgot password" in app.test_client().get("/login").get_data(as_text=True)
