"""JWT_SECRET 空值回退与签发护栏。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from config import _JWT_SECRET_DEFAULT, resolve_jwt_secret


def test_resolve_jwt_secret_treats_blank_as_missing():
    assert resolve_jwt_secret("") == _JWT_SECRET_DEFAULT
    assert resolve_jwt_secret("   ") == _JWT_SECRET_DEFAULT
    assert resolve_jwt_secret(None)  # env may be unset; still non-empty
    assert resolve_jwt_secret("prod-secret") == "prod-secret"
    assert resolve_jwt_secret("  prod-secret  ") == "prod-secret"


def test_create_access_token_rejects_empty_secret(monkeypatch):
    import auth

    monkeypatch.setattr(auth, "JWT_SECRET", "")
    with pytest.raises(HTTPException) as exc:
        auth.create_access_token(1, "admin", is_admin=True)
    assert exc.value.status_code == 500
    assert "JWT_SECRET" in exc.value.detail


def test_create_access_token_works_with_secret(monkeypatch):
    import auth

    monkeypatch.setattr(auth, "JWT_SECRET", "unit-test-secret")
    token, expires = auth.create_access_token(1, "admin", is_admin=True)
    assert token
    assert expires > 0
    payload = auth.decode_token(token)
    assert payload["username"] == "admin"
    assert payload["is_admin"] is True
