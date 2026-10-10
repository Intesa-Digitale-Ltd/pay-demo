from django.db import models


class WebhookEvent(models.Model):
    received_at = models.DateTimeField(auto_now_add=True)
    event_type = models.CharField(max_length=128, blank=True)
    delivery_id = models.CharField(max_length=64, blank=True)
    signature_ok = models.BooleanField(default=False)
    http_headers = models.TextField(blank=True)
    payload = models.TextField()

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"{self.event_type or 'unknown'} @ {self.received_at:%H:%M:%S}"


class CatalogProduct(models.Model):
    """Local catalog row. PARTNER rows push via unmanaged sync; INTESAPAY rows are a pull mirror."""

    ORIGIN_PARTNER = "PARTNER"
    ORIGIN_INTESAPAY = "INTESAPAY"
    ORIGIN_CHOICES = (
        (ORIGIN_PARTNER, "This app"),
        (ORIGIN_INTESAPAY, "IntesaPay"),
    )

    partner_id = models.SlugField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    origin = models.CharField(max_length=16, choices=ORIGIN_CHOICES, default=ORIGIN_PARTNER)
    intesapay_id = models.CharField(max_length=80, blank=True)
    remote_source = models.CharField(max_length=16, blank=True)
    sync_error = models.TextField(blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "partner_id"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_partner_owned(self) -> bool:
        return self.origin == self.ORIGIN_PARTNER

    @property
    def uses_unmanaged_sync(self) -> bool:
        if self.remote_source == "MANAGED":
            return False
        if self.remote_source == "UNMANAGED":
            return True
        return self.origin == self.ORIGIN_PARTNER

    @property
    def source_label(self) -> str:
        if self.remote_source == "UNMANAGED" or (
            not self.remote_source and self.origin == self.ORIGIN_PARTNER
        ):
            return "Unmanaged"
        return "Managed"

    @property
    def gateway_status(self) -> str:
        if self.origin == self.ORIGIN_INTESAPAY:
            return "pulled"
        if self.sync_error:
            return "error"
        if self.intesapay_id:
            return "synced"
        return "not_synced"

    @property
    def gateway_label(self) -> str:
        return {
            "pulled": "Pulled from IntesaPay",
            "error": "Sync failed",
            "synced": "Synced",
            "not_synced": "Not synced",
        }[self.gateway_status]


class CatalogPrice(models.Model):
    ONE_TIME = "ONE_TIME"
    RECURRING = "RECURRING"

    product = models.ForeignKey(CatalogProduct, on_delete=models.CASCADE, related_name="prices")
    partner_id = models.SlugField(max_length=80)
    unit_amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default="UGX")
    pricing_type = models.CharField(max_length=16, default=ONE_TIME)
    billing_interval = models.CharField(max_length=16, blank=True)
    interval_count = models.PositiveSmallIntegerField(default=1)
    active = models.BooleanField(default=True)
    intesapay_id = models.CharField(max_length=80, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["unit_amount"]
        constraints = [
            models.UniqueConstraint(fields=["product", "partner_id"], name="uniq_catalog_price_partner"),
        ]

    def schedule_label(self) -> str:
        if self.pricing_type != self.RECURRING or not self.billing_interval:
            return "one-time"
        n = self.interval_count or 1
        unit = self.billing_interval.lower()
        if n == 1:
            return f"per {unit}"
        return f"every {n} {unit}s"


class CatalogCoupon(models.Model):
    product = models.ForeignKey(CatalogProduct, on_delete=models.CASCADE, related_name="coupons")
    partner_id = models.SlugField(max_length=80)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=64, blank=True)
    percent_off = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    amount_off = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True)
    duration = models.CharField(max_length=16, default="ONCE")
    duration_in_months = models.PositiveSmallIntegerField(null=True, blank=True)
    max_redemptions = models.PositiveIntegerField(null=True, blank=True)
    partner_price_id = models.SlugField(max_length=80, blank=True)
    active = models.BooleanField(default=True)
    intesapay_id = models.CharField(max_length=80, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["product", "partner_id"], name="uniq_catalog_coupon_partner"),
        ]

    def offer_label(self) -> str:
        if self.percent_off is not None:
            return f"{self.percent_off}% off"
        if self.amount_off is not None:
            return f"{self.amount_off} {self.currency or 'UGX'} off"
        return "offer"

