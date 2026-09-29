"""Tests for the driver names added to
`app.ai.route_planning.summarize_todays_routes` (the chat "today's routes"
answer) -- see docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md,
"Driver names in existing output".

Direct function tests against an in-memory SQLite session: no FastAPI, and
no LLM (summarize_todays_routes never calls one). Rows are created with the
default `created_at` (now), which always falls inside today's Sydney-local
window that summarize_todays_routes filters on.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import route_plan_driver_label, summarize_todays_routes
from app.auth.security import hash_password
from app.models import Base, Customer, Driver, RoutePlan
from app.models.enums import PreferredNotificationMethod, RoutePlanStatus


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture()
def db_session(engine):
    with Session(engine) as session:
        yield session


def _make_customer(db_session: Session, *, tag: str) -> Customer:
    customer = Customer(
        full_name=f"Summary Customer {tag}",
        email=f"summary-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_driver(db_session: Session, *, customer: Customer, full_name: str, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=full_name,
        license_number=f"LIC-SUMMARY-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def _make_route_plan(
    db_session: Session,
    *,
    customer: Customer,
    origin: str = "Sydney CBD",
    destination: str = "Parramatta",
    driver_id: int | None = None,
    status: RoutePlanStatus = RoutePlanStatus.ACTIVE,
    warnings: list | None = None,
    unavailable: bool = False,
) -> RoutePlan:
    route_plan = RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="customer",
        created_by_id=customer.customer_id,
        driver_id=driver_id,
        origin_label=origin,
        destination_label=destination,
        warnings=warnings or [],
        unavailable=unavailable,
        status=status,
    )
    db_session.add(route_plan)
    db_session.commit()
    db_session.refresh(route_plan)
    return route_plan


def _capture_statements(engine):
    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    return statements, lambda: event.remove(engine, "before_cursor_execute", _capture)


def test_route_line_names_the_assigned_driver(db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")
    _make_route_plan(
        db_session,
        customer=customer,
        driver_id=driver.driver_id,
        warnings=[
            {"type": "risk_zone", "severity": "high", "description": "x"},
            {"type": "weather", "severity": "moderate", "description": "y"},
        ],
    )

    summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)

    assert (
        "- Sydney CBD -> Parramatta (active, driver: Alice Driver): 2 warning(s), 1 high-severity"
        in summary
    )


def test_unassigned_route_line_says_unassigned(db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(
        db_session,
        customer=customer,
        origin="Bondi Beach",
        destination="Sydney Airport",
        status=RoutePlanStatus.COMPLETED,
    )

    summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)

    assert "- Bondi Beach -> Sydney Airport (completed, driver: Unassigned): no warnings" in summary


def test_unavailable_route_line_still_names_the_driver(db_session):
    customer = _make_customer(db_session, tag="A")
    driver = _make_driver(db_session, customer=customer, full_name="Bob Driver", tag="B")
    _make_route_plan(db_session, customer=customer, driver_id=driver.driver_id, unavailable=True)

    summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)

    assert (
        "- Sydney CBD -> Parramatta (active, driver: Bob Driver): "
        "route data was unavailable when planned" in summary
    )


def test_drivers_are_fetched_in_one_batched_query_not_per_row(engine, db_session):
    customer = _make_customer(db_session, tag="A")
    alice = _make_driver(db_session, customer=customer, full_name="Alice Driver", tag="A")
    bob = _make_driver(db_session, customer=customer, full_name="Bob Driver", tag="B")
    _make_route_plan(db_session, customer=customer, driver_id=alice.driver_id)
    _make_route_plan(db_session, customer=customer, driver_id=bob.driver_id)
    _make_route_plan(db_session, customer=customer, driver_id=alice.driver_id)
    _make_route_plan(db_session, customer=customer)

    statements, stop = _capture_statements(engine)
    try:
        summary = summarize_todays_routes(db_session, customer_id=customer.customer_id)
    finally:
        stop()

    assert len([s for s in statements if "FROM drivers" in s]) == 1
    assert summary.count("driver: Alice Driver") == 2
    assert summary.count("driver: Bob Driver") == 1
    assert summary.count("driver: Unassigned") == 1


def test_no_driver_query_when_every_route_is_unassigned(engine, db_session):
    customer = _make_customer(db_session, tag="A")
    _make_route_plan(db_session, customer=customer)

    statements, stop = _capture_statements(engine)
    try:
        summarize_todays_routes(db_session, customer_id=customer.customer_id)
    finally:
        stop()

    assert [s for s in statements if "FROM drivers" in s] == []


def test_route_plan_driver_label_covers_unassigned_known_and_unknown_drivers():
    driver = Driver(driver_id=7, customer_id=1, full_name="Alice Driver", license_number="LIC-X")

    assert route_plan_driver_label(None, {}) == "Unassigned"
    assert route_plan_driver_label(7, {7: driver}) == "Alice Driver"
    assert route_plan_driver_label(42, {7: driver}) == "driver 42"
