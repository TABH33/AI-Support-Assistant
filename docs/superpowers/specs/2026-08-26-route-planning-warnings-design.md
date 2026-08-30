# Route Planning + Route Warnings — Design

**Status**: Approved, ready for implementation planning.
**Date**: 2026-08-26

## Goal

Add a route-planning feature to the AI-Driven Telematics Support Assistant:
given an origin/destination, compute a route, flag likely weather hazards
and historical driving-event "risk zones" along it, and surface both the
raw structured data and an LLM-generated natural-language summary — through
the existing API-centered backend and RAG-grounded chat, not as a bolted-on
map widget.

## Non-goals

- Turn-by-turn navigation, live GPS tracking, or route re-optimization.
- Real driver/customer PII in risk-zone data — risk zones are anonymized,
  aggregate, cross-fleet signals (see "Risk-zone scoping" below).
- Guaranteeing a weather warning fires on demand — weather is live, variable
  data; only the risk-zone signal is deterministic for demo purposes.
- A general-purpose geocoding feature — geocoding exists only to resolve the
  origin/destination this feature itself needs.

## External services (Step 1)

- **OpenRouteService** (directions + geocoding). Free-tier API key, already
  obtained by the user. Stored as `ORS_API_KEY` in `.env` — **never**
  hardcoded, never committed. Added to `.env.example` / `backend/.env.example`
  as a placeholder with a comment on where to get a real one.
- **Open-Meteo** (weather). No API key.

## Data model change

`DrivingEvent` currently has no coordinates — `location` is free text (e.g.
"Main Street"), picked from a fixed name list by the seed generator. Risk-zone
lookups need real coordinates.

**Migration**: add nullable `latitude: float`, `longitude: float` to
`driving_events` (new Alembic revision, chained after the current head
`ca294b499e12_add_ces_score_to_chat_sessions`). Nullable because historical
rows (and any future event source) may not always carry coordinates.

**Seed generator update** (`app/seed/generator.py`): define 2–3 hardcoded
Sydney corridor anchor pairs (see "Demo routes" below) as
`(lat, lon)` start/end points. When generating a `DrivingEvent`, in addition
to the existing random `ROAD_NAMES` pick for `location`, assign
`latitude`/`longitude` by linearly interpolating a random point along a
randomly-chosen corridor and jittering it by a small random offset (tens of
meters), so events cluster near — but not exactly on — the corridor line.
This keeps the data synthetic and anonymized (same rule as the rest of the
POC) while guaranteeing the demo routes actually pass near seeded risk
zones.

## Backend architecture

### `app/integrations/` (new package)

One thin HTTP client per external service, following the existing
`app/ai/embeddings.py` / `app/ai/llm.py` pattern (dedicated client module,
own error hierarchy, nothing above it touches `httpx` directly):

