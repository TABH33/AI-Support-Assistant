"""`POST /auth/login` -- local password-based auth against the Customer /
SupportAgent tables (no OAuth/third-party auth provider; POC-scale local
auth per the plan's global constraints).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.security import create_access_token, verify_password
from app.database import get_db
from app.models.customer import Customer
from app.models.support_agent import SupportAgent
from app.models.telematics import Driver

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

    # A driver logging in directly (see app/seed/seed_demo_driver_logins.py
    # for how credentials get set) still comes out as role="customer",
    # subject=driver.customer_id -- their own fleet -- exactly like a fleet
    # Customer login above; only the extra `driver_id` claim distinguishes
    # it, purely for GET /auth/me. password_hash is None for the vast
    # majority of seeded drivers, who simply can't log in.
    driver = db.query(Driver).filter(Driver.email == credentials.email).one_or_none()
    if driver is not None and driver.password_hash is not None:
        if not verify_password(credentials.password, driver.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=_GENERIC_LOGIN_ERROR)
        token = create_access_token(
            subject=driver.customer_id, role="customer", driver_id=driver.driver_id
        )
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


class MeVehicle(BaseModel):
    registration_number: str
    make: str
    model: str


class MeResponse(BaseModel):
    role: str
    full_name: str
    company: str
    email: str | None
    phone_number: str | None
    vehicle: MeVehicle | None = None


@router.get("/me", response_model=MeResponse)
def me(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
) -> MeResponse:
    """The signed-in principal's own profile fields, for the nav bar's
    profile section -- resolved here (rather than the frontend piecing it
    together from several other endpoints) so a driver login's extra
    fields (company, assigned vehicle) and a support agent's fixed
    "Ctrack" company stay in one place.
    """
    if current_user.role == "support_agent":
        agent = db.get(SupportAgent, current_user.user_id)
        if agent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Profile not found")
        return MeResponse(
            role="support_agent",
            full_name=agent.full_name,
            company="Ctrack",
            email=agent.email,
            phone_number=agent.phone_number,
        )

    customer = db.get(Customer, current_user.user_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Profile not found")

    if current_user.driver_id is not None:
        driver = db.get(Driver, current_user.driver_id)
        if driver is None or driver.customer_id != customer.customer_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Profile not found")
        vehicle = driver.assigned_vehicle
        return MeResponse(
            role="customer",
            full_name=driver.full_name,
            company=customer.full_name,
            email=driver.email,
            phone_number=driver.phone_number,
            vehicle=(
                MeVehicle(
                    registration_number=vehicle.registration_number,
                    make=vehicle.make,
                    model=vehicle.model,
                )
                if vehicle is not None
                else None
            ),
        )

    # Legacy fleet-level customer login, with no specific driver attached.
    return MeResponse(
        role="customer",
        full_name=customer.full_name,
        company=customer.full_name,
        email=customer.email,
        phone_number=customer.phone_number,
    )
