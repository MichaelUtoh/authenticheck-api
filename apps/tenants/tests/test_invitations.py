"""Tests for inviting users to a tenant with a role."""

import re
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.audit.models import AuditEvent
from apps.identity.models import Invitation
from apps.tenants.models import Membership, Tenant

User = get_user_model()
pytestmark = pytest.mark.django_db

API = "/api/v1"
PASSWORD = "a-long-test-password"


@pytest.fixture(autouse=True)
def _fresh_throttles():
    cache.clear()


@pytest.fixture(autouse=True)
def _run_on_commit(monkeypatch):
    """Invitation emails are sent on commit. Tests run inside a transaction that never commits, so
    run on_commit callbacks immediately."""
    monkeypatch.setattr("django.db.transaction.on_commit", lambda func, *a, **k: func())


def _client(user, tenant):
    c = APIClient()
    c.force_authenticate(user=user)
    c.credentials(HTTP_X_TENANT_ID=str(tenant.pk))
    return c


def _member(tenant, email, role):
    user = User.objects.create_user(email, PASSWORD, full_name=email.split("@")[0].title())
    Membership.objects.create(user=user, tenant=tenant, role=role)
    return user


@pytest.fixture
def tenant():
    return Tenant.objects.create(
        legal_name="Invite Co", trading_name="Invite Cosmetics", status=Tenant.Status.APPROVED
    )


@pytest.fixture
def owner(tenant):
    return _member(tenant, "owner@invite.test", Membership.Role.OWNER)


@pytest.fixture
def client(owner, tenant):
    return _client(owner, tenant)


def invite(client, email="new@invite.test", role="production_quality", **extra):
    return client.post(
        f"{API}/tenants/me/invitations", {"email": email, "role": role, **extra}, format="json"
    )


def emailed_token() -> str:
    match = re.search(r"token=([\w-]+)", mail.outbox[-1].body)
    assert match, mail.outbox[-1].body
    return match.group(1)


def accept(token, client=None, **body):
    return (client or APIClient()).post(
        f"{API}/invitations/accept", {"token": token, **body}, format="json"
    )


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def test_owner_invites_production_quality_and_the_link_is_emailed(client, tenant):
    resp = invite(client, "Editor@Invite.test", "production_quality", can_submit_products=True)
    assert resp.status_code == 201, resp.data
    assert resp.data["email"] == "editor@invite.test"  # normalised
    assert resp.data["role"] == "production_quality" and resp.data["can_submit_products"] is True
    assert resp.data["status"] == "pending"
    assert resp.data["invited_by"]["name"] == "Owner"
    assert "token" not in resp.data  # the token only ever travels by email

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["editor@invite.test"]
    assert "Invite Cosmetics" in message.subject
    assert "Production/Quality" in message.body and "Owner" in message.body
    assert "/accept-invite?token=" in message.body


def test_raw_token_is_not_stored(client):
    invite(client)
    raw = emailed_token()
    stored = Invitation.objects.get()
    assert raw not in (stored.token_hash, "")
    assert len(stored.token_hash) == 64  # SHA-256 hex


def test_invitation_expires_after_the_configured_days(client, settings):
    settings.INVITATION_TTL_DAYS = 3
    resp = invite(client)
    expires = Invitation.objects.get(pk=resp.data["id"]).expires_at
    assert timedelta(days=2, hours=23) < expires - timezone.now() <= timedelta(days=3)


def test_creating_an_invitation_is_audited(client):
    resp = invite(client, role="viewer")
    event = AuditEvent.objects.get(action="tenant.invitation.created", target_id=resp.data["id"])
    assert event.after["email"] == "new@invite.test" and event.after["role"] == "viewer"


@pytest.mark.parametrize("role", ["production_quality", "investigator", "viewer", "admin"])
def test_owner_can_invite_every_invitable_role(client, role):
    assert invite(client, role=role).status_code == 201


def test_cannot_invite_an_owner_or_unknown_role(client):
    assert invite(client, role="owner").status_code == 400
    assert invite(client, role="superuser").status_code == 400


