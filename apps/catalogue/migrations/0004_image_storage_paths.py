from django.db import migrations, models


def url_to_path(apps, schema_editor):
    """Existing rows stored a URL; keep only the storage path (no host, no leading slash)."""
    ProductImage = apps.get_model("catalogue", "ProductImage")
    for image in ProductImage.objects.all():
        path = image.url.split("://", 1)[-1]
        if "/" in path and not image.url.startswith("/"):
            path = path.split("/", 1)[1]  # drop the host
        image.storage_path = path.lstrip("/").removeprefix("media/")
        image.save(update_fields=["storage_path"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalogue", "0003_product_workflow_decisions"),
    ]

    operations = [
        migrations.AddField(
            model_name="productimage",
            name="storage_path",
            field=models.CharField(default="", max_length=500),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="productimage",
            name="thumbnail_path",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="productimage",
            name="size_bytes",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RunPython(url_to_path, migrations.RunPython.noop),
        migrations.RemoveField(model_name="productimage", name="url"),
    ]
