"""Demo route data: ``python -m app.seed.seed_demo_routes``.

**This script generates SYNTHETIC DEMO DATA for exercising the chat and
report surfaces. It is not real incident detection, and not a placeholder
for it.** No GPS or incident-detection hardware exists anywhere in this
codebase (all telematics data is synthetic/seeded -- see
app/seed/generator.py). The route "problems" written here are drawn at
random from a fixed pool of realistic-sounding warnings, so that a customer
asking "are there any problems with my route?" -- or a support agent asking
for today's overview -- has something to look at. See
docs/superpowers/specs/2026-09-30-demo-routes-and-escalation-design.md.

Run it manually, once (it is never run per question). Each run inserts a
fresh batch of `RoutePlan` rows dated *today* in Sydney-local time (via
app.timeutil) for a handful of existing customers -- by default the first
DEFAULT_CUSTOMER_COUNT by id, plus any passed with ``--customer-id`` (e.g.
whichever customer you are demoing as). For each customer it writes a mix
of `active` and `completed` routes, assigns a random driver from THAT
customer's own fleet (leaving a minority "Unassigned"), and attributes the
dispatch to the first `SupportAgent` (`created_by_role="support_agent"`).
Customers with no drivers are skipped and reported, not fatal. Re-running
adds another batch rather than erroring -- idempotent by convention, not by
constraint, since "today" may span several demo runs.

No network and no API key: origin/destination labels come from the fixed
Sydney place list below (approximate real landmark coordinates),
`geometry` is a plain 2-point GeoJSON LineString between them (enough for
the live map to draw a line and interpolate a position -- not a real routed
polyline), and distance/duration are fixed per pair, lightly jittered. This
is the second deliberate home of hardcoded real-world geography, alongside
app/seed/generator.py's DEMO_CORRIDORS; the spec above is what authorizes
it.

Every warning dict has exactly the shape `save_route_plan` stores for a
real `app.ai.route_planning.Warning` (`location`, `distance_from_origin_km`,
`type`, `severity`, `description`), so reports, chat, the Routes page and
the live map treat seeded warnings identically to real risk-pipeline
output, with no format branching anywhere else.
"""

from __future__ import annotations

import argparse
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.customer import Customer
from app.models.enums import RoutePlanStatus
from app.models.route_plan import RoutePlan
from app.models.support_agent import SupportAgent
from app.models.telematics import Driver
from app.timeutil import SITE_TZ, site_day_bounds


@dataclass(frozen=True)
class DemoPlace:
    label: str
    latitude: float
    longitude: float


@dataclass(frozen=True)
class DemoRoutePair:
    origin: DemoPlace
    destination: DemoPlace
    #: Typical driving distance/time for this pair -- fixed, not computed.
    distance_km: float
    duration_min: float


_SYDNEY_CBD = DemoPlace("Sydney CBD", -33.8688, 151.2093)
_PARRAMATTA = DemoPlace("Parramatta", -33.8150, 151.0011)
_BONDI_BEACH = DemoPlace("Bondi Beach", -33.8908, 151.2743)
_SYDNEY_AIRPORT = DemoPlace("Sydney Airport", -33.9399, 151.1753)
_CHATSWOOD = DemoPlace("Chatswood", -33.7969, 151.1803)
_MACQUARIE_PARK = DemoPlace("Macquarie Park", -33.7757, 151.1245)
_PORT_BOTANY = DemoPlace("Port Botany", -33.9725, 151.2173)
_LIVERPOOL = DemoPlace("Liverpool", -33.9200, 150.9238)
_NORTH_SYDNEY = DemoPlace("North Sydney", -33.8390, 151.2070)
_MANLY = DemoPlace("Manly", -33.7969, 151.2840)
_PENRITH = DemoPlace("Penrith", -33.7507, 150.6877)
_BLACKTOWN = DemoPlace("Blacktown", -33.7710, 150.9063)
_HORNSBY = DemoPlace("Hornsby", -33.7025, 151.0990)
_BANKSTOWN = DemoPlace("Bankstown", -33.9181, 151.0350)
_CRONULLA = DemoPlace("Cronulla", -34.0572, 151.1522)

