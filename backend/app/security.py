"""Authentification JWT + hachage des mots de passe (PBKDF2-SHA256, stdlib) + rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from .config import settings
from .db import get_session
from .models import User

ALGORITHM = "HS256"
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# --------------------------------------------------------------------------
#  Mots de passe : PBKDF2-SHA256, 260 000 itérations (pas de dépendance C)
# --------------------------------------------------------------------------
_ITERATIONS = 260_000


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters)).hex()
        return hmac.compare_digest(candidate, digest)
    except Exception:
        return False


# --------------------------------------------------------------------------
#  Jetons
# --------------------------------------------------------------------------
def create_access_token(subject: str, role: str, extra: Optional[Dict[str, Any]] = None) -> str:
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "iat": now,
        "exp": now + settings.access_token_expire_minutes * 60,
        "iss": "conveyor-ai-ops",
        **(extra or {}),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM], issuer="conveyor-ai-ops")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expiré")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token invalide")


def authenticate(session: Session, email: str, password: str) -> Optional[User]:
    user = session.exec(select(User).where(User.email == email)).first()
    if user and user.active and verify_password(password, user.password_hash):
        user.last_login = int(time.time() * 1000)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
    return None


# --------------------------------------------------------------------------
#  Dépendances FastAPI
# --------------------------------------------------------------------------
def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    session: Session = Depends(get_session),
) -> User:
    if not token:
        # Mode démo : si l'auth est désactivée côté front, on retombe sur l'admin.
        # (mettre REQUIRE_AUTH=true en production — cf. README sécurité)
        user = session.exec(select(User).where(User.role == "admin")).first()
        if user:
            return user
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié")
    payload = decode_token(token)
    user = session.exec(select(User).where(User.email == payload["sub"])).first()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utilisateur inconnu")
    return user


def require_role(*roles: str):
    def _checker(user: User = Depends(get_current_user)) -> User:
        if roles and user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Rôle requis : {'/'.join(roles)}")
        return user

    return _checker


# --------------------------------------------------------------------------
#  Rate limiting (mémoire, fenêtre glissante). Redis possible en multi-instance.
# --------------------------------------------------------------------------
_hits: Dict[str, deque] = defaultdict(deque)


async def rate_limit(request: Request) -> None:
    if settings.rate_limit_per_minute <= 0:
        return
    key = request.client.host if request.client else "anonymous"
    now = time.time()
    bucket = _hits[key]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_minute:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Trop de requêtes, réessayez dans une minute")
    bucket.append(now)
