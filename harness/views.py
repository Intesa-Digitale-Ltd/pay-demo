from __future__ import annotations

import json

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .catalog_display import build_pricing_cards
from .intesapay import IntesaPayClient, IntesaPayError
from .models import WebhookEvent
from .webhook_sig import verify_signature


def _intesapay_flash(e: IntesaPayError) -> str:
    body = e.body if isinstance(e.body, dict) else {}
    code = body.get("code") if isinstance(body, dict) else None
    message = body.get("message") if isinstance(body, dict) else None
    if code == "INSUFFICIENT_FUNDS":
        return f"{code}: available cannot cover payout + fee"
    if code == "CONCURRENT_REQUEST_IN_PROGRESS":
        return f"{code}: same Idempotency-Key already in flight — wait, then replay"
    if code and message:
        return f"{code}: {message}"
    return str(e)


def _cfg() -> dict:
    return {
        "base_url": settings.INTESAPAY_BASE_URL,
        "account_id": settings.INTESAPAY_ACCOUNT_ID,
        "has_api_key": bool(settings.INTESAPAY_API_KEY),
        "has_webhook_secret": bool(settings.INTESAPAY_WEBHOOK_SECRET),
        "ready": bool(settings.INTESAPAY_API_KEY and settings.INTESAPAY_ACCOUNT_ID),
    }


def _redirect_after_action(request: HttpRequest, default: str = "ops") -> HttpResponse:
    nxt = (request.POST.get("next") or "").strip()
    if nxt == "pricing":
        return redirect("pricing")
    return redirect(default)


@require_GET
def pricing(request: HttpRequest) -> HttpResponse:
    cards: list = []
    fetch_error: str | None = None
    cfg = _cfg()
    if cfg["ready"]:
        try:
            products = IntesaPayClient().list_products()
            cards = build_pricing_cards(products if isinstance(products, list) else [])
        except IntesaPayError as e:
            fetch_error = str(e)
        except Exception as e:
            fetch_error = str(e)
    return render(
        request,
        "harness/pricing.html",
        {
            "cfg": cfg,
            "cards": cards,
            "fetch_error": fetch_error,
            "last_result": request.session.pop("last_result", None),
        },
    )


@require_GET
def ops(request: HttpRequest) -> HttpResponse:
    events = WebhookEvent.objects.all()[:30]
    return render(
        request,
        "harness/home.html",
        {
            "cfg": _cfg(),
            "nav": "ops",
            "events": events,
            "last_result": request.session.pop("last_result", None),
        },
    )


# Back-compat alias used by older templates / bookmarks.
home = ops


def _store_result(request: HttpRequest, title: str, payload) -> None:
    request.session["last_result"] = {
        "title": title,
        "body": json.dumps(payload, indent=2, default=str),
    }


