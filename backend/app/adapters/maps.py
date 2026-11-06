"""Travel times for Check B.

GoogleRoutesMaps uses live traffic (needs GOOGLE_MAPS_API_KEY and a billing account).
OsmMaps uses OpenStreetMap: free, no key, but no live traffic (MAPS_PROVIDER=osm).
DemoTableMaps reads app/rules/commute_matrix.yaml and is used only when DEMO_MODE=true.
FakeMaps is for tests.
None of them ever invents a number: no data gives a RouteLookup with minutes=None and a reason.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
import yaml

from app.config import get_settings
from app.db.models import CommuteMode
from app.domain.commute_rules import RouteLookup, next_weekday_peak

DEMO_TABLE = Path(__file__).resolve().parents[1] / "rules" / "commute_matrix.yaml"


@dataclass(frozen=True)
class Place:
    """Where the flat is. The origin of every route is the manager-verified pin, never the broker's."""

    property_id: str
    latitude: Decimal
    longitude: Decimal
    city: str
    state: str  # used by Check C (deposit rules), not by routing


def normalize(destination: str) -> str:
    return " ".join(destination.lower().split())


class Maps(Protocol):
    name: str

    def travel_minutes(self, place: Place, destination: str, mode: CommuteMode) -> RouteLookup: ...


class GoogleRoutesMaps:
    """Routes API computeRoutes, departing at the next weekday 08:30 IST (rush hour)."""

    URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
    TRAVEL_MODE = {CommuteMode.WALKING: "WALK", CommuteMode.DRIVING: "DRIVE", CommuteMode.TRANSIT: "TRANSIT"}
    # A "{destination}, {city}" that resolves further away than this was almost certainly the wrong place.
    MAX_DISTANCE_M = 50_000

    name = "google-routes"

    def __init__(self, api_key: str, client: httpx.Client | None = None, now: Callable[[], datetime] | None = None):
        self._key = api_key
        self._client = client or httpx.Client(timeout=10)
        self._now = now or (lambda: datetime.now(UTC))
        # Cache per (flat, destination, mode): a route doesn't change between one broker's edits.
        self._cache: dict[tuple[str, str, CommuteMode], RouteLookup] = {}

    def travel_minutes(self, place: Place, destination: str, mode: CommuteMode) -> RouteLookup:
        key = (place.property_id, normalize(destination), mode)
        if key in self._cache:
            return self._cache[key]

        body = {
            "origin": {"location": {"latLng": {"latitude": float(place.latitude), "longitude": float(place.longitude)}}},
            # Routes geocodes the address itself; adding the city keeps "Metro" from resolving in another town.
            "destination": {"address": f"{destination}, {place.city}"},
            "travelMode": self.TRAVEL_MODE[mode],
            "regionCode": "in",
            "units": "METRIC",
        }
        if mode is not CommuteMode.WALKING:
            body["departureTime"] = next_weekday_peak(self._now()).isoformat().replace("+00:00", "Z")
        if mode is CommuteMode.DRIVING:
            body["routingPreference"] = "TRAFFIC_AWARE"  # use predicted traffic at departureTime

        try:
            resp = self._client.post(
                self.URL,
                json=body,
                headers={"X-Goog-Api-Key": self._key, "X-Goog-FieldMask": "routes.duration,routes.distanceMeters"},
            )
        except httpx.HTTPError as e:
            return RouteLookup(None, self.name, f"Routes API unreachable: {type(e).__name__}")
        if resp.status_code != 200:
            return RouteLookup(None, self.name, f"Routes API returned HTTP {resp.status_code}")

        routes = resp.json().get("routes") or []
        if not routes:
            return RouteLookup(None, self.name, "no route found")
        route = routes[0]
        if route.get("distanceMeters", 0) > self.MAX_DISTANCE_M:
            km = route["distanceMeters"] / 1000
            return RouteLookup(None, self.name, f"destination resolved {km:.0f} km away, probably the wrong place")
        seconds = int(route["duration"].removesuffix("s"))  # protobuf Duration, e.g. "1320s"
        result = RouteLookup(round(seconds / 60), self.name)
        self._cache[key] = result
        return result


