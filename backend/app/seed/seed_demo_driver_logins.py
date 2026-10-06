"""Grant two existing seeded drivers real login credentials, and make sure
each has an active route today: ``python -m app.seed.seed_demo_driver_logins``.

The synthetic dataset (`app.seed.generator`) gives every `Driver` a
placeholder, non-functional `password_hash` of `None` -- none of them can
log in. This script picks the first two `Driver` rows by id, gives each a
known email/password and an `assigned_vehicle_id` from their own fleet (for
the profile section's "vehicle and plate" field), and prints the plaintext
credentials once, the same way `admin@example.test` / `customer-01@example.test`
were set up for this deployment. Re-running overwrites the same two rows
rather than creating duplicates.

Also ensures each of the two drivers has an ACTIVE `RoutePlan` dated today
(creating one from a fixed demo pair below if they don't already have one) --
so the chat "my vehicle" and "active drivers" intents both have something
real to answer with immediately after a fresh deploy, for both a driver
login and an admin asking about that driver's fleet. Skipped (not
duplicated) if the driver already has an active route today, e.g. from a
previous run of this script.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models.enums import RoutePlanStatus
from app.models.route_plan import RoutePlan
from app.models.support_agent import SupportAgent
from app.models.telematics import Driver, Vehicle
from app.timeutil import site_day_bounds, site_today

_NUM_DRIVER_LOGINS = 2

#: Fixed, deterministic demo route per driver index -- same style as
#: app/seed/seed_demo_routes.py's own DEMO_ROUTE_PAIRS/DEMO_WARNING_POOL
#: (approximate real Sydney landmark coordinates, a 2-point GeoJSON
#: LineString, no network call), kept self-contained here rather than
#: importing that module's private helpers.
_DEMO_ACTIVE_ROUTES = (
    {
        "origin_label": "Sydney CBD",
        "destination_label": "Parramatta",
        "origin": (-33.8688, 151.2093),
        "destination": (-33.8150, 151.0011),
        "distance_km": 24.1,
        "duration_min": 35.0,
        "warning": {
            "location": {"lat": -33.84, "lon": 151.10},
            "distance_from_origin_km": 12.0,
            "type": "risk_zone",
            "severity": "moderate",
            "description": "11 driving events recorded within 500m of this point (5 speeding).",
        },
    },
    {
        "origin_label": "Bondi Beach",
        "destination_label": "Sydney Airport",
        "origin": (-33.8908, 151.2743),
        "destination": (-33.9399, 151.1753),
        "distance_km": 13.5,
        "duration_min": 25.0,
        "warning": {
            "location": {"lat": -33.91, "lon": 151.22},
            "distance_from_origin_km": 6.0,
            "type": "weather",
            "severity": "moderate",
            "description": "Heavy rain forecast near this segment (65% probability).",
        },
    },
)


class DemoDriverLoginError(RuntimeError):
    """The database isn't in a state this script can seed into."""


@dataclass(frozen=True)
class UpdatedDriverLogin:
    """Plain data, not the ORM `Driver` itself -- `run()` closes its session
    before returning, so holding onto the mapped object would make
    `_print_summary` touch a detached instance and raise."""

    driver_id: int
    email: str
    password: str
    assigned_vehicle_id: int | None
    active_route: str | None


