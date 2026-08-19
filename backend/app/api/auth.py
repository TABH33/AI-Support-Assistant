"""`POST /auth/login` -- local password-based auth against the Customer /
SupportAgent tables (no OAuth/third-party auth provider; POC-scale local
auth per the plan's global constraints).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.security import create_access_token, verify_password
from app.database import get_db
from app.models.customer import Customer
from app.models.support_agent import SupportAgent

router = APIRouter(prefix="/auth", tags=["auth"])

# Never reveal whether an email is unregistered vs. the password being wrong
# -- both produce this exact same 401 response.
_GENERIC_LOGIN_ERROR = "Incorrect email or password"


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    access_level: str | None = None


@router.post("/login", response_model=LoginResponse)
def login(credentials: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    """Authenticate against `Customer` first, then `SupportAgent`, by email.

    On any failure (unknown email on both tables, or a matching email with
    the wrong password) this raises the same generic 401 -- the response
    never distinguishes "no such account" from "wrong password".
    """
    customer = db.query(Customer).filter(Customer.email == credentials.email).one_or_none()
    if customer is not None:
        if not verify_password(credentials.password, customer.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)
        token = create_access_token(subject=customer.customer_id, role="customer")
        return LoginResponse(access_token=token, role="customer")

    agent = db.query(SupportAgent).filter(SupportAgent.email == credentials.email).one_or_none()
    if agent is not None:
        if not verify_password(credentials.password, agent.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)
        token = create_access_token(
            subject=agent.support_agent_id,
            role="support_agent",
            access_level=agent.access_level.value,
        )
        return LoginResponse(
            access_token=token, role="support_agent", access_level=agent.access_level.value
        )

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)
