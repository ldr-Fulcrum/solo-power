from backend.app.financials import SaleAmount, revenue_summary
from backend.app.integrations import MissingSourceAdapter


def test_weekly_goal_math_and_financial_classifications():
    sales = [
        SaleAmount(20000, "SETTLED", True, 500, 7000),
        SaleAmount(12550, "SETTLED", True, 200, 3000),
        SaleAmount(90000, "DRAFT", True),
        SaleAmount(80000, "UNPAID", True),
        SaleAmount(50000, "SETTLED", False),
        SaleAmount(5000, "REFUNDED", True),
    ]
    summary = revenue_summary(sales)
    assert summary == {
        "target_cents": 50000,
        "gross_sales_cents": 32550,
        "fees_cents": 700,
        "costs_cents": 10000,
        "gross_margin_cents": 22550,
        "profit_cents": 21850,
        "remaining_cents": 17450,
        "progress_percent": "65.10",
    }


def test_unknown_costs_do_not_become_zero_profit_estimates():
    summary = revenue_summary([SaleAmount(10000, "SETTLED", True)])
    assert summary["gross_sales_cents"] == 10000
    assert summary["fees_cents"] is None and summary["profit_cents"] is None


def test_missing_bakery_adapter_does_not_invent_stock_catalog_or_recipe():
    adapter = MissingSourceAdapter("scaled_baking")
    for snapshot in (adapter.products(), adapter.recipes(), adapter.inventory()):
        assert snapshot.status == "MISSING_SOURCE"
        assert snapshot.records is None and snapshot.evidence_reference is None
