from django.db import models

from apps.core.tenancy import TenantScopedModel


class Brand(TenantScopedModel):
    """A brand name owned by the tenant (manufacturer)."""

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "slug"], name="uniq_brand_tenant_slug")
        ]

    def __str__(self):
        return self.name


class Product(TenantScopedModel):
    """A product SKU belonging to a brand."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        DISCONTINUED = "discontinued", "Discontinued"

    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=255)
    sku = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sku"], name="uniq_product_tenant_sku")
        ]

    def __str__(self):
        return f"{self.sku} – {self.name}"


class RegulatoryRef(TenantScopedModel):
    """Regulatory approval / registration number for a product in a given market."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="regulatory_refs")
    country_code = models.CharField(max_length=3)  # ISO 3166-1 alpha-3
    authority = models.CharField(max_length=255, blank=True)
    reference_number = models.CharField(max_length=255)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "product", "country_code", "reference_number"],
                name="uniq_regref_tenant_product_country_ref",
            )
        ]

    def __str__(self):
        return f"{self.reference_number} ({self.country_code})"


class Batch(TenantScopedModel):
    """A production batch of a product. Codes are issued against batches."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RECALLED = "recalled", "Recalled"
        EXPIRED = "expired", "Expired"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="batches")
    batch_number = models.CharField(max_length=100)
    manufactured_at = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)
    quantity = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "product", "batch_number"],
                name="uniq_batch_tenant_product_number",
            )
        ]

    def __str__(self):
        return f"{self.batch_number} ({self.product.sku})"


class BulkImportJob(TenantScopedModel):
    """Tracks a CSV bulk-import task (brands/products/regulatory refs/batches)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    class ImportType(models.TextChoices):
        BRANDS = "brands", "Brands"
        PRODUCTS = "products", "Products"
        BATCHES = "batches", "Batches"

    import_type = models.CharField(max_length=20, choices=ImportType.choices)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    file_name = models.CharField(max_length=255)
    rows_total = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    rows_failed = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list)
    submitted_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, related_name="+"
    )

    def __str__(self):
        return f"{self.import_type} import #{self.pk} [{self.status}]"
