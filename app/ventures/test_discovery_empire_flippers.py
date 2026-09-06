import json
from copy import deepcopy
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app.ventures import discovery_empire_flippers as source


class Response(BytesIO):
    status = 200


def fixture():
    return {
        "data": {
            "listings": [{
                "id": "synthetic_listing",
                "listing_number": 1,
                "listing_status": "For Sale",
                "average_monthly_net_profit": None,
                "summary": "Synthetic source text.",
            }],
            "count": 1,
            "pages": 1,
            "page": 1,
            "limit": 5,
        },
        "errors": [],
    }


def fetch_payload(payload):
    raw = json.dumps(payload).encode()
    with patch.object(
        source, "urlopen", return_value=Response(raw)
    ) as network:
        result = source.fetch_listing_page(limit=5)
        assert network.call_count == 1
        assert network.call_args.kwargs["timeout"] == 30
        return result


def expect_error(action, error_type=ValueError):
    try:
        action()
    except error_type:
        return
    raise AssertionError(f"Expected {error_type.__name__}")


def main():
    payload = fixture()
    result = fetch_payload(payload)
    assert result["listings"] == payload["data"]["listings"]
    assert result["listings"][0]["average_monthly_net_profit"] is None
    assert result["source"] == "empire_flippers"
    assert result["total_reported"] == 1
    assert result["fetched_at"]
    print("source_fields_and_provenance: PASS")

    empty = fixture()
    empty["data"].update(listings=[], count=0, pages=0)
    assert fetch_payload(empty)["listings"] == []
    print("valid_empty_response: PASS")

    invalid_payloads = [
        [],
        {"errors": ["Source unavailable"]},
        {"data": None},
    ]

    for field, value in (
        ("listings", {}),
        ("page", 2),
        ("limit", 100),
        ("count", -1),
        ("pages", "1"),
    ):
        broken = fixture()
        broken["data"][field] = value
        invalid_payloads.append(broken)

    for changes in (
        {"id": ""},
        {"listing_status": "Sold"},
    ):
        broken = fixture()
        broken["data"]["listings"][0].update(changes)
        invalid_payloads.append(broken)

    duplicate = fixture()
    duplicate["data"]["listings"] *= 2
    invalid_payloads.append(duplicate)

    for broken in invalid_payloads:
        expect_error(lambda: fetch_payload(broken))
    print("malformed_source_responses_rejected: PASS")

    with patch.object(source, "urlopen") as network:
        for kwargs in (
            {"page": 0},
            {"page": True},
            {"limit": 0},
            {"limit": 101},
        ):
            expect_error(lambda: source.fetch_listing_page(**kwargs))
        network.assert_not_called()
    print("invalid_requests_never_sent: PASS")

    with patch.object(
        source, "urlopen", return_value=Response(b"not JSON")
    ):
        expect_error(source.fetch_listing_page)

    with (
        patch.object(source, "MAX_RESPONSE_BYTES", 10),
        patch.object(
            source, "urlopen", return_value=Response(b"x" * 11)
        ),
    ):
        expect_error(source.fetch_listing_page)
    print("invalid_json_and_size_limit: PASS")

    for error in (
        URLError("Synthetic network failure"),
        HTTPError(source.ENDPOINT, 429, "Rate limited", {}, None),
        TimeoutError("Synthetic timeout"),
    ):
        with patch.object(source, "urlopen", side_effect=error):
            expect_error(source.fetch_listing_page, type(error))
    print("network_failures_propagate: PASS")


if __name__ == "__main__":
    main()
