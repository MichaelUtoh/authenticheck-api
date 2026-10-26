import pytest

from apps.accounts.models import User
from apps.audit.models import AuditEvent, AuditImmutableError
from apps.audit.services import AuditError, record
from apps.tenants.models import Tenant


@pytest.mark.django_db
def test_record_captures_actor_target_and_before_after():
    actor = User.objects.create_user("qa@example.com", "a-long-test-password")
    tenant = Tenant.objects.create(legal_name="Acme Cosmetics")

    event = record(
        action="tenant.approved",
        actor=actor,
        tenant=tenant,
        target=tenant,
        reason="Documents verified",
        before={"status": "pending"},
        after={"status": "approved"},
    )

    assert event.actor == actor
    assert event.target_type == "tenants.tenant"
    assert event.target_id == str(tenant.pk)
    assert event.before == {"status": "pending"} and event.after == {"status": "approved"}


@pytest.mark.django_db
def test_events_cannot_be_modified_or_deleted():
    event = record(action="system.test")
    event.reason = "tampered"
    with pytest.raises(AuditImmutableError):
        event.save()
    with pytest.raises(AuditImmutableError):
        event.delete()
    with pytest.raises(AuditImmutableError):
        AuditEvent.objects.all().update(reason="tampered")
    with pytest.raises(AuditImmutableError):
        AuditEvent.objects.all().delete()


@pytest.mark.django_db(transaction=True)
def test_record_outside_a_transaction_is_refused():
    with pytest.raises(AuditError):
        record(action="system.test")
