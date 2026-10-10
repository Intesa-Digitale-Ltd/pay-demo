from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("harness", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CatalogProduct",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("partner_id", models.SlugField(max_length=80, unique=True)),
                ("name", models.CharField(max_length=200)),
                ("description", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("origin", models.CharField(choices=[("PARTNER", "This app"), ("INTESAPAY", "IntesaPay")], default="PARTNER", max_length=16)),
                ("intesapay_id", models.CharField(blank=True, max_length=80)),
                ("remote_source", models.CharField(blank=True, max_length=16)),
                ("sync_error", models.TextField(blank=True)),
                ("synced_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["name", "partner_id"]},
        ),
        migrations.CreateModel(
            name="CatalogPrice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("partner_id", models.SlugField(max_length=80)),
                ("unit_amount", models.DecimalField(decimal_places=2, max_digits=18)),
                ("currency", models.CharField(default="UGX", max_length=3)),
                ("pricing_type", models.CharField(default="ONE_TIME", max_length=16)),
                ("billing_interval", models.CharField(blank=True, max_length=16)),
                ("interval_count", models.PositiveSmallIntegerField(default=1)),
                ("active", models.BooleanField(default=True)),
                ("intesapay_id", models.CharField(blank=True, max_length=80)),
                ("synced_at", models.DateTimeField(blank=True, null=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="prices", to="harness.catalogproduct")),
            ],
            options={"ordering": ["unit_amount"]},
        ),
        migrations.CreateModel(
            name="CatalogCoupon",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("partner_id", models.SlugField(max_length=80)),
                ("name", models.CharField(max_length=200)),
                ("code", models.CharField(blank=True, max_length=64)),
                ("percent_off", models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ("amount_off", models.DecimalField(blank=True, decimal_places=2, max_digits=18, null=True)),
                ("currency", models.CharField(blank=True, max_length=3)),
                ("duration", models.CharField(default="ONCE", max_length=16)),
                ("duration_in_months", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("max_redemptions", models.PositiveIntegerField(blank=True, null=True)),
                ("partner_price_id", models.SlugField(blank=True, max_length=80)),
                ("active", models.BooleanField(default=True)),
                ("intesapay_id", models.CharField(blank=True, max_length=80)),
                ("synced_at", models.DateTimeField(blank=True, null=True)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="coupons", to="harness.catalogproduct")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddConstraint(
            model_name="catalogprice",
            constraint=models.UniqueConstraint(fields=("product", "partner_id"), name="uniq_catalog_price_partner"),
        ),
        migrations.AddConstraint(
            model_name="catalogcoupon",
            constraint=models.UniqueConstraint(fields=("product", "partner_id"), name="uniq_catalog_coupon_partner"),
        ),
    ]
