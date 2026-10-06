"""Grant two existing seeded drivers real login credentials:
``python -m app.seed.seed_demo_driver_logins``.

The synthetic dataset (`app.seed.generator`) gives every `Driver` a
placeholder, non-functional `password_hash` of `None` -- none of them can
log in. This script picks the first two `Driver` rows by id, gives each a
known email/password and an `assigned_vehicle_id` from their own fleet (for
the profile section's "vehicle and plate" field), and prints the plaintext
credentials once, the same way `admin@example.test` / `customer-01@example.test`
were set up for this deployment. Re-running overwrites the same two rows
rather than creating duplicates.
"""

from __future__ import annotations

from app.auth.security import hash_password
from app.database import SessionLocal
from app.models.telematics import Driver, Vehicle

_NUM_DRIVER_LOGINS = 2


class DemoDriverLoginError(RuntimeError):
    """The database isn't in a state this script can seed into."""


def run() -> list[tuple[Driver, str]]:
    """Set credentials on the first `_NUM_DRIVER_LOGINS` drivers. Returns
    each updated `Driver` paired with its plaintext password, for
    `_print_summary` -- the only place the plaintext is ever printed."""
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

        updated: list[tuple[Driver, str]] = []
        for index, driver in enumerate(drivers, start=1):
            email = f"driver-{index:02d}@example.test"
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
            updated.append((driver, password))

        session.commit()
        return updated
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _print_summary(updated: list[tuple[Driver, str]]) -> None:
    print("Driver login credentials (SYNTHETIC demo data):")
    for driver, password in updated:
        vehicle_note = (
            f"vehicle_id={driver.assigned_vehicle_id}"
            if driver.assigned_vehicle_id is not None
            else "no vehicle in their fleet to assign"
        )
        print(f"  {driver.email} / {password}  (driver_id={driver.driver_id}, {vehicle_note})")


def main(argv: list[str] | None = None) -> None:
    del argv  # no arguments today; accepted for consistency with the other seed scripts
    try:
        _print_summary(run())
    except DemoDriverLoginError as exc:
        print(f"Driver login seeding aborted: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