def _ensure_active_route_today(session: Session, driver: Driver, *, demo_route: dict) -> str | None:
    """Returns a human-readable "origin -> destination" label if an ACTIVE
    route for `driver` dated today now exists (either already did, or was
    just created from `demo_route`), or None if neither was possible (no
    SupportAgent row exists yet to attribute the dispatch to)."""
    today_start, tomorrow_start = site_day_bounds(site_today())
    existing = (
        session.query(RoutePlan)
        .filter(
            RoutePlan.driver_id == driver.driver_id,
            RoutePlan.status == RoutePlanStatus.ACTIVE,
            RoutePlan.created_at >= today_start,
            RoutePlan.created_at < tomorrow_start,
        )
        .order_by(RoutePlan.created_at.desc())
        .first()
    )
    if existing is not None:
        return f"{existing.origin_label} -> {existing.destination_label} (already active)"

    agent = session.query(SupportAgent).order_by(SupportAgent.support_agent_id).first()
    if agent is None:
        return None

    origin_lat, origin_lon = demo_route["origin"]
    destination_lat, destination_lon = demo_route["destination"]
    session.add(
        RoutePlan(
            customer_id=driver.customer_id,
            created_by_role="support_agent",
            created_by_id=agent.support_agent_id,
            driver_id=driver.driver_id,
            origin_label=demo_route["origin_label"],
            destination_label=demo_route["destination_label"],
            distance_km=demo_route["distance_km"],
            duration_min=demo_route["duration_min"],
            geometry={
                "type": "LineString",
                # GeoJSON order is [longitude, latitude].
                "coordinates": [[origin_lon, origin_lat], [destination_lon, destination_lat]],
            },
            warnings=[demo_route["warning"]],
            unavailable=False,
            status=RoutePlanStatus.ACTIVE,
        )
    )
    return f"{demo_route['origin_label']} -> {demo_route['destination_label']} (created)"


def run() -> list[UpdatedDriverLogin]:
    """Set credentials on the first `_NUM_DRIVER_LOGINS` drivers, and make
    sure each has an active route today (see module docstring)."""
    session = SessionLocal()
    try:
        drivers = (
            session.query(Driver).order_by(Driver.driver_id).limit(_NUM_DRIVER_LOGINS).all()
        )
        if len(drivers) < _NUM_DRIVER_LOGINS:
            raise DemoDriverLoginError(
                f"Only {len(drivers)} driver(s) exist, need {_NUM_DRIVER_LOGINS} -- "
                "run `python -m app.seed.seed` first."
            )

        updated: list[UpdatedDriverLogin] = []
        for index, driver in enumerate(drivers, start=1):
            # "driver-login-" (not generator.py's plain "driver-NN@..."):
            # the live DB's driver rows don't actually line up with their
            # generation-time index (ids 1/2 didn't hold "driver-01"/
            # "driver-02"@example.test when this was first run against a
            # real deployment) -- a distinct prefix sidesteps the
            # uq_drivers_email collision entirely rather than depending on
            # that alignment.
            email = f"driver-login-{index:02d}@example.test"
            password = f"Driver{index}Pass123!"
            driver.email = email
            driver.password_hash = hash_password(password)
            if driver.assigned_vehicle_id is None:
                vehicle = (
                    session.query(Vehicle)
                    .filter(Vehicle.customer_id == driver.customer_id)
                    .order_by(Vehicle.vehicle_id)
                    .first()
                )
                if vehicle is not None:
                    driver.assigned_vehicle_id = vehicle.vehicle_id

            demo_route = _DEMO_ACTIVE_ROUTES[(index - 1) % len(_DEMO_ACTIVE_ROUTES)]
            active_route = _ensure_active_route_today(session, driver, demo_route=demo_route)

            updated.append(
                UpdatedDriverLogin(
                    driver_id=driver.driver_id,
                    email=email,
                    password=password,
                    assigned_vehicle_id=driver.assigned_vehicle_id,
                    active_route=active_route,
                )
            )

        session.commit()
        return updated
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _print_summary(updated: list[UpdatedDriverLogin]) -> None:
    print("Driver login credentials (SYNTHETIC demo data):")
    for login in updated:
        vehicle_note = (
            f"vehicle_id={login.assigned_vehicle_id}"
            if login.assigned_vehicle_id is not None
            else "no vehicle in their fleet to assign"
        )
        route_note = login.active_route or "no SupportAgent row exists to attribute a route to"
        print(
            f"  {login.email} / {login.password}  "
            f"(driver_id={login.driver_id}, {vehicle_note}, route: {route_note})"
        )


def main(argv: list[str] | None = None) -> None:
    del argv  # no arguments today; accepted for consistency with the other seed scripts
    try:
        _print_summary(run())
    except DemoDriverLoginError as exc:
        print(f"Driver login seeding aborted: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