DEMO_ROUTE_PAIRS: tuple[DemoRoutePair, ...] = (
    DemoRoutePair(_SYDNEY_CBD, _PARRAMATTA, 24.1, 35.0),
    DemoRoutePair(_BONDI_BEACH, _SYDNEY_AIRPORT, 13.5, 25.0),
    DemoRoutePair(_CHATSWOOD, _MACQUARIE_PARK, 8.2, 15.0),
    DemoRoutePair(_PORT_BOTANY, _LIVERPOOL, 31.0, 38.0),
    DemoRoutePair(_NORTH_SYDNEY, _MANLY, 12.4, 24.0),
    DemoRoutePair(_PARRAMATTA, _PENRITH, 30.5, 30.0),
    DemoRoutePair(_BLACKTOWN, _HORNSBY, 29.8, 36.0),
    DemoRoutePair(_BANKSTOWN, _CRONULLA, 22.6, 33.0),
    DemoRoutePair(_SYDNEY_CBD, _CHATSWOOD, 10.9, 20.0),
    DemoRoutePair(_SYDNEY_AIRPORT, _BANKSTOWN, 17.3, 26.0),
)

#: `(type, severity, description)` -- descriptions phrased exactly the way
#: `evaluate_weather_warnings`/`evaluate_risk_zone_warnings` phrase real ones.
DEMO_WARNING_POOL: tuple[tuple[str, str, str], ...] = (
    ("weather", "high", "Heavy rain forecast near this segment (85% probability)."),
    ("weather", "moderate", "Heavy rain forecast near this segment (65% probability)."),
    ("weather", "moderate", "Strong winds forecast near this segment (48 km/h)."),
    ("weather", "moderate", "Low visibility forecast near this segment (700m)."),
    (
        "risk_zone",
        "high",
        "16 driving events recorded within 500m of this point (7 harsh braking).",
    ),
    (
        "risk_zone",
        "high",
        "14 driving events recorded within 500m of this point (8 speeding).",
    ),
    (
        "risk_zone",
        "moderate",
        "11 driving events recorded within 500m of this point (5 speeding).",
    ),
    (
        "risk_zone",
        "moderate",
        "12 driving events recorded within 500m of this point (6 route deviation).",
    ),
)

DEFAULT_CUSTOMER_COUNT = 3
MIN_ROUTES_PER_CUSTOMER = 3
MAX_ROUTES_PER_CUSTOMER = 5
MAX_WARNINGS_PER_ROUTE = 3
UNASSIGNED_DRIVER_PROBABILITY = 0.2
COMPLETED_PROBABILITY = 0.35


class DemoSeedError(RuntimeError):
    """The database isn't in a state this script can seed into."""


@dataclass
class DemoRouteSummary:
    #: customer_id -> {"active": n, "completed": n}
    created: dict[int, dict[str, int]] = field(default_factory=dict)
    #: customer_id -> why it was skipped
    skipped: dict[int, str] = field(default_factory=dict)

    @property
    def total_created(self) -> int:
        return sum(counts["active"] + counts["completed"] for counts in self.created.values())


def _choose_customers(
    db: Session, customer_ids: list[int] | None, customer_count: int
) -> tuple[list[Customer], dict[int, str]]:
    """Explicitly requested customers first (unknown ids are skipped, not
    fatal), then the first `customer_count` customers by id, de-duplicated."""
    chosen: list[Customer] = []
    skipped: dict[int, str] = {}
    seen: set[int] = set()

    for customer_id in customer_ids or []:
        if customer_id in seen:
            continue
        seen.add(customer_id)
        customer = db.get(Customer, customer_id)
        if customer is None:
            skipped[customer_id] = "no such customer"
            continue
        chosen.append(customer)

    for customer in db.query(Customer).order_by(Customer.customer_id).limit(customer_count).all():
        if customer.customer_id in seen:
            continue
        seen.add(customer.customer_id)
        chosen.append(customer)

    return chosen, skipped


