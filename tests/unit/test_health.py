"""Tests for the /health route's Redis probe (issue #62).

The probe used to build its client from ``settings.redis_host`` and
``settings.redis_port``, neither of which exists on ``Settings``. The
resulting ``AttributeError`` was swallowed by the handler's
``except Exception`` and reported as a Redis outage, so ``/health``
answered 503 even with Redis reachable. These tests pin the probe to
``settings.redis_url``, the field that is actually defined, and keep a
genuine outage reporting as one.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from api.routes.health import health_check
from core.config import settings


@pytest.mark.unit
class TestHealthRedisProbe:
    """Test suite for the Redis dependency probe in health_check."""

    @pytest.fixture
    def mock_db(self):
        """A database session whose health probe succeeds."""
        db = AsyncMock()
        db.execute = AsyncMock()
        return db

    def test_settings_has_no_redis_host_or_port(self):
        """The attributes the old probe read do not exist.

        This is the defect's root condition. If someone later adds
        these fields to Settings, this test fails and the probe should
        be revisited deliberately rather than by accident.
        """
        assert not hasattr(settings, "redis_host")
        assert not hasattr(settings, "redis_port")
        assert settings.redis_url

    @pytest.mark.asyncio
    async def test_redis_probe_uses_redis_url_and_reports_healthy(self, mock_db):
        """A reachable Redis is reported healthy, and built from redis_url."""
        fake_client = Mock()
        fake_client.ping = Mock(return_value=True)

        with patch("redis.Redis.from_url", return_value=fake_client) as from_url:
            result = await health_check(db=mock_db)

        from_url.assert_called_once_with(settings.redis_url, decode_responses=True)
        fake_client.ping.assert_called_once()
        assert result["dependencies"]["redis"] == "healthy"

    @pytest.mark.asyncio
    async def test_redis_probe_reports_unhealthy_when_unreachable(self, mock_db):
        """A real outage still reports unhealthy.

        Guards against "fixing" the probe by making it always pass.
        """
        fake_client = Mock()
        fake_client.ping = Mock(side_effect=ConnectionError("connection refused"))

        with (
            patch("redis.Redis.from_url", return_value=fake_client),
            pytest.raises(Exception) as excinfo,
        ):
            await health_check(db=mock_db)

        # The handler raises HTTPException(503) when any dependency is down.
        detail = getattr(excinfo.value, "detail", {})
        assert detail["dependencies"]["redis"] == "unhealthy"
        assert detail["status"] == "unhealthy"
