from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.ventures import discovery_empire_flippers as source
from app.ventures import opportunities
from app.ventures.models import BusinessType, OpportunityStatus


def page(number, ids):
    return {
        "source": "empire_flippers",
        "source_url": f"https://example.invalid/page/{number}",
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "page": number,
        "pages": 2,
        "limit": 2,
        "total_reported": 3,
        "listings": [{"id": value} for value in ids],
    }


def expect_failure(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def test_pagination():
    first = page(1, ["a", "b"])
    second = page(2, ["c"])

    with (
        patch.object(
            source, "fetch_listing_page",
            side_effect=[first, second],
        ) as fetch,
        patch("time.sleep") as sleep,
    ):
        result = source.fetch_all_listings(limit=2)
        assert result["fetched_count"] == 3
        assert result["pages_fetched"] == 2
        assert [item["id"] for item in result["listings"]] == [
            "a", "b", "c"
        ]
        assert fetch.call_count == 2
        sleep.assert_called_once_with(1.1)

    for broken in (
        page(2, ["a"]),
        page(2, []),
        dict(second, total_reported=4),
    ):
        with (
            patch.object(
                source, "fetch_listing_page",
                side_effect=[deepcopy(first), broken],
            ),
            patch("time.sleep"),
        ):
            expect_failure(
                lambda: source.fetch_all_listings(limit=2)
            )

    with patch.object(
        source, "fetch_listing_page", return_value=first
    ) as fetch:
        expect_failure(
            lambda: source.fetch_all_listings(limit=2, max_pages=1)
        )
        assert fetch.call_count == 1

    print("pagination_rate_limit_and_incomplete_scan_rejection: PASS")


def test_concurrent_writes():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        with (
            patch.object(
                opportunities, "VENTURES_STATE_DIRECTORY", root
            ),
            patch.object(
                opportunities, "OPPORTUNITIES_FILE",
                root / "opportunities.json",
            ),
        ):
            def create(index):
                return opportunities.create_opportunity(
                    name=f"Synthetic {index}",
                    business_type=BusinessType.SAAS,
                    asking_price_usd=10_000,
                    source=f"synthetic:{index}",
                    source_url=f"https://example.invalid/{index}",
                    deduplicate_source=True,
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                repeated = list(pool.map(create, [0] * 16))

            assert len({item.opportunity_id for item in repeated}) == 1
            assert len(opportunities.list_opportunities()) == 1
            target = repeated[0].opportunity_id

            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [
                    pool.submit(create, index)
                    for index in range(1, 17)
                ]
                futures.append(pool.submit(
                    opportunities.update_opportunity_status,
                    target,
                    OpportunityStatus.SCREENING,
                ))
                for future in futures:
                    future.result()

            assert len(opportunities.list_opportunities()) == 17
            assert opportunities.get_opportunity(
                target
            ).status == OpportunityStatus.SCREENING

            assert create(0).opportunity_id == target
            assert opportunities.get_opportunity(
                target
            ).status == OpportunityStatus.SCREENING

    print("concurrent_intake_and_status_preservation: PASS")


if __name__ == "__main__":
    test_pagination()
    test_concurrent_writes()
