"""A stateful fake of the Engine's message feedback endpoints (#83).

:class:`FeedbackStore` answers ``GET``/``PUT``/``DELETE /stories/{id}/messages/{mid}/feedback``
as the Engine does: one rating per (story, message, principal) with its history, newest first;
201 for a new rating, 200 for a replacement or the same value again (``updated_at`` unchanged);
204 on a retract even when nothing was rated; 404 for a message it does not know; 422
``unknown_feedback_tag`` for a tag outside ``tags``. It also answers ``GET /admin/settings`` with
``feedback_tags`` and ``feedback_text_max_chars``. Every answer is validated against the schema.
"""

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

from tests.fakes.engine import FakeEngine
from tests.fixtures import engine_fixture

FEEDBACK = "/stories/{story_id}/messages/{message_id}/feedback"
SETTINGS = "/admin/settings"
OPERATION = "01JB2Z8Y5Q3W6K9M1N4P7R0OP1"
CONTEXT: dict[str, Any] = {
    "operation_id": OPERATION,
    "config_fingerprint": "sha256:abc123",
    "profile_version": "00000000000000000000PRF001@3",
    "models": {
        "generation": {"id": "stub-gen", "reported_version": "2026-09"},
        "extraction": {"id": "stub-ext", "reported_version": None},
    },
    "rating_version_id": "01JB2Z8Y5Q3W6K9M1N4P7R0RV1",
    "quality_status": "uncertified",
}

Key = tuple[str, str, str]


def settings_json(*, tags: list[str], cap: int = 2000) -> dict[str, Any]:
    body: dict[str, Any] = engine_fixture("get_deployment_settings")
    body.update(feedback_tags=tags, feedback_text_max_chars=cap)
    return body


@dataclass
class FeedbackStore:
    fake: FakeEngine
    messages: dict[str, tuple[int, int]] = field(default_factory=dict)  # mid → (turn, variant)
    tags: list[str] = field(default_factory=lambda: ["repetitive", "off_topic", "great_prose"])
    current: dict[Key, dict[str, Any]] = field(default_factory=dict)
    history: dict[Key, list[dict[str, Any]]] = field(default_factory=dict)
    clock: int = 0

    def install(self, *, settings: bool = True, cap: int = 2000) -> FeedbackStore:
        engine = self.fake
        engine.side_effect("GET", FEEDBACK, self._get)
        engine.side_effect("PUT", FEEDBACK, self._put)
        engine.side_effect("DELETE", FEEDBACK, self._delete)
        if settings:
            engine.respond("GET", SETTINGS, 200, json=settings_json(tags=self.tags, cap=cap))
        return self

    # --- state -----------------------------------------------------------------------------

    def _now(self) -> str:
        self.clock += 1
        return f"2026-10-25T10:00:{self.clock:02d}Z"

    def seed(
        self,
        story_id: str,
        message_id: str,
        principal: str,
        *,
        rating: int,
        tags: list[str] | None = None,
        text: str | None = None,
    ) -> None:
        now = self._now()
        self.current[(story_id, message_id, principal)] = {
            "rating": rating,
            "tags": tags or [],
            "text": text,
            "created_at": now,
            "updated_at": now,
        }

    def body(self, story_id: str, message_id: str, principal: str) -> dict[str, Any]:
        key = (story_id, message_id, principal)
        turn, variant = self.messages[message_id]
        current = self.current.get(key)
        return {
            "story_id": story_id,
            "message_id": message_id,
            "turn": turn,
            "variant": variant,
            "principal": principal,
            "rating": current["rating"] if current else None,
            "tags": current["tags"] if current else [],
            "text": current["text"] if current else None,
            "compacted_at": None,
            "created_at": current["created_at"] if current else None,
            "updated_at": current["updated_at"] if current else None,
            "context": dict(CONTEXT) if current else None,
            "history": list(self.history.get(key, [])),
        }

    # --- answers ---------------------------------------------------------------------------

    def _key(self, request: httpx.Request) -> Key | None:
        segments = request.url.raw_path.decode("ascii").split("?", 1)[0].split("/")
        story_id, message_id = unquote(segments[2]), unquote(segments[4])
        principal = request.headers.get("X-Epistrel-Principal", "")
        if message_id not in self.messages:
            return None
        return story_id, message_id, principal

    def _answer(self, method: str, status: int, body: Any = None) -> httpx.Response:
        engine = self.fake
        engine._validated(method, FEEDBACK, status, body, None, 0.0)
        if body is None:
            return httpx.Response(status)
        return httpx.Response(status, json=body)

    def _not_found(self, method: str) -> httpx.Response:
        error = {"error": {"code": "not_found", "message": "no such message", "details": {}}}
        return self._answer(method, 404, error)

    def _get(self, request: httpx.Request) -> httpx.Response:
        key = self._key(request)
        if key is None:
            return self._not_found("GET")
        return self._answer("GET", 200, self.body(*key))

    def _put(self, request: httpx.Request) -> httpx.Response:
        key = self._key(request)
        if key is None:
            return self._not_found("PUT")
        sent = json.loads(request.content)
        tags = sorted({t.strip().lower() for t in sent.get("tags", [])})
        unknown = [t for t in tags if t not in {c.strip().lower() for c in self.tags}]
        if unknown:
            error = {
                "error": {
                    "code": "unknown_feedback_tag",
                    "message": "unknown tags",
                    "details": {"unknown_tags": unknown},
                }
            }
            return self._answer("PUT", 422, error)
        text = (sent.get("text") or "").strip() or None
        value = {"rating": sent["rating"], "tags": tags, "text": text}
        current = self.current.get(key)
        if current is None:
            now = self._now()
            self.current[key] = {**value, "created_at": now, "updated_at": now}
            return self._answer("PUT", 201, self.body(*key))
        if all(current[k] == v for k, v in value.items()):
            return self._answer("PUT", 200, self.body(*key))
        now = self._now()
        self.history.setdefault(key, []).insert(0, self._entry(current, "replaced", now))
        self.current[key] = {**value, "created_at": current["created_at"], "updated_at": now}
        return self._answer("PUT", 200, self.body(*key))

    def _delete(self, request: httpx.Request) -> httpx.Response:
        key = self._key(request)
        if key is None:
            return self._not_found("DELETE")
        current = self.current.pop(key, None)
        if current is not None:
            self.history.setdefault(key, []).insert(
                0, self._entry(current, "retracted", self._now())
            )
        return self._answer("DELETE", 204)

    @staticmethod
    def _entry(current: dict[str, Any], kind: str, now: str) -> dict[str, Any]:
        return {
            "rating": current["rating"],
            "tags": current["tags"],
            "text": current["text"],
            "kind": kind,
            "rated_at": current["updated_at"],
            "replaced_at": now,
        }