def test_can_submit_products_only_applies_to_production_quality(client):
    resp = invite(client, role="viewer", can_submit_products=True)
    assert resp.status_code == 400
    assert "can_submit_products" in resp.data["error"]["details"]


def test_admin_can_invite_production_quality_but_not_admins(tenant):
    admin = _client(_member(tenant, "admin@invite.test", Membership.Role.ADMIN), tenant)
    assert invite(admin, "e@invite.test", "production_quality").status_code == 201
    forbidden = invite(admin, "a@invite.test", "admin")
    assert forbidden.status_code == 403


@pytest.mark.parametrize("role", [Membership.Role.PRODUCTION_QUALITY, Membership.Role.INVESTIGATOR, Membership.Role.VIEWER])
def test_lower_roles_cannot_invite(tenant, role):
    user = _client(_member(tenant, f"{role}@invite.test", role), tenant)
    assert invite(user).status_code == 403


def test_unauthenticated_cannot_invite():
    assert APIClient().post(f"{API}/tenants/me/invitations", {}, format="json").status_code in (401, 403)


def test_duplicate_pending_invitation_is_rejected(client):
    invite(client)
    resp = invite(client)
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "invitation_pending"


def test_cannot_invite_an_existing_member(client, tenant):
    _member(tenant, "already@invite.test", Membership.Role.VIEWER)
    resp = invite(client, "already@invite.test")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "already_member"


def test_expired_pending_invitation_can_be_replaced(client):
    first = invite(client).data["id"]
    Invitation.objects.filter(pk=first).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert invite(client).status_code == 201
    assert Invitation.objects.get(pk=first).status == Invitation.Status.EXPIRED


def test_same_email_can_be_invited_to_two_tenants(client):
    other = Tenant.objects.create(legal_name="Other Co", status=Tenant.Status.APPROVED)
    other_owner = _client(_member(other, "owner@other.test", Membership.Role.OWNER), other)
    assert invite(client).status_code == 201
    assert invite(other_owner).status_code == 201


def test_email_failure_does_not_fail_the_invitation(client, monkeypatch):
    def boom(*a, **k):
        raise OSError("smtp down")

    monkeypatch.setattr("apps.tenants.invitations.send_mail", boom)
    assert invite(client).status_code == 201
    assert Invitation.objects.count() == 1  # still there, so it can be resent


# ---------------------------------------------------------------------------
# Listing, revoking, resending
# ---------------------------------------------------------------------------


def test_list_defaults_to_pending_and_filters_by_status(client):
    keep = invite(client, "keep@invite.test").data["id"]
    gone = invite(client, "gone@invite.test").data["id"]
    client.delete(f"{API}/tenants/me/invitations/{gone}")

    pending = client.get(f"{API}/tenants/me/invitations").data
    assert [r["id"] for r in pending] == [keep]
    revoked = client.get(f"{API}/tenants/me/invitations?status=revoked").data
    assert [r["id"] for r in revoked] == [gone]
    assert len(client.get(f"{API}/tenants/me/invitations?status=all").data) == 2
    assert client.get(f"{API}/tenants/me/invitations?status=nonsense").status_code == 400


def test_list_marks_expired_invitations(client):
    inv = invite(client).data["id"]
    Invitation.objects.filter(pk=inv).update(expires_at=timezone.now() - timedelta(hours=1))
    assert client.get(f"{API}/tenants/me/invitations").data == []
    assert client.get(f"{API}/tenants/me/invitations?status=expired").data[0]["id"] == inv


def test_invitations_are_tenant_scoped(client):
    other = Tenant.objects.create(legal_name="Other Co", status=Tenant.Status.APPROVED)
    other_owner = _client(_member(other, "owner@other.test", Membership.Role.OWNER), other)
    theirs = invite(other_owner, "x@other.test").data["id"]
    assert client.get(f"{API}/tenants/me/invitations").data == []
    assert client.delete(f"{API}/tenants/me/invitations/{theirs}").status_code == 404
    assert client.post(f"{API}/tenants/me/invitations/{theirs}/resend").status_code == 404


