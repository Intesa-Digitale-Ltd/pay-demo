# Partner test harness (Django)

Minimal stand-in for a SaaS backend integrating **one IntesaPay account** on **https://api-test.intesapay.io**.

Uses:

- **Account id** (`externalMerchantId`)
- **API key** (`X-API-Key`)
- **Webhook** (optional) — verifies `IntesaPay-Signature`

## Setup

```powershell
cd apps\partner_test
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# INTESAPAY_BASE_URL is already https://api-test.intesapay.io
# edit account id, API key, and webhook secret from console → Accounts → account detail
python manage.py migrate
python manage.py runserver 8001
```

Open http://127.0.0.1:8001/

## Webhook

The API at `https://api-test.intesapay.io` cannot reach `127.0.0.1` on your laptop. Expose this app with a tunnel, then set the console account webhook URL to:

```text
https://<your-tunnel>/webhooks/intesapay/
```

Put the signing secret in `INTESAPAY_WEBHOOK_SECRET`.

## Catalog

Open **http://127.0.0.1:8001/catalog/** (also **Catalog** in the sidebar). Full-screen product list with gateway sync status, **New product**, and Open / Push / Delete on each row. Click a product for prices and coupons.

- **Forward sync** — push products you created here to IntesaPay.
- **Backward sync** — pull the live IntesaPay catalog into this list. Console-managed items show as read-only mirrors.

**Managed** — create in the IntesaPay console, or `POST /api/v1/catalog/products`, then backward sync.

**Unmanaged** — keep products in this app; on create/update/delete call (or use Forward sync):

```http
PUT    /api/v1/catalog/unmanaged/products/{yourProductId}
PUT    /api/v1/catalog/unmanaged/products/{yourProductId}/prices/{yourPriceId}
DELETE /api/v1/catalog/unmanaged/products/{yourProductId}
DELETE /api/v1/catalog/unmanaged/prices/{yourPriceId}

PUT    /api/v1/catalog/unmanaged/products/{yourProductId}/coupons/{yourCouponId}
DELETE /api/v1/catalog/unmanaged/coupons/{yourCouponId}
```

Collect with `priceId` (IntesaPay `price_…`) or `externalPriceId` (your local price id). Pass `couponId` or `couponCode` on subscribe/collect to redeem an offer — **nothing auto-applies**. Expired, maxed, or already-used coupons return friendly API errors (`COUPON_*` codes).

Optional payer fields:

- `customerName` — shown on wallet ledger / payment
- `description` — note on the payment (also ledger `note`)
- Checkout: also `customerEmail` for invoice/receipt

Full guide: IntesaPay **/docs/coupons** and **/docs/ledger**.


| Intent | Amount / MSISDN |
|--------|-----------------|
| Success | `500` / `+256700000001` |
| Fail | amount ends `02` or MSISDN ends `0002` |
| Stuck → cancel | amount ends `03` or MSISDN ends `0003` |
