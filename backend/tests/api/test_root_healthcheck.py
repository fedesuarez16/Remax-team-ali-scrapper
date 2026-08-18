"""`/` answers 200 so a default healthcheck probe succeeds.

Railway reads `railway.toml` from the service's Root Directory, not the repo
root. This service's Root Directory is `backend` — it has to be, since the
Dockerfile does `COPY pyproject.toml uv.lock ./` and those live there — so the
root-level file was never being read. Deploys then ran with no
`healthcheckPath`, and the default probe hits `/`, which this app did not
serve: the container was up and still failed with "Failed to connect before
the deadline".

Placing `railway.toml` inside `backend/` is the actual fix. This route is the
belt to that suspenders: a platform probing `/` gets a 200 whether or not any
config file was picked up. It deliberately touches nothing — no database, no
Supabase client — so it answers during startup rather than depending on it.
"""
from fastapi.testclient import TestClient


def _client() -> TestClient:
    from app.main import app
    # Bare constructor: no `with`, so the lifespan does NOT run. That is the
    # point — the probe must not depend on Supabase being reachable.
    return TestClient(app)


class TestRootProbe:
    def test_root_returns_200(self):
        assert _client().get('/').status_code == 200

    def test_root_reports_status_ok(self):
        assert _client().get('/').json()['status'] == 'ok'

    def test_root_needs_no_database(self):
        """No lifespan ran, so `app.state.supabase` was never set — the probe
        must still answer instead of raising."""
        res = _client().get('/')
        assert res.status_code == 200


class TestHealthKeepsWorking:
    def test_health_still_returns_200(self):
        """`healthcheckPath = "/health"` stays the configured probe."""
        assert _client().get('/health').status_code == 200

    def test_both_probes_agree(self):
        c = _client()
        assert c.get('/').json() == c.get('/health').json()
