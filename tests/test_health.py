import httpx
import pytest

from backend.config import Settings
from backend.health import check_groq, check_hindsight, check_langfuse


def settings(**overrides) -> Settings:
    base = dict(
        hindsight_api_key="hsk_test",
        groq_api_key="gsk_test",
        langfuse_public_key="pk",
        langfuse_secret_key="sk",
    )
    return Settings(_env_file=None, **(base | overrides))


def client_returning(status: int, seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def client_raising() -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("proxy refused", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("check", [check_hindsight, check_groq, check_langfuse])
async def test_missing_keys_report_not_configured(check):
    s = settings(hindsight_api_key="", groq_api_key="", langfuse_public_key="")
    async with client_returning(200) as c:
        assert (await check(c, s)).status == "not_configured"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, "ok"),
        (401, "auth_failed"),
        (403, "auth_failed"),
        (500, "error"),
    ],
)
@pytest.mark.parametrize("check", [check_hindsight, check_groq, check_langfuse])
async def test_http_status_mapping(check, status, expected):
    async with client_returning(status) as c:
        assert (await check(c, settings())).status == expected


@pytest.mark.parametrize("check", [check_hindsight, check_groq, check_langfuse])
async def test_network_failure_is_reported_not_raised(check):
    async with client_raising() as c:
        result = await check(c, settings())
    assert result.status == "unreachable"
    assert "proxy refused" in result.detail


async def test_requests_are_authenticated():
    seen: list[httpx.Request] = []
    s = settings()
    async with client_returning(200, seen) as c:
        await check_hindsight(c, s)
        await check_groq(c, s)
        await check_langfuse(c, s)
    hindsight, groq, langfuse = seen
    assert hindsight.url.path == "/v1/default/banks"
    assert hindsight.headers["authorization"] == "Bearer hsk_test"
    assert groq.url.path == "/openai/v1/models"
    assert groq.headers["authorization"] == "Bearer gsk_test"
    assert langfuse.url.path == "/api/public/projects"
    assert langfuse.headers["authorization"].startswith("Basic ")
