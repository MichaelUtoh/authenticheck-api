"""Tests for reference-image upload, processing, deletion rules and cleanup."""

from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.catalogue.models import Batch, Brand, Product, ProductImage
from apps.catalogue.tasks import cleanup_unattached_images
from apps.core.tenancy import tenant_context
from apps.issuance.models import Code, IssuanceJob
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db

API = "/api/v1"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.MEDIA_URL = "/media/"
    return tmp_path


@pytest.fixture
def tenant():
    return Tenant.objects.create(legal_name="Image Co", status=Tenant.Status.APPROVED)


def _client(role, tenant, email):
    user = User.objects.create_user(email, "a-long-test-password")
    Membership.objects.create(user=user, tenant=tenant, role=role)
    c = APIClient()
    c.force_authenticate(user=user)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


@pytest.fixture
def client(tenant):
    return _client(Membership.Role.OWNER, tenant, "owner@image.test")


@pytest.fixture
def brand(tenant):
    with tenant_context(tenant.pk):
        return Brand.objects.create(tenant=tenant, name="Glow", slug="glow")


@pytest.fixture
def staff_client():
    staff = User.objects.create_user("staff@image.test", "a-long-test-password", is_staff=True)
    c = APIClient()
    c.force_authenticate(user=staff, token={"amr": ["pwd", "otp"]})
    return c


def make_image(size=(800, 600), fmt="JPEG", mode="RGB", name="photo", exif=None, color=(200, 60, 60)):
    img = Image.new(mode, size, color if mode == "RGB" else color + (128,))
    buf = BytesIO()
    kwargs = {"exif": exif} if exif is not None else {}
    img.save(buf, fmt, **kwargs)
    ext = "png" if fmt == "PNG" else "jpg"
    return SimpleUploadedFile(f"{name}.{ext}", buf.getvalue(), content_type=f"image/{ext}")


def upload(client, file):
    return client.post(f"{API}/catalogue/images", {"file": file}, format="multipart")


def stored(media: Path, path: str) -> Path:
    return media / path


def product_with(client, brand, **extra):
    resp = client.post(
        f"{API}/catalogue/products",
        {
            "brand_id": str(brand.pk), "name": "Shea Butter Body Lotion", "industry": "cosmetics",
            "variant": "Unscented", "size_value": "400", "size_unit": "ml",
            "category": "body-care-lotion", "sku": "IMG-001", **extra,
        },
        format="json",
    )
    assert resp.status_code == 201, resp.data
    return resp.data


# ---------------------------------------------------------------------------
# Upload and processing
# ---------------------------------------------------------------------------


def test_upload_returns_urls_and_stores_both_files(client, tenant, media):
    resp = upload(client, make_image())
    assert resp.status_code == 201, resp.data
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    assert resp.data["url"] == f"/media/{image.storage_path}"
    assert resp.data["thumbnail_url"] == f"/media/{image.thumbnail_path}"
    assert image.storage_path.startswith(f"tenants/{tenant.pk}/images/")
    assert stored(media, image.storage_path).exists()
    assert stored(media, image.thumbnail_path).exists()


def test_upload_resizes_and_creates_thumbnail(client, media):
    resp = upload(client, make_image(size=(3000, 2000)))
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    with Image.open(stored(media, image.storage_path)) as main:
        assert max(main.size) == 1600
    with Image.open(stored(media, image.thumbnail_path)) as thumb:
        assert max(thumb.size) == 320
    assert (resp.data["width"], resp.data["height"]) == (1600, 1067)


def test_upload_does_not_upscale(client, media):
    resp = upload(client, make_image(size=(500, 400)))
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    with Image.open(stored(media, image.storage_path)) as main:
        assert main.size == (500, 400)


def test_upload_strips_exif(client, media):
    exif = Image.Exif()
    exif[0x010F] = "SecretCameraMaker"  # Make
    exif[0x0132] = "2026:10:30 10:00:00"  # DateTime
    resp = upload(client, make_image(exif=exif.tobytes()))
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    for path in (image.storage_path, image.thumbnail_path):
        raw = stored(media, path).read_bytes()
        assert b"SecretCameraMaker" not in raw
        with Image.open(stored(media, path)) as out:
            assert len(out.getexif()) == 0


