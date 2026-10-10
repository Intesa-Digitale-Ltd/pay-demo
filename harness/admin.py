from django.contrib import admin

from .models import WebhookEvent


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "event_type", "signature_ok", "delivery_id")
    list_filter = ("signature_ok", "event_type")
    readonly_fields = ("received_at", "event_type", "delivery_id", "signature_ok", "http_headers", "payload")
