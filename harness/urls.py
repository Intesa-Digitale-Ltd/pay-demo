from django.urls import path

from . import catalog_views, views

urlpatterns = [
    path("", views.pricing, name="pricing"),
    path("ops/", views.ops, name="ops"),
    path("catalog/", catalog_views.catalog, name="catalog"),
    path("catalog/forward/", catalog_views.catalog_forward, name="catalog_forward"),
    path("catalog/backward/", catalog_views.catalog_backward, name="catalog_backward"),
    path("catalog/products/create/", catalog_views.catalog_create_product, name="catalog_create_product"),
    path("catalog/products/<int:pk>/", catalog_views.catalog_product, name="catalog_product"),
    path("catalog/products/<int:pk>/update/", catalog_views.catalog_update_product, name="catalog_update_product"),
    path("catalog/products/<int:pk>/delete/", catalog_views.catalog_delete_product, name="catalog_delete_product"),
    path("catalog/products/<int:pk>/push/", catalog_views.catalog_push_product, name="catalog_push_product"),
    path("catalog/products/<int:pk>/prices/", catalog_views.catalog_add_price, name="catalog_add_price"),
    path("catalog/prices/<int:pk>/delete/", catalog_views.catalog_delete_price, name="catalog_delete_price"),
    path("catalog/products/<int:pk>/coupons/", catalog_views.catalog_add_coupon, name="catalog_add_coupon"),
    path("catalog/coupons/<int:pk>/delete/", catalog_views.catalog_delete_coupon, name="catalog_delete_coupon"),
    path("actions/me/", views.action_me, name="action_me"),
    path("actions/collect/", views.action_collect, name="action_collect"),
    path("actions/poll/", views.action_poll, name="action_poll"),
    path("actions/wallet/", views.action_wallet, name="action_wallet"),
    path("actions/payout/", views.action_payout, name="action_payout"),
    path("actions/list-products/", views.action_list_products, name="action_list_products"),
    path("actions/create-product/", views.action_create_product, name="action_create_product"),
    path("actions/upsert-unmanaged/", views.action_upsert_unmanaged, name="action_upsert_unmanaged"),
    path("actions/create-price/", views.action_create_price, name="action_create_price"),
    path("actions/subscribe/", views.action_subscribe, name="action_subscribe"),
    path("actions/checkout/", views.action_checkout, name="action_checkout"),
    path("actions/clear-events/", views.clear_events, name="clear_events"),
    path("webhooks/intesapay/", views.webhook_intesapay, name="webhook_intesapay"),
]
