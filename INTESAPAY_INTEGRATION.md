# How this app talks to IntesaPay

A walkthrough of `apps/partner_test`. Use it as a case study: one pretend partner product, one IntesaPay account, and the calls a real integration makes.

The app is a small Django site on port **8001**. IntesaPay (the payments API) is expected on **http://localhost:8080**. Nothing in this app moves money by itself. Every collect, payout, checkout, and catalog change is an HTTP call to IntesaPay, using credentials from the console.

Open three pages while you read:

| Page | URL | What it pretends to be |
|------|-----|------------------------|
| Plans | http://127.0.0.1:8001/ | The partner’s public pricing page |
| Catalog | http://127.0.0.1:8001/catalog/ | The partner’s product admin |
| Ops | http://127.0.0.1:8001/ops/ | A debug console: raw API calls and the webhook inbox |

---

## 1. The idea in one picture

A partner does not embed mobile-money logic. They keep their own products, customers, and orders, and they ask IntesaPay to collect, pay out, and host checkout.

This harness stands in for that partner backend. It stores three things locally (SQLite):

- webhook deliveries, so you can see what IntesaPay posted
- a catalog of products, prices, and coupons
- the Django session, which holds the last API response so the page can show it

Everything else lives in IntesaPay: the account, the wallet, payment status, and the canonical catalog.

```text
Browser  →  this Django app (port 8001)
                │
                │  X-API-Key
                │  X-Account-Id
                ▼
            IntesaPay API (port 8080)
                │
                │  POST /webhooks/intesapay/
                ▼
            this Django app (webhook inbox)
```

Three credentials, all from the console account page, all in `.env`:

| Setting | What it is | Where it goes |
|---------|------------|----------------|
| `INTESAPAY_ACCOUNT_ID` | The account id (`acct_…`). Sent as `externalMerchantId` on money calls, and as `X-Account-Id`. | Console → Accounts → account detail |
| `INTESAPAY_API_KEY` | Secret API key. Sent as `X-API-Key`. | Shown once when the key is created or rotated |
| `INTESAPAY_WEBHOOK_SECRET` | Used only to check `IntesaPay-Signature`. Never sent on outbound calls. | Account webhook settings |

`INTESAPAY_BASE_URL` is where the API lives. Locally that is `http://localhost:8080`.

The app refuses to call the API until both the key and the account id are set. The Ops page shows that as “API key set / missing”.

---

## 2. Run it before the session

From `apps/partner_test`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py runserver 8001
```

Edit `.env` with the account id, API key, and webhook secret from the console. Then, on that same account, set the webhook URL to:

```text
http://127.0.0.1:8001/webhooks/intesapay/
```

If IntesaPay is running in Docker on another machine, it cannot reach `127.0.0.1` on your laptop. Use a tunnel, or `http://host.docker.internal:8001/webhooks/intesapay/` when the API container is on the same machine.

Settings are loaded once at startup:

```69:73:apps/partner_test/config/settings.py
# IntesaPay partner credentials (from console account detail)
INTESAPAY_BASE_URL = os.getenv("INTESAPAY_BASE_URL", "http://localhost:8080").rstrip("/")
INTESAPAY_ACCOUNT_ID = os.getenv("INTESAPAY_ACCOUNT_ID", "")
INTESAPAY_API_KEY = os.getenv("INTESAPAY_API_KEY", "")
INTESAPAY_WEBHOOK_SECRET = os.getenv("INTESAPAY_WEBHOOK_SECRET", "")
```

Restart the server after you change `.env`. Django does not reload environment variables on its own.

---

## 3. One HTTP client does every call

All outbound traffic goes through `IntesaPayClient` in `harness/intesapay.py`. Views never build URLs or headers themselves. That is the pattern to copy: one client, one place that knows the API.

### Headers

Every request sends the API key. If an account id is configured, it also sends `X-Account-Id`. Writes that must not be double-charged send a fresh `Idempotency-Key`.

```30:57:apps/partner_test/harness/intesapay.py
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
```

What to point out:

- **API key authenticates the caller.** Account id says which account the call is for.
- **HTTP 400 and above become `IntesaPayError`.** The view catches that, stores the JSON body, and shows a flash message. The raw body is how you teach error codes (`INSUFFICIENT_FUNDS`, `COUPON_*`, and so on).
- **Timeout is 60 seconds.** Collect can wait on the mobile-money rail. A short timeout would look like a failure while the payment is still in flight.

