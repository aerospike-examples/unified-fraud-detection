"""Tests for cross-case memory helpers (no Aerospike)."""

from workflow.case_memory import entity_token, _encode, _parse, _SEP


class TestCaseMemoryEncoding:
    def test_entity_token_unique_per_id(self):
        a = entity_token("U0001234")
        b = entity_token("U0005678")
        assert a != b
        assert a.isalpha() or "id" in a

    def test_entity_token_stable(self):
        assert entity_token("A000123401") == entity_token("A000123401")

    def test_encode_deduplicates(self):
        encoded = _encode(["U0001", "U0001", "U0002"])
        assert encoded.count(entity_token("U0001")) == 1

    def test_parse_roundtrip(self):
        case = {
            "investigation_id": "inv_abc",
            "user_id": "U0001",
            "entities": ["U0001", "A0001"],
            "decision": "temporary_freeze",
        }
        import json
        text = f"{_encode(case['entities'])} {_SEP} {json.dumps(case)}"
        parsed = _parse(text)
        assert parsed["investigation_id"] == "inv_abc"
        assert parsed["decision"] == "temporary_freeze"

    def test_parse_invalid_returns_none(self):
        assert _parse("no separator here") is None
