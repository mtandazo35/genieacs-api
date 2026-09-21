"""Login, cuenta propia y gestion de usuarios (admin)."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator

from .. import db, ratelimit
from ..config import get_settings
from ..deps import CurrentUser, current_user, require_admin
from ..schemas import TokenOut, UserIn
from ..security import PASSWORD_MAX, create_token, hash_password, password_problem, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _check_policy(v: str) -> str:
    problem = password_problem(v)
    if problem:
        raise ValueError(problem)
    return v


class ChangeMyPassword(BaseModel):
    current_password: str = Field(..., max_length=PASSWORD_MAX)
    new_password: str

    _policy = field_validator("new_password")(_check_policy)


class SetPassword(BaseModel):
    new_password: str

    _policy = field_validator("new_password")(_check_policy)


class ActiveIn(BaseModel):
    active: bool


def client_ip(request: Request) -> str:
    # detras del proxy, uvicorn --proxy-headers ya pone aqui la IP real
    return request.client.host if request.client else "?"


@router.post("/login", response_model=TokenOut)
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    s = get_settings()
    ip, uname = client_ip(request), form.username[:150]
    ua, rid = request.headers.get("user-agent"), getattr(request.state, "request_id", None)
    wait = ratelimit.retry_after(ip, uname, s.login_max_fails_ip, s.login_max_fails_user)
    if wait:
        db.add_audit(uname, None, "Login bloqueado (demasiados intentos)", "POST", "/auth/login",
                     429, ip, ua, rid)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"Demasiados intentos fallidos. Espera {wait} s.",
                            headers={"Retry-After": str(wait)})
    user = db.get_user(uname)
    if not verify_password(form.password[:PASSWORD_MAX], user["password"] if user else None):
        ratelimit.record_failure(ip, uname)
        db.add_audit(uname, None, "Login fallido", "POST", "/auth/login", 401, ip, ua, rid)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales invalidas")
    if not user["active"]:
        db.add_audit(uname, None, "Login de usuario desactivado", "POST", "/auth/login", 403, ip, ua, rid)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Usuario desactivado")
    ratelimit.record_success(uname)
    db.add_audit(uname, None, "Login", "POST", "/auth/login", 200, ip, ua, rid)
    token = create_token(user)
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
    # el token actual queda revocado: se entrega uno nuevo para seguir en sesion
    u = db.get_user(user.username)
    return {"ok": True, "access_token": create_token(u)}


# ---- gestion de usuarios (admin) ----
@router.get("/users", dependencies=[Depends(require_admin)])
async def users():
    return db.list_users()


@router.get("/audit", dependencies=[Depends(require_admin)])
async def audit(limit: int = 300):
    """Registro de auditoria: cada cambio hecho por la API (solo admin)."""
    return db.list_audit(limit=max(1, min(limit, 1000)))


@router.post("/users", status_code=201, dependencies=[Depends(require_admin)])
async def create_user(body: UserIn):
    if db.get_user(body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "El usuario ya existe")
    if body.role == "isp" and not body.isp_tag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un usuario ISP requiere isp_tag")
    db.create_user(body.username, hash_password(body.password), body.role,
                   body.isp_tag if body.role == "isp" else None)
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