### Idempotency, and a demo shortcut

Collect, payout, subscribe, and checkout pass `idempotent=True`, so each of those requests gets an `Idempotency-Key`.

This demo generates a **new** UUID on every click. That is fine for a classroom click-through. It is the wrong pattern in production.

In a real app, create the key when the customer places the order, save it with that order, and send the **same** key if the HTTP call times out and you retry. IntesaPay then returns the original payment instead of starting a second one. The Ops page even documents the other side of that rule: HTTP **409** `CONCURRENT_REQUEST_IN_PROGRESS` means that same key is still in flight. Wait, then replay the same key. Do not invent a new one.

### How a button becomes an API call

Ops buttons are ordinary HTML forms. Django receives the POST, calls the client, and stashes the JSON in the session so the next page can print it.

```92:96:apps/partner_test/harness/views.py
def _store_result(request: HttpRequest, title: str, payload) -> None:
    request.session["last_result"] = {
        "title": title,
        "body": json.dumps(payload, indent=2, default=str),
    }
```

The next GET pops that value (`request.session.pop("last_result")`), so a refresh does not keep showing the same payload. Errors follow the same path: the title says which call failed, and the body is IntesaPay’s JSON.

Friendly messages for the two payout failures you will actually hit:

```18:28:apps/partner_test/harness/views.py
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
```

---

## 4. “Who am I” — prove the key works

On Ops, **Who am I (/me)** calls:

```text
GET /api/v1/me
```

```59:60:apps/partner_test/harness/intesapay.py
    def me(self) -> Any:
        return self._request("GET", "/api/v1/me")
```

No body. If the key is wrong you get an error before any money moves. Start the demo here. The JSON tells you which account the key belongs to.

---

## 5. Collect — ask a phone to pay

**Collect MM** on Ops is a one-off mobile-money charge. The form sends an amount, a phone number (MSISDN), a payer name, a note, and an optional catalog `priceId`.

The view decides which payload to send:

- If the form has a `priceId`, the amount comes from that catalog price. The amount box is ignored.
- If it does not, the call is an ad-hoc charge: amount + currency `UGX`.

```113:139:apps/partner_test/harness/views.py
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
```

The client always attaches the account id, and only adds optional fields when they were filled in:

```62:93:apps/partner_test/harness/intesapay.py
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
```

Two ways to name a price:

| Field | Whose id | Example |
|-------|----------|---------|
| `priceId` | IntesaPay’s id | `price_…` |
| `externalPriceId` | The id your app already uses | `sku_monthly` |

`customerName` shows up on the wallet ledger and the payment. `description` is the note on the payment (and the ledger note).

The response includes a `reference`. That is the handle for the next step. The green flash on Ops prints it: `collect → <reference>`.

### Sandbox phones and amounts

The sandbox does not send a real debit. These patterns are built into the test rail so you can force an outcome without a handset:

| You want | Amount | MSISDN |
|----------|--------|--------|
| Success | `500` (does not end in 02 or 03) | `+256700000001` |
| Failure | ends in `02` | or ends in `0002` |
| Stuck, so you can cancel | ends in `03` | or ends in `0003` |

Demo line: “500 on a number ending 0001 succeeds. Change the amount to 502 and the same button fails. 503 sits in progress.”

---

## 6. Status — poll, and also wait for the webhook

A collect response is the start of the payment, not the final word. Status moves: pending, succeeded, failed, cancelled.

**Poll status** on Ops calls:

```text
GET /api/v1/payments/{reference}?sync=true
```

`sync=true` asks IntesaPay to refresh from the rail before answering, instead of returning only what it last stored.

```95:97:apps/partner_test/harness/intesapay.py
    def get_payment(self, reference: str, *, sync: bool = True) -> Any:
        q = "?sync=true" if sync else ""
        return self._request("GET", f"/api/v1/payments/{reference}{q}")
```

Teach both channels:

1. **Poll** when your server needs the status now (the customer is waiting on your page, or a job is reconciling).
2. **Webhook** when IntesaPay should push the change to you (section 11).

Do not mark an order paid from the collect response alone. Wait until poll or a verified webhook says the payment succeeded.

---

## 7. Wallet and payout

**Wallet** loads the balance and the latest TEST ledger lines.

```99:115:apps/partner_test/harness/intesapay.py
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
```

The Ops button always asks for `mode=TEST` and 10 rows. Live and test money are separate. A sandbox collect does not increase the live balance.

