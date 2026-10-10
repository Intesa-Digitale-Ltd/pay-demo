"""Push local catalog to IntesaPay (forward) and pull IntesaPay into local rows (backward)."""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils import timezone
from django.utils.text import slugify

from .intesapay import IntesaPayClient, IntesaPayError
from .models import CatalogCoupon, CatalogPrice, CatalogProduct


def new_partner_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def as_product_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("value", "content", "products"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _slug(value: str, fallback: str) -> str:
    cleaned = slugify(value, allow_unicode=False).replace("-", "_")[:72]
    return cleaned or fallback


def _price_payload(price: CatalogPrice) -> dict[str, Any]:
    interval = price.billing_interval or None
    if price.pricing_type != CatalogPrice.RECURRING:
        interval = None
    return {
        "unit_amount": float(price.unit_amount),
        "currency": price.currency or "UGX",
        "pricing_type": price.pricing_type,
        "billing_interval": interval,
        "interval_count": price.interval_count or 1,
    }


def _coupon_kwargs(coupon: CatalogCoupon) -> dict[str, Any]:
    return {
        "name": coupon.name,
        "percent_off": float(coupon.percent_off) if coupon.percent_off is not None else None,
        "amount_off": float(coupon.amount_off) if coupon.amount_off is not None else None,
        "currency": coupon.currency or None,
        "duration": coupon.duration or "ONCE",
        "duration_in_months": coupon.duration_in_months,
        "max_redemptions": coupon.max_redemptions,
        "code": coupon.code or None,
    }


def _push_public_id_product(product: CatalogProduct, client: IntesaPayClient, now) -> list[str]:
    log: list[str] = []
    if not product.intesapay_id:
        remote = client.create_product(name=product.name, description=product.description or None)
        product.intesapay_id = (remote or {}).get("id") or product.intesapay_id
        product.remote_source = (remote or {}).get("source") or "MANAGED"
        log.append(f"created {product.partner_id} → {product.intesapay_id}")
    else:
        remote = client.update_product(
            product.intesapay_id,
            name=product.name,
            description=product.description or None,
            active=product.active,
        )
        product.remote_source = (remote or {}).get("source") or product.remote_source or "MANAGED"
        log.append(f"product {product.intesapay_id}")
    product.sync_error = ""
    product.synced_at = now
    product.save(update_fields=["intesapay_id", "remote_source", "sync_error", "synced_at", "updated_at"])

    for price in product.prices.all():
        payload = _price_payload(price)
        if price.intesapay_id:
            remote_price = client.update_price(price.intesapay_id, **payload)
        else:
            remote_price = client.create_price(product.intesapay_id, **payload)
        price.intesapay_id = (remote_price or {}).get("id") or price.intesapay_id
        price.synced_at = now
        price.save(update_fields=["intesapay_id", "synced_at"])
        log.append(f"  price {price.partner_id} → {price.intesapay_id}")

    for coupon in product.coupons.all():
        kwargs = _coupon_kwargs(coupon)
        if coupon.intesapay_id:
            log.append(f"  coupon {coupon.partner_id} already {coupon.intesapay_id}")
            continue
        scoped = None
        if coupon.partner_price_id:
            scoped_price = product.prices.filter(partner_id=coupon.partner_price_id).first()
            scoped = scoped_price.intesapay_id if scoped_price else None
        remote_coupon = client.create_coupon(product.intesapay_id, price_id=scoped, **kwargs)
        coupon.intesapay_id = (remote_coupon or {}).get("id") or coupon.intesapay_id
        coupon.synced_at = now
        coupon.save(update_fields=["intesapay_id", "synced_at"])
        log.append(f"  coupon {coupon.partner_id} → {coupon.intesapay_id}")
    return log


def _push_unmanaged_product(product: CatalogProduct, client: IntesaPayClient, now) -> list[str]:
    log: list[str] = []
    remote = client.upsert_unmanaged_product(
        product.partner_id,
        name=product.name,
        description=product.description or None,
        active=product.active,
    )
    product.intesapay_id = (remote or {}).get("id") or product.intesapay_id
    product.remote_source = (remote or {}).get("source") or "UNMANAGED"
    product.sync_error = ""
    product.synced_at = now
    product.save(update_fields=["intesapay_id", "remote_source", "sync_error", "synced_at", "updated_at"])
    log.append(f"product {product.partner_id} → {product.intesapay_id}")

    for price in product.prices.all():
        payload = _price_payload(price)
        remote_price = client.upsert_unmanaged_price(product.partner_id, price.partner_id, **payload)
        price.intesapay_id = (remote_price or {}).get("id") or price.intesapay_id
        price.synced_at = now
        price.save(update_fields=["intesapay_id", "synced_at"])
        log.append(f"  price {price.partner_id} → {price.intesapay_id}")

    for coupon in product.coupons.all():
        kwargs = _coupon_kwargs(coupon)
        remote_coupon = client.upsert_unmanaged_coupon(
            product.partner_id,
            coupon.partner_id,
            partner_price_id=coupon.partner_price_id or None,
            active=coupon.active,
            **kwargs,
        )
        coupon.intesapay_id = (remote_coupon or {}).get("id") or coupon.intesapay_id
        coupon.synced_at = now
        coupon.save(update_fields=["intesapay_id", "synced_at"])
        log.append(f"  coupon {coupon.partner_id} → {coupon.intesapay_id}")
    return log


def forward_sync_product(product: CatalogProduct, client: IntesaPayClient | None = None) -> list[str]:
    """Push one product (and its prices/coupons) to IntesaPay."""
    client = client or IntesaPayClient()
    now = timezone.now()
    if product.uses_unmanaged_sync:
        return _push_unmanaged_product(product, client, now)
    return _push_public_id_product(product, client, now)


def forward_sync(client: IntesaPayClient | None = None) -> dict[str, Any]:
    """This app → IntesaPay: upsert every local product/price/coupon."""
    client = client or IntesaPayClient()
    log: list[str] = []
    errors: list[str] = []
    pushed = 0

    products = CatalogProduct.objects.all().prefetch_related("prices", "coupons")
    for product in products:
        try:
            log.extend(forward_sync_product(product, client))
            pushed += 1
        except IntesaPayError as e:
            product.sync_error = str(e)
            product.save(update_fields=["sync_error", "updated_at"])
            errors.append(f"{product.partner_id}: {e}")
            log.append(f"FAIL {product.partner_id}: {e}")

    return {"ok": not errors, "pushed": pushed, "errors": errors, "log": log}


def backward_sync(client: IntesaPayClient | None = None) -> dict[str, Any]:
    """IntesaPay → partner: upsert a local mirror of the live catalog."""
    client = client or IntesaPayClient()
    now = timezone.now()
    remote_products = as_product_list(client.list_products())
    log: list[str] = []
    seen_product_ids: set[str] = set()

    for row in remote_products:
        source = str(row.get("source") or "")
        intesapay_id = str(row.get("id") or "")
        partner_id = str(row.get("partnerProductId") or "").strip()
        origin = CatalogProduct.ORIGIN_PARTNER if source == "UNMANAGED" and partner_id else CatalogProduct.ORIGIN_INTESAPAY
        if not partner_id:
            partner_id = intesapay_id or new_partner_id("prod")
        seen_product_ids.add(partner_id)

        product, created = CatalogProduct.objects.update_or_create(
            partner_id=partner_id,
            defaults={
                "name": row.get("name") or partner_id,
                "description": row.get("description") or "",
                "active": bool(row.get("active", True)),
                "origin": origin,
                "intesapay_id": intesapay_id,
                "remote_source": source,
                "sync_error": "",
                "synced_at": now,
            },
        )
        log.append(f"{'created' if created else 'updated'} {partner_id} ({source or origin})")

        seen_prices: set[str] = set()
        for price_row in row.get("prices") or []:
            if not isinstance(price_row, dict):
                continue
            price_partner = str(price_row.get("partnerPriceId") or "").strip() or str(price_row.get("id") or "")
            if not price_partner:
                continue
            seen_prices.add(price_partner)
            amount = _dec(price_row.get("unitAmount")) or Decimal("0")
            CatalogPrice.objects.update_or_create(
                product=product,
                partner_id=price_partner,
                defaults={
                    "unit_amount": amount,
                    "currency": str(price_row.get("currency") or "UGX")[:3],
                    "pricing_type": str(price_row.get("pricingType") or CatalogPrice.ONE_TIME),
                    "billing_interval": str(price_row.get("billingInterval") or ""),
                    "interval_count": int(price_row.get("intervalCount") or 1),
                    "active": bool(price_row.get("active", True)),
                    "intesapay_id": str(price_row.get("id") or ""),
                    "synced_at": now,
                },
            )

        seen_coupons: set[str] = set()
        for coupon_row in row.get("coupons") or []:
            if not isinstance(coupon_row, dict):
                continue
            coupon_partner = str(coupon_row.get("partnerCouponId") or "").strip() or str(coupon_row.get("id") or "")
            if not coupon_partner:
                continue
            seen_coupons.add(coupon_partner)
            scoped_price = str(coupon_row.get("partnerPriceId") or "").strip()
            CatalogCoupon.objects.update_or_create(
                product=product,
                partner_id=coupon_partner,
                defaults={
                    "name": coupon_row.get("name") or coupon_partner,
                    "code": coupon_row.get("code") or "",
                    "percent_off": _dec(coupon_row.get("percentOff")),
                    "amount_off": _dec(coupon_row.get("amountOff")),
                    "currency": str(coupon_row.get("currency") or ""),
                    "duration": str(coupon_row.get("duration") or "ONCE"),
                    "duration_in_months": coupon_row.get("durationInMonths"),
                    "max_redemptions": coupon_row.get("maxRedemptions"),
                    "partner_price_id": scoped_price,
                    "active": bool(coupon_row.get("active", True)),
                    "intesapay_id": str(coupon_row.get("id") or ""),
                    "synced_at": now,
                },
            )

        if origin == CatalogProduct.ORIGIN_INTESAPAY:
            product.prices.exclude(partner_id__in=seen_prices).filter(intesapay_id__gt="").delete()
            product.coupons.exclude(partner_id__in=seen_coupons).filter(intesapay_id__gt="").delete()

    stale = CatalogProduct.objects.filter(origin=CatalogProduct.ORIGIN_INTESAPAY).exclude(
        partner_id__in=seen_product_ids
    )
    removed = stale.count()
    stale.delete()
    if removed:
        log.append(f"removed {removed} local mirror(s) no longer on IntesaPay")

    return {
        "ok": True,
        "pulled": len(remote_products),
        "log": log,
        "errors": [],
    }


def suggest_product_id(name: str) -> str:
    return _slug(name, "sku") + "_" + uuid.uuid4().hex[:6]
