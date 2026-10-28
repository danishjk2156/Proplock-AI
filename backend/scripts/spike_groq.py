"""Phase 0 spike: prove the Groq key works and find models for Feature 1.

Checks three things with real API calls:
  1. the key is valid (list available models)
  2. a text model returns valid JSON for claim extraction
  3. a vision model accepts TWO images in one request and answers in JSON

Run from backend/:  .venv/Scripts/python -m scripts.spike_groq [--text-model ID] [--vision-model ID]
Exit code 0 = all passed, 1 = a check failed, 2 = no API key.
"""

import argparse
import base64
import io
import json
import sys

from PIL import Image, ImageDraw
from pydantic import BaseModel, ValidationError

from app.config import get_settings

AD_TEXT = (
    "Title: Luxury 2BHK, 2 MIN WALK TO INDIRANAGAR METRO!!\n"
    "Description: Fully furnished, 10 mins drive to Bagmane Tech Park. "
    "Rent 35000, deposit 3 months."
)

TEXT_PROMPT = """Extract every factual claim from this rental ad.
Return ONLY a JSON object with this shape:
{"commute": [{"destination": str, "minutes": int, "mode": "walking"|"transit"|"driving"}],
 "furnishing": "Unfurnished"|"Semi-Furnished"|"Fully Furnished"|null}
Ad:
"""

VISION_PROMPT = """Image 1 is the owner's verified photo of an empty room.
Image 2 is a broker's listing photo. Ignore furniture.
Compare windows, doors and room shape. Return ONLY a JSON object:
{"same_room": bool, "confidence": number between 0 and 1, "reason": str}"""


class CommuteClaim(BaseModel):
    destination: str
    minutes: int
    mode: str


class ExtractedClaims(BaseModel):
    commute: list[CommuteClaim]
    furnishing: str | None = None


class SameRoom(BaseModel):
    same_room: bool
    confidence: float
    reason: str


def _room_image(furnished: bool) -> str:
    """A simple drawn room (window + door). Only tests that two images are accepted, not accuracy."""
    img = Image.new("RGB", (480, 360), (235, 230, 220))
    draw = ImageDraw.Draw(img)
    draw.rectangle((60, 60, 200, 170), fill=(170, 210, 240), outline=(80, 80, 80), width=4)  # window
    draw.rectangle((330, 120, 410, 330), fill=(150, 110, 70), outline=(60, 40, 20), width=4)  # door
    draw.line((0, 300, 480, 300), fill=(120, 120, 120), width=3)  # floor line
    if furnished:
        draw.rectangle((40, 240, 260, 310), fill=(90, 60, 140))  # sofa
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _pick(ids: list[str], wanted: str, hints: list[str]) -> str | None:
    if wanted:
        return wanted
    for hint in hints:
        for model_id in ids:
            if hint in model_id:
                return model_id
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-model", default="")
    parser.add_argument("--vision-model", default="")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.groq_api_key:
        print("No GROQ_API_KEY found. Add it to backend/.env (see .env.example) and run again.")
        return 2

    from groq import Groq

    client = Groq(api_key=settings.groq_api_key)
    ok = True

    print("1) Listing models ...")
    ids = sorted(m.id for m in client.models.list().data)
    print("   " + "\n   ".join(ids))

    text_model = _pick(ids, args.text_model or settings.groq_text_model, ["llama-3.3-70b", "gpt-oss-120b", "gpt-oss-20b"])
    vision_model = _pick(ids, args.vision_model or settings.groq_vision_model, ["llama-4-scout", "llama-4-maverick", "vision"])

    print(f"\n2) Text JSON extraction with {text_model} ...")
    if not text_model:
        print("   FAIL: no text model chosen")
        ok = False
    else:
        resp = client.chat.completions.create(
            model=text_model,
            messages=[{"role": "user", "content": TEXT_PROMPT + AD_TEXT}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = resp.choices[0].message.content
        try:
            claims = ExtractedClaims.model_validate(json.loads(raw))
            print(f"   PASS: {claims.model_dump()}")
        except (json.JSONDecodeError, ValidationError) as exc:
            print(f"   FAIL: invalid JSON for our schema: {exc}\n   raw: {raw}")
            ok = False

    print(f"\n3) Vision with TWO images in one request using {vision_model} ...")
    if not vision_model:
        print("   FAIL: no vision model found in the list above")
        ok = False
    else:
        try:
            resp = client.chat.completions.create(
                model=vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VISION_PROMPT},
                        {"type": "image_url", "image_url": {"url": _room_image(furnished=False)}},
                        {"type": "image_url", "image_url": {"url": _room_image(furnished=True)}},
                    ],
                }],
                response_format={"type": "json_object"},
                temperature=0,
            )
            raw = resp.choices[0].message.content
            answer = SameRoom.model_validate(json.loads(raw))
            print(f"   PASS: two images accepted, JSON valid: {answer.model_dump()}")
        except Exception as exc:  # report any API or parsing error; this is a probe
            print(f"   FAIL: {type(exc).__name__}: {exc}")
            ok = False

    print("\nPut the models that passed into backend/.env:")
    print(f"   GROQ_TEXT_MODEL={text_model or ''}")
    print(f"   GROQ_VISION_MODEL={vision_model or ''}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
