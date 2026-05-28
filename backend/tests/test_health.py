import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.asyncio
async def test_health_returns_ok() -> None:
    # Import here to avoid settings validation at collection time
    from app.main import app

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