def test_revoked_invitation_cannot_be_accepted(client):
    inv = invite(client).data["id"]
    token = emailed_token()
    assert client.delete(f"{API}/tenants/me/invitations/{inv}").status_code == 204
    assert accept(token, password=PASSWORD).status_code == 404
    assert AuditEvent.objects.filter(action="tenant.invitation.revoked", target_id=inv).exists()


def test_cannot_revoke_a_non_pending_invitation(client):
    inv = invite(client).data["id"]
    client.delete(f"{API}/tenants/me/invitations/{inv}")
    resp = client.delete(f"{API}/tenants/me/invitations/{inv}")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "invitation_not_pending"


def test_resend_replaces_the_link_and_restarts_expiry(client):
    inv = invite(client).data["id"]
    old = emailed_token()
    Invitation.objects.filter(pk=inv).update(expires_at=timezone.now() + timedelta(hours=1))

    resp = client.post(f"{API}/tenants/me/invitations/{inv}/resend")
    assert resp.status_code == 200
    assert len(mail.outbox) == 2
    new = emailed_token()
    assert new != old
    assert Invitation.objects.get(pk=inv).expires_at > timezone.now() + timedelta(days=6)
    assert accept(old, password=PASSWORD).status_code == 404  # the old link is dead
    assert accept(new, password=PASSWORD).status_code == 201


def test_cannot_resend_after_accept(client):
    inv = invite(client).data["id"]
    accept(emailed_token(), password=PASSWORD)
    resp = client.post(f"{API}/tenants/me/invitations/{inv}/resend")
    assert resp.status_code == 409


def test_admin_cannot_manage_admin_invitations(client, tenant):
    inv = invite(client, "boss@invite.test", "admin").data["id"]
    admin = _client(_member(tenant, "admin@invite.test", Membership.Role.ADMIN), tenant)
    assert admin.delete(f"{API}/tenants/me/invitations/{inv}").status_code == 403
    assert admin.post(f"{API}/tenants/me/invitations/{inv}/resend").status_code == 403


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


def test_preview_shows_what_the_invitee_needs(client):
    invite(client, "newbie@invite.test", "production_quality")
    resp = APIClient().get(f"{API}/invitations/preview", {"token": emailed_token()})
    assert resp.status_code == 200
    assert resp.data["email"] == "newbie@invite.test"
    assert resp.data["role"] == "production_quality" and resp.data["role_label"] == "Production/Quality"
    assert resp.data["tenant_name"] == "Invite Cosmetics"
    assert resp.data["invited_by"] == "Owner"
    assert resp.data["has_account"] is False


def test_preview_reports_an_existing_account(client):
    User.objects.create_user("known@invite.test", PASSWORD)
    invite(client, "known@invite.test")
    assert APIClient().get(f"{API}/invitations/preview", {"token": emailed_token()}).data["has_account"] is True


def test_preview_unknown_token_and_missing_token(client):
    assert APIClient().get(f"{API}/invitations/preview", {"token": "nope"}).status_code == 404
    assert APIClient().get(f"{API}/invitations/preview").status_code == 404


def test_preview_expired_is_410(client):
    inv = invite(client).data["id"]
    Invitation.objects.filter(pk=inv).update(expires_at=timezone.now() - timedelta(seconds=1))
    resp = APIClient().get(f"{API}/invitations/preview", {"token": emailed_token()})
    assert resp.status_code == 410
    assert resp.data["error"]["code"] == "invitation_expired"


# ---------------------------------------------------------------------------
# Accepting: new person
# ---------------------------------------------------------------------------


def test_new_person_accepts_creates_account_and_membership(client, tenant):
    invite(client, "fresh@invite.test", "production_quality", can_submit_products=True)
    resp = accept(emailed_token(), password=PASSWORD, full_name="Funmi Adeyemi")
    assert resp.status_code == 201, resp.data
    assert resp.data["account_created"] is True and resp.data["role"] == "production_quality"
    assert resp.data["tenant_name"] == "Invite Cosmetics"

    user = User.objects.get(email="fresh@invite.test")
    assert user.full_name == "Funmi Adeyemi"
    assert user.email_verified_at is not None
    assert user.check_password(PASSWORD)
    membership = Membership.objects.get(user=user, tenant=tenant)
    assert membership.role == "production_quality" and membership.can_submit_products is True
    assert membership.status == Membership.Status.ACTIVE
    assert membership.invited_by.email == "owner@invite.test"
    assert Invitation.objects.get().status == Invitation.Status.ACCEPTED
    assert AuditEvent.objects.filter(action="tenant.invitation.accepted").exists()


