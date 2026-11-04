"""Maps adapters: Google Routes (against a mock HTTP server), the demo table, and how one is chosen."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.adapters import maps as maps_module
from app.adapters.maps import DemoTableMaps, GoogleRoutesMaps, Place, UnconfiguredMaps, get_maps
from app.config import Settings
from app.db.models import CommuteMode

BLR = Place("PROP-BLR-001", Decimal("12.9718910"), Decimal("77.6411510"), "Bengaluru")
# Wednesday 4 Nov 2026, 15:00 IST -> next peak is Thursday 5 Nov 08:30 IST = 03:00 UTC.
NOW = datetime(2026, 11, 4, 9, 30, tzinfo=UTC)


def _google(handler):
    requests = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return GoogleRoutesMaps("test-key", client=client, now=lambda: NOW), requests


def _ok(seconds, meters=5200):
    return lambda _: httpx.Response(200, json={"routes": [{"duration": f"{seconds}s", "distanceMeters": meters}]})


def test_driving_asks_for_rush_hour_traffic_from_the_flats_pin():
    maps, requests = _google(_ok(1320))

    result = maps.travel_minutes(BLR, "Bagmane Tech Park", CommuteMode.DRIVING)

    assert (result.minutes, result.source) == (22, "google-routes")
    [req] = requests
    assert str(req.url) == GoogleRoutesMaps.URL
    assert req.headers["X-Goog-Api-Key"] == "test-key"
    assert req.headers["X-Goog-FieldMask"] == "routes.duration,routes.distanceMeters"
    body = json.loads(req.content)
    assert body["origin"]["location"]["latLng"] == {"latitude": 12.971891, "longitude": 77.641151}
    assert body["destination"] == {"address": "Bagmane Tech Park, Bengaluru"}
    assert body["travelMode"] == "DRIVE"
    assert body["routingPreference"] == "TRAFFIC_AWARE"
    assert body["departureTime"] == "2026-11-05T03:00:00Z"


def test_walking_has_no_departure_time_or_traffic():
    maps, requests = _google(_ok(660))

    assert maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.WALKING).minutes == 11
    body = json.loads(requests[0].content)
    assert body["travelMode"] == "WALK"
    assert "departureTime" not in body and "routingPreference" not in body


def test_transit_departs_at_rush_hour():
    maps, requests = _google(_ok(2100))

    assert maps.travel_minutes(BLR, "Bagmane Tech Park", CommuteMode.TRANSIT).minutes == 35
    body = json.loads(requests[0].content)
    assert (body["travelMode"], body["departureTime"]) == ("TRANSIT", "2026-11-05T03:00:00Z")


def test_same_route_is_asked_once():
    maps, requests = _google(_ok(660))

    maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.WALKING)
    maps.travel_minutes(BLR, "  indiranagar   METRO ", CommuteMode.WALKING)
    maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.DRIVING)  # different mode -> new request

    assert len(requests) == 2


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (lambda _: httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED"}}), "HTTP 403"),
        (lambda _: httpx.Response(200, json={}), "no route found"),
        (_ok(9000, meters=310_000), "310 km away"),
    ],
    ids=["http-error", "no-route", "wrong-place"],
)
def test_problems_give_no_minutes_and_a_reason(handler, reason):
    maps, _ = _google(handler)

    result = maps.travel_minutes(BLR, "Metro", CommuteMode.WALKING)

    assert result.minutes is None
    assert reason in result.reason


def test_network_failure_gives_no_minutes_and_is_retried_next_time():
    def down(request):
        raise httpx.ConnectError("no network", request=request)

    maps, requests = _google(down)

    first = maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.WALKING)
    maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.WALKING)

    assert (first.minutes, first.reason) == (None, "Routes API unreachable: ConnectError")
    assert len(requests) == 2  # failures are not cached


# --- demo table ---

def test_demo_table_matches_name_or_alias_exactly():
    demo = DemoTableMaps()

    assert demo.travel_minutes(BLR, "Indiranagar Metro Station", CommuteMode.WALKING).minutes == 11
    assert demo.travel_minutes(BLR, "indiranagar METRO", CommuteMode.WALKING).minutes == 11
    assert demo.travel_minutes(BLR, "Bagmane", CommuteMode.DRIVING).minutes == 25


@pytest.mark.parametrize("destination", ["a", "Metro", "Indiranagar Metro Station Road", "Manyata Tech Park"])
def test_demo_table_never_matches_substrings_or_unknown_places(destination):
    # The old code matched "a" against "tech park" (M3). Now anything not listed is unknown.
    result = DemoTableMaps().travel_minutes(BLR, destination, CommuteMode.WALKING)

    assert (result.minutes, result.reason) == (None, "destination not in the demo route table")


def test_demo_table_is_per_flat_and_per_mode():
    chennai = Place("PROP-CHN-002", Decimal("12.94"), Decimal("80.24"), "Chennai")
    demo = DemoTableMaps()

    assert demo.travel_minutes(chennai, "Indiranagar Metro", CommuteMode.WALKING).minutes is None
    result = demo.travel_minutes(BLR, "Embassy Golf Links", CommuteMode.WALKING)
    assert (result.minutes, result.reason) == (None, "no demo walking time for this destination")


# --- choosing an adapter ---

@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({"google_maps_api_key": "k", "demo_mode": True}, GoogleRoutesMaps),  # a real key always wins
        ({"google_maps_api_key": None, "demo_mode": True}, DemoTableMaps),
        ({"google_maps_api_key": None, "demo_mode": False}, UnconfiguredMaps),
    ],
)
def test_get_maps_picks_real_then_demo_then_none(monkeypatch, settings, expected):
    monkeypatch.setattr(maps_module, "get_settings", lambda: Settings(_env_file=None, **settings))
    get_maps.cache_clear()
    try:
        assert isinstance(get_maps(), expected)
    finally:
        get_maps.cache_clear()


def test_unconfigured_says_why():
    result = UnconfiguredMaps("GOOGLE_MAPS_API_KEY is not set and DEMO_MODE is off").travel_minutes(
        BLR, "Indiranagar Metro", CommuteMode.WALKING
    )

    assert (result.minutes, result.reason) == (None, "GOOGLE_MAPS_API_KEY is not set and DEMO_MODE is off")
