"""Tests for the pure outbound-webhook helpers in :mod:`app.services.webhooks`."""

import hashlib
import hmac
import json

from app.services import webhooks


class TestValidEvents:
    def test_filters_unknown(self) -> None:
        assert webhooks.valid_events(["member.join", "not.a.real.event"]) == ["member.join"]

    def test_dedupes_preserving_order(self) -> None:
        result = webhooks.valid_events(["role.create", "member.join", "role.create"])
        assert result == ["role.create", "member.join"]

    def test_empty_when_all_unknown(self) -> None:
        assert webhooks.valid_events(["nope", "also.nope"]) == []


class TestBuildEnvelope:
    def test_has_required_fields(self) -> None:
        env = webhooks.build_envelope("member.join", 123, {"id": "456"})
        assert env["event"] == "member.join"
        assert env["guild_id"] == "123"  # stringified to survive JS number limits
        assert env["data"] == {"id": "456"}
        assert env["id"] and env["sent_at"]

    def test_ids_are_unique(self) -> None:
        a = webhooks.build_envelope("member.join", 1, {})
        b = webhooks.build_envelope("member.join", 1, {})
        assert a["id"] != b["id"]


class TestSerializeAndSign:
    def test_serialize_is_valid_compact_json(self) -> None:
        env = {"id": "x", "event": "e", "guild_id": "1", "sent_at": "t", "data": {"a": 1}}
        body = webhooks.serialize_envelope(env)
        assert b" " not in body  # compact separators
        assert json.loads(body) == env

    def test_signature_matches_manual_hmac(self) -> None:
        body = b'{"hello":"world"}'
        sig = webhooks.sign_body("s3cr3t", body)
        expected = "sha256=" + hmac.new(b"s3cr3t", body, hashlib.sha256).hexdigest()
        assert sig == expected

    def test_signature_is_stable(self) -> None:
        body = b"payload"
        assert webhooks.sign_body("k", body) == webhooks.sign_body("k", body)

    def test_signature_differs_by_secret(self) -> None:
        body = b"payload"
        assert webhooks.sign_body("k1", body) != webhooks.sign_body("k2", body)
