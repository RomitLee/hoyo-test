"""Per-account gem calculator settings and input validation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

GEM_LEVELS = {"normal": range(2, 13), "star": range(2, 9), "dust": range(2, 9)}
DEFAULT_RATIOS = {
    "normal": {str(level): value for level, value in enumerate(
        (1.15, 1.16, 1.17, 1.18, 1.19, 1.19, 1.20, 1.22, 1.22, 1.23, 1.24), start=2)},
    "star": {str(level): value for level, value in enumerate(
        (1.10, 1.10, 1.10, 1.12, 1.14, 1.16, 1.16), start=2)},
    "dust": {str(level): value for level, value in enumerate(
        (1.10, 1.10, 1.10, 1.12, 1.14, 1.16, 1.16), start=2)},
}
DEFAULT_PRICES = {"normal": 100000.0, "star": 141111.0, "dust": 322222.0}


class GemZone(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    name: str = Field(min_length=1, max_length=32)
    quote_yuan: float = Field(gt=0, le=100000)
    purchase_wan: float = Field(default=9000, ge=0, le=100000000)
    margin_percent: float = Field(default=17, ge=0, le=1000)
    account_count: int = Field(default=3, ge=1, le=100, strict=True)
    target_profit_yuan: float = Field(default=30, ge=0, le=100000000)


class GemSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(default=0, ge=0, strict=True)
    zones: list[GemZone] = Field(default_factory=list, max_length=50)
    price_ratios: dict[str, dict[str, float]] = Field(default_factory=lambda: {
        kind: ratios.copy() for kind, ratios in DEFAULT_RATIOS.items()})
    synthesis_prices: dict[str, float] = Field(default_factory=lambda: DEFAULT_PRICES.copy())
    average_sale_prices: dict[str, float] = Field(default_factory=lambda: {
        kind: round(price * 1.27) for kind, price in DEFAULT_PRICES.items()})

    @model_validator(mode="after")
    def validate_calculator(self) -> GemSettings:
        if len({zone.id for zone in self.zones}) != len(self.zones):
            raise ValueError("区服编号不能重复")
        if len({zone.name for zone in self.zones}) != len(self.zones):
            raise ValueError("区服名称不能重复")
        if set(self.price_ratios) != set(GEM_LEVELS):
            raise ValueError("宝石类型不完整")
        for kind, levels in GEM_LEVELS.items():
            ratios = self.price_ratios[kind]
            if set(ratios) != {str(level) for level in levels}:
                raise ValueError("宝石等级售价比例不完整")
            if any(not 0 <= ratio <= 100 for ratio in ratios.values()):
                raise ValueError("售价比例需在 0～100 之间")
        for prices in (self.synthesis_prices, self.average_sale_prices):
            if set(prices) != set(GEM_LEVELS) or any(not 0 <= price <= 1_000_000_000 for price in prices.values()):
                raise ValueError("宝石价格数据无效")
        return self


def default_gem_settings() -> dict:
    """New accounts start with no invented server quote."""
    return GemSettings().model_dump()