**TEST payout** sends money to a phone:

```text
POST /api/v1/payments/mobile-money/payout
```

Same account id, amount, currency, MSISDN, and description as collect, in the other direction.

A payout reserves **the amount plus the fee**. If the available balance cannot cover both, IntesaPay returns HTTP **402** `INSUFFICIENT_FUNDS`. That is the line on the Ops page: “Cashout holds principal + fee.”

Collect first (sandbox success), open Wallet, then payout a small amount (the form defaults to `100`). If you payout more than the available balance, you get 402 on purpose. That is a good slide.

---

## 8. Plans page — what the customer sees

`/` is not the admin. It is a pricing page that could sit on the partner’s marketing site.

On each load it asks IntesaPay for the live catalog and turns that JSON into cards:

```48:70:apps/partner_test/harness/views.py
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
```

`GET /api/v1/catalog/products` returns products with nested prices and coupons. `build_pricing_cards` (`harness/catalog_display.py`) does presentation only:

- drops inactive products and prices
- sorts recurring prices longer-interval first (year, then month, week, day)
- picks a coupon to show on each price
- formats money as `UGX 5,000`
- if the price carries a tax rate, shows tax-inclusive or tax-exclusive totals

Tax math here is for the label on the card. IntesaPay charges the real amount. The page is showing the customer what they will pay, using fields the API already returned (`taxPercent`, `taxBehavior`, `taxRateId`).

Coupons are **never applied automatically by IntesaPay**. The page chooses one for display, then sends it explicitly when the customer clicks pay. If a product has any price-scoped coupon, the page shows only those and hides product-wide codes. A typed promo code wins over the pre-selected offer (the small script at the bottom of `pricing.html` copies the choice into hidden `coupon_id` / `coupon_code` fields).

Each card posts to hosted checkout. There is no card form and no PIN form in this app.

---

## 9. Hosted checkout

**Pay with hosted checkout** creates a session, then sends the browser to IntesaPay’s pay page.

```254:293:apps/partner_test/harness/views.py
def action_checkout(request: HttpRequest) -> HttpResponse:
    """Create a hosted checkout session and redirect to IntesaPay /pay/…."""
    price_id = request.POST.get("price_id", "").strip()
    ...
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
        ...
        url = (data or {}).get("url") if isinstance(data, dict) else None
        if not url:
            messages.error(request, "checkout session created but no url returned")
            return _redirect_after_action(request, "pricing")
        return redirect(url)
```

The client posts that to:

```text
POST /api/v1/checkout/sessions
```

and sends `externalMerchantId` plus whichever of these you have: price, amount, coupon, return URLs, payer name, email, description.

```378:403:apps/partner_test/harness/intesapay.py
        body: dict[str, Any] = {"externalMerchantId": self.account_id}
        if price_id:
            body["priceId"] = price_id
        if external_price_id:
            body["externalPriceId"] = external_price_id
        if amount is not None:
            body["amount"] = float(amount)
        ...
        if customer_email:
            body["customerEmail"] = customer_email
        if customer_name:
            body["customerName"] = customer_name
        ...
        return self._request("POST", "/api/v1/checkout/sessions", json=body, idempotent=True)
```

Story for the room:

1. Customer picks a price on **your** site.
2. Your server creates a checkout session. The secret API key never reaches the browser. The browser only receives a URL.
3. IntesaPay hosts the pay page (`/pay/…`).
4. IntesaPay sends the browser back to `success_url` or `cancel_url`.
5. Those return URLs are **not** proof of payment. Confirm with a verified webhook or by polling the payment reference. A customer can be redirected to the success URL and the debit can still fail, or they can close the tab before the redirect.

`customerEmail` is what checkout uses for the invoice or receipt. `customerName` and `description` land on the payment the same way they do for collect.

If the customer types a promo code, the view drops `couponId` and sends `couponCode` only. One identifier, not both. Expired, fully redeemed, or already-used coupons come back as `COUPON_*` errors with a readable message. Nothing is discounted unless you pass one of those fields.

---

## 10. Subscriptions

**Subscribe** on Ops starts a recurring charge against a recurring `priceId` and an MSISDN:

```text
POST /api/v1/catalog/subscriptions
```

```341:357:apps/partner_test/harness/intesapay.py
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
```

The price has to be `RECURRING` (day, week, month, or year). A one-time price is a collect or a checkout, not a subscription.

