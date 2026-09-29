"""Smoke tests for app.seed.seed_demo_routes -- the manual demo-route seed
script (docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md).

Same in-memory SQLite (FK enforcement on) pattern as test_seed.py. The key
property is that every row the script writes round-trips through the REAL
model and the REAL API/response validators every consumer uses --
`_row_to_list_item` (-> `WarningOut`) and `compute_route_progress` -- so the
chat summary, reports, Routes page and live map all accept seeded rows with
no format branching.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.route_planning import summarize_todays_routes
from app.ai.route_tracking import compute_route_progress
from app.api.route_plan import _row_to_list_item
from app.auth.security import hash_password
from app.models import Base, Customer, Driver, RoutePlan, SupportAgent
from app.models.enums import AccessLevel, PreferredNotificationMethod, RoutePlanStatus
from app.seed import seed_demo_routes as seed_module
from app.seed.seed_demo_routes import (
    DEMO_ROUTE_PAIRS,
    DEMO_WARNING_POOL,
    DemoSeedError,
    seed_demo_routes,
)
from app.timeutil import SITE_TZ, site_day_bounds

_WARNING_KEYS = {"location", "distance_from_origin_km", "type", "severity", "description"}


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
        full_name=f"Demo Customer {tag}",
        email=f"demo-customer-{tag.lower()}@example.test",
        phone_number="+61000000000",
        preferred_notification_method=PreferredNotificationMethod.EMAIL,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(customer)
    db_session.commit()
    db_session.refresh(customer)
    return customer


def _make_driver(db_session: Session, *, customer: Customer, tag: str) -> Driver:
    driver = Driver(
        customer_id=customer.customer_id,
        full_name=f"Demo Driver {tag}",
        license_number=f"LIC-DEMO-{tag}",
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    return driver


def _make_agent(db_session: Session) -> SupportAgent:
    agent = SupportAgent(
        full_name="Demo Dispatch Agent",
        email="demo-dispatch-agent@example.test",
        access_level=AccessLevel.TIER_1,
        password_hash=hash_password("irrelevant-not-used-here"),
    )
    db_session.add(agent)
    db_session.commit()
    db_session.refresh(agent)
    return agent


@pytest.fixture()
def world(db_session):
    """Customers created in id order A < B < C: A has two drivers, B has
    one, C has none (must be skipped). One support agent to attribute the
    simulated dispatch to."""
    customer_a = _make_customer(db_session, tag="A")
    customer_b = _make_customer(db_session, tag="B")
    customer_c = _make_customer(db_session, tag="C")
    drivers_a = [
        _make_driver(db_session, customer=customer_a, tag="A1"),
        _make_driver(db_session, customer=customer_a, tag="A2"),
    ]
    drivers_b = [_make_driver(db_session, customer=customer_b, tag="B1")]
    agent = _make_agent(db_session)
    return {
        "customer_a": customer_a,
        "customer_b": customer_b,
        "customer_c": customer_c,
        "fleets": {
            customer_a.customer_id: {d.driver_id for d in drivers_a},
            customer_b.customer_id: {d.driver_id for d in drivers_b},
        },
        "agent": agent,
    }


def _as_utc(value: datetime) -> datetime:
    """SQLite returns tz-naive values for DateTime(timezone=True) columns."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _seed_and_reload(db_session, *, now: datetime, **kwargs) -> tuple[object, list[RoutePlan]]:
    summary = seed_demo_routes(db_session, rng=random.Random(7), now=now, **kwargs)
    db_session.commit()
    db_session.expire_all()
    return summary, db_session.query(RoutePlan).all()


def test_every_created_row_round_trips_through_the_real_model_and_api_validators(
    db_session, world
):
    now = datetime.now(timezone.utc)
    summary, rows = _seed_and_reload(db_session, now=now)

    assert summary.total_created == len(rows) > 0
    for row in rows:
        item = _row_to_list_item(row)  # validates every warning via WarningOut
        assert item.unavailable is False
        progress = compute_route_progress(row, now=now)
        assert 0.0 <= progress.progress_percent <= 100.0


def test_rows_are_dated_today_in_sydney_with_an_active_and_completed_mix(db_session, world):
    now = datetime.now(timezone.utc)
    _, rows = _seed_and_reload(db_session, now=now)
    start, end = site_day_bounds(now.astimezone(SITE_TZ).date())

    for row in rows:
        created_at = _as_utc(row.created_at)
        assert start <= created_at <= now < end
        if row.status == RoutePlanStatus.COMPLETED:
            assert row.completed_at is not None
            assert created_at <= _as_utc(row.completed_at) <= now
        else:
            assert row.completed_at is None

    for key in ("customer_a", "customer_b"):
        statuses = {r.status for r in rows if r.customer_id == world[key].customer_id}
        assert statuses == {RoutePlanStatus.ACTIVE, RoutePlanStatus.COMPLETED}


