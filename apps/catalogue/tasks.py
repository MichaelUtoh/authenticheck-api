"""Celery tasks for catalogue bulk import.

Each task is idempotent: re-running it from a PROCESSING or PENDING job replays
the import. Rows that already exist (unique constraint) are skipped, not failed.
"""

import csv
import io
import logging
import uuid

from celery import shared_task
from django.db import IntegrityError, transaction
from django.utils.text import slugify

from apps.core.tenancy import tenant_context

logger = logging.getLogger(__name__)

_MAX_ERRORS = 100  # stop accumulating row errors beyond this


@shared_task(bind=True, acks_late=True, max_retries=3, default_retry_delay=30)
def process_bulk_import(self, job_id: str, csv_content: str):
    from .models import BulkImportJob

    try:
        job = BulkImportJob.unscoped.select_for_update().get(pk=job_id)
    except BulkImportJob.DoesNotExist:
        logger.error("BulkImportJob %s not found", job_id)
        return

    if job.status not in (BulkImportJob.Status.PENDING, BulkImportJob.Status.PROCESSING):
        return

    job.status = BulkImportJob.Status.PROCESSING
    job.save(update_fields=["status", "updated_at"])

    handler = {
        BulkImportJob.ImportType.BRANDS: _import_brands,
        BulkImportJob.ImportType.PRODUCTS: _import_products,
        BulkImportJob.ImportType.BATCHES: _import_batches,
    }.get(job.import_type)

    if handler is None:
        job.status = BulkImportJob.Status.FAILED
        job.errors = [{"error": f"Unknown import type: {job.import_type}"}]
        job.save(update_fields=["status", "errors", "updated_at"])
        return

    rows_total, rows_imported, rows_failed, errors = handler(job, csv_content)
    job.rows_total = rows_total
    job.rows_imported = rows_imported
    job.rows_failed = rows_failed
    job.errors = errors
    job.status = BulkImportJob.Status.DONE if rows_failed == 0 else BulkImportJob.Status.FAILED
    job.save(update_fields=["status", "rows_total", "rows_imported", "rows_failed", "errors", "updated_at"])


def _parse_csv(csv_content: str):
    reader = csv.DictReader(io.StringIO(csv_content))
    return list(reader), reader.fieldnames or []


def _import_brands(job, csv_content: str):
    from .models import Brand

    rows, _ = _parse_csv(csv_content)
    rows_imported = rows_failed = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        name = (row.get("name") or "").strip()
        slug = (row.get("slug") or slugify(name)).strip().lower()

        if not name:
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": "name is required"})
            continue

        try:
            with transaction.atomic(), tenant_context(job.tenant_id):
                Brand.objects.get_or_create(slug=slug, defaults={"name": name})
            rows_imported += 1
        except Exception as exc:
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": str(exc)})

    return len(rows), rows_imported, rows_failed, errors


def _import_products(job, csv_content: str):
    from .models import Brand, Product

    rows, _ = _parse_csv(csv_content)
    rows_imported = rows_failed = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        brand_slug = (row.get("brand_slug") or "").strip().lower()
        name = (row.get("name") or "").strip()
        sku = (row.get("sku") or "").strip().upper()

        if not (brand_slug and name and sku):
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": "brand_slug, name and sku are required"})
            continue

        try:
            with transaction.atomic(), tenant_context(job.tenant_id):
                brand = Brand.objects.get(slug=brand_slug)
                Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        "brand": brand,
                        "name": name,
                        "description": (row.get("description") or "").strip(),
                    },
                )
            rows_imported += 1
        except Brand.DoesNotExist:
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": f"Brand '{brand_slug}' not found"})
        except Exception as exc:
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": str(exc)})

    return len(rows), rows_imported, rows_failed, errors


def _import_batches(job, csv_content: str):
    from .models import Batch, Product

    rows, _ = _parse_csv(csv_content)
    rows_imported = rows_failed = 0
    errors = []

    for i, row in enumerate(rows, start=2):
        sku = (row.get("sku") or "").strip().upper()
        batch_number = (row.get("batch_number") or "").strip()

        if not (sku and batch_number):
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": "sku and batch_number are required"})
            continue

        try:
            quantity = int(row.get("quantity") or 0)
        except (ValueError, TypeError):
            quantity = 0

        try:
            with transaction.atomic(), tenant_context(job.tenant_id):
                product = Product.objects.get(sku=sku)
                Batch.objects.get_or_create(
                    product=product,
                    batch_number=batch_number,
                    defaults={
                        "quantity": quantity,
                        "manufactured_at": row.get("manufactured_at") or None,
                        "expires_at": row.get("expires_at") or None,
                        "notes": (row.get("notes") or "").strip(),
                    },
                )
            rows_imported += 1
        except Product.DoesNotExist:
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": f"Product with SKU '{sku}' not found"})
        except Exception as exc:
            rows_failed += 1
            if len(errors) < _MAX_ERRORS:
                errors.append({"row": i, "error": str(exc)})

    return len(rows), rows_imported, rows_failed, errors


@shared_task(acks_late=True)
def cleanup_unattached_images(batch_size: int = 500) -> int:
    """Delete uploads that no product (or pending revision) references once they are older than
    UNATTACHED_IMAGE_TTL_HOURS. Files are removed only after the row delete commits."""
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone

    from . import workflow
    from .models import ProductImage

    cutoff = timezone.now() - timedelta(hours=settings.UNATTACHED_IMAGE_TTL_HOURS)
    removed = 0
    for image in ProductImage.unscoped.filter(created_at__lt=cutoff).order_by("created_at")[:batch_size]:
        if workflow.image_in_use(image):
            continue
        with transaction.atomic():
            transaction.on_commit(image.delete_files)
            image.delete()
        removed += 1
    if removed:
        logger.info("Removed %d unattached product images", removed)
    return removed
