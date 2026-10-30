"""Deterministic financial classification; only source-verified sales count as actual."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class SaleAmount:
    amount_cents: int
    state: Literal["DRAFT", "UNPAID", "SETTLED", "REFUNDED"]
    verified_source: bool
    fees_cents: int | None = None
    costs_cents: int | None = None

    def __post_init__(self):
        if self.amount_cents < 0 or any(
            x is not None and x < 0 for x in (self.fees_cents, self.costs_cents)
        ):
            raise ValueError("Amounts must be nonnegative integer cents")


def revenue_summary(sales: list[SaleAmount], target_cents: int = 50000):
    if target_cents <= 0:
        raise ValueError("Target must be positive")
    actual = [sale for sale in sales if sale.state == "SETTLED" and sale.verified_source]
    gross = sum(sale.amount_cents for sale in actual)
    fees = (
        sum(sale.fees_cents for sale in actual)
        if all(s.fees_cents is not None for s in actual)
        else None
    )
    costs = (
        sum(sale.costs_cents for sale in actual)
        if all(s.costs_cents is not None for s in actual)
        else None
    )
    margin = gross - costs if costs is not None else None
    profit = margin - fees if margin is not None and fees is not None else None
    return {
        "target_cents": target_cents,
        "gross_sales_cents": gross,
        "fees_cents": fees,
        "costs_cents": costs,
        "gross_margin_cents": margin,
        "profit_cents": profit,
        "remaining_cents": max(0, target_cents - gross),
        "progress_percent": str((Decimal(gross) * 100 / target_cents).quantize(Decimal("0.01"))),
    }