def _point_along(pair: DemoRoutePair, fraction: float) -> tuple[float, float]:
    """(latitude, longitude) `fraction` of the way along the straight line
    between the pair's endpoints -- the same 2-point line stored as the
    row's geometry, so a warning marker always sits on the drawn route."""
    latitude = pair.origin.latitude + (pair.destination.latitude - pair.origin.latitude) * fraction
    longitude = pair.origin.longitude + (pair.destination.longitude - pair.origin.longitude) * fraction
    return latitude, longitude


def _demo_warnings(rng: random.Random, pair: DemoRoutePair, distance_km: float) -> list[dict]:
    count = rng.randint(0, MAX_WARNINGS_PER_ROUTE)
    warnings: list[dict] = []
    for warning_type, severity, description in rng.sample(DEMO_WARNING_POOL, count):
        fraction = rng.uniform(0.1, 0.9)
        latitude, longitude = _point_along(pair, fraction)
        warnings.append(
            {
                "location": {"lat": round(latitude, 6), "lon": round(longitude, 6)},
                "distance_from_origin_km": round(distance_km * fraction, 2),
                "type": warning_type,
                "severity": severity,
                "description": description,
            }
        )
    warnings.sort(key=lambda warning: warning["distance_from_origin_km"])
    return warnings


def _statuses_for_one_customer(rng: random.Random) -> list[RoutePlanStatus]:
    """At least one active and one completed route per customer (so every
    demo customer shows a real mix), then a random mix for the rest."""
    count = rng.randint(MIN_ROUTES_PER_CUSTOMER, MAX_ROUTES_PER_CUSTOMER)
    statuses = [RoutePlanStatus.ACTIVE, RoutePlanStatus.COMPLETED]
    statuses += [
        RoutePlanStatus.COMPLETED if rng.random() < COMPLETED_PROBABILITY else RoutePlanStatus.ACTIVE
        for _ in range(count - len(statuses))
    ]
    return statuses


def _demo_route_plan(
    rng: random.Random,
    *,
    customer: Customer,
    drivers: list[Driver],
    agent: SupportAgent,
    status: RoutePlanStatus,
    now: datetime,
    day_start: datetime,
) -> RoutePlan:
    pair = rng.choice(DEMO_ROUTE_PAIRS)
    distance_km = round(pair.distance_km * rng.uniform(0.95, 1.1), 2)
    duration_min = round(pair.duration_min * rng.uniform(0.9, 1.3), 2)

    # Every timestamp is clamped into [day_start, now]: always "today" in
    # Sydney, never in the future.
    if status == RoutePlanStatus.COMPLETED:
        created_at = max(day_start, now - timedelta(minutes=duration_min + rng.uniform(10, 180)))
        completed_at = min(now, created_at + timedelta(minutes=duration_min))
    else:
        # Somewhere between just-departed and 90% of the way there, so the
        # live map shows vehicles genuinely mid-route.
        created_at = max(day_start, now - timedelta(minutes=rng.uniform(0, duration_min * 0.9)))
        completed_at = None

    driver_id = (
        None if rng.random() < UNASSIGNED_DRIVER_PROBABILITY else rng.choice(drivers).driver_id
    )

    return RoutePlan(
        customer_id=customer.customer_id,
        created_by_role="support_agent",
        created_by_id=agent.support_agent_id,
        driver_id=driver_id,
        origin_label=pair.origin.label,
        destination_label=pair.destination.label,
        distance_km=distance_km,
        duration_min=duration_min,
        geometry={
            "type": "LineString",
            # GeoJSON order is [longitude, latitude].
            "coordinates": [
                [pair.origin.longitude, pair.origin.latitude],
                [pair.destination.longitude, pair.destination.latitude],
            ],
        },
        warnings=_demo_warnings(rng, pair, distance_km),
        unavailable=False,
        unavailable_reason=None,
        status=status,
        created_at=created_at,
        completed_at=completed_at,
    )