Same coupon rule: pass `couponId` or `couponCode` if you want an offer. Duration on the coupon says how long it lasts: once, repeating for N months, or forever. IntesaPay will not pick a coupon for you.

---

## 11. Webhooks — IntesaPay calls you back

You register one URL on the account. When a payment changes, IntesaPay POSTs the event to:

```text
POST /webhooks/intesapay/
```

The view is CSRF-exempt because the caller is IntesaPay, not a browser form. That exemption is safe only because the signature check below is mandatory.

### The signature

Header shape:

```text
IntesaPay-Signature: t=<unix seconds>,v1=<hex hmac>
```

Also useful: `IntesaPay-Event` (what happened) and `IntesaPay-Delivery` (this delivery’s id).

The signed bytes are the timestamp, a dot, and the **raw** request body:

```text
{timestamp}.{raw json body}
```

HMAC-SHA256 with the webhook secret. Compare in constant time.

```10:48:apps/partner_test/harness/webhook_sig.py
def verify_signature(
    secret: str,
    header: str | None,
    body: bytes | str,
    *,
    tolerance_seconds: int = 300,
) -> tuple[bool, str]:
    if not secret:
        return False, "INTESAPAY_WEBHOOK_SECRET not configured"
    ...
    if abs(time.time() - ts_i) > tolerance_seconds:
        return False, f"timestamp outside tolerance ({tolerance_seconds}s)"

    raw = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
    payload = f"{ts}.{raw}".encode("utf-8")
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, v1):
        return False, "v1 mismatch"
    return True, "ok"
```

Why each check exists:

| Check | Why |
|-------|-----|
| Secret configured | Refuse everything if you forgot the secret, instead of accepting unsigned posts |
| `t` and `v1` present | A missing piece is not a signature |
| Timestamp within 5 minutes | Stops someone replaying a captured valid webhook later |
| HMAC over the raw body | A proxy that re-formats JSON would break the signature. Verify bytes, then parse JSON |
| `compare_digest` | Avoids leaking how much of the signature matched |

The view **stores every delivery**, including bad signatures, then returns 401 if verification failed. Ops shows a green “ok” or red “bad” pill. That inbox is the teaching tool: do a sandbox collect, switch to Ops, open the row, read the event type and the JSON.

```351:376:apps/partner_test/harness/views.py
@csrf_exempt
@require_http_methods(["POST"])
def webhook_intesapay(request: HttpRequest) -> HttpResponse:
    """Inbound account webhook — configure this URL on the console account."""
    raw = request.body
    sig = request.headers.get("IntesaPay-Signature") or request.META.get("HTTP_INTESAPAY_SIGNATURE")
    event_type = request.headers.get("IntesaPay-Event") or ""
    delivery_id = request.headers.get("IntesaPay-Delivery") or ""
    ok, reason = verify_signature(settings.INTESAPAY_WEBHOOK_SECRET, sig, raw)
    ...
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
```

Production habits this demo only sketches:

- Return 2xx quickly after you have verified and stored the event. Do heavy work after that.
- Treat `IntesaPay-Delivery` as an idempotency key. IntesaPay may retry. Applying “payment succeeded” twice must not fulfill the order twice.
- Still poll if you care about a payment and no webhook arrived. Webhooks are a notification, not the only record.

---

## 12. Catalog — your products and IntesaPay’s products

There are two ways a product gets into IntesaPay.

**Managed.** Created in the console, or with `POST /api/v1/catalog/products`. IntesaPay assigns the ids (`prod_…`, `price_…`). You store those ids if you need to charge against them. This app’s backward sync copies them in as read-only mirrors.

**Unmanaged.** Your database is the source of truth. You already have a product id. You push it with PUT, using **your** id in the URL. IntesaPay stores the mapping and returns its own `price_…` id as well. Charge later with either `priceId` or `externalPriceId`.

```text
PUT    /api/v1/catalog/unmanaged/products/{yourProductId}
PUT    /api/v1/catalog/unmanaged/products/{yourProductId}/prices/{yourPriceId}
PUT    /api/v1/catalog/unmanaged/products/{yourProductId}/coupons/{yourCouponId}

DELETE /api/v1/catalog/unmanaged/products/{yourProductId}
DELETE /api/v1/catalog/unmanaged/prices/{yourPriceId}
DELETE /api/v1/catalog/unmanaged/coupons/{yourCouponId}
```

