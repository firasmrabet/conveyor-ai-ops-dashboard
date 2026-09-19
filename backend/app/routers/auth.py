"""Authentification : login JWT, profil, gestion des utilisateurs."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..models import User, now_ts_ms
from ..schemas import LoginRequest, TokenResponse, UserCreate
from ..security import authenticate, create_access_token, get_current_user, hash_password, require_role

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Connexion et obtention du jeton JWT")
def login(payload: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    user = authenticate(session, payload.email, payload.password)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Identifiants invalides")
    token = create_access_token(user.email, user.role, {"uid": user.id, "name": user.full_name})
    return TokenResponse(
        access_token=token,
        expires_in=settings.access_token_expire_minutes * 60,
        user={"email": user.email, "full_name": user.full_name, "role": user.role},
    )


@router.get("/me", summary="Profil de l'utilisateur courant")
def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": user.id, "email": user.email, "full_name": user.full_name,
        "role": user.role, "last_login": user.last_login, "active": user.active,
    }


@router.get("/users", summary="Liste des utilisateurs (admin)")
def list_users(session: Session = Depends(get_session),
               _: User = Depends(require_role("admin"))) -> List[dict]:
    users = session.exec(select(User)).all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role,
             "active": u.active, "last_login": u.last_login} for u in users]


@router.post("/users", status_code=201, summary="Créer un utilisateur (admin)")
def create_user(payload: UserCreate, session: Session = Depends(get_session),
                _: User = Depends(require_role("admin"))) -> dict:
    existing = session.exec(select(User).where(User.email == payload.email)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cet e-mail est déjà enregistré")
    user = User(email=payload.email, full_name=payload.full_name, role=payload.role,
                password_hash=hash_password(payload.password), created_at=now_ts_ms())
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"id": user.id, "email": user.email, "role": user.role}


@router.delete("/users/{user_id}", summary="Désactiver un utilisateur (admin)")
def deactivate_user(user_id: int, session: Session = Depends(get_session),
                    _: User = Depends(require_role("admin"))) -> dict:
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Utilisateur introuvable")
    user.active = False
    session.add(user)
    session.commit()
    return {"id": user.id, "active": user.active}
