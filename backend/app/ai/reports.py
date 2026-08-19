"""Proactive reporting (Task 16): `generate_start_of_day_report` and
`generate_end_of_day_report`.

Each function pulls one customer's *whole fleet* data for the relevant
window via `TelematicsDataSource` (Task 10, extended in
`app/datasources/base.py`/`synthetic.py` for this task with `list_drivers`,
`list_vehicles`, `list_devices`, `list_trips_for_customer`), formats it into
a plain-text context block, and asks the LLM (via `app.ai.llm.chat_completion`
-- the plan's single LLM-call module, same one Task 13's `chat_service.py`
uses) to turn that structured data into a natural-language operational
summary.

Why this module does NOT reuse Task 13's `answer_query` (a deliberate,
documented choice, per the task brief's "your call" note): `answer_query`
is shaped around *answering one user question* from a `RetrievedContext` --
at most one driver/vehicle/trip plus a handful of KB articles -- and its
prompt contract requires the model to reply with Task 13's exact
`FALLBACK_TEXT` verbatim whenever the context can't support an answer, a
signal Task 14's escalation logic then acts on. Neither half of that fits a
daily report:
  * A report isn't answering a question at all -- there's no `query`
    string, and its "context" is fleet-wide (every driver/vehicle/device/
    trip for a customer, not one resolved entity), so wedging it into
    `RetrievedContext`'s `driver: Driver | None` / single `trip: Trip |
    None` fields would mean picking one arbitrary trip to represent "all of
    today's trips", losing the rest.
  * A report is always delivered -- per the task brief, "reports are
    operational summaries always delivered, not confidence-gated Q&A."
    There is no fallback/escalation path here (Task 14's escalation logic
    is explicitly out of scope for this task), so `answer_query`'s
    fallback-text contract and heuristic `confidence` score would be
    meaningless noise on every report response.
This module therefore builds its own report-specific prompt
(`_build_report_messages`) and calls `chat_completion` directly -- still
funneling through the one sanctioned LLM-call module, just with a prompt
shaped for summarization instead of grounded Q&A.

Data-field interpretations (documented per the task brief's instruction to
use "your best reasonable interpretation" where the schema has no explicit
concept for a report section, without inventing new DB entities):
  * "Vehicle health": `Vehicle` itself (`app/models/telematics.py`) has no
    health/maintenance fields at all (just make/model/year). `Device`
    (`app/models/device.py`) is the one entity with health-like signals --
    `battery_status`, `signal_strength`, `device_status`, `last_seen` -- and
    every vehicle in this schema is tracked via an installed telematics
    device, so device health is used as the vehicle-health proxy.
  * "Unresolved incidents": `DrivingEvent` has no resolved/acknowledged
    flag, and `Trip` has no "incident" concept beyond its recorded events.
    A trip whose `end_time` is still `NULL` is, by construction, still in
    progress -- nothing has "closed the loop" on whatever happened during
    it yet -- so this module treats driving events belonging to
    still-open trips (`end_time IS NULL`) as the unresolved set, and events
    on completed trips as already resolved/recorded history.
  * "Planned routes": there is no separate scheduled-vs-actual trip
    concept in this schema (see `app/models/telematics.py`'s module
    docstring -- `Trip` only has `start_time`/`end_time`). This module
    treats every `Trip` whose `start_time` falls on today's UTC calendar
    date as today's "planned routes" for the start-of-day briefing.
  * "Risk alerts" (start-of-day): driving events from the last 24 hours
    (not just "today", since a morning briefing needs to surface overnight
    activity too) -- a rolling lookback window, computed in real SQL via
    `list_trips_for_customer(since=...)` plus the existing tenant-scoped
    `get_driving_events` per trip.
  * "Fuel" (end-of-day): the schema has no fuel-consumption field anywhere
    (confirmed against `app/models/telematics.py`, `app/models/device.py`;
    the only "fuel" mentions in the codebase are a `"fuel-sensor"` device
    *type* string and a knowledge-base article's prose about idling wasting
    fuel -- see `app/seed/generator.py`). This module uses `IDLING` driving
    events as the fuel proxy, labelled explicitly as a proxy in both the
    formatted context and the prompt, rather than inventing a fuel-reading
    column that doesn't exist.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.ai.llm import chat_completion
from app.datasources.base import TelematicsDataSource
from app.datasources.synthetic import SyntheticDataSource
from app.models.device import Device
from app.models.enums import DrivingEventType
from app.models.telematics import Driver, DrivingEvent, Trip, Vehicle

_REPORT_SYSTEM_PROMPT = (
    "You are a fleet operations assistant for a telematics platform, writing "
    "a proactive summary report for a fleet manager. You are given "
    "structured operational data below -- use ONLY that data, never outside "
    "knowledge or invented figures. Write a concise, clearly organized "
    "natural-language summary (short sections with headings are fine) "
    "covering every section of data provided. If a section's data is empty, "
    "say so plainly (e.g. \"No risk alerts in the last 24 hours\") instead "
    "of omitting the section. This is an operational report, not an answer "
    "to a question -- always produce a summary, never refuse."
)


# ---------------------------------------------------------------------------
# Time-window helpers
# ---------------------------------------------------------------------------


def _today_bounds(now: datetime) -> tuple[datetime, datetime]:
    """Return `(start_of_today, start_of_tomorrow)` in UTC, both midnight,
    for `now`'s calendar date."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start, today_start + timedelta(days=1)