class OsmMaps:
    """OpenStreetMap, no key or payment needed: Nominatim finds the place, the FOSSGIS OSRM
    servers (the ones openstreetmap.org uses for directions) give the route.

    Walking times don't depend on traffic, so they are judged fully. OSM has no live traffic,
    so a driving time is a best case (`lower_bound`): over the limit is a proven FAIL, inside it
    goes to a manager. OSM has no public-transport timetables, so transit is UNVERIFIABLE.

    Public servers, fair use: at most one Nominatim request per second, an identifying
    User-Agent, and results cached so the same route is asked for once.
    """

    GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
    ROUTE_URL = {
        CommuteMode.WALKING: "https://routing.openstreetmap.de/routed-foot/route/v1/foot",
        CommuteMode.DRIVING: "https://routing.openstreetmap.de/routed-car/route/v1/driving",
    }
    USER_AGENT = "PropLock-hackathon/0.1 (+https://github.com/danishjk2156/Proplock-AI)"
    SEARCH_BOX_DEG = 0.3  # look for the destination within ~30 km of the flat
    MAX_DISTANCE_M = 50_000
    MIN_INTERVAL_S = 1.1  # Nominatim's usage policy: no more than one request a second

    name = "openstreetmap"

    def __init__(self, client: httpx.Client | None = None, sleep: Callable[[float], None] | None = None,
                 clock: Callable[[], float] | None = None):
        self._client = client or httpx.Client(timeout=15, headers={"User-Agent": self.USER_AGENT})
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._last_geocode = float("-inf")
        self._places: dict[tuple[str, str], tuple[float, float] | None] = {}
        self._cache: dict[tuple[str, str, CommuteMode], RouteLookup] = {}

    def _geocode(self, place: Place, destination: str) -> tuple[float, float] | None:
        key = (place.property_id, normalize(destination))
        if key in self._places:
            return self._places[key]
        wait = self.MIN_INTERVAL_S - (self._clock() - self._last_geocode)
        if wait > 0:
            self._sleep(wait)
        lat, lon, d = float(place.latitude), float(place.longitude), self.SEARCH_BOX_DEG
        resp = self._client.get(self.GEOCODE_URL, params={
            "q": f"{destination}, {place.city}",
            "format": "jsonv2",
            "limit": 1,
            # Only accept matches near the flat, so "Metro" can't resolve to another city.
            "viewbox": f"{lon - d},{lat + d},{lon + d},{lat - d}",
            "bounded": 1,
        })
        self._last_geocode = self._clock()
        resp.raise_for_status()
        hits = resp.json()
        found = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
        self._places[key] = found
        return found

    def travel_minutes(self, place: Place, destination: str, mode: CommuteMode) -> RouteLookup:
        if mode is CommuteMode.TRANSIT:
            return RouteLookup(None, self.name, "OpenStreetMap has no public-transport times")
        key = (place.property_id, normalize(destination), mode)
        if key in self._cache:
            return self._cache[key]
        try:
            target = self._geocode(place, destination)
            if target is None:
                return RouteLookup(None, self.name, f'no place called "{destination}" found near the flat')
            coords = f"{float(place.longitude)},{float(place.latitude)};{target[1]},{target[0]}"
            resp = self._client.get(f"{self.ROUTE_URL[mode]}/{coords}", params={"overview": "false"})
        except httpx.HTTPError as e:
            return RouteLookup(None, self.name, f"OpenStreetMap unreachable: {type(e).__name__}")
        if resp.status_code != 200:
            return RouteLookup(None, self.name, f"OpenStreetMap routing returned HTTP {resp.status_code}")
        body = resp.json()
        routes = body.get("routes") or []
        if body.get("code") != "Ok" or not routes:
            return RouteLookup(None, self.name, "no route found")
        route = routes[0]
        if route.get("distance", 0) > self.MAX_DISTANCE_M:
            return RouteLookup(None, self.name, f"route is {route['distance'] / 1000:.0f} km, probably the wrong place")
        minutes = round(route["duration"] / 60)
        if mode is CommuteMode.WALKING:
            result = RouteLookup(minutes, self.name, basis="on foot (walking isn't affected by traffic)")
        else:
            result = RouteLookup(minutes, self.name, basis="with empty roads (no live traffic)", lower_bound=True)
        self._cache[key] = result
        return result


class DemoTableMaps:
    """Exact-match lookups in the demo route table (fixes the old substring bug in M3)."""

    name = "demo-table"

    def __init__(self, path: Path = DEMO_TABLE):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        self._routes: dict[tuple[str, str], dict[CommuteMode, int]] = {}
        for property_id, entries in data["routes"].items():
            for entry in entries:
                minutes = {CommuteMode(mode): int(m) for mode, m in entry["minutes"].items()}
                for alias in [entry["destination"], *entry.get("aliases", [])]:
                    self._routes[(property_id, normalize(alias))] = minutes

    def travel_minutes(self, place: Place, destination: str, mode: CommuteMode) -> RouteLookup:
        minutes = self._routes.get((place.property_id, normalize(destination)))
        if minutes is None:
            return RouteLookup(None, self.name, "destination not in the demo route table")
        if mode not in minutes:
            return RouteLookup(None, self.name, f"no demo {mode.value} time for this destination")
        return RouteLookup(minutes[mode], self.name)


class UnconfiguredMaps:
    name = "none"

    def __init__(self, reason: str):
        self._reason = reason

    def travel_minutes(self, place: Place, destination: str, mode: CommuteMode) -> RouteLookup:
        return RouteLookup(None, self.name, self._reason)


class FakeMaps:
    """Answers from a {(destination, mode): minutes} dict and records every lookup."""

    name = "fake"

    def __init__(self, routes: dict[tuple[str, CommuteMode], int] | None = None):
        self.routes = dict(routes or {})  # tests may replace this; names are normalised on lookup
        self.calls: list[tuple[str, str, CommuteMode]] = []

    def travel_minutes(self, place: Place, destination: str, mode: CommuteMode) -> RouteLookup:
        self.calls.append((place.property_id, destination, mode))
        routes = {(normalize(d), m): v for (d, m), v in self.routes.items()}
        minutes = routes.get((normalize(destination), mode))
        if minutes is None:
            return RouteLookup(None, self.name, "no fake route")
        return RouteLookup(minutes, self.name)


@lru_cache
def get_maps() -> Maps:
    """FastAPI dependency. MAPS_PROVIDER=osm always uses OpenStreetMap. The default, "auto":
    Google if there's a key, else the demo table when DEMO_MODE is on, else none."""
    settings = get_settings()
    if settings.maps_provider == "osm":
        return OsmMaps()
    if settings.google_maps_api_key:
        return GoogleRoutesMaps(settings.google_maps_api_key)
    if settings.demo_mode:
        return DemoTableMaps()
    return UnconfiguredMaps("GOOGLE_MAPS_API_KEY is not set and DEMO_MODE is off")
