"""Tests for CORS configuration.

Without `CORSMiddleware`, a real browser blocks every cross-origin response
before any frontend JS ever sees it -- silently, since the request itself
still reaches the backend and gets a normal 200, but the browser withholds
it from script access. `TestClient` (httpx under the hood) doesn't enforce
CORS the way a browser does, so these tests can't reproduce "the browser
blocked it" directly -- instead they assert on the same signal a real
browser's CORS check relies on: the `Access-Control-Allow-Origin` response
header (and, for the preflight case, the full set of
`Access-Control-Allow-*` headers FastAPI's `CORSMiddleware` returns for an
`OPTIONS` preflight). If these headers are present and correct, the
browser's CORS check passes; if they're missing, it doesn't -- regardless
of what TestClient itself does with the response.

Manual/integration verification beyond what's practical here: actually
running `docker compose up`, opening the frontend in a real browser at
http://localhost:3000 (or `npm run dev` at :5173), and confirming the
Network tab shows a successful (not CORS-blocked) `/auth/login` request
with no red CORS console error. That's the actual failure mode this task
was flagging, and it's inherently a browser-level behavior no
`TestClient`-based test can fully stand in for.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

ALLOWED_ORIGIN = "http://localhost:5173"
OTHER_ALLOWED_ORIGIN = "http://localhost:3000"
DISALLOWED_ORIGIN = "http://evil.example.com"


def test_cors_allowed_origins_list_matches_documented_defaults():
    """Sanity-check the parsed settings value itself, independent of any
    HTTP request, so a future change to the default string is caught here
    even if it accidentally still "looks right" in the header tests below."""
    assert settings.cors_allowed_origins_list == [
        "http://localhost:5173",
        "http://localhost:3000",
    ]


def test_preflight_request_from_allowed_origin_is_approved():
    """Simulates the preflight `OPTIONS` request a browser sends ahead of
    the real `POST /auth/login` call (since it carries a `Content-Type:
    application/json` header, that's a non-"simple" request)."""
    client = TestClient(app)
    response = client.options(
        "/auth/login",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers


def test_preflight_request_allows_patch_method():
    """Task 22's `PATCH /chat/messages/{id}/feedback` endpoint (and the
    frontend's `apiPatch` in `frontend/src/lib/apiClient.ts`) issue real
    `PATCH` requests, which browsers always preflight. If `PATCH` isn't in
    `allow_methods`, Starlette's `CORSMiddleware` rejects the preflight with
    `400 Disallowed CORS method` and the browser never sends the real
    request -- silently breaking thumbs up/down in every real browser
    session (invisible to TestClient-based tests that don't check this
    header)."""
    client = TestClient(app)
    response = client.options(
        "/chat/messages/1/feedback",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_cors_allow_methods_covers_every_method_the_frontend_client_issues():
    """`frontend/src/lib/apiClient.ts` defines `apiGet`, `apiPost`, `apiPut`,
    `apiDelete`, and `apiPatch` -- every HTTP method the frontend is capable
    of issuing. The CORS config must allow all of them, not just whichever
    ones happen to be exercised by a specific preflight test above, so a
    future new `apiPatch`/`apiDelete`/etc. call site doesn't silently regain
    this same bug for a different method."""
    frontend_methods = {"GET", "POST", "PUT", "DELETE", "PATCH"}
    client = TestClient(app)
    for method in frontend_methods:
        response = client.options(
            "/chat/messages/1/feedback",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200, f"{method} preflight was rejected"
        assert method in response.headers["access-control-allow-methods"], (
            f"{method} missing from access-control-allow-methods"
        )


def test_preflight_request_from_docker_compose_frontend_origin_is_approved():
    """The other origin this app actually runs from (see docker-compose.yml,
    which maps the `frontend` service's `serve` process to host port 3000)."""
    client = TestClient(app)
    response = client.options(
        "/auth/login",
        headers={
            "Origin": OTHER_ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == OTHER_ALLOWED_ORIGIN


def test_preflight_request_from_disallowed_origin_is_not_approved():
    """An arbitrary third-party origin must not be granted CORS access --
    Starlette's CORSMiddleware responds 400 to a preflight from an origin
    that isn't on the allow-list."""
    client = TestClient(app)
    response = client.options(
        "/auth/login",
        headers={
            "Origin": DISALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}


def test_actual_response_from_allowed_origin_carries_cors_header():
    """The real (non-preflight) response also needs the header -- it's what
    the browser checks against the initiating page's origin before handing
    the response body to JS."""
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": ALLOWED_ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_actual_response_from_disallowed_origin_lacks_cors_header():
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": DISALLOWED_ORIGIN})
    # The request still succeeds server-side (CORS is enforced by the
    # browser, not the server) -- but without the header, a real browser
    # would withhold this response from the page's JS.
    assert response.status_code == 200
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