def _lookback_bounds(now: datetime, hours: int = 24) -> tuple[datetime, datetime]:
    """Return `(now - hours, now)` -- a rolling lookback window."""
    return now - timedelta(hours=hours), now


# ---------------------------------------------------------------------------
# Data-gathering helpers
# ---------------------------------------------------------------------------


def _events_for_trips(
    ds: TelematicsDataSource, trips: list[Trip], customer_id: int
) -> dict[int, list[DrivingEvent]]:
    """`{trip_id: [DrivingEvent, ...]}` for every trip in `trips`, via the
    existing tenant-scoped `get_driving_events` (one real SQL query per
    trip -- reuses Task 10's method rather than adding a new join)."""
    return {trip.trip_id: ds.get_driving_events(trip.trip_id, customer_id=customer_id) for trip in trips}


def _flatten(events_by_trip: dict[int, list[DrivingEvent]]) -> list[DrivingEvent]:
    flattened: list[DrivingEvent] = []
    for events in events_by_trip.values():
        flattened.extend(events)
    return flattened


def _count_by_type(events: list[DrivingEvent]) -> dict[DrivingEventType, int]:
    counts: dict[DrivingEventType, int] = {event_type: 0 for event_type in DrivingEventType}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Formatting helpers -- turn raw rows into plain-text context blocks
# ---------------------------------------------------------------------------


def _format_fleet_overview(drivers: list[Driver], vehicles: list[Vehicle], devices: list[Device]) -> str:
    return f"{len(drivers)} driver(s), {len(vehicles)} vehicle(s), {len(devices)} telematics device(s)."


def _format_vehicle_health(devices: list[Device]) -> str:
    """Device health, used as the vehicle-health proxy (see module
    docstring). Flags anything not in a fully-healthy state; a healthy
    fleet gets an explicit "no issues" line rather than an empty section."""
    if not devices:
        return "(no devices registered)"
    flagged = [
        d
        for d in devices
        if d.battery_status.value != "ok" or d.device_status.value not in ("active",)
    ]
    if not flagged:
        return f"All {len(devices)} device(s) reporting healthy (battery OK, status active)."
    lines = [f"{len(flagged)} of {len(devices)} device(s) need attention:"]
    for d in flagged:
        last_seen = d.last_seen.isoformat() if d.last_seen else "never"
        lines.append(
            f"  - Device {d.device_id} (serial {d.serial_number}): "
            f"battery={d.battery_status.value}, status={d.device_status.value}, "
            f"last seen {last_seen}"
        )
    return "\n".join(lines)


