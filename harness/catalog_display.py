"""Normalize catalog API payloads for partner pricing UI."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


INTERVAL_LABELS = {
    "DAY": "day",
    "WEEK": "week",
    "MONTH": "month",
    "YEAR": "year",
}


def format_money(amount: Any, currency: str | None = "UGX") -> str:
    try:
        value = Decimal(str(amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except Exception:
        return f"{amount} {currency or ''}".strip()
    cur = (currency or "UGX").upper()
    formatted = f"{value:,.0f}"
    return f"{cur} {formatted}"


def schedule_label(price: dict) -> str:
    pricing_type = (price.get("pricingType") or "").upper()
    if pricing_type == "ONE_TIME":
        return "one-time"
    interval = (price.get("billingInterval") or "MONTH").upper()
    count = int(price.get("intervalCount") or 1)
    unit = INTERVAL_LABELS.get(interval, interval.lower())
    if count == 1:
        return f"per {unit}"
    return f"every {count} {unit}s"


def coupon_badge(coupon: dict) -> str:
    if coupon.get("percentOff") is not None:
        pct = Decimal(str(coupon["percentOff"]))
        if pct == pct.to_integral_value():
            off = f"{int(pct)}% off"
        else:
            off = f"{format(pct, 'f').rstrip('0').rstrip('.')}% off"
    elif coupon.get("amountOff") is not None:
        off = f"{format_money(coupon['amountOff'], coupon.get('currency') or 'UGX')} off"
    else:
        off = "Discount"
    duration = (coupon.get("duration") or "ONCE").upper()
    if duration == "ONCE":
        when = "first period"
    elif duration == "FOREVER":
        when = "forever"
    else:
        months = coupon.get("durationInMonths")
        when = f"{months} mo" if months else "limited"
    return f"{off} · {when}"


def discounted_amount(list_amount: Any, coupon: dict) -> Decimal | None:
    try:
        base = Decimal(str(list_amount))
    except Exception:
        return None
    if coupon.get("percentOff") is not None:
        pct = Decimal(str(coupon["percentOff"]))
        return (base * (Decimal("100") - pct) / Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    if coupon.get("amountOff") is not None:
        off = Decimal(str(coupon["amountOff"]))
        return max(Decimal("0"), base - off)
    return None


def apply_tax(base: Any, price: dict) -> tuple[Decimal, Decimal, Decimal, str | None]:
    """Returns subtotal, tax, total, label."""
    try:
        amount = Decimal(str(base)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except Exception:
        amount = Decimal("0")
    pct_raw = price.get("taxPercent")
    behavior = (price.get("taxBehavior") or "").upper()
    if pct_raw is None or not behavior or not price.get("taxRateId"):
        return amount, Decimal("0"), amount, None
    pct = Decimal(str(pct_raw))
    if behavior == "EXCLUSIVE":
        tax = (amount * pct / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        total = amount + tax
        label = f"excl. {format(pct, 'f').rstrip('0').rstrip('.')}% tax"
        return amount, tax, total, label
    # Inclusive
    factor = Decimal("1") + (pct / Decimal("100"))
    subtotal = (amount / factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    tax = amount - subtotal
    label = f"incl. {format(pct, 'f').rstrip('0').rstrip('.')}% tax"
    return subtotal, tax, amount, label


def build_pricing_cards(products: list[dict] | Any) -> list[dict]:
    if not isinstance(products, list):
        return []

    cards: list[dict] = []
    for raw in products:
        if not isinstance(raw, dict) or not raw.get("active", True):
            continue

        active_prices = [
            p for p in (raw.get("prices") or []) if isinstance(p, dict) and p.get("active", True)
        ]
        # Prefer longer intervals first for display (year → day), then by amount
        order = {"YEAR": 0, "MONTH": 1, "WEEK": 2, "DAY": 3}
        active_prices.sort(
            key=lambda p: (
                0 if (p.get("pricingType") or "").upper() == "RECURRING" else 1,
                order.get((p.get("billingInterval") or "").upper(), 9),
                float(p.get("unitAmount") or 0),
            )
        )

        all_coupons = [c for c in (raw.get("coupons") or []) if isinstance(c, dict)]
        valid_coupons = [c for c in all_coupons if c.get("valid") and c.get("active", True)]
        # If this product has any price-scoped coupon (e.g. monthly onboarding),
        # only surface those — never fall back to product-wide promos like SAVE20.
        has_price_scoped = any(c.get("priceId") for c in all_coupons if c.get("active", True))
        if has_price_scoped:
            display_coupons = [c for c in valid_coupons if c.get("priceId")]
        else:
            display_coupons = valid_coupons

        price_rows = []
        offer_ids: set[str] = set()
        for price in active_prices:
            applicable = [
                c
                for c in display_coupons
                if not c.get("priceId") or c.get("priceId") == price.get("id")
            ]
            best = applicable[0] if applicable else None
            if best and best.get("id"):
                offer_ids.add(best["id"])
            list_amount = price.get("unitAmount")
            intro = discounted_amount(list_amount, best) if best else None
            pre_tax = intro if intro is not None else list_amount
            _sub, _tax, total, tax_label = apply_tax(pre_tax, price)
            price_rows.append(
                {
                    "id": price.get("id"),
                    "amount_label": format_money(list_amount, price.get("currency")),
                    "schedule": schedule_label(price),
                    "pricing_type": price.get("pricingType"),
                    "intro_amount_label": format_money(intro, price.get("currency"))
                    if intro is not None and best
                    else None,
                    "momo_total_label": format_money(total, price.get("currency")),
                    "tax_label": tax_label,
                    "coupon_id": best.get("id") if best else None,
                    "coupon_code": best.get("code") if best else None,
                    "coupon_badge": coupon_badge(best) if best else None,
                    "coupons": [
                        {
                            "id": c.get("id"),
                            "name": c.get("name"),
                            "code": c.get("code"),
                            "badge": coupon_badge(c),
                            "new_customers_only": bool(c.get("newCustomersOnly")),
                        }
                        for c in applicable
                    ],
                }
            )

        offers = [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "code": c.get("code"),
                "badge": coupon_badge(c),
                "new_customers_only": bool(c.get("newCustomersOnly")),
            }
            for c in display_coupons
            if c.get("id") in offer_ids
        ]
        featured = bool(offers) or len(active_prices) > 1
        cards.append(
            {
                "id": raw.get("id"),
                "name": raw.get("name") or "Plan",
                "description": raw.get("description") or "",
                "source": raw.get("source"),
                "featured": featured,
                "prices": price_rows,
                "offers": offers,
            }
        )

    # Featured (has offers / multi-price) first, then by name
    cards.sort(key=lambda c: (0 if c["featured"] else 1, c["name"].lower()))
    return cards