PUT means “create or replace”. Send the full price or coupon each time. You do not need to remember whether it existed.

### What this app stores

`CatalogProduct` is one local row.

```19:57:apps/partner_test/harness/models.py
class CatalogProduct(models.Model):
    """Local catalog row. PARTNER rows push via unmanaged sync; INTESAPAY rows are a pull mirror."""

    ORIGIN_PARTNER = "PARTNER"
    ORIGIN_INTESAPAY = "INTESAPAY"
    ...
    partner_id = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    ...
    origin = models.CharField(max_length=16, choices=ORIGIN_CHOICES, default=ORIGIN_PARTNER)
    intesapay_id = models.CharField(max_length=80, blank=True)
    remote_source = models.CharField(max_length=16, blank=True)
    sync_error = models.TextField(blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
```

| Column | Meaning |
|--------|---------|
| `partner_id` | Your id. For unmanaged products this is the id in the PUT URL. |
| `intesapay_id` | IntesaPay’s id, filled after a successful push or pull. |
| `origin` | `PARTNER` if this app created it. `INTESAPAY` if it was pulled from the console. |
| `remote_source` | `MANAGED` or `UNMANAGED`, copied from the API’s `source` field. |
| `sync_error` | Last push failure. Cleared on success. The catalog row shows “Sync failed”. |

Prices (`CatalogPrice`) and coupons (`CatalogCoupon`) hang off the product the same way: your id, IntesaPay’s id, amount, interval, and discount.

Gateway status on the list is derived, not stored:

```68:84:apps/partner_test/harness/models.py
    def gateway_status(self) -> str:
        if self.origin == self.ORIGIN_INTESAPAY:
            return "pulled"
        if self.sync_error:
            return "error"
        if self.intesapay_id:
            return "synced"
        return "not_synced"
```

A pulled mirror, a failed push, a synced product, or a local-only product. That is the whole badge.

### Forward sync — this app pushes

**Forward sync** walks every local product and pushes it.

Products created here use the unmanaged PUT, unless IntesaPay has already marked that row managed. Console products that were pulled in use the managed API: `POST` the first time, `PATCH` after that.

```158:164:apps/partner_test/harness/catalog_sync.py
def forward_sync_product(product: CatalogProduct, client: IntesaPayClient | None = None) -> list[str]:
    """Push one product (and its prices/coupons) to IntesaPay."""
    client = client or IntesaPayClient()
    now = timezone.now()
    if product.uses_unmanaged_sync:
        return _push_unmanaged_product(product, client, now)
    return _push_public_id_product(product, client, now)
```

The unmanaged push is the one to teach. One product PUT, then a PUT per price, then a PUT per coupon. The returned `id` is saved on the local row so the Plans page and collect can use `price_…` later.

```119:140:apps/partner_test/harness/catalog_sync.py
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
    ...
    for price in product.prices.all():
        payload = _price_payload(price)
        remote_price = client.upsert_unmanaged_price(product.partner_id, price.partner_id, **payload)
        price.intesapay_id = (remote_price or {}).get("id") or price.intesapay_id
```

Creating a product in the UI does **not** stop at SQLite. After the row is saved, the view immediately pushes that one product:

```152:161:apps/partner_test/harness/catalog_views.py
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
```

If IntesaPay is down, the local row still exists and `sync_error` is filled. **Push** on the row retries. Adding a price or a coupon also pushes the whole product again. That matches how a partner should think: your database commits first, then you sync.

Deleting is the reverse. Unmanaged rows are `DELETE`d on the unmanaged URL. Managed prices and coupons are archived (`POST …/archive`). A managed product is set `active: false` rather than hard-deleted. If the remote call fails, the local row is kept so you can retry. You do not want a product gone locally and still chargeable in IntesaPay, or the opposite, without noticing.

### Backward sync — IntesaPay pulls into this app

**Backward sync** calls `GET /api/v1/catalog/products` and upserts a local row per product.

```196:217:apps/partner_test/harness/catalog_sync.py
    for row in remote_products:
        source = str(row.get("source") or "")
        intesapay_id = str(row.get("id") or "")
        partner_id = str(row.get("partnerProductId") or "").strip()
        origin = CatalogProduct.ORIGIN_PARTNER if source == "UNMANAGED" and partner_id else CatalogProduct.ORIGIN_INTESAPAY
        if not partner_id:
            partner_id = intesapay_id or new_partner_id("prod")
        ...
        product, created = CatalogProduct.objects.update_or_create(
            partner_id=partner_id,
            defaults={
                "name": row.get("name") or partner_id,
                ...
                "origin": origin,
                "intesapay_id": intesapay_id,
                "remote_source": source,
```