def test_upload_applies_exif_orientation(client, media):
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees clockwise to display
    resp = upload(client, make_image(size=(800, 500), exif=exif.tobytes()))
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    with Image.open(stored(media, image.storage_path)) as main:
        assert main.size == (500, 800)  # portrait after the orientation is baked in


def test_png_with_transparency_stays_png(client, media):
    resp = upload(client, make_image(fmt="PNG", mode="RGBA", color=(10, 200, 10)))
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    assert image.storage_path.endswith(".png")
    with Image.open(stored(media, image.storage_path)) as main:
        assert main.mode == "RGBA"
        assert main.getpixel((0, 0))[3] == 128  # alpha preserved


def test_opaque_png_becomes_jpeg(client):
    resp = upload(client, make_image(fmt="PNG", mode="RGB"))
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    assert image.storage_path.endswith(".jpg")


@pytest.mark.parametrize("size,reason,hint", [
    ((300, 800), "too_small", "at least 400 px on its shortest side"),
    ((800, 300), "too_small", "at least 400 px on its shortest side"),
    ((4001, 1000), "too_large", "longest side is 4000 px or less"),
    ((1000, 4001), "too_large", "longest side is 4000 px or less"),
])
def test_dimension_limits_explain_what_to_do(client, size, reason, hint):
    resp = upload(client, make_image(size=size))
    assert resp.status_code == 422
    error = resp.data["error"]
    assert error["code"] == "image_invalid"
    assert f"{size[0]} x {size[1]} px" in error["message"]
    assert hint in error["message"]
    assert error["details"]["reason"] == reason
    assert error["details"]["received_width_px"] == size[0]
    assert error["details"]["limits"]["min_side_px"] == 400
    assert not ProductImage.unscoped.exists()


def test_non_image_rejected_by_content_not_extension(client):
    fake = SimpleUploadedFile("evil.jpg", b"<html><script>alert(1)</script></html>", "image/jpeg")
    resp = upload(client, fake)
    assert resp.status_code == 415
    error = resp.data["error"]
    assert error["code"] == "unsupported_media_type"
    assert "PNG or JPEG" in error["message"]
    assert error["details"]["limits"]["accepted_types"] == ["image/png", "image/jpeg"]


def test_gif_rejected(client):
    buf = BytesIO()
    Image.new("RGB", (800, 600)).save(buf, "GIF")
    resp = upload(client, SimpleUploadedFile("x.png", buf.getvalue(), "image/png"))
    assert resp.status_code == 415