def _format_driving_events(events: list[DrivingEvent], *, empty_label: str) -> str:
    if not events:
        return empty_label
    lines = []
    for event in sorted(events, key=lambda e: e.event_time):
        location = f" at {event.location}" if event.location else ""
        details = f" -- {event.details}" if event.details else ""
        lines.append(f"  - [trip {event.trip_id}] {event.event_type.value} at {event.event_time}{location}{details}")
    return "\n".join(lines)


def _format_planned_routes(trips: list[Trip], drivers_by_id: dict[int, Driver], vehicles_by_id: dict[int, Vehicle]) -> str:
    if not trips:
        return "(no trips scheduled/started today)"
    lines = []
    for trip in sorted(trips, key=lambda t: t.start_time):
        driver = drivers_by_id.get(trip.driver_id)
        vehicle = vehicles_by_id.get(trip.vehicle_id)
        driver_label = driver.full_name if driver else f"driver {trip.driver_id}"
        vehicle_label = (
            f"{vehicle.make} {vehicle.model} ({vehicle.registration_number})"
            if vehicle
            else f"vehicle {trip.vehicle_id}"
        )
        status = "in progress" if trip.end_time is None else "completed"
        route = f"{trip.start_location or 'unknown'} -> {trip.end_location or 'unknown'}"
        lines.append(
            f"  - Trip {trip.trip_id}: {driver_label} in {vehicle_label}, {route}, "
            f"starts {trip.start_time} ({status})"
        )
    return "\n".join(lines)


def _format_event_type_breakdown(counts: dict[DrivingEventType, int]) -> str:
    return (
        f"Speeding: {counts.get(DrivingEventType.SPEEDING, 0)}\n"
        f"Harsh braking: {counts.get(DrivingEventType.HARSH_BRAKING, 0)}\n"
        f"Route deviations: {counts.get(DrivingEventType.ROUTE_DEVIATION, 0)}\n"
        f"Fuel (idling-time proxy -- excessive idling wastes fuel; the schema has no direct "
        f"fuel-consumption reading, see module docstring): {counts.get(DrivingEventType.IDLING, 0)} idling event(s)"
    )