def seed_demo_routes(
    db: Session,
    *,
    rng: random.Random,
    customer_ids: list[int] | None = None,
    customer_count: int = DEFAULT_CUSTOMER_COUNT,
    now: datetime | None = None,
) -> DemoRouteSummary:
    """Insert one batch of synthetic today-dated `RoutePlan` rows (see
    module docstring). Flushes, never commits -- `run()` owns the commit.

    Raises DemoSeedError when there is no `SupportAgent` row to attribute
    the simulated dispatch to (`RoutePlan.created_by_id` is NOT NULL)."""
    now = now if now is not None else datetime.now(timezone.utc)
    day_start, _ = site_day_bounds(now.astimezone(SITE_TZ).date())

    agent = db.query(SupportAgent).order_by(SupportAgent.support_agent_id).first()
    if agent is None:
        raise DemoSeedError(
            "No SupportAgent row exists to attribute the demo dispatch to -- "
            "run `python -m app.seed.seed` first."
        )

    customers, skipped = _choose_customers(db, customer_ids, customer_count)
    summary = DemoRouteSummary(skipped=skipped)

    for customer in customers:
        drivers = (
            db.query(Driver)
            .filter(Driver.customer_id == customer.customer_id)
            .order_by(Driver.driver_id)
            .all()
        )
        if not drivers:
            summary.skipped[customer.customer_id] = "no drivers in this customer's fleet"
            continue

        counts = {"active": 0, "completed": 0}
        for status in _statuses_for_one_customer(rng):
            db.add(
                _demo_route_plan(
                    rng,
                    customer=customer,
                    drivers=drivers,
                    agent=agent,
                    status=status,
                    now=now,
                    day_start=day_start,
                )
            )
            counts[status.value] += 1
        summary.created[customer.customer_id] = counts

    db.flush()
    return summary


def _print_summary(summary: DemoRouteSummary) -> None:
    print("Demo routes created (SYNTHETIC demo data -- not real incident detection):")
    if not summary.created:
        print("  (none)")
    for customer_id, counts in sorted(summary.created.items()):
        print(
            f"  customer {customer_id}: "
            f"{counts['active']} active, {counts['completed']} completed"
        )
    print(f"  total: {summary.total_created}")
    if summary.skipped:
        print("Skipped customers:")
        for customer_id, reason in sorted(summary.skipped.items()):
            print(f"  customer {customer_id}: {reason}")


def run(
    *,
    customer_ids: list[int] | None = None,
    customer_count: int = DEFAULT_CUSTOMER_COUNT,
    seed: int | None = None,
) -> DemoRouteSummary:
    """Seed one batch into the configured database, commit, print a summary.
    Same session/commit/rollback shape as `app.seed.seed.run`."""
    session = SessionLocal()
    try:
        summary = seed_demo_routes(
            session,
            rng=random.Random(seed),
            customer_ids=customer_ids,
            customer_count=customer_count,
        )
        session.commit()
        _print_summary(summary)
        return summary
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m app.seed.seed_demo_routes",
        description=(
            "Insert a batch of SYNTHETIC today-dated demo routes (with random "
            "warnings and drivers) for testing the chat/report surfaces."
        ),
    )
    parser.add_argument(
        "--customer-id",
        dest="customer_ids",
        type=int,
        action="append",
        default=None,
        help="Always include this customer (repeatable) -- e.g. the one you are demoing as.",
    )
    parser.add_argument(
        "--customers",
        dest="customer_count",
        type=int,
        default=DEFAULT_CUSTOMER_COUNT,
        help=f"Also include the first N customers by id (default {DEFAULT_CUSTOMER_COUNT}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed, for a reproducible batch (default: random).",
    )
    args = parser.parse_args(argv)

    try:
        run(customer_ids=args.customer_ids, customer_count=args.customer_count, seed=args.seed)
    except DemoSeedError as exc:
        print(f"Demo route seeding aborted: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
