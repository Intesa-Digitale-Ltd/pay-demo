"""Minimal IntesaPay EXTERNAL client for local testing."""

from __future__ import annotations

import uuid
from typing import Any

import requests
from django.conf import settings


class IntesaPayError(Exception):
    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body
        super().__init__(f"IntesaPay HTTP {status}: {body}")


class IntesaPayClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        account_id: str | None = None,
    ):
        self.base_url = (base_url or settings.INTESAPAY_BASE_URL).rstrip("/")
        self.api_key = api_key or settings.INTESAPAY_API_KEY
        self.account_id = account_id or settings.INTESAPAY_ACCOUNT_ID

    def _headers(self, idempotency: bool = False) -> dict[str, str]:
        h = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.account_id:
            h["X-Account-Id"] = self.account_id
        if idempotency:
            h["Idempotency-Key"] = str(uuid.uuid4())
        return h

    def _request(self, method: str, path: str, *, json: dict | None = None, idempotent: bool = False) -> Any:
        url = f"{self.base_url}{path}"
        resp = requests.request(
            method,
            url,
            headers=self._headers(idempotency=idempotent),
            json=json,
            timeout=60,
        )
        try:
            body = resp.json() if resp.content else None
        except ValueError:
            body = resp.text
        if resp.status_code >= 400:
            raise IntesaPayError(resp.status_code, body)
        return body

    def me(self) -> Any:
        return self._request("GET", "/api/v1/me")

    def collect_mobile_money(
        self,
        *,
        msisdn: str,
        amount: str | float | None = None,
        currency: str | None = "UGX",
        description: str = "partner_test collect",
        customer_name: str | None = None,
        price_id: str | None = None,
        external_price_id: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "externalMerchantId": self.account_id,
            "msisdn": msisdn,
            "description": description,
        }
        if customer_name:
            payload["customerName"] = customer_name
        if price_id:
            payload["priceId"] = price_id
        if external_price_id:
            payload["externalPriceId"] = external_price_id
        if amount is not None:
            payload["amount"] = float(amount)
        if currency:
            payload["currency"] = currency
        return self._request(
            "POST",
            "/api/v1/payments/mobile-money/collect",
            idempotent=True,
            json=payload,
        )

    def get_payment(self, reference: str, *, sync: bool = True) -> Any:
        q = "?sync=true" if sync else ""
        return self._request("GET", f"/api/v1/payments/{reference}{q}")

    def wallet(self) -> Any:
        return self._request("GET", f"/api/v1/merchants/{self.account_id}/wallet")

    def wallet_ledger(
        self,
        *,
        mode: str = "TEST",
        q: str | None = None,
        page: int = 0,
        size: int = 20,
    ) -> Any:
        qs = f"?mode={mode}&page={page}&size={size}&sort=createdAt,desc"
        if q:
            from urllib.parse import quote

            qs += f"&q={quote(q)}"
        return self._request("GET", f"/api/v1/merchants/{self.account_id}/wallet/ledger{qs}")

    def payout_mobile_money(
        self,
        *,
        amount: str | float,
        msisdn: str,
        currency: str = "UGX",
        description: str = "partner_test payout",
    ) -> Any:
        return self._request(
            "POST",
            "/api/v1/payments/mobile-money/payout",
            idempotent=True,
            json={
                "externalMerchantId": self.account_id,
                "amount": float(amount),
                "currency": currency,
                "msisdn": msisdn,
                "description": description,
            },
        )

    def list_products(self) -> Any:
        return self._request("GET", "/api/v1/catalog/products")

    def create_product(self, *, name: str, description: str | None = None) -> Any:
        body: dict[str, Any] = {"name": name}
        if description:
            body["description"] = description
        return self._request("POST", "/api/v1/catalog/products", json=body)

    def update_product(
        self,
        product_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        active: bool | None = None,
    ) -> Any:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if active is not None:
            body["active"] = active
        return self._request("PATCH", f"/api/v1/catalog/products/{product_id}", json=body)

    def upsert_unmanaged_product(
        self,
        partner_product_id: str,
        *,
        name: str,
        description: str | None = None,
        active: bool = True,
    ) -> Any:
        body: dict[str, Any] = {"name": name, "active": active}
        if description:
            body["description"] = description
        return self._request(
            "PUT",
            f"/api/v1/catalog/unmanaged/products/{partner_product_id}",
            json=body,
        )

    def delete_unmanaged_product(self, partner_product_id: str) -> Any:
        return self._request("DELETE", f"/api/v1/catalog/unmanaged/products/{partner_product_id}")

    def create_price(
        self,
        product_id: str,
        *,
        unit_amount: float,
        currency: str = "UGX",
        pricing_type: str = "ONE_TIME",
        billing_interval: str | None = None,
        interval_count: int = 1,
    ) -> Any:
        body: dict[str, Any] = {
            "unitAmount": unit_amount,
            "currency": currency,
            "pricingType": pricing_type,
            "intervalCount": interval_count,
        }
        if billing_interval:
            body["billingInterval"] = billing_interval
        return self._request("POST", f"/api/v1/catalog/products/{product_id}/prices", json=body)

    def update_price(
        self,
        price_id: str,
        *,
        unit_amount: float,
        currency: str = "UGX",
        pricing_type: str = "ONE_TIME",
        billing_interval: str | None = None,
        interval_count: int = 1,
    ) -> Any:
        body: dict[str, Any] = {
            "unitAmount": unit_amount,
            "currency": currency,
            "pricingType": pricing_type,
            "intervalCount": interval_count,
        }
        if billing_interval:
            body["billingInterval"] = billing_interval
        return self._request("PATCH", f"/api/v1/catalog/prices/{price_id}", json=body)

    def archive_price(self, price_id: str) -> Any:
        return self._request("POST", f"/api/v1/catalog/prices/{price_id}/archive", json={})

    def upsert_unmanaged_price(
        self,
        partner_product_id: str,
        partner_price_id: str,
        *,
        unit_amount: float,
        currency: str = "UGX",
        pricing_type: str = "ONE_TIME",
        billing_interval: str | None = None,
        interval_count: int = 1,
    ) -> Any:
        body: dict[str, Any] = {
            "unitAmount": unit_amount,
            "currency": currency,
            "pricingType": pricing_type,
            "intervalCount": interval_count,
        }
        if billing_interval:
            body["billingInterval"] = billing_interval
        return self._request(
            "PUT",
            f"/api/v1/catalog/unmanaged/products/{partner_product_id}/prices/{partner_price_id}",
            json=body,
        )

    def delete_unmanaged_price(self, partner_price_id: str) -> Any:
        return self._request("DELETE", f"/api/v1/catalog/unmanaged/prices/{partner_price_id}")

    def create_coupon(
        self,
        product_id: str,
        *,
        name: str,
        percent_off: float | None = None,
        amount_off: float | None = None,
        currency: str | None = None,
        duration: str = "ONCE",
        duration_in_months: int | None = None,
        max_redemptions: int | None = None,
        price_id: str | None = None,
        code: str | None = None,
        new_customers_only: bool | None = None,
        one_per_customer: bool | None = None,
    ) -> Any:
        body: dict[str, Any] = {"name": name, "duration": duration}
        if percent_off is not None:
            body["percentOff"] = percent_off
        if amount_off is not None:
            body["amountOff"] = amount_off
        if currency:
            body["currency"] = currency
        if duration_in_months is not None:
            body["durationInMonths"] = duration_in_months
        if max_redemptions is not None:
            body["maxRedemptions"] = max_redemptions
        if price_id:
            body["priceId"] = price_id
        if code:
            body["code"] = code
        if new_customers_only is not None:
            body["newCustomersOnly"] = new_customers_only
        if one_per_customer is not None:
            body["onePerCustomer"] = one_per_customer
        return self._request("POST", f"/api/v1/catalog/products/{product_id}/coupons", json=body)

    def archive_coupon(self, coupon_id: str) -> Any:
        return self._request("POST", f"/api/v1/catalog/coupons/{coupon_id}/archive", json={})

    def upsert_unmanaged_coupon(
        self,
        partner_product_id: str,
        partner_coupon_id: str,
        *,
        name: str,
        percent_off: float | None = None,
        amount_off: float | None = None,
        currency: str | None = None,
        duration: str = "ONCE",
        duration_in_months: int | None = None,
        max_redemptions: int | None = None,
        partner_price_id: str | None = None,
        active: bool = True,
        code: str | None = None,
        new_customers_only: bool | None = None,
        one_per_customer: bool | None = None,
    ) -> Any:
        body: dict[str, Any] = {"name": name, "duration": duration, "active": active}
        if percent_off is not None:
            body["percentOff"] = percent_off
        if amount_off is not None:
            body["amountOff"] = amount_off
        if currency:
            body["currency"] = currency
        if duration_in_months is not None:
            body["durationInMonths"] = duration_in_months
        if max_redemptions is not None:
            body["maxRedemptions"] = max_redemptions
        if partner_price_id:
            body["partnerPriceId"] = partner_price_id
        if code:
            body["code"] = code
        if new_customers_only is not None:
            body["newCustomersOnly"] = new_customers_only
        if one_per_customer is not None:
            body["onePerCustomer"] = one_per_customer
        return self._request(
            "PUT",
            f"/api/v1/catalog/unmanaged/products/{partner_product_id}/coupons/{partner_coupon_id}",
            json=body,
        )

    def delete_unmanaged_coupon(self, partner_coupon_id: str) -> Any:
        return self._request("DELETE", f"/api/v1/catalog/unmanaged/coupons/{partner_coupon_id}")

    def subscribe(
        self,
        *,
        price_id: str,
        msisdn: str,
        description: str | None = None,
        coupon_id: str | None = None,
        coupon_code: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {"priceId": price_id, "msisdn": msisdn}
        if description:
            body["description"] = description
        if coupon_id:
            body["couponId"] = coupon_id
        if coupon_code:
            body["couponCode"] = coupon_code
        return self._request("POST", "/api/v1/catalog/subscriptions", json=body, idempotent=True)

    def list_subscriptions(self) -> Any:
        return self._request("GET", "/api/v1/catalog/subscriptions")

    def create_checkout_session(
        self,
        *,
        price_id: str | None = None,
        external_price_id: str | None = None,
        amount: float | None = None,
        currency: str | None = None,
        description: str | None = None,
        coupon_id: str | None = None,
        coupon_code: str | None = None,
        success_url: str | None = None,
        cancel_url: str | None = None,
        customer_email: str | None = None,
        customer_name: str | None = None,
        expires_in_minutes: int | None = None,
    ) -> Any:
        body: dict[str, Any] = {"externalMerchantId": self.account_id}
        if price_id:
            body["priceId"] = price_id
        if external_price_id:
            body["externalPriceId"] = external_price_id
        if amount is not None:
            body["amount"] = float(amount)
        if currency:
            body["currency"] = currency
        if description:
            body["description"] = description
        if coupon_id:
            body["couponId"] = coupon_id
        if coupon_code:
            body["couponCode"] = coupon_code
        if success_url:
            body["successUrl"] = success_url
        if cancel_url:
            body["cancelUrl"] = cancel_url
        if customer_email:
            body["customerEmail"] = customer_email
        if customer_name:
            body["customerName"] = customer_name
        if expires_in_minutes is not None:
            body["expiresInMinutes"] = expires_in_minutes
        return self._request("POST", "/api/v1/checkout/sessions", json=body, idempotent=True)