@require_POST
def action_me(request: HttpRequest) -> HttpResponse:
    try:
        data = IntesaPayClient().me()
        _store_result(request, "GET /api/v1/me", data)
        messages.success(request, "me ok")
    except IntesaPayError as e:
        _store_result(request, f"me failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_collect(request: HttpRequest) -> HttpResponse:
    amount = request.POST.get("amount", "500").strip()
    msisdn = request.POST.get("msisdn", "+256700000001").strip()
    price_id = request.POST.get("price_id", "").strip() or None
    customer_name = request.POST.get("customer_name", "").strip() or None
    description = request.POST.get("description", "").strip() or "partner_test collect"
    try:
        kwargs: dict = {
            "msisdn": msisdn,
            "description": description,
            "customer_name": customer_name,
        }
        if price_id:
            kwargs["price_id"] = price_id
        else:
            kwargs["amount"] = amount
            kwargs["currency"] = "UGX"
        data = IntesaPayClient().collect_mobile_money(**kwargs)
        _store_result(request, "collect", data)
        messages.success(request, f"collect → {data.get('reference') or data.get('status') or 'ok'}")
    except IntesaPayError as e:
        _store_result(request, f"collect failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_list_products(request: HttpRequest) -> HttpResponse:
    try:
        data = IntesaPayClient().list_products()
        _store_result(request, "catalog products", data)
        messages.success(request, f"{len(data) if isinstance(data, list) else 1} product(s)")
    except IntesaPayError as e:
        _store_result(request, f"products failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_create_product(request: HttpRequest) -> HttpResponse:
    name = request.POST.get("name", "Demo product").strip()
    try:
        data = IntesaPayClient().create_product(name=name)
        _store_result(request, "create product", data)
        messages.success(request, f"product {data.get('id')}")
    except IntesaPayError as e:
        _store_result(request, f"create product failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_upsert_unmanaged(request: HttpRequest) -> HttpResponse:
    partner_id = request.POST.get("partner_product_id", "local_sku_001").strip()
    name = request.POST.get("name", "Local product").strip()
    try:
        data = IntesaPayClient().upsert_unmanaged_product(partner_id, name=name)
        price = IntesaPayClient().upsert_unmanaged_price(
            partner_id,
            f"{partner_id}_price",
            unit_amount=float(request.POST.get("amount", "5000")),
            pricing_type=request.POST.get("pricing_type", "ONE_TIME"),
            billing_interval=request.POST.get("billing_interval") or None
            if request.POST.get("pricing_type") == "RECURRING"
            else None,
        )
        _store_result(request, "unmanaged sync", {"product": data, "price": price})
        messages.success(request, f"synced {partner_id}")
    except IntesaPayError as e:
        _store_result(request, f"unmanaged sync failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_create_price(request: HttpRequest) -> HttpResponse:
    product_id = request.POST.get("product_id", "").strip()
    amount = request.POST.get("amount", "5000").strip()
    pricing_type = request.POST.get("pricing_type", "ONE_TIME").strip()
    interval = request.POST.get("billing_interval", "MONTH").strip() or None
    if not product_id:
        messages.error(request, "product_id required")
        return redirect("ops")
    try:
        data = IntesaPayClient().create_price(
            product_id,
            unit_amount=float(amount),
            pricing_type=pricing_type,
            billing_interval=interval if pricing_type == "RECURRING" else None,
        )
        _store_result(request, "create price", data)
        messages.success(request, f"price {data.get('id')}")
    except IntesaPayError as e:
        _store_result(request, f"create price failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_subscribe(request: HttpRequest) -> HttpResponse:
    price_id = request.POST.get("price_id", "").strip()
    msisdn = request.POST.get("msisdn", "+256700000001").strip()
    coupon_id = request.POST.get("coupon_id", "").strip() or None
    coupon_code = (
        request.POST.get("coupon_code_override", "").strip()
        or request.POST.get("coupon_code", "").strip()
        or None
    )
    if coupon_code:
        coupon_id = None
    if not price_id:
        messages.error(request, "price_id required")
        return _redirect_after_action(request)
    try:
        data = IntesaPayClient().subscribe(
            price_id=price_id,
            msisdn=msisdn,
            coupon_id=coupon_id,
            coupon_code=coupon_code,
        )
        _store_result(request, "subscribe", data)
        messages.success(request, "subscription started")
    except IntesaPayError as e:
        _store_result(request, f"subscribe failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return _redirect_after_action(request)


@require_POST
def action_checkout(request: HttpRequest) -> HttpResponse:
    """Create a hosted checkout session and redirect to IntesaPay /pay/…."""
    price_id = request.POST.get("price_id", "").strip()
    coupon_id = request.POST.get("coupon_id", "").strip() or None
    coupon_code = (
        request.POST.get("coupon_code_override", "").strip()
        or request.POST.get("coupon_code", "").strip()
        or None
    )
    customer_email = request.POST.get("customer_email", "").strip() or None
    customer_name = request.POST.get("customer_name", "").strip() or None
    description = request.POST.get("description", "").strip() or None
    if coupon_code:
        coupon_id = None
    if not price_id:
        messages.error(request, "price_id required")
        return _redirect_after_action(request, "pricing")

    origin = request.build_absolute_uri("/").rstrip("/")
    success_url = f"{origin}/?checkout=ok"
    cancel_url = f"{origin}/?checkout=cancel"

    try:
        data = IntesaPayClient().create_checkout_session(
            price_id=price_id,
            coupon_id=coupon_id,
            coupon_code=coupon_code,
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=customer_email,
            customer_name=customer_name,
            description=description,
        )
        _store_result(request, "checkout session", data)
        url = (data or {}).get("url") if isinstance(data, dict) else None
        if not url:
            messages.error(request, "checkout session created but no url returned")
            return _redirect_after_action(request, "pricing")
        return redirect(url)
    except IntesaPayError as e:
        _store_result(request, f"checkout failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return _redirect_after_action(request, "pricing")

@require_POST
def action_poll(request: HttpRequest) -> HttpResponse:
    reference = request.POST.get("reference", "").strip()
    if not reference:
        messages.error(request, "reference required")
        return redirect("ops")
    try:
        data = IntesaPayClient().get_payment(reference, sync=True)
        _store_result(request, f"payment {reference}", data)
        messages.success(request, f"status={data.get('status')}")
    except IntesaPayError as e:
        _store_result(request, f"poll failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_wallet(request: HttpRequest) -> HttpResponse:
    try:
        client = IntesaPayClient()
        wallet = client.wallet()
        ledger = client.wallet_ledger(mode="TEST", size=10)
        _store_result(request, "wallet + ledger", {"wallet": wallet, "ledger": ledger})
        messages.success(request, "wallet ok")
    except IntesaPayError as e:
        _store_result(request, f"wallet failed ({e.status})", e.body)
        messages.error(request, str(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@require_POST
def action_payout(request: HttpRequest) -> HttpResponse:
    amount = request.POST.get("amount", "100").strip()
    msisdn = request.POST.get("msisdn", "+256700000001").strip()
    try:
        data = IntesaPayClient().payout_mobile_money(amount=amount, msisdn=msisdn)
        _store_result(request, "payout", data)
        messages.success(request, f"payout → {data.get('reference') or data.get('status') or 'ok'}")
    except IntesaPayError as e:
        _store_result(request, f"payout failed ({e.status})", e.body)
        messages.error(request, _intesapay_flash(e))
    except Exception as e:
        messages.error(request, str(e))
    return redirect("ops")


@csrf_exempt
@require_http_methods(["POST"])
def webhook_intesapay(request: HttpRequest) -> HttpResponse:
    """Inbound account webhook — configure this URL on the console account."""
    raw = request.body
    sig = request.headers.get("IntesaPay-Signature") or request.META.get("HTTP_INTESAPAY_SIGNATURE")
    event_type = request.headers.get("IntesaPay-Event") or ""
    delivery_id = request.headers.get("IntesaPay-Delivery") or ""
    ok, reason = verify_signature(settings.INTESAPAY_WEBHOOK_SECRET, sig, raw)

    interesting = {
        k: v
        for k, v in request.headers.items()
        if k.lower().startswith("intesapay") or k.lower() in ("content-type", "user-agent")
    }
    WebhookEvent.objects.create(
        event_type=event_type,
        delivery_id=delivery_id,
        signature_ok=ok,
        http_headers=json.dumps({"verify": reason, **interesting}, indent=2),
        payload=raw.decode("utf-8", errors="replace"),
    )

    if not ok:
        return JsonResponse({"ok": False, "error": reason}, status=401)
    return JsonResponse({"ok": True})


@require_POST
def clear_events(request: HttpRequest) -> HttpResponse:
    WebhookEvent.objects.all().delete()
    messages.info(request, "webhook inbox cleared")
    return redirect("ops")
