"""
SkinVision AI — 认证(组员 3)
=============================
bcrypt 密码哈希(直接用 bcrypt 包,不依赖 passlib 以避 5.x 兼容问题)
  + JWT 签发/校验(PyJWT,HS256,7 天有效)
  + FastAPI 依赖 get_current_user(Bearer 头)

密码处理:bcrypt 有 72 字节上限,先用 sha256+base64 预哈希再 bcrypt,支持任意长度密码。
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from config import JWT_SECRET, JWT_ALGORITHM, JWT_EXPIRE_DAYS
from database import get_connection

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=True)
# 可选认证:无 token 时不报错(用于"先不管登录"场景,默认回落 demo 用户)
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


def _demo_user() -> dict:
    """查内置 demo 用户(没有则临时返回 id=0 的匿名 demo)。"""
    from config import DEMO_USERNAME
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, is_demo FROM users WHERE username=?", (DEMO_USERNAME,)
        ).fetchone()
    if row:
        return {"id": row["id"], "username": row["username"], "is_demo": bool(row["is_demo"])}
    return {"id": 0, "username": "demo", "is_demo": True}


# ============================================================
# 密码哈希
# ============================================================
def _prehash(plain: str) -> bytes:
    """sha256+base64 预哈希,绕过 bcrypt 72 字节上限。"""
    return base64.b64encode(hashlib.sha256(plain.encode("utf-8")).digest())


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_prehash(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))
    except Exception:
        return False


# ============================================================
# JWT
# ============================================================
def create_access_token(user_id: int, username: str) -> tuple[str, int]:
    """签发 JWT,返回 (token, expires_in_seconds)。"""
    now = datetime.now(timezone.utc)
    expire_delta = timedelta(days=JWT_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + expire_delta,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, int(expire_delta.total_seconds())


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 已过期")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效 token")


# ============================================================
# FastAPI 依赖
# ============================================================
def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """从 Bearer token 解出当前用户。无/失效 token → 401。"""
    payload = decode_token(token)
    try:
        user_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        raise HTTPException(401, "无效 token")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, is_demo FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        raise HTTPException(401, "用户不存在")
    return {"id": row["id"], "username": row["username"], "is_demo": bool(row["is_demo"])}


def get_current_user_optional(token: str | None = Depends(oauth2_scheme_optional)) -> dict:
    """有 token → 本人;无 token / 失效 → 默认 demo 用户(不报错)。前端免登录也能用个人接口。"""
    if not token:
        return _demo_user()
    try:
        payload = decode_token(token)
    except HTTPException:
        return _demo_user()
    try:
        user_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        user_id = 0
    if not user_id:
        return _demo_user()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, is_demo FROM users WHERE id=?", (user_id,)
        ).fetchone()
    if not row:
        return _demo_user()
    return {"id": row["id"], "username": row["username"], "is_demo": bool(row["is_demo"])}


# ============================================================
# 业务:注册 / 登录
# ============================================================
def register_user(username: str, password: str) -> dict:
    username = (username or "").strip()
    if not username or not password:
        raise HTTPException(400, "用户名和密码不能为空")
    if len(password) < 4:
        raise HTTPException(400, "密码至少 4 位")
    with get_connection() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise HTTPException(409, "用户名已存在")
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, created_at, is_demo) VALUES (?,?,?,0)",
            (username, hash_password(password), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        uid = cur.lastrowid
    token, exp_in = create_access_token(uid, username)
    return {"token": token, "expires_in": exp_in, "user": {"id": uid, "username": username}}


def authenticate_user(username: str, password: str) -> dict:
    username = (username or "").strip()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, username, password_hash, is_demo FROM users WHERE username=?",
            (username,),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")
    token, exp_in = create_access_token(row["id"], row["username"])
    return {"token": token, "expires_in": exp_in,
            "user": {"id": row["id"], "username": row["username"]}}
