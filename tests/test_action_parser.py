from environment.parsers.action_parser import parse


def test_parse_returns_raw_attributes_without_validation() -> None:
    result = parse('<tap x="1.7" y="-0.2" extra="keep"/>')
    assert result == {
        "action": "tap",
        "params": {"x": "1.7", "y": "-0.2", "extra": "keep"},
    }


def test_parse_does_not_require_action_specific_fields() -> None:
    result = parse("<swipe />")
    assert result == {"action": "swipe", "params": {}}


def test_parse_rejects_multiple_action_tags() -> None:
    result = parse("<wait /><finished />")
    assert result is None


def test_parse_rejects_unknown_action_tag() -> None:
    result = parse("<click x='0.4' y='0.3' />")
    assert result is None
