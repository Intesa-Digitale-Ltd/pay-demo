from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .catalog_sync import backward_sync, forward_sync, forward_sync_product, new_partner_id
from .intesapay import IntesaPayClient, IntesaPayError
from .models import CatalogCoupon, CatalogPrice, CatalogProduct
from .views import _cfg, _store_result


def _dec(raw: str, field: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{field} must be a number") from None
    if value <= 0:
        raise ValueError(f"{field} must be greater than 0")
    return value


def _try_push(request: HttpRequest, product: CatalogProduct) -> None:
    if not _cfg()["ready"]:
        return
    try:
        log = forward_sync_product(product)
        _store_result(request, f"Sync {product.partner_id}", {"log": log})
    except IntesaPayError as e:
        product.sync_error = str(e)
        product.save(update_fields=["sync_error", "updated_at"])
        _store_result(request, f"Sync failed ({e.status})", e.body)
        messages.error(request, f"Saved locally, but IntesaPay sync failed: {e}")


def _archive_remote_product(product: CatalogProduct) -> None:
    client = IntesaPayClient()
    if not product.intesapay_id:
        return
    if product.uses_unmanaged_sync:
        client.delete_unmanaged_product(product.partner_id)
        return
    client.update_product(
        product.intesapay_id,
        name=product.name,
        description=product.description or None,
        active=False,
    )


def _archive_remote_price(price: CatalogPrice) -> None:
    client = IntesaPayClient()
    if price.intesapay_id:
        client.archive_price(price.intesapay_id)
        return
    if price.product.uses_unmanaged_sync:
        client.delete_unmanaged_price(price.partner_id)


def _archive_remote_coupon(coupon: CatalogCoupon) -> None:
    client = IntesaPayClient()
    if coupon.intesapay_id:
        client.archive_coupon(coupon.intesapay_id)
        return
    if coupon.product.uses_unmanaged_sync:
        client.delete_unmanaged_coupon(coupon.partner_id)


def _product_url(product: CatalogProduct) -> HttpResponse:
    return redirect("catalog_product", pk=product.pk)


@require_GET
def catalog(request: HttpRequest) -> HttpResponse:
    products = CatalogProduct.objects.prefetch_related("prices", "coupons")
    return render(
        request,
        "harness/catalog.html",
        {
            "cfg": _cfg(),
            "nav": "catalog",
            "products": products,
            "partner_count": products.filter(origin=CatalogProduct.ORIGIN_PARTNER).count(),
            "mirror_count": products.filter(origin=CatalogProduct.ORIGIN_INTESAPAY).count(),
            "last_result": request.session.pop("last_result", None),
            "open_new": request.GET.get("new") == "1",
        },
    )


@require_GET
def catalog_product(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(CatalogProduct.objects.prefetch_related("prices", "coupons"), pk=pk)
    return render(
        request,
        "harness/catalog_product.html",
        {
            "cfg": _cfg(),
            "nav": "catalog",
            "product": product,
            "last_result": request.session.pop("last_result", None),
        },
    )


@require_POST
def catalog_forward(request: HttpRequest) -> HttpResponse:
    if not _cfg()["ready"]:
        messages.error(request, "Set INTESAPAY_API_KEY and INTESAPAY_ACCOUNT_ID first")
        return redirect("catalog")
    result = forward_sync()
    _store_result(request, "Forward sync (this app → IntesaPay)", result)
    if result["ok"]:
        messages.success(request, f"Forward sync pushed {result['pushed']} product(s)")
    else:
        messages.error(request, f"Forward sync finished with {len(result['errors'])} error(s)")
    return redirect("catalog")


@require_POST
def catalog_backward(request: HttpRequest) -> HttpResponse:
    if not _cfg()["ready"]:
        messages.error(request, "Set INTESAPAY_API_KEY and INTESAPAY_ACCOUNT_ID first")
        return redirect("catalog")
    try:
        result = backward_sync()
        _store_result(request, "Backward sync (IntesaPay → this app)", result)
        messages.success(request, f"Backward sync pulled {result['pulled']} IntesaPay product(s)")
    except IntesaPayError as e:
        _store_result(request, f"Backward sync failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("catalog")


@require_POST
def catalog_create_product(request: HttpRequest) -> HttpResponse:
    name = request.POST.get("name", "").strip()
    partner_id = request.POST.get("partner_id", "").strip()
    description = request.POST.get("description", "").strip()
    if not name:
        messages.error(request, "Product name is required")
        return redirect("catalog")
    partner_id = partner_id or new_partner_id("sku")
    if CatalogProduct.objects.filter(partner_id=partner_id).exists():
        messages.error(request, f"Partner id {partner_id} is already used")
        return redirect("catalog")
    product = CatalogProduct.objects.create(
        partner_id=partner_id,
        name=name,
        description=description,
        active=True,
        origin=CatalogProduct.ORIGIN_PARTNER,
    )
    messages.success(request, f"Created {name}")
    _try_push(request, product)
    return _product_url(product)


@require_POST
def catalog_update_product(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(CatalogProduct, pk=pk)
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "Product name is required")
        return _product_url(product)
    product.name = name
    product.description = request.POST.get("description", "").strip()
    product.active = request.POST.get("active") == "on"
    product.save()
    messages.success(request, f"Updated {product.name}")
    _try_push(request, product)
    return _product_url(product)


@require_POST
def catalog_delete_product(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(CatalogProduct, pk=pk)
    name = product.name
    if product.intesapay_id:
        try:
            _archive_remote_product(product)
        except IntesaPayError as e:
            messages.error(request, f"IntesaPay delete failed: {e}")
            _store_result(request, f"delete product failed ({e.status})", e.body)
            return redirect("catalog")
    product.delete()
    messages.success(request, f"Deleted {name}")
    return redirect("catalog")


@require_POST
def catalog_add_price(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(CatalogProduct, pk=pk)
    try:
        amount = _dec(request.POST.get("unit_amount", ""), "Amount")
    except ValueError as e:
        messages.error(request, str(e))
        return _product_url(product)
    pricing_type = request.POST.get("pricing_type", CatalogPrice.ONE_TIME).strip() or CatalogPrice.ONE_TIME
    interval = request.POST.get("billing_interval", "").strip()
    if pricing_type == CatalogPrice.RECURRING and not interval:
        messages.error(request, "Recurring prices need a billing interval")
        return _product_url(product)
    partner_id = request.POST.get("partner_id", "").strip() or new_partner_id("price")
    CatalogPrice.objects.create(
        product=product,
        partner_id=partner_id,
        unit_amount=amount,
        currency=(request.POST.get("currency") or "UGX").strip().upper()[:3],
        pricing_type=pricing_type,
        billing_interval=interval if pricing_type == CatalogPrice.RECURRING else "",
        interval_count=int(request.POST.get("interval_count") or 1),
        active=True,
    )
    messages.success(request, f"Price {partner_id} added")
    _try_push(request, product)
    return _product_url(product)


@require_POST
def catalog_delete_price(request: HttpRequest, pk: int) -> HttpResponse:
    price = get_object_or_404(CatalogPrice, pk=pk)
    product = price.product
    if price.intesapay_id or product.uses_unmanaged_sync:
        try:
            _archive_remote_price(price)
        except IntesaPayError as e:
            messages.error(request, f"IntesaPay price delete failed: {e}")
            _store_result(request, f"delete price failed ({e.status})", e.body)
            return _product_url(product)
    product_pk = product.pk
    price.delete()
    messages.success(request, "Price removed")
    return redirect("catalog_product", pk=product_pk)


@require_POST
def catalog_add_coupon(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(CatalogProduct, pk=pk)
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "Coupon name is required")
        return _product_url(product)
    percent_raw = request.POST.get("percent_off", "").strip()
    amount_raw = request.POST.get("amount_off", "").strip()
    percent_off = None
    amount_off = None
    try:
        if percent_raw:
            percent_off = _dec(percent_raw, "Percent off")
        elif amount_raw:
            amount_off = _dec(amount_raw, "Amount off")
        else:
            messages.error(request, "Set percent off or amount off")
            return _product_url(product)
    except ValueError as e:
        messages.error(request, str(e))
        return _product_url(product)
    duration = request.POST.get("duration", "ONCE").strip() or "ONCE"
    months = request.POST.get("duration_in_months", "").strip()
    CatalogCoupon.objects.create(
        product=product,
        partner_id=request.POST.get("partner_id", "").strip() or new_partner_id("coup"),
        name=name,
        code=request.POST.get("code", "").strip().upper(),
        percent_off=percent_off,
        amount_off=amount_off,
        currency=(request.POST.get("currency") or "UGX").strip().upper()[:3] if amount_off is not None else "",
        duration=duration,
        duration_in_months=int(months) if months and duration == "REPEATING" else None,
        partner_price_id=request.POST.get("partner_price_id", "").strip(),
        active=True,
    )
    messages.success(request, f"Coupon {name} added")
    _try_push(request, product)
    return _product_url(product)


@require_POST
def catalog_delete_coupon(request: HttpRequest, pk: int) -> HttpResponse:
    coupon = get_object_or_404(CatalogCoupon, pk=pk)
    product = coupon.product
    if coupon.intesapay_id or product.uses_unmanaged_sync:
        try:
            _archive_remote_coupon(coupon)
        except IntesaPayError as e:
            messages.error(request, f"IntesaPay coupon delete failed: {e}")
            _store_result(request, f"delete coupon failed ({e.status})", e.body)
            return _product_url(product)
    product_pk = product.pk
    coupon.delete()
    messages.success(request, "Coupon removed")
    return redirect("catalog_product", pk=product_pk)


@require_POST
def catalog_push_product(request: HttpRequest, pk: int) -> HttpResponse:
    product = get_object_or_404(CatalogProduct, pk=pk)
    if not _cfg()["ready"]:
        messages.error(request, "Set INTESAPAY_API_KEY and INTESAPAY_ACCOUNT_ID first")
        return _product_url(product)
    try:
        log = forward_sync_product(product)
        _store_result(request, f"Push {product.partner_id}", {"log": log})
        messages.success(request, f"Pushed {product.name} to IntesaPay")
    except IntesaPayError as e:
        product.sync_error = str(e)
        product.save(update_fields=["sync_error", "updated_at"])
        _store_result(request, f"Push failed ({e.status})", e.body)
        messages.error(request, str(e))
    return _product_url(product)