Rules that matter in the demo:

- An unmanaged product comes back with your `partnerProductId`. The local row stays “this app”.
- A console product has no partner id. It is stored as an IntesaPay mirror and is not edited here.
- Mirrors that disappeared from IntesaPay are deleted locally. Partner-owned rows are not. A failed network call must not wipe the catalog you created.

After a backward sync, refresh **Plans**. The cards are built from the live API, not from SQLite, so what you see there is exactly what IntesaPay has.

### Coupons on the catalog

A coupon belongs to a product. It is either a percent off or a fixed amount off, with a duration (`ONCE`, `REPEATING`, `FOREVER`). Optional pieces this app can send:

- `code` — what the customer types
- `maxRedemptions` — cap on total uses
- `partnerPriceId` / `priceId` — limit the offer to one price
- `newCustomersOnly`, `onePerCustomer` — the client supports these fields; the catalog form in this app does not expose every flag

Again: creating the coupon does not discount anyone. Checkout and subscribe must pass `couponId` or `couponCode`.

---

## 13. Suggested demo (about 20 minutes)

Have the API running, `.env` filled, and the webhook URL saved on the account before people sit down.

1. **Ops → Who am I.** Key works. Show the account in the JSON.
2. **Ops → Collect** with amount `500` and `+256700000001`. Read the reference in the flash and in the JSON panel.
3. **Ops → Poll** that reference. Status should be success. Open **Webhook inbox** and show the signed event. Point at `signature ok`.
4. Change the amount to `502` and collect again. Show a failed payment. Mention `503` / MSISDN ending `0003` if you want a “still pending” example.
5. **Wallet.** TEST balance and ledger lines. Point out payer name and note from the collect form.
6. **Payout** `100` to the same MSISDN. Then try an absurd amount and show `INSUFFICIENT_FUNDS` (402). Say “principal plus fee”.
7. **Catalog → New product.** Name it, add a one-time price and a recurring price, add a coupon with a code. Watch the row go to Synced. Mention unmanaged PUT and “your id in the URL”.
8. **Backward sync** after you create something in the IntesaPay console, if you have one ready. Show it land as a read-only mirror.
9. **Plans.** The new product is a card. Click **Pay with hosted checkout** and land on IntesaPay’s pay page. Come back and say the return URL is not the receipt — the webhook is.
10. If you created a recurring price, **Ops → Subscribe** with that `price_…` id.

Close on the three rules:

- The API key stays on the server.
- Verify the webhook, and treat retries as the same event.
- Save the idempotency key with the order. This demo mints a new one per click on purpose, so you can say what not to copy.

---

## 14. Map of files

| File | Role |
|------|------|
| `config/settings.py` | Loads `.env`, points Django at SQLite |
| `.env.example` | The four IntesaPay settings, with no real secrets |
| `harness/intesapay.py` | Every outbound endpoint |
| `harness/views.py` | Plans page, Ops actions, webhook receiver |
| `harness/webhook_sig.py` | Signature check |
| `harness/catalog_views.py` | Catalog screens: create, edit, delete, push |
| `harness/catalog_sync.py` | Forward and backward sync |
| `harness/catalog_display.py` | Turns catalog JSON into pricing cards |
| `harness/models.py` | `WebhookEvent`, `CatalogProduct`, `CatalogPrice`, `CatalogCoupon` |
| `harness/templates/harness/pricing.html` | Customer pricing page and checkout form |
| `harness/templates/harness/home.html` | Ops console |
| `harness/templates/harness/catalog.html` | Product list |

---

## 15. What this app does not do

Say this so nobody thinks the harness is the full product.

- It does not create IntesaPay accounts or API keys. That happens in the console.
- It does not log customers in. There is no partner user model.
- It does not fulfill an order. A verified webhook is stored and shown. A real app would mark the order paid inside the `if ok` branch.
- It does not remember idempotency keys. See section 3.
- It does not mix live and test money. Wallet on Ops is hardcoded to `TEST`.
- It talks to **one** account, from environment variables. A multi-tenant partner would load the key and account id per merchant.

That is the whole integration: authenticate, catalog sync, collect or checkout or subscribe, then confirm with a signature-checked webhook or a poll.