def test_new_person_can_then_log_in_with_the_role(client):
    invite(client, "login@invite.test", "viewer")
    accept(emailed_token(), password=PASSWORD)
    anon = APIClient()
    login = anon.post(
        f"{API}/auth/login", {"email": "login@invite.test", "password": PASSWORD}, format="json"
    )
    assert login.status_code == 200, login.data
    anon.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access_token']}")
    me = anon.get(f"{API}/auth/me")
    assert me.status_code == 200, me.data
    assert "Invite Cosmetics" in str(me.data) and "viewer" in str(me.data)


def test_new_person_must_choose_a_valid_password(client):
    invite(client)
    token = emailed_token()
    assert accept(token).status_code == 400  # no password
    assert accept(token, password="short").status_code == 400
    assert accept(token, password="1234567890123").status_code == 400  # too common/numeric
    assert not User.objects.filter(email="new@invite.test").exists()
    assert Invitation.objects.get().status == Invitation.Status.PENDING  # still usable
    assert accept(token, password=PASSWORD).status_code == 201


def test_token_is_single_use(client):
    invite(client)
    token = emailed_token()
    assert accept(token, password=PASSWORD).status_code == 201
    second = accept(token, password=PASSWORD)
    assert second.status_code == 404
    assert second.data["error"]["code"] == "invitation_invalid"
    assert Membership.objects.filter(user__email="new@invite.test").count() == 1


def test_expired_invitation_cannot_be_accepted(client):
    inv = invite(client).data["id"]
    Invitation.objects.filter(pk=inv).update(expires_at=timezone.now() - timedelta(seconds=1))
    resp = accept(emailed_token(), password=PASSWORD)
    assert resp.status_code == 410
    assert not User.objects.filter(email="new@invite.test").exists()


def test_garbage_token_is_rejected():
    resp = accept("definitely-not-a-token", password=PASSWORD)
    assert resp.status_code == 404
    assert resp.data["error"]["code"] == "invitation_invalid"


def test_accept_fails_when_tenant_is_not_active(client, tenant):
    invite(client)
    token = emailed_token()
    tenant.status = Tenant.Status.SUSPENDED
    tenant.save(update_fields=["status"])
    resp = accept(token, password=PASSWORD)
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "tenant_not_active"


# ---------------------------------------------------------------------------
# Accepting: existing account
# ---------------------------------------------------------------------------


def test_existing_user_must_log_in_first(client):
    User.objects.create_user("known@invite.test", PASSWORD)
    invite(client, "known@invite.test")
    resp = accept(emailed_token(), password="whatever-it-is-123")
    assert resp.status_code == 409
    assert resp.data["error"]["code"] == "account_exists"
    assert not Membership.objects.filter(user__email="known@invite.test").exists()


def test_existing_user_accepts_when_logged_in_as_that_email(client, tenant):
    known = User.objects.create_user("known@invite.test", PASSWORD)
    invite(client, "known@invite.test", "investigator")
    signed_in = APIClient()
    signed_in.force_authenticate(user=known)
    resp = accept(emailed_token(), client=signed_in)
    assert resp.status_code == 201, resp.data
    assert resp.data["account_created"] is False
    assert Membership.objects.get(user=known, tenant=tenant).role == "investigator"
    known.refresh_from_db()
    assert known.check_password(PASSWORD)  # untouched


def test_logged_in_as_someone_else_is_refused(client, tenant):
    User.objects.create_user("known@invite.test", PASSWORD)
    other = User.objects.create_user("other@invite.test", PASSWORD)
    invite(client, "known@invite.test")
    signed_in = APIClient()
    signed_in.force_authenticate(user=other)
    resp = accept(emailed_token(), client=signed_in)
    assert resp.status_code == 403
    assert resp.data["error"]["code"] == "invitation_email_mismatch"
    assert not Membership.objects.filter(user=other, tenant=tenant).exists()


