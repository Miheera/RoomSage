from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Usage, User
from security import CurrentUser, hash_password, issue_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().casefold()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid email address.")
        return value


def _response(user: User) -> dict[str, str]:
    return {"access_token": issue_token(user.id), "token_type": "bearer", "user_id": str(user.id), "email": user.email}


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: Credentials, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    if db.scalar(select(User.id).where(User.email == payload.email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=payload.email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    db.add(Usage(user_id=user.id))
    db.commit()
    db.refresh(user)
    return _response(user)


@router.post("/login")
def login(payload: Credentials, db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    return _response(user)


@router.get("/me")
def current_account(user: CurrentUser) -> dict[str, str]:
    return {"user_id": str(user.id), "email": user.email}
