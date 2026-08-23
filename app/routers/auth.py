"""Login, cuenta propia y gestion de usuarios (admin)."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field

from .. import db
from ..deps import CurrentUser, current_user, require_admin
from ..schemas import TokenOut, UserIn
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class ChangeMyPassword(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


class SetPassword(BaseModel):
    new_password: str = Field(..., min_length=6)


class ActiveIn(BaseModel):
    active: bool


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = db.get_user(form.username)
    if not user or not verify_password(form.password, user["password"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales invalidas")
    if not user["active"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Usuario desactivado")
    token = create_token(user["username"], user["role"], user["isp_tag"])
    return TokenOut(access_token=token, role=user["role"], isp=user["isp_tag"])


# ---- cuenta propia (cualquier usuario) ----
@router.get("/me")
async def me(user: CurrentUser = Depends(current_user)):
    return {"username": user.username, "role": user.role, "isp": user.isp}


@router.put("/me/password")
async def change_my_password(body: ChangeMyPassword, user: CurrentUser = Depends(current_user)):
    u = db.get_user(user.username)
    if not u or not verify_password(body.current_password, u["password"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta")
    db.update_password(user.username, hash_password(body.new_password))
    return {"ok": True}


# ---- gestion de usuarios (admin) ----
@router.get("/users", dependencies=[Depends(require_admin)])
async def users():
    return db.list_users()


@router.get("/audit", dependencies=[Depends(require_admin)])
async def audit(limit: int = 300):
    """Registro de auditoria: cada cambio hecho por la API (solo admin)."""
    return db.list_audit(limit=min(limit, 1000))


@router.post("/users", status_code=201, dependencies=[Depends(require_admin)])
async def create_user(body: UserIn):
    if db.get_user(body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "El usuario ya existe")
    if body.role == "isp" and not body.isp_tag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un usuario ISP requiere isp_tag")
    db.create_user(body.username, hash_password(body.password), body.role, body.isp_tag)
    return {"ok": True, "username": body.username}


@router.put("/users/{username}/password", dependencies=[Depends(require_admin)])
async def admin_set_password(username: str, body: SetPassword):
    if not db.get_user(username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    db.update_password(username, hash_password(body.new_password))
    return {"ok": True}


@router.post("/users/{username}/active")
async def set_user_active(username: str, body: ActiveIn, admin: CurrentUser = Depends(require_admin)):
    u = db.get_user(username)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    if not body.active and u["role"] == "admin" and db.count_active_admins() <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes desactivar al último admin activo")
    db.set_active(username, body.active)
    return {"ok": True}


@router.delete("/users/{username}")
async def delete_user(username: str, admin: CurrentUser = Depends(require_admin)):
    u = db.get_user(username)
    if not u:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado")
    if username == admin.username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes eliminar tu propio usuario")
    if u["role"] == "admin" and db.count_active_admins() <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes eliminar al último admin activo")
    db.delete_user(username)
    return {"ok": True}