def test_corrupt_image_rejected(client):
    good = make_image().read()
    resp = upload(client, SimpleUploadedFile("broken.jpg", good[: len(good) // 3], "image/jpeg"))
    assert resp.status_code == 422
    assert resp.data["error"]["code"] == "image_invalid"
    assert resp.data["error"]["details"]["reason"] == "unreadable"
    assert "may be damaged" in resp.data["error"]["message"]


def test_file_too_large_states_size_and_limit(client):
    data = b"\xff\xd8\xff" + b"\x00" * (7 * 1024 * 1024)
    resp = upload(client, SimpleUploadedFile("big.jpg", data, "image/jpeg"))
    assert resp.status_code == 413
    error = resp.data["error"]
    assert error["code"] == "file_too_large"
    assert "7.0 MB" in error["message"] and "5.0 MB limit" in error["message"]
    assert "compress" in error["message"]
    assert error["details"]["limits"]["max_bytes"] == 5 * 1024 * 1024
    assert error["details"]["received_bytes"] == len(data)


def test_missing_file(client):
    resp = client.post(f"{API}/catalogue/images", {}, format="multipart")
    assert resp.status_code == 400
    assert resp.data["error"]["details"]["file"] == ["Choose an image to upload."]


def test_original_filename_is_stored_but_never_used_in_path(client):
    resp = upload(client, make_image(name="../../etc/passwd"))
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    assert "passwd" not in image.storage_path
    assert ".." not in image.storage_path


def test_viewer_cannot_upload(tenant):
    viewer = _client(Membership.Role.VIEWER, tenant, "viewer@image.test")
    assert upload(viewer, make_image()).status_code == 403


def test_editor_can_upload(tenant):
    editor = _client(Membership.Role.PRODUCTION_QUALITY, tenant, "editor@image.test")
    assert upload(editor, make_image()).status_code == 201


# ---------------------------------------------------------------------------
# Attaching and tenant isolation
# ---------------------------------------------------------------------------


def test_attached_image_is_exposed_on_product(client, brand):
    image = upload(client, make_image()).data
    product = product_with(client, brand, reference_image_id=image["id"])
    assert product["reference_image_id"] == image["id"]
    assert product["reference_image"]["url"] == image["url"]
    assert product["reference_image"]["thumbnail_url"] == image["thumbnail_url"]


def test_cannot_attach_another_tenants_image(client, brand):
    other = Tenant.objects.create(legal_name="Rival", status=Tenant.Status.APPROVED)
    rival = _client(Membership.Role.OWNER, other, "owner@rival.test")
    foreign = upload(rival, make_image()).data
    resp = client.post(
        f"{API}/catalogue/products",
        {"brand_id": str(brand.pk), "name": "Lotion", "sku": "IMG-002",
         "reference_image_id": foreign["id"]},
        format="json",
    )
    assert resp.status_code == 400
    assert "reference_image_id" in resp.data["error"]["details"]


def test_cannot_delete_another_tenants_image(client, tenant):
    other = Tenant.objects.create(legal_name="Rival", status=Tenant.Status.APPROVED)
    rival = _client(Membership.Role.OWNER, other, "owner@rival.test")
    foreign = upload(rival, make_image()).data
    assert client.delete(f"{API}/catalogue/images/{foreign['id']}").status_code == 404


def test_verify_exposes_image_urls(client, brand, staff_client, tenant):
    image = upload(client, make_image()).data
    product = product_with(client, brand, reference_image_id=image["id"])
    client.post(f"{API}/catalogue/products/{product['id']}/submit")
    staff_client.post(f"{API}/platform/products/{product['id']}/approve", format="json")
    owner = User.objects.get(email="owner@image.test")
    with tenant_context(tenant.pk):
        p = Product.objects.get(pk=product["id"])
        batch = Batch.objects.create(tenant=tenant, product=p, batch_number="B1", quantity=1)
        job = IssuanceJob.objects.create(
            tenant=tenant, batch=batch, quantity_requested=1, submitted_by=owner,
            status=IssuanceJob.Status.DONE, quantity_issued=1,
        )
        Code.objects.create(tenant=tenant, code="IMGCODE0000001", batch=batch, product=p, issuance_job=job)

    resp = APIClient().get(f"{API}/verify/IMGCODE0000001")
    assert resp.status_code == 200
    assert resp.data["product_image"] == {
        "url": image["url"], "thumbnail_url": image["thumbnail_url"],
    }


# ---------------------------------------------------------------------------
# Deletion rules
# ---------------------------------------------------------------------------


def test_delete_unattached_image_removes_row_and_files(client, media, django_capture_on_commit_callbacks):
    resp = upload(client, make_image())
    image = ProductImage.unscoped.get(pk=resp.data["id"])
    main, thumb = stored(media, image.storage_path), stored(media, image.thumbnail_path)
    assert main.exists() and thumb.exists()

    with django_capture_on_commit_callbacks(execute=True):
        assert client.delete(f"{API}/catalogue/images/{image.pk}").status_code == 204

    assert not ProductImage.unscoped.filter(pk=image.pk).exists()
    assert not main.exists() and not thumb.exists()


def test_cannot_delete_image_attached_to_product(client, brand, media):
    image = upload(client, make_image()).data
    product = product_with(client, brand, reference_image_id=image["id"])
    resp = client.delete(f"{API}/catalogue/images/{image['id']}")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "image_in_use"
    # the product and its image are untouched
    detail = client.get(f"{API}/catalogue/products/{product['id']}").data
    assert detail["reference_image_id"] == image["id"]
    assert ProductImage.unscoped.filter(pk=image["id"]).exists()


def test_cannot_delete_image_referenced_by_pending_revision(client, brand, staff_client):
    old = upload(client, make_image()).data
    new = upload(client, make_image(color=(10, 10, 200))).data
    product = product_with(client, brand, reference_image_id=old["id"])
    client.post(f"{API}/catalogue/products/{product['id']}/submit")
    staff_client.post(f"{API}/platform/products/{product['id']}/approve", format="json")
    patched = client.patch(
        f"{API}/catalogue/products/{product['id']}", {"reference_image_id": new["id"]}, format="json"
    )
    assert patched.data["pending_revision"] == {"reference_image_id": new["id"]}

    resp = client.delete(f"{API}/catalogue/images/{new['id']}")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "image_in_use"


def test_replacing_image_on_approved_product_keeps_live_image_until_review(client, brand, staff_client):
    old = upload(client, make_image()).data
    new = upload(client, make_image(color=(10, 10, 200))).data
    product = product_with(client, brand, reference_image_id=old["id"])
    client.post(f"{API}/catalogue/products/{product['id']}/submit")
    staff_client.post(f"{API}/platform/products/{product['id']}/approve", format="json")
    patched = client.patch(
        f"{API}/catalogue/products/{product['id']}", {"reference_image_id": new["id"]}, format="json"
    ).data
    assert patched["reference_image_id"] == old["id"]  # live image unchanged
    approved = staff_client.post(f"{API}/platform/products/{product['id']}/approve", format="json").data
    assert approved["reference_image_id"] == new["id"]
    assert approved["reference_image"]["url"] == new["url"]


# ---------------------------------------------------------------------------
# Cleanup of unattached uploads
# ---------------------------------------------------------------------------


def _age(image_id, hours):
    ProductImage.unscoped.filter(pk=image_id).update(created_at=timezone.now() - timedelta(hours=hours))


def test_cleanup_removes_only_old_unattached_images(client, brand, media, django_capture_on_commit_callbacks):
    old_orphan = upload(client, make_image()).data["id"]
    fresh_orphan = upload(client, make_image()).data["id"]
    attached = upload(client, make_image()).data["id"]
    product_with(client, brand, reference_image_id=attached)
    for image_id in (old_orphan, attached):
        _age(image_id, 48)
    orphan = ProductImage.unscoped.get(pk=old_orphan)
    orphan_files = [stored(media, orphan.storage_path), stored(media, orphan.thumbnail_path)]
    assert all(f.exists() for f in orphan_files)

    with django_capture_on_commit_callbacks(execute=True):
        assert cleanup_unattached_images() == 1

    assert not ProductImage.unscoped.filter(pk=old_orphan).exists()
    assert not any(f.exists() for f in orphan_files)
    assert ProductImage.unscoped.filter(pk=fresh_orphan).exists()  # younger than the TTL
    assert ProductImage.unscoped.filter(pk=attached).exists()  # still referenced


def test_cleanup_keeps_image_referenced_by_pending_revision(client, brand, staff_client):
    old = upload(client, make_image()).data
    new = upload(client, make_image(color=(10, 10, 200))).data["id"]
    product = product_with(client, brand, reference_image_id=old["id"])
    client.post(f"{API}/catalogue/products/{product['id']}/submit")
    staff_client.post(f"{API}/platform/products/{product['id']}/approve", format="json")
    client.patch(f"{API}/catalogue/products/{product['id']}", {"reference_image_id": new}, format="json")
    _age(new, 72)
    assert cleanup_unattached_images() == 0
    assert ProductImage.unscoped.filter(pk=new).exists()


def test_cleanup_collects_image_after_it_is_replaced_and_ttl_passes(client, brand):
    old = upload(client, make_image()).data["id"]
    new = upload(client, make_image(color=(10, 10, 200))).data["id"]
    product = product_with(client, brand, reference_image_id=old)
    client.patch(f"{API}/catalogue/products/{product['id']}", {"reference_image_id": new}, format="json")
    _age(old, 48)
    assert cleanup_unattached_images() == 1
    assert not ProductImage.unscoped.filter(pk=old).exists()
    assert ProductImage.unscoped.filter(pk=new).exists()