- **`openrouteservice.py`**
  - `geocode(place_name: str) -> Coordinates` — ORS's Pelias-based
    geocoding endpoint, same API key.
  - `get_directions(origin: Coordinates, destination: Coordinates, waypoints: list[Coordinates] | None = None) -> RouteResult` —
    `RouteResult(geometry: GeoJSON LineString, distance_km: float, duration_min: float)`.
  - Error hierarchy: `RouteServiceError` (base), `RouteServiceRequestError`
    (HTTP/network failure), `RouteServiceResponseError` (malformed
    response), `GeocodingError` (place name didn't resolve) — mirrors
    `app/ai/embeddings.py`'s `EmbeddingError`/`EmbeddingRequestError`/
    `EmbeddingResponseError` naming.

- **`open_meteo.py`**
  - `get_forecast(lat: float, lon: float) -> ForecastResult` —
    `ForecastResult(precipitation_probability: float, wind_speed_kmh: float, visibility_m: float)`.
  - Error hierarchy: `WeatherServiceError`, `WeatherServiceRequestError`,
    `WeatherServiceResponseError`.

Both clients read their base URL (and, for ORS, the key) from
`app.config.settings`, never hardcoded.

### `app/ai/route_planning.py` (new module — orchestration)

Mirrors `app/ai/reports.py`'s shape: plain, testable functions, no FastAPI
imports.

- `sample_route_points(geometry, target_count=8) -> list[SamplePoint]` —
  `SamplePoint(lat, lon, distance_from_origin_km)`, evenly spaced along the
  GeoJSON LineString.
- `evaluate_weather_warnings(points) -> list[Warning]` — calls
  `open_meteo.get_forecast` per point; flags a point when precipitation
  probability > 60%, wind > 40 km/h, or visibility below a defined
  threshold; attaches a plain-language `description`
  ("heavy rain forecast near this segment").
- `evaluate_risk_zone_warnings(points, *, db, data_source=None) -> list[Warning]` —
  for each point, calls the new `TelematicsDataSource.get_driving_events_near`
  method (radius 500m–1km); flags a point when the event count exceeds a
  threshold (e.g. ≥3 events), with `description` naming the dominant event
  type and count ("3 harsh-braking events recorded near this point").
- `build_route_plan(origin, destination, waypoints=None, *, db, data_source=None) -> RoutePlanResult` —
  the top-level entry point: geocodes string inputs, calls
  `get_directions`, samples points, runs both warning evaluators, returns
  `RoutePlanResult(distance_km, duration_min, geometry, warnings, unavailable=False)`.
  On any `RouteServiceError`/`GeocodingError`, catches it and returns
  `RoutePlanResult(unavailable=True, ...)` with the other fields `None`/
  empty — **never lets the exception propagate**, per Step 2/10's "don't
  crash the chatbot" requirement.
- `build_route_summary_prompt(route_plan, origin_label, destination_label) -> list[dict]` —
  builds the system/user messages for the LLM summary, in the same
  "answer only from the given structured data, never invent numbers" style
  as `app/ai/chat_service.py`'s `build_prompt_messages`. Only called when
  `route_plan.unavailable is False`.
- `ROUTE_DATA_UNAVAILABLE_TEXT` — a dedicated constant (**not** a reuse of
  `chat_service.FALLBACK_TEXT`), returned verbatim by both `POST /route-plan`
  callers when `unavailable=True`. This is a deliberate distinction from the
  existing RAG fallback: `FALLBACK_TEXT` means "the AI doesn't have enough
  context to answer" and auto-creates a `SupportTicket`; a route-service
  outage is an infrastructure failure, not a knowledge gap, so it gets its
  own message and **does not** auto-escalate.

### `TelematicsDataSource` extension (`app/datasources/base.py` + `synthetic.py`)

New method:

```python
def get_driving_events_near(
    self, latitude: float, longitude: float, radius_km: float
) -> list[DrivingEvent]:
    """Return DrivingEvents within radius_km of (latitude, longitude),
    across the whole synthetic fleet -- NOT customer-scoped. A risk zone is
    a property of a road segment, not of any one customer's fleet; results
    carry only event type/location/time, never driver identity, matching
    the POC's existing anonymization rule (no PII surfaced by this method)."""
    ...
```

`SyntheticDataSource`'s implementation: bounding-box prefilter in SQL
(cheap index-friendly range on `latitude`/`longitude`), then a Python-side
haversine-distance filter for the precise radius — no PostGIS dependency,
consistent with the "no new architectural layer" constraint already applied
elsewhere in this POC (e.g. the at-rest encryption design).

**Note on the Protocol's existing pattern**: every other method on
`TelematicsDataSource` takes a required `customer_id: int | None` for
defense-in-depth tenant scoping. `get_driving_events_near` deliberately
does not — it has no tenant to scope to (see design decision above). This
is documented inline in `base.py` right next to the method, referencing
the module's existing "why customer_id is required elsewhere" reasoning so
a future reader doesn't mistake the omission for an oversight.

### `POST /route-plan` (`app/api/telematics.py` or new `app/api/route_plan.py`)

New file `app/api/route_plan.py`, registered in `main.py` — keeps `chat.py`/
`telematics.py` focused, matches the one-router-per-concern layout.

`require_role("customer", "support_agent")` (same as every other route).
No customer scoping needed for the endpoint's own logic (route/weather/
risk-zone data isn't customer-owned), but the RBAC gate still applies for
basic authenticated access.

**Request**:
```json
{
  "origin": "Sydney CBD",
  "destination": "Parramatta",
  "waypoints": null
}
```
`origin`/`destination` accept either a place-name string (geocoded
server-side via ORS) or `{"lat": ..., "lon": ...}` — this lets both the
frontend (which may have exact coordinates from a map click) and the
chatbot (which only has free-text place names) use the same endpoint
without a separate client-side geocoding step.

**Response** `200` (always 200 — failures are reported in-band, never a
5xx, per Step 2/10):
```json
{
  "distance_km": 23.4,
  "duration_min": 38.2,
  "geometry": { "type": "LineString", "coordinates": [[151.2, -33.87], ...] },
  "warnings": [
    {
      "location": { "lat": -33.80, "lon": 151.00 },
      "distance_from_origin_km": 12.1,
      "type": "weather",
      "severity": "moderate",
      "description": "Heavy rain forecast near this segment (72% probability)."
    },
    {
      "location": { "lat": -33.81, "lon": 151.02 },
      "distance_from_origin_km": 13.5,
      "type": "risk_zone",
      "severity": "high",
      "description": "4 harsh-braking events recorded within 800m of this point."
    }
  ],
  "unavailable": false
}
```
On failure: `{"distance_km": null, "duration_min": null, "geometry": null, "warnings": [], "unavailable": true}`.

Every call (success or not) writes an `AuditLog` row —
`ACTION_ROUTE_PLAN_GENERATED = "route_plan_generated"`, new constant in
`app/security/audit.py`, same pattern as `ACTION_CHAT_ANSWER`/
`ACTION_REPORT_GENERATED` — noting origin/destination and whether it
succeeded.

## Chat integration (Step 7)

In `app/api/chat.py`, alongside the existing `_detect_report_intent`:

- `_detect_route_plan_intent(query: str) -> RoutePlanIntent | None` —
  regex-based extraction (not full NLP, matching the existing
  substring/keyword style) for patterns like `"plan a trip from X to Y"`,
  `"route from X to Y"`, `"warnings on the route to Z"`,
  `"directions to Z"`. If only a destination is found (no "from"), the
  intent is still detected but `origin` is `None`.
- In `post_chat`, checked **before** `_detect_report_intent` runs (route
  requests and report requests use disjoint keyword sets, so ordering is
  arbitrary in practice, but route intent is checked first since it's the
  more specific match).
- If `origin is None`: skip route-plan/RAG entirely and return a
  clarifying question ("Which starting point should I plan this route
  from?") as the assistant's message — `confidence=1.0`, `escalated=False`
  (this is a deterministic conversational branch, not a knowledge-gap
  escalation).
- If both resolved: call `route_planning.build_route_plan(...)`. If
  `unavailable`, the assistant's message is `ROUTE_DATA_UNAVAILABLE_TEXT`
  (no escalation ticket — see "infra failure vs. knowledge gap" distinction
  above). If available, build the LLM summary prompt
  (`build_route_summary_prompt`), call `app.ai.llm.chat_completion` (the
  same single LLM-call module every other AI path already uses), and set
  the assistant's message to that summary text.
- `ChatResponse` gains a new optional field: `route_plan: RoutePlanResult | None = None`,
  populated only on a route-plan-intent turn with a successful result —
  lets the frontend render the map from the same response, no second
  round-trip. `ChatMessage.content` (persisted) stores just the LLM's
  natural-language summary text, same as every other assistant turn; the
  structured `route_plan` payload is not persisted (transient, regenerable
  from the same inputs) — this matches how report text itself is persisted
  today (`ChatMessage.content`) without its `ReportResponse`-shaped
  metadata.
- Audited as `ACTION_ROUTE_PLAN_GENERATED` (reusing the same constant the
  direct endpoint uses), same one-commit-per-request discipline as the
  rest of `post_chat`.

## Frontend (Step 8)

- New npm deps: `leaflet`, `react-leaflet`, `@types/leaflet` (dev).
- New component `frontend/src/components/RouteMap.tsx`: renders the
  GeoJSON LineString as a Leaflet `Polyline` over OpenStreetMap tiles, plus
  a `CircleMarker` per warning — color-coded (`weather` = blue,
  `risk_zone` = red/orange) — each with a `Popup` showing `description`
  and `severity`.
- `ChatWidget.tsx`: when an assistant message's `route_plan` field is
  present (and `unavailable` is false), render `RouteMap` inline as part
  of that message — the widget's container grows from `w-80` to a wider
  two-column layout (chat transcript + map side by side) for the duration
  the map is shown, rather than opening a separate page/window. This keeps
  Step 8's "don't create a separate disconnected page" requirement while
  keeping the narrow default widget width for ordinary chat.
- `frontend/src/types/chat.ts`: extend `ChatResponse`'s TS type with the
  new optional `route_plan` field, mirroring the backend Pydantic model
  (same "copied from the backend, not guessed" discipline the file's
  existing header comment describes).

## Error handling & escalation consistency (Step 10)

Two distinct failure modes, handled differently and both graceful:

1. **Route service unavailable** (ORS/geocoding/Open-Meteo down or
   erroring): `RoutePlanResult.unavailable=True` →
   `ROUTE_DATA_UNAVAILABLE_TEXT` shown to the user, **no** auto-escalation
   ticket, logged via `AuditLog` with the failure noted in `description`.
   This is new but consistent in *spirit* with the existing "never let a
   downstream failure crash the assistant" pattern (`chat_service`/
   `escalation` never propagate raw exceptions to the customer either).
2. **Route data available but the LLM can't produce a sensible summary**:
   out of scope for this feature — the summary prompt only asks the LLM to
   restate structured data already known to be correct, so there's no
   "not enough context" failure mode analogous to RAG's `FALLBACK_TEXT`
   here.

## Demo routes (Step 9)

2–3 fixed Sydney corridor pairs, used both as the seed generator's risk-zone
anchors and as the pairs tested during development:

1. Sydney CBD → Parramatta
2. Sydney CBD → Sydney Airport (Mascot)
3. Sydney CBD → Bondi Beach

Risk-zone warnings are deterministic (seeded events sit on these exact
corridors). Weather warnings are opportunistic/live — thresholds are tuned
to be reasonably sensitive, but a live weather flag on any given demo run
is not guaranteed.

## Testing

- Backend: unit tests for `sample_route_points`, `evaluate_weather_warnings`
  (mocked `open_meteo` responses at/below/above thresholds),
  `evaluate_risk_zone_warnings` (seeded/fake `DrivingEvent` rows at known
  distances), `build_route_plan`'s failure path (mocked `RouteServiceError`
  → `unavailable=True`, never raises), `_detect_route_plan_intent`'s regex
  matching (including the "destination only" clarifying-question branch),
  and `get_driving_events_near`'s bounding-box + haversine filtering
  against known synthetic coordinates.
- Frontend: `RouteMap` renders a polyline + correct marker count/colors
  from a fixed `RoutePlanResult` fixture; `ChatWidget` widens and renders
  the map only when `route_plan` is present and `unavailable` is false.
- No live-network tests against ORS/Open-Meteo (same policy as the
  existing Ollama-mocked test suite) — all external calls are mocked.

## Open items carried forward (not blocking this feature)

- No live demo can force a weather warning; this is inherent to using real
  weather data and is accepted, not a bug.
- `get_driving_events_near`'s cross-tenant, unscoped-by-design behavior is
  a deliberate exception to this codebase's otherwise-universal tenant
  isolation rule — documented inline in `base.py`, called out here for
  visibility to any future reviewer.
