"""Application-side bakery adapter contracts. No live external tools are registered."""

from typing import Literal, Protocol

from pydantic import Field

from backend.app.domain import Model


class ProductReference(Model):
    source_id: str
    source_version: str
    name: str
    price_cents: int | None = Field(default=None, ge=0)
    price_status: Literal["VERIFIED", "BANK_REVIEW"]


class RecipeReference(Model):
    source_id: str
    source_version: str
    recipe_id: str
    provenance_reference: str


class StockReference(Model):
    source_id: str
    product_id: str
    observed_at: str
    available_units: int = Field(ge=0)
    provenance_reference: str


class AdapterSnapshot[T](Model):
    adapter_id: str
    status: Literal["VERIFIED", "MISSING_SOURCE", "UNAVAILABLE", "TEST_DOUBLE"]
    records: list[T] | None = None
    source_version: str | None = None
    evidence_reference: str | None = None


class BakeryAdapter(Protocol):
    def products(self) -> AdapterSnapshot[ProductReference]: ...
    def recipes(self) -> AdapterSnapshot[RecipeReference]: ...
    def inventory(self) -> AdapterSnapshot[StockReference]: ...


class MissingSourceAdapter:
    """Unknown stock/catalog stays unknown, rather than becoming invented empty stock."""

    def __init__(self, adapter_id: str):
        self.adapter_id = adapter_id

    def products(self):
        return AdapterSnapshot[ProductReference](
            adapter_id=self.adapter_id, status="MISSING_SOURCE"
        )

    def recipes(self):
        return AdapterSnapshot[RecipeReference](adapter_id=self.adapter_id, status="MISSING_SOURCE")

    def inventory(self):
        return AdapterSnapshot[StockReference](adapter_id=self.adapter_id, status="MISSING_SOURCE")