def _format_driver_performance(
    trips: list[Trip],
    events_by_trip: dict[int, list[DrivingEvent]],
    drivers_by_id: dict[int, Driver],
) -> str:
    if not trips:
        return "(no trips today -- no driver performance to report)"

    per_driver: dict[int, dict] = {}
    for trip in trips:
        bucket = per_driver.setdefault(
            trip.driver_id, {"trips": 0, "distance_km": 0.0, "event_counts": {}}
        )
        bucket["trips"] += 1
        if trip.distance_km is not None:
            bucket["distance_km"] += float(trip.distance_km)
        for event in events_by_trip.get(trip.trip_id, []):
            bucket["event_counts"][event.event_type] = bucket["event_counts"].get(event.event_type, 0) + 1

    lines = []
    for driver_id, stats in sorted(per_driver.items()):
        driver = drivers_by_id.get(driver_id)
        driver_label = driver.full_name if driver else f"driver {driver_id}"
        total_events = sum(stats["event_counts"].values())
        lines.append(
            f"  - {driver_label}: {stats['trips']} trip(s), "
            f"{stats['distance_km']:.1f} km total, {total_events} flagged event(s)"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt building + LLM call
# ---------------------------------------------------------------------------


def _build_report_messages(report_title: str, context_block: str) -> list[dict[str, str]]:
    user_content = f"Report: {report_title}\n\nData:\n{context_block}"
    return [
        {"role": "system", "content": _REPORT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _generate_report(report_title: str, context_block: str) -> str:
    messages = _build_report_messages(report_title, context_block)
    return chat_completion(messages)


# ---------------------------------------------------------------------------
# Public report generators
# ---------------------------------------------------------------------------


def generate_start_of_day_report(
    customer_id: int,
    *,
    db: Session,
    data_source: TelematicsDataSource | None = None,
    now: datetime | None = None,
) -> str:
    """Generate a start-of-day report for `customer_id`: risk alerts (last
    24h driving events), vehicle health (device status/battery, see module
    docstring), unresolved incidents (events on still-in-progress trips),
    and today's planned routes (trips starting today).

    `db`/`data_source` follow `app.ai.retrieval.retrieve_context`'s own
    pattern -- `data_source` defaults to `SyntheticDataSource(db)` when not
    given. `now` is injectable for deterministic tests; defaults to the
    real current UTC time.
    """
    ds = data_source if data_source is not None else SyntheticDataSource(db)
    now = now if now is not None else datetime.now(timezone.utc)

    today_start, tomorrow_start = _today_bounds(now)
    lookback_start, _ = _lookback_bounds(now)

    drivers = ds.list_drivers(customer_id)
    vehicles = ds.list_vehicles(customer_id)
    devices = ds.list_devices(customer_id)
    drivers_by_id = {d.driver_id: d for d in drivers}
    vehicles_by_id = {v.vehicle_id: v for v in vehicles}

    planned_trips = ds.list_trips_for_customer(customer_id, since=today_start, until=tomorrow_start)
    recent_trips = ds.list_trips_for_customer(customer_id, since=lookback_start)
    recent_events_by_trip = _events_for_trips(ds, recent_trips, customer_id)

    risk_alert_events = _flatten(recent_events_by_trip)
    unresolved_trips = [t for t in recent_trips if t.end_time is None]
    unresolved_events = _flatten({t.trip_id: recent_events_by_trip.get(t.trip_id, []) for t in unresolved_trips})

    context_block = (
        f"Fleet overview: {_format_fleet_overview(drivers, vehicles, devices)}\n\n"
        f"Vehicle health:\n{_format_vehicle_health(devices)}\n\n"
        f"Risk alerts (driving events in the last 24 hours):\n"
        f"{_format_driving_events(risk_alert_events, empty_label='(no driving events in the last 24 hours)')}\n\n"
        f"Unresolved incidents (driving events on trips still in progress):\n"
        f"{_format_driving_events(unresolved_events, empty_label='(no trips currently in progress)')}\n\n"
        f"Planned routes for today:\n{_format_planned_routes(planned_trips, drivers_by_id, vehicles_by_id)}"
    )

    return _generate_report(f"Start-of-day fleet report for customer {customer_id}", context_block)


def generate_end_of_day_report(
    customer_id: int,
    *,
    db: Session,
    data_source: TelematicsDataSource | None = None,
    now: datetime | None = None,
) -> str:
    """Generate an end-of-day report for `customer_id`: a speeding /
    harsh-braking / route-deviation / fuel(idling-proxy) summary plus a
    per-driver performance breakdown, all scoped to today's trips.

    `db`/`data_source`/`now` follow `generate_start_of_day_report`'s pattern.
    """
    ds = data_source if data_source is not None else SyntheticDataSource(db)
    now = now if now is not None else datetime.now(timezone.utc)

    today_start, tomorrow_start = _today_bounds(now)

    drivers = ds.list_drivers(customer_id)
    drivers_by_id = {d.driver_id: d for d in drivers}

    today_trips = ds.list_trips_for_customer(customer_id, since=today_start, until=tomorrow_start)
    events_by_trip = _events_for_trips(ds, today_trips, customer_id)
    all_events = _flatten(events_by_trip)
    counts = _count_by_type(all_events)

    context_block = (
        f"Trips today: {len(today_trips)}\n\n"
        f"Event summary (speeding / harsh braking / route deviations / fuel):\n"
        f"{_format_event_type_breakdown(counts)}\n\n"
        f"Driver performance summary:\n"
        f"{_format_driver_performance(today_trips, events_by_trip, drivers_by_id)}"
    )

    return _generate_report(f"End-of-day fleet report for customer {customer_id}", context_block)