def test_logged_in_user_cannot_use_an_invite_for_a_new_email(client):
    other = User.objects.create_user("other@invite.test", PASSWORD)
    invite(client, "brandnew@invite.test")
    signed_in = APIClient()
    signed_in.force_authenticate(user=other)
    resp = accept(emailed_token(), client=signed_in, password=PASSWORD)
    assert resp.status_code == 403
    assert not User.objects.filter(email="brandnew@invite.test").exists()


def test_revoked_member_is_reactivated_with_the_new_role(client, tenant):
    old = _member(tenant, "returning@invite.test", Membership.Role.VIEWER)
    Membership.objects.filter(user=old, tenant=tenant).update(
        status=Membership.Status.REVOKED, revoked_at=timezone.now()
    )
    invite(client, "returning@invite.test", "production_quality")
    signed_in = APIClient()
    signed_in.force_authenticate(user=old)
    assert accept(emailed_token(), client=signed_in).status_code == 201
    membership = Membership.objects.get(user=old, tenant=tenant)
    assert membership.status == Membership.Status.ACTIVE
    assert membership.role == "production_quality"
    assert membership.revoked_at is None


def test_email_match_is_case_insensitive(client, tenant):
    known = User.objects.create_user("known@invite.test", PASSWORD)
    invite(client, "KNOWN@invite.TEST")
    signed_in = APIClient()
    signed_in.force_authenticate(user=known)
    assert accept(emailed_token(), client=signed_in).status_code == 201


# ---------------------------------------------------------------------------
# End to end with the role permissions
# ---------------------------------------------------------------------------


def test_invited_production_quality_gets_the_submit_permission_only_if_granted(client, tenant):
    invite(client, "can@invite.test", "production_quality", can_submit_products=True)
    accept(emailed_token(), password=PASSWORD)
    invite(client, "cannot@invite.test", "production_quality")
    accept(emailed_token(), password=PASSWORD)
    assert Membership.objects.get(user__email="can@invite.test").can_submit_products is True
    assert Membership.objects.get(user__email="cannot@invite.test").can_submit_products is False


def test_member_list_shows_email_and_name(client, tenant):
    invite(client, "listed@invite.test", "viewer")
    accept(emailed_token(), password=PASSWORD, full_name="Ada Lovelace")
    rows = {m["email"]: m for m in client.get(f"{API}/tenants/me/members").data}
    assert rows["listed@invite.test"]["full_name"] == "Ada Lovelace"
    assert rows["listed@invite.test"]["role"] == "viewer"


def test_sending_is_rate_limited(client, settings):
    from apps.identity.throttles import InvitationSendThrottle

    InvitationSendThrottle.THROTTLE_RATES = {**InvitationSendThrottle.THROTTLE_RATES,
                                             "invitation_send": "3/hour"}
    codes = [invite(client, f"u{i}@invite.test").status_code for i in range(5)]
    assert codes == [201, 201, 201, 429, 429]


@pytest.mark.parametrize("legacy", ["editor", "member"])
def test_legacy_role_names_map_to_production_quality(client, legacy):
    resp = invite(client, role=legacy)
    assert resp.status_code == 201
    assert resp.data["role"] == "production_quality"


def test_can_submit_products_with_legacy_editor_name(client):
    resp = invite(client, role="editor", can_submit_products=True)
    assert resp.status_code == 201 and resp.data["can_submit_products"] is True


def test_member_update_accepts_legacy_name_and_stores_new_role(client, tenant):
    m = Membership.objects.get(user=_member(tenant, "legacy@invite.test", Membership.Role.VIEWER), tenant=tenant)
    resp = client.patch(f"{API}/tenants/me/members/{m.pk}", {"role": "editor"}, format="json")
    assert resp.status_code == 200
    m.refresh_from_db()
    assert m.role == Membership.Role.PRODUCTION_QUALITY
