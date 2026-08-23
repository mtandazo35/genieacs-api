"""Login y gestion de usuarios (solo admin)."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from .. import db
from ..deps import require_admin
from ..schemas import TokenOut, UserIn
from ..security import create_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = db.get_user(form.username)
    if not user or not verify_password(form.password, user["password"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales invalidas")
    token = create_token(user["username"], user["role"], user["isp_tag"])
    return TokenOut(access_token=token, role=user["role"], isp=user["isp_tag"])


@router.get("/users", dependencies=[Depends(require_admin)])
async def users():
    return db.list_users()


@router.post("/users", status_code=201, dependencies=[Depends(require_admin)])
async def create_user(body: UserIn):
    if db.get_user(body.username):
        raise HTTPException(status.HTTP_409_CONFLICT, "El usuario ya existe")
    if body.role == "isp" and not body.isp_tag:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Un usuario ISP requiere isp_tag")
    db.create_user(body.username, hash_password(body.password), body.role, body.isp_tag)
    return {"ok": True, "username": body.username}
