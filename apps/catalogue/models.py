from django.core.files.storage import default_storage
from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel
from apps.core.tenancy import TenantScopedModel


def _default_consumer_visibility():
    return {
        "brand": True,
        "variant_size": True,
        "batch_number": True,
        "manufacture_date": True,
        "expiry": True,
        "regulatory_reference": True,
        "product_image": True,
        "support_contact": True,
    }


class Industry(BaseModel):
    """Platform-managed industry. Managed by platform admins, not tenants."""

    key = models.SlugField(max_length=40, unique=True)
    label = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True)
    available = models.BooleanField(default=True)
    required_fields = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "label"]

    def __str__(self):
        return self.label


class Category(BaseModel):
    """Platform-managed category within an industry. Deactivated, never deleted, so existing
    products keep their historical category reference."""

    industry = models.ForeignKey(Industry, on_delete=models.PROTECT, related_name="categories")
    key = models.SlugField(max_length=100)
    label = models.CharField(max_length=160)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    allowed_units = models.JSONField(default=list, blank=True)  # empty = any unit
    required_fields = models.JSONField(default=list, blank=True)  # extends the industry's list
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["label"]
        constraints = [
            models.UniqueConstraint(fields=["industry", "key"], name="uniq_category_industry_key")
        ]

    def __str__(self):
        return f"{self.industry.key}/{self.key}"


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


class ProductImage(TenantScopedModel):
    """A reference image uploaded for a product. Only storage paths are stored; URLs are built
    from the configured storage/CDN at read time, so changing the CDN host needs no data change."""

    storage_path = models.CharField(max_length=500)
    thumbnail_path = models.CharField(max_length=500, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    original_filename = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, related_name="+"
    )

    def __str__(self):
        return self.original_filename or str(self.pk)

    @property
    def url(self) -> str:
        return default_storage.url(self.storage_path)

    @property
    def thumbnail_url(self) -> str:
        return default_storage.url(self.thumbnail_path) if self.thumbnail_path else self.url

    def delete_files(self) -> None:
        for path in (self.storage_path, self.thumbnail_path):
            if path:
                default_storage.delete(path)


class Product(TenantScopedModel):
    """A product SKU belonging to a brand."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_REVIEW = "in_review", "In Review"
        APPROVED = "approved", "Approved"
        CHANGES_REQUESTED = "changes_requested", "Changes requested"
        REJECTED = "rejected", "Rejected"

    class SizeUnit(models.TextChoices):
        ML = "ml", "ml"
        L = "L", "L"
        G = "g", "g"
        KG = "kg", "kg"
        MG = "mg", "mg"
        UNITS = "units", "units"

    class Industry(models.TextChoices):
        COSMETICS = "cosmetics", "Cosmetics"
        PHARMACEUTICALS = "pharmaceuticals", "Pharmaceuticals"

    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=120)
    sku = models.CharField(max_length=64)
    description = models.TextField(blank=True)
    industry = models.CharField(
        max_length=20, choices=Industry.choices, blank=True, db_index=True
    )
    variant = models.CharField(max_length=80, blank=True)
    # `size` is generated from size_value + size_unit on save (e.g. "400 ml"); never written directly.
    size = models.CharField(max_length=40, blank=True, editable=False)
    size_value = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    size_unit = models.CharField(max_length=10, choices=SizeUnit.choices, blank=True)
    # e.g. {"packs": 2, "per_pack": 10, "unit": "tablets"}; separate from size.
    pack_configuration = models.JSONField(default=dict, blank=True)
    dosage_form = models.CharField(max_length=80, blank=True)
    strength = models.CharField(max_length=80, blank=True)
    category = models.CharField(max_length=100, blank=True)
    gtin = models.CharField(max_length=14, blank=True)
    consumer_visibility = models.JSONField(default=_default_consumer_visibility)
    # Overrides the tenant support contact on the verification page when set.
    support_contact = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True
    )
    is_active = models.BooleanField(default=True, db_index=True)
    review_note = models.TextField(blank=True)
    reference_image = models.ForeignKey(
        ProductImage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    submitted_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_products",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_products",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Versioning. 0 = never approved. The live columns always hold the last approved values;
    # edits to an approved product wait in pending_revision until a reviewer approves them.
    approved_version = models.PositiveIntegerField(default=0)
    pending_revision = models.JSONField(null=True, blank=True)
    # Staff can suspend public verification while an identity/regulatory/safety issue is resolved.
    public_suspended = models.BooleanField(default=False)
    suspended_reason = models.TextField(blank=True)
    # Set by staff when a GTIN clash has been investigated; allows approval to proceed.
    gtin_conflict_resolution = models.TextField(blank=True)
    # Manufacturer's request for a controlled correction of a locked field (sku / gtin).
    correction_request = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "sku"], name="uniq_product_tenant_sku")
        ]

    @property
    def is_publicly_resolvable(self) -> bool:
        """Ever approved and not suspended. A revision under review does not hide the last
        approved version."""
        ever_approved = self.approved_version > 0 or self.status == self.Status.APPROVED
        return ever_approved and not self.public_suspended

    @property
    def display_name(self) -> str:
        """e.g. "Shea Butter Body Lotion · Unscented · 400 ml" (empty parts are skipped)."""
        return " · ".join(p for p in (self.name, self.variant, self.size) if p)

    @staticmethod
    def format_size(value, unit) -> str:
        if value is None or not unit:
            return ""
        text = f"{value:f}".rstrip("0").rstrip(".") if "." in f"{value:f}" else f"{value:f}"
        return f"{text} {unit}"

    def save(self, *args, **kwargs):
        self.size = self.format_size(self.size_value, self.size_unit)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and (
            "size_value" in update_fields or "size_unit" in update_fields
        ):
            kwargs["update_fields"] = {*update_fields, "size"}
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sku} – {self.name}"


class RegulatoryRef(TenantScopedModel):
    """Regulatory approval / registration number for a product in a given market."""

    class Source(models.TextChoices):
        ENTERED_BY_MANUFACTURER = "entered_by_manufacturer", "Entered by manufacturer"
        IMPORTED = "imported", "Imported"
        VERIFIED_BY_REVIEWER = "verified_by_reviewer", "Verified by reviewer"

    class VerificationStatus(models.TextChoices):
        UNVERIFIED = "unverified", "Unverified"
        VERIFIED = "verified", "Verified"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="regulatory_refs")
    country_code = models.CharField(max_length=3)  # ISO 3166-1 alpha-3
    authority = models.CharField(max_length=255, blank=True)
    reference_number = models.CharField(max_length=255)
    valid_from = models.DateField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    source = models.CharField(
        max_length=40,
        choices=Source.choices,
        default=Source.ENTERED_BY_MANUFACTURER,
    )
    verification_status = models.CharField(
        max_length=20,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    verified_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    verification_method = models.CharField(max_length=255, blank=True)
    verification_note = models.TextField(blank=True)

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
    pack_format = models.CharField(max_length=100, blank=True)  # e.g. "5 x 12 bottle"
    # Set by the recall/withdraw action (one action, one status). Shown to consumers.
    recalled_at = models.DateTimeField(null=True, blank=True)
    recall_message = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "product", "batch_number"],
                name="uniq_batch_tenant_product_number",
            )
        ]

    @property
    def effective_status(self) -> str:
        """Stored status, except an active batch past its best-before date reads as expired."""
        if (
            self.status == self.Status.ACTIVE
            and self.expires_at
            and self.expires_at < timezone.localdate()
        ):
            return self.Status.EXPIRED
        return self.status

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