def test_drivers_come_only_from_the_rows_own_customer_and_dispatch_is_a_support_agent(
    db_session, world
):
    _, rows = _seed_and_reload(db_session, now=datetime.now(timezone.utc))

    for row in rows:
        if row.driver_id is not None:
            assert row.driver_id in world["fleets"][row.customer_id]
        assert row.created_by_role == "support_agent"
        assert row.created_by_id == world["agent"].support_agent_id


def test_labels_geometry_and_warnings_use_the_demo_shapes(db_session, world):
    _, rows = _seed_and_reload(db_session, now=datetime.now(timezone.utc))
    known_pairs = {(p.origin.label, p.destination.label) for p in DEMO_ROUTE_PAIRS}

    for row in rows:
        assert (row.origin_label, row.destination_label) in known_pairs
        assert row.geometry["type"] == "LineString"
        assert len(row.geometry["coordinates"]) == 2
        assert 0 <= len(row.warnings) <= 3
        for warning in row.warnings:
            assert set(warning) == _WARNING_KEYS
            assert (warning["type"], warning["severity"], warning["description"]) in DEMO_WARNING_POOL
            assert warning["type"] in {"weather", "risk_zone"}
            assert warning["severity"] in {"moderate", "high"}


def test_customer_with_no_drivers_is_skipped_and_reported(db_session, world):
    summary, _ = _seed_and_reload(db_session, now=datetime.now(timezone.utc))

    customer_c_id = world["customer_c"].customer_id
    assert summary.skipped == {customer_c_id: "no drivers in this customer's fleet"}
    assert customer_c_id not in summary.created
    assert db_session.query(RoutePlan).filter_by(customer_id=customer_c_id).count() == 0


def test_requested_customer_is_included_beyond_the_default_count(db_session, world):
    customer_a_id = world["customer_a"].customer_id
    customer_b_id = world["customer_b"].customer_id

    summary, _ = _seed_and_reload(
        db_session, now=datetime.now(timezone.utc), customer_ids=[customer_b_id], customer_count=1
    )

    assert set(summary.created) == {customer_a_id, customer_b_id}


def test_unknown_requested_customer_is_skipped_not_fatal(db_session, world):
    summary, _ = _seed_and_reload(
        db_session, now=datetime.now(timezone.utc), customer_ids=[999999]
    )

    assert summary.skipped[999999] == "no such customer"
    assert summary.total_created > 0


def test_rerunning_adds_another_batch_instead_of_erroring(db_session, world):
    now = datetime.now(timezone.utc)
    first = seed_demo_routes(db_session, rng=random.Random(1), now=now)
    db_session.commit()
    second = seed_demo_routes(db_session, rng=random.Random(2), now=now)
    db_session.commit()

    assert db_session.query(RoutePlan).count() == first.total_created + second.total_created


def test_raises_when_no_support_agent_exists(db_session):
    customer = _make_customer(db_session, tag="SOLO")
    _make_driver(db_session, customer=customer, tag="SOLO")

    with pytest.raises(DemoSeedError, match="SupportAgent"):
        seed_demo_routes(db_session, rng=random.Random(1))


def test_seeded_routes_show_up_with_driver_names_in_the_chat_summary(db_session, world):
    _, rows = _seed_and_reload(db_session, now=datetime.now(timezone.utc))
    customer_a_id = world["customer_a"].customer_id

    summary_text = summarize_todays_routes(db_session, customer_id=customer_a_id)

    a_rows = [r for r in rows if r.customer_id == customer_a_id]
    assert f"{len(a_rows)} route(s) planned today" in summary_text
    for row in a_rows:
        if row.driver_id is None:
            assert "driver: Unassigned" in summary_text
        else:
            driver = db_session.get(Driver, row.driver_id)
            assert f"driver: {driver.full_name}" in summary_text


def test_main_commits_and_prints_a_summary(engine, db_session, world, monkeypatch, capsys):
    customer_c_id = world["customer_c"].customer_id
    db_session.close()
    monkeypatch.setattr(seed_module, "SessionLocal", sessionmaker(bind=engine))

    seed_module.main(["--seed", "3"])

    out = capsys.readouterr().out
    assert "Demo routes created" in out
    assert "Skipped customers:" in out
    assert f"customer {customer_c_id}: no drivers in this customer's fleet" in out
    with Session(engine) as fresh:
        assert fresh.query(RoutePlan).count() > 0


def test_main_exits_non_zero_without_a_support_agent(engine, db_session, monkeypatch, capsys):
    customer = _make_customer(db_session, tag="SOLO")
    _make_driver(db_session, customer=customer, tag="SOLO")
    db_session.close()
    monkeypatch.setattr(seed_module, "SessionLocal", sessionmaker(bind=engine))

    with pytest.raises(SystemExit) as exc_info:
        seed_module.main([])

    assert exc_info.value.code == 1
    assert "Demo route seeding aborted" in capsys.readouterr().out
