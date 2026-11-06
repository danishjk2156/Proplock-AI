"""OpenStreetMap adapter against a mock HTTP server, and the best-case (lower-bound) rule."""

from decimal import Decimal

import httpx
import pytest

from app.adapters import maps as maps_module
from app.adapters.maps import OsmMaps, Place, get_maps
from app.config import Settings
from app.db.models import ClaimSource, CommuteMode
from app.domain.check_result import Verdict
from app.domain.claims import ClaimedCommute
from app.domain.commute_rules import RouteLookup, check_commute

BLR = Place("PROP-BLR-001", Decimal("12.9718910"), Decimal("77.6411510"), "Bengaluru", "Karnataka")
METRO = [{"lat": "12.9783", "lon": "77.6408"}]


def _osm(geocode=METRO, route=None, route_status=200):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "nominatim.openstreetmap.org":
            return httpx.Response(200, json=geocode)
        body = route if route is not None else {"code": "Ok", "routes": [{"duration": 660.0, "distance": 900.0}]}
        return httpx.Response(route_status, json=body)

    slept = []
    clock = iter(range(0, 10_000)).__next__  # 1 "second" passes per call
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": OsmMaps.USER_AGENT})
    return OsmMaps(client=client, sleep=slept.append, clock=lambda: float(clock())), requests, slept


def test_walking_route_from_the_flat_to_the_geocoded_place():
    maps, requests, _ = _osm()

    result = maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.WALKING)

    assert (result.minutes, result.source, result.lower_bound) == (11, "openstreetmap", False)
    geocode, route = requests
    assert geocode.url.params["q"] == "Indiranagar Metro, Bengaluru"
    assert geocode.url.params["bounded"] == "1"  # only places near the flat
    assert "PropLock" in geocode.headers["User-Agent"]
    assert route.url.path == "/routed-foot/route/v1/foot/77.641151,12.971891;77.6408,12.9783"


def test_driving_is_a_best_case_without_live_traffic():
    maps, requests, _ = _osm(route={"code": "Ok", "routes": [{"duration": 1080.0, "distance": 7000.0}]})

    result = maps.travel_minutes(BLR, "Bagmane Tech Park", CommuteMode.DRIVING)

    assert (result.minutes, result.lower_bound) == (18, True)
    assert "empty roads" in result.basis
    assert requests[1].url.path.startswith("/routed-car/route/v1/driving/")


def test_transit_is_unknown_without_asking_anyone():
    maps, requests, _ = _osm()

    result = maps.travel_minutes(BLR, "Bagmane Tech Park", CommuteMode.TRANSIT)

    assert (result.minutes, result.reason) == (None, "OpenStreetMap has no public-transport times")
    assert requests == []


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"geocode": []}, 'no place called "Metro" found near the flat'),
        ({"route": {"code": "NoRoute", "routes": []}}, "no route found"),
        ({"route_status": 429}, "HTTP 429"),
        ({"route": {"code": "Ok", "routes": [{"duration": 9000.0, "distance": 120_000.0}]}}, "probably the wrong place"),
    ],
    ids=["unknown-place", "no-route", "rate-limited", "too-far"],
)
def test_problems_give_no_minutes_and_a_reason(kwargs, reason):
    maps, _, _ = _osm(**kwargs)

    result = maps.travel_minutes(BLR, "Metro", CommuteMode.WALKING)

    assert result.minutes is None
    assert reason in result.reason


def test_same_route_is_asked_once_and_nominatim_is_throttled():
    maps, requests, slept = _osm()

    maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.WALKING)
    maps.travel_minutes(BLR, "indiranagar  metro", CommuteMode.WALKING)  # cached
    maps.travel_minutes(BLR, "Bagmane Tech Park", CommuteMode.WALKING)  # new place: second geocode

    assert len(requests) == 4  # 2 geocodes + 2 routes
    assert slept and all(s <= OsmMaps.MIN_INTERVAL_S for s in slept)  # waited between geocodes


def test_network_failure_gives_no_minutes():
    def down(request):
        raise httpx.ConnectError("no network", request=request)

    maps = OsmMaps(client=httpx.Client(transport=httpx.MockTransport(down)), sleep=lambda s: None)

    result = maps.travel_minutes(BLR, "Indiranagar Metro", CommuteMode.WALKING)

    assert (result.minutes, result.reason) == (None, "OpenStreetMap unreachable: ConnectError")


# --- the best-case rule in Check B ---

def _claim(minutes, mode=CommuteMode.DRIVING):
    return ClaimedCommute(destination="Bagmane Tech Park", minutes=minutes, mode=mode, source=ClaimSource.FORM)


def _best_case(minutes):
    return RouteLookup(minutes, "openstreetmap", basis="with empty roads (no live traffic)", lower_bound=True)


def test_best_case_over_the_limit_is_a_proven_fail():
    # Claimed 10 min drive; even with empty roads it takes 25: the claim is false at any hour.
    result = check_commute([_claim(10)], [_best_case(25)], source="openstreetmap")

    assert result.verdict is Verdict.FAIL


def test_best_case_inside_the_limit_goes_to_review_not_pass():
    result = check_commute([_claim(25)], [_best_case(18)], source="openstreetmap")

    assert result.verdict is Verdict.UNVERIFIABLE
    [e] = result.evidence
    assert e.ok is None
    assert "rush-hour traffic can't be checked" in e.note


def test_walking_times_are_judged_fully():
    walk = RouteLookup(11, "openstreetmap", basis="on foot (walking isn't affected by traffic)")

    result = check_commute([_claim(10, CommuteMode.WALKING)], [walk], source="openstreetmap")

    assert result.verdict is Verdict.PASS
    assert "on foot" in result.evidence[0].note


def test_maps_provider_osm_is_used_even_in_demo_mode(monkeypatch):
    monkeypatch.setattr(maps_module, "get_settings",
                        lambda: Settings(_env_file=None, maps_provider="osm", demo_mode=True))
    get_maps.cache_clear()
    try:
        assert isinstance(get_maps(), OsmMaps)
    finally:
        get_maps.cache_clear()
