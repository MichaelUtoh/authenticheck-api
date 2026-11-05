"""Give every pre-existing code a new 12-character code, keeping the old value in legacy_code.

Reversible: the reverse restores `code` from `legacy_code`. Codes issued after the
conversion have no legacy_code and keep their (already short) value on reverse.
"""
from django.db import migrations

from apps.issuance.codes import generate_code, is_valid_format

CHUNK = 2000


def convert(apps, schema_editor):
    Code = apps.get_model("issuance", "Code")
    taken = set(Code.objects.values_list("code", flat=True))
    pending = [
        pk for pk, code in Code.objects.filter(legacy_code__isnull=True).values_list("pk", "code")
        if not is_valid_format(code)
    ]
    for start in range(0, len(pending), CHUNK):
        rows = list(Code.objects.filter(pk__in=pending[start:start + CHUNK]))
        for row in rows:
            new = generate_code()
            while new in taken:
                new = generate_code()
            taken.add(new)
            row.legacy_code, row.code = row.code, new
        # Two passes avoid unique clashes between old and new values within a chunk.
        Code.objects.bulk_update(rows, ["legacy_code"])
        Code.objects.bulk_update(rows, ["code"])


def revert(apps, schema_editor):
    Code = apps.get_model("issuance", "Code")
    rows = list(Code.objects.filter(legacy_code__isnull=False))
    for row in rows:
        row.code, row.legacy_code = row.legacy_code, None
    Code.objects.bulk_update(rows, ["legacy_code"])
    Code.objects.bulk_update(rows, ["code"])


class Migration(migrations.Migration):
    dependencies = [("issuance", "0004_code_legacy_code")]
    operations = [migrations.RunPython(convert, revert)]
