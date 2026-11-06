"""GroqVision against a fake client, and the photos node with a FakeVision."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import groq
import httpx
import pytest

from app.adapters.llm import FakeVision, GroqVision, UnconfiguredVision, VisionUnavailable
from app.agents.nodes import photos as photos_node
from app.db.models import Furnishing, PhotoKind
from app.domain.check_result import Verdict
from app.domain.ground_truth import ListingTerms
from app.domain.photo_rules import PhotoInspection, PhotoInput
from tests.conftest import photo_bytes

GOOD = {"same_flat": True, "watermark": False, "furniture_level": "Unfurnished", "confidence": 0.9, "reason": "same tiles"}


class FakeGroqClient:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


@pytest.fixture
def files(tmp_path):
    paths = []
    for seed in range(5):
        path = tmp_path / f"p{seed}.jpg"
        path.write_bytes(photo_bytes(seed))
        paths.append(str(path))
    return paths


def _vision(*replies):
    client = FakeGroqClient(*replies)
    return GroqVision("key", "vision-model", client=client), client


def _images_sent(request):
    return [part for part in request["messages"][0]["content"] if part["type"] == "image_url"]


def test_photo_and_up_to_three_references_go_in_one_request(files):
    vision, client = _vision(json.dumps(GOOD))

    result = vision.inspect_photo(files[0], files[1:])  # four references offered

    assert result.same_flat is True
    [req] = client.requests
    assert len(_images_sent(req)) == 4  # the photo + 3 references
    assert req["model"] == "vision-model"
    assert req["response_format"] == {"type": "json_object"}
    assert "Images 2 to 4 are the owner's verified photos" in req["messages"][0]["content"][0]["text"]


def test_without_references_same_flat_is_forced_to_unknown(files):
    vision, client = _vision(json.dumps(GOOD))  # the model claims "same flat" with nothing to compare

    result = vision.inspect_photo(files[0], [])

    assert result.same_flat is None
    assert len(_images_sent(client.requests[0])) == 1


def test_one_retry_on_an_invalid_reply(files):
    vision, client = _vision("not json", json.dumps(GOOD))

    assert vision.inspect_photo(files[0], files[1:2]).confidence == 0.9
    assert len(client.requests) == 2


def test_two_invalid_replies_make_vision_unavailable(files):
    bad = json.dumps({**GOOD, "confidence": 7})
    vision, _ = _vision(bad, bad)

    with pytest.raises(VisionUnavailable, match="invalid JSON 2 times"):
        vision.inspect_photo(files[0], [])


def test_api_error_makes_vision_unavailable(files):
    vision, _ = _vision(groq.APIConnectionError(request=httpx.Request("POST", "https://api.groq.com")))

    with pytest.raises(VisionUnavailable, match="Groq API error"):
        vision.inspect_photo(files[0], [])


def test_missing_file_makes_vision_unavailable():
    vision, client = _vision()

    with pytest.raises(VisionUnavailable, match="could not read an image file"):
        vision.inspect_photo("does/not/exist.jpg", [])
    assert client.requests == []


def test_unconfigured_vision_says_why():
    with pytest.raises(VisionUnavailable, match="GROQ_VISION_MODEL is not set"):
        UnconfiguredVision("GROQ_VISION_MODEL is not set").inspect_photo("x.jpg", [])


# --- the photos node ---

def _photo(pid, kind, distance=10.0):
    return PhotoInput(pid, kind, f"{pid}.jpg", "0" * 16, distance, datetime.now(UTC), None)


def _state(photos, baselines=("owner.jpg",), furnishing=Furnishing.UNFURNISHED, text_furnishing=None):
    listing = ListingTerms(rent=1, deposit=1, brokerage_fee=0, furnishing=furnishing)
    return {"photos": photos, "baselines": list(baselines), "listing": listing, "text_furnishing": text_furnishing}


def _run(state, vision):
    [result] = photos_node(state, {"configurable": {"vision": vision}})["results"]
    return result


def test_only_extra_photos_are_sent_to_vision_with_the_owners_photos():
    vision = FakeVision(PhotoInspection.model_validate(GOOD))

    result = _run(_state([_photo("c", PhotoKind.CHECKIN), _photo("e1", PhotoKind.EXTRA), _photo("e2", PhotoKind.EXTRA)]),
                  vision)

    assert result.verdict is Verdict.PASS
    assert sorted(path for path, _ in vision.calls) == ["e1.jpg", "e2.jpg"]
    assert all(refs == ["owner.jpg"] for _, refs in vision.calls)
    assert result.source == "phash+gps+fake"


def test_vision_outage_makes_extra_photos_unverifiable():
    vision = FakeVision(error=VisionUnavailable("GROQ_API_KEY is not set"))

    result = _run(_state([_photo("c", PhotoKind.CHECKIN), _photo("e", PhotoKind.EXTRA)]), vision)

    assert result.verdict is Verdict.UNVERIFIABLE
    assert "GROQ_API_KEY is not set" in result.evidence[-1].note


def test_furnishing_claimed_in_the_text_is_what_photos_are_held_to():
    empty_room = PhotoInspection.model_validate(GOOD)  # Unfurnished
    state = _state([_photo("c", PhotoKind.CHECKIN), _photo("e", PhotoKind.EXTRA)], text_furnishing=Furnishing.FULLY)

    result = _run(state, FakeVision(empty_room))

    assert result.verdict is Verdict.FAIL
    assert [e.field for e in result.evidence if e.ok is False] == ["furnishing in photos"]
