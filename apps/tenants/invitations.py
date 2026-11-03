"""Inviting people to a tenant with a role.

Flow
1. An owner (or admin) invites an email address with a role:  POST /tenants/me/invitations.
   A single-use token is emailed as a link; only its SHA-256 hash is stored.
2. The invitee opens the link. GET /invitations/preview shows who invited them and as what.
3. POST /invitations/accept creates the membership. A new person sets a password in the same
   call; someone who already has an account must be logged in as the invited email.

Only the owner can invite admins (the same least-privilege rule as changing roles). The invite can
never grant the owner role. Tokens are hashed at rest, single use, expire after
INVITATION_TTL_DAYS, and are replaced when an invitation is resent.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_field
from rest_framework import serializers
from rest_framework import status as http
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import services as audit
from apps.core.exceptions import ApiError
from apps.identity.models import Invitation
from apps.identity.services import token_service
from apps.identity.throttles import (
    InvitationAcceptThrottle,
    InvitationPreviewThrottle,
    InvitationSendThrottle,
)

from .models import LEGACY_ROLE_ALIASES, Membership, Tenant
from .permissions import HasActiveTenantMembership, IsOwnerOrAdmin

logger = logging.getLogger(__name__)
User = get_user_model()

INVITABLE_ROLES = (
    Membership.Role.ADMIN,
    Membership.Role.PRODUCTION_QUALITY,
    Membership.Role.INVESTIGATOR,
    Membership.Role.VIEWER,
)
_MIN_PASSWORD_LEN = 10
_MAX_PASSWORD_LEN = 128
_MANAGE_PERMS = [IsAuthenticated, HasActiveTenantMembership, IsOwnerOrAdmin]


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)
    role = serializers.ChoiceField(
        choices=[r.value for r in INVITABLE_ROLES] + list(LEGACY_ROLE_ALIASES)
    )
    can_submit_products = serializers.BooleanField(default=False)

    def validate_role(self, value):
        return LEGACY_ROLE_ALIASES.get(value, value)

    def validate_email(self, value):
        return value.lower().strip()

    def validate(self, attrs):
        if attrs["can_submit_products"] and attrs["role"] != Membership.Role.PRODUCTION_QUALITY:
            raise serializers.ValidationError(
                {"can_submit_products": ["Only applies to the production_quality role."]}
            )
        return attrs


class InvitationSerializer(serializers.ModelSerializer):
    invited_by = serializers.SerializerMethodField()

    class Meta:
        model = Invitation
        fields = [
            "id", "email", "role", "can_submit_products", "status",
            "expires_at", "invited_by", "accepted_at", "created_at",
        ]
        read_only_fields = fields

    @extend_schema_field(OpenApiTypes.OBJECT)
    def get_invited_by(self, obj):
        user = obj.invited_by
        return {"id": str(user.pk), "name": user.full_name or user.email} if user else None


class AcceptInvitationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)
    # Only for someone without an account yet.
    password = serializers.CharField(
        write_only=True, required=False, min_length=_MIN_PASSWORD_LEN, max_length=_MAX_PASSWORD_LEN
    )
    full_name = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, token_service.hash_token(raw)


def _expiry():
    return timezone.now() + timedelta(days=settings.INVITATION_TTL_DAYS)


def _expire_stale(tenant_id=None) -> None:
    """Mark pending invitations past their expiry as expired (lazy, on access)."""
    qs = Invitation.objects.filter(status=Invitation.Status.PENDING, expires_at__lte=timezone.now())
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)
    qs.update(status=Invitation.Status.EXPIRED, updated_at=timezone.now())


def _tenant_name(tenant: Tenant) -> str:
    return tenant.trading_name or tenant.legal_name


def _require_can_invite_role(request, role: str) -> None:
    """Admins may invite everyone except admins; only the owner can invite admins."""
    if role == Membership.Role.ADMIN and request.active_membership.role != Membership.Role.OWNER:
        raise PermissionDenied("Only the owner can invite or manage admins.")


def _send_invitation_email(invitation: Invitation, raw_token: str, inviter) -> None:
    link = f"{settings.FRONTEND_URL.rstrip('/')}/accept-invite?token={raw_token}"
    tenant_name = _tenant_name(invitation.tenant)
    role = Membership.Role(invitation.role).label
    who = (inviter.full_name or inviter.email) if inviter else "A colleague"
    try:
        send_mail(
            subject=f"You're invited to join {tenant_name} on AuthentiCheck",
            message=(
                f"{who} has invited you to join {tenant_name} on AuthentiCheck as {role}.\n\n"
                f"Accept the invitation: {link}\n\n"
                f"This link can be used once and expires on "
                f"{invitation.expires_at:%d %b %Y at %H:%M} UTC. "
                "If you weren't expecting it, you can ignore this email."
            ),
            from_email=None,
            recipient_list=[invitation.email],
            fail_silently=False,
        )
    except Exception:
        # The invitation exists; the inviter can resend. Never leak mail errors to the caller.
        logger.exception("Could not send invitation email for invitation %s", invitation.pk)


def _get_managed_invitation(request, pk) -> Invitation:
    try:
        invitation = Invitation.objects.select_related("tenant", "invited_by").get(
            pk=pk, tenant_id=request.active_tenant_id
        )
    except Invitation.DoesNotExist:
        raise NotFound("Invitation not found.") from None
    _require_can_invite_role(request, invitation.role)
    return invitation


def _find_by_token(raw: str):
    return Invitation.objects.filter(token_hash=token_service.hash_token(raw)).first()


# ---------------------------------------------------------------------------
# Owner / admin: create, list, resend, revoke
# ---------------------------------------------------------------------------


@extend_schema(tags=["Tenants — Invitations"])
class InvitationListCreateView(APIView):
    permission_classes = _MANAGE_PERMS

    def get_throttles(self):
        return [InvitationSendThrottle()] if self.request.method == "POST" else []

    @extend_schema(
        summary="List invitations for the tenant",
        parameters=[
            OpenApiParameter(
                "status", OpenApiTypes.STR,
                enum=["pending", "accepted", "revoked", "expired", "all"],
                description="Default: pending",
            )
        ],
        responses={200: InvitationSerializer(many=True)},
    )
    def get(self, request):
        _expire_stale(request.active_tenant_id)
        qs = Invitation.objects.filter(tenant_id=request.active_tenant_id).select_related("invited_by")
        wanted = request.query_params.get("status", "pending")
        if wanted != "all":
            if wanted not in Invitation.Status.values:
                raise ValidationError({"status": [f"Unknown status: {wanted}."]})
            qs = qs.filter(status=wanted)
        return Response(InvitationSerializer(qs.order_by("-created_at"), many=True).data)

    @extend_schema(
        summary="Invite someone to the tenant with a role",
        description=(
            "Emails a single-use link. Roles: `admin` (owner only), `production_quality`, `investigator`, `viewer` "
            "(legacy `editor` and `member` map to `production_quality`). "
            "`can_submit_products` applies to production_quality. The token is only ever sent by email."
        ),
        request=InvitationCreateSerializer,
        responses={201: InvitationSerializer},
    )
    def post(self, request):
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        _require_can_invite_role(request, data["role"])

        tenant = request.active_membership.tenant
        _expire_stale(tenant.pk)
        if Membership.objects.filter(
            tenant=tenant, user__email=data["email"], status=Membership.Status.ACTIVE
        ).exists():
            raise ApiError(409, "already_member", "This person is already a member of the company.")
        if Invitation.objects.filter(
            tenant=tenant, email=data["email"], status=Invitation.Status.PENDING
        ).exists():
            raise ApiError(
                409, "invitation_pending",
                "There is already a pending invitation for this email. Resend or revoke it.",
            )

        raw, token_hash = _new_token()
        try:
            with transaction.atomic():
                invitation = Invitation.objects.create(
                    tenant=tenant,
                    email=data["email"],
                    role=data["role"],
                    can_submit_products=data["can_submit_products"],
                    token_hash=token_hash,
                    expires_at=_expiry(),
                    invited_by=request.user,
                )
        except IntegrityError:  # lost a race with a concurrent invite
            raise ApiError(
                409, "invitation_pending", "There is already a pending invitation for this email."
            ) from None

        audit.record(
            action="tenant.invitation.created",
            actor=request.user,
            tenant=tenant,
            target=invitation,
            after={
                "email": invitation.email,
                "role": invitation.role,
                "can_submit_products": invitation.can_submit_products,
                "expires_at": invitation.expires_at.isoformat(),
            },
        )
        # Send only once the invitation is committed, so a rollback never emails a dead link.
        transaction.on_commit(lambda: _send_invitation_email(invitation, raw, request.user))
        return Response(InvitationSerializer(invitation).data, status=http.HTTP_201_CREATED)


@extend_schema(tags=["Tenants — Invitations"])
class InvitationDetailView(APIView):
    permission_classes = _MANAGE_PERMS

    @extend_schema(summary="Revoke a pending invitation", responses={204: None})
    def delete(self, request, pk):
        invitation = _get_managed_invitation(request, pk)
        if invitation.status != Invitation.Status.PENDING:
            raise ApiError(
                409, "invitation_not_pending",
                f"A {invitation.status} invitation cannot be revoked.",
            )
        invitation.status = Invitation.Status.REVOKED
        invitation.save(update_fields=["status", "updated_at"])
        audit.record(
            action="tenant.invitation.revoked",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=invitation,
            after={"email": invitation.email, "role": invitation.role},
        )
        return Response(status=http.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Tenants — Invitations"])
class InvitationResendView(APIView):
    permission_classes = _MANAGE_PERMS
    throttle_classes = [InvitationSendThrottle]

    @extend_schema(
        summary="Resend a pending invitation",
        description="Issues a new link (the old one stops working) and restarts the expiry.",
        request=None,
        responses={200: InvitationSerializer},
    )
    def post(self, request, pk):
        _expire_stale(request.active_tenant_id)
        invitation = _get_managed_invitation(request, pk)
        if invitation.status != Invitation.Status.PENDING:
            raise ApiError(
                409, "invitation_not_pending",
                f"A {invitation.status} invitation cannot be resent. Send a new invitation instead.",
            )
        raw, token_hash = _new_token()
        invitation.token_hash = token_hash
        invitation.expires_at = _expiry()
        invitation.save(update_fields=["token_hash", "expires_at", "updated_at"])
        audit.record(
            action="tenant.invitation.resent",
            actor=request.user,
            tenant=request.active_membership.tenant,
            target=invitation,
            after={"email": invitation.email, "expires_at": invitation.expires_at.isoformat()},
        )
        transaction.on_commit(lambda: _send_invitation_email(invitation, raw, request.user))
        return Response(InvitationSerializer(invitation).data)


# ---------------------------------------------------------------------------
# Invitee: preview and accept (public, token-based)
# ---------------------------------------------------------------------------


@extend_schema(
    tags=["Tenants — Invitations"],
    summary="Look up an invitation by its token",
    parameters=[OpenApiParameter("token", OpenApiTypes.STR, required=True)],
    responses={
        200: OpenApiTypes.OBJECT,
        404: OpenApiTypes.OBJECT,
        410: OpenApiTypes.OBJECT,
    },
    description=(
        "Public. Shows who has been invited, to which company and as what, and whether the invited "
        "email already has an account (so the page can ask for a password or a login). "
        "Unknown, used and revoked tokens all return the same 404; expired ones return 410."
    ),
)
class InvitationPreviewView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [InvitationPreviewThrottle]

    def get(self, request):
        invitation = _usable_invitation(request.query_params.get("token", ""))
        return Response({
            "email": invitation.email,
            "role": invitation.role,
            "role_label": Membership.Role(invitation.role).label,
            "tenant_name": _tenant_name(invitation.tenant),
            "invited_by": invitation.invited_by.full_name or invitation.invited_by.email
            if invitation.invited_by else None,
            "expires_at": invitation.expires_at,
            "has_account": User.objects.filter(email=invitation.email).exists(),
        })


def _usable_invitation(raw: str, *, lock: bool = False) -> Invitation:
    """The pending, unexpired invitation for a token, or the right error."""
    qs = Invitation.objects.select_related("tenant", "invited_by")
    if lock:
        qs = qs.select_for_update(of=("self",))
    invitation = qs.filter(token_hash=token_service.hash_token(raw)).first() if raw else None
    if invitation is None or invitation.status in (
        Invitation.Status.ACCEPTED, Invitation.Status.REVOKED,
    ):
        raise ApiError(404, "invitation_invalid", "This invitation link is not valid.")
    if invitation.status == Invitation.Status.EXPIRED or invitation.expires_at <= timezone.now():
        if invitation.status == Invitation.Status.PENDING:
            invitation.status = Invitation.Status.EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
        raise ApiError(
            410, "invitation_expired",
            "This invitation has expired. Ask the person who invited you to send a new one.",
        )
    return invitation


@extend_schema(
    tags=["Tenants — Invitations"],
    summary="Accept an invitation",
    request=AcceptInvitationSerializer,
    responses={201: OpenApiTypes.OBJECT},
    description=(
        "New people send `token` and `password` (and optionally `full_name`); the account is "
        "created with the invited email. Someone who already has an account must be logged in as "
        "that email and send only `token` (`409 account_exists` otherwise). Creates the membership "
        "with the invited role. The token is single use."
    ),
)
class InvitationAcceptView(APIView):
    permission_classes = [AllowAny]  # authentication stays on: an existing user logs in first
    throttle_classes = [InvitationAcceptThrottle]

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            invitation = _usable_invitation(data["token"], lock=True)
            tenant = invitation.tenant
            if tenant.status != Tenant.Status.APPROVED:
                raise ApiError(
                    409, "tenant_not_active", "This company is not active, so it cannot add members."
                )

            existing = User.objects.filter(email=invitation.email).first()
            signed_in = request.user if request.user and request.user.is_authenticated else None
            account_created = False

            if existing is not None:
                if signed_in is None:
                    raise ApiError(
                        409, "account_exists",
                        f"An account already exists for {invitation.email}. "
                        "Log in with it, then accept the invitation.",
                    )
                if signed_in.pk != existing.pk:
                    raise ApiError(
                        403, "invitation_email_mismatch",
                        f"This invitation is for {invitation.email}. Log in with that account.",
                    )
                user = existing
            else:
                if signed_in is not None:
                    raise ApiError(
                        403, "invitation_email_mismatch",
                        f"This invitation is for {invitation.email}, which is a different account "
                        "from the one you are logged in with.",
                    )
                if not data.get("password"):
                    raise ValidationError({"password": ["Choose a password to create your account."]})
                try:
                    validate_password(data["password"])
                except Exception as exc:
                    raise ValidationError({"password": list(getattr(exc, "messages", [str(exc)]))}) from None
                user = User.objects.create_user(
                    email=invitation.email,
                    password=data["password"],
                    full_name=data.get("full_name", ""),
                    email_verified_at=timezone.now(),  # they proved control of the mailbox
                )
                account_created = True

            membership = Membership.objects.filter(user=user, tenant=tenant).first()
            now = timezone.now()
            if membership is not None and membership.status == Membership.Status.ACTIVE:
                raise ApiError(409, "already_member", "You are already a member of this company.")
            if membership is None:
                membership = Membership(user=user, tenant=tenant)
            membership.role = invitation.role
            membership.can_submit_products = invitation.can_submit_products
            membership.status = Membership.Status.ACTIVE
            membership.invited_by = invitation.invited_by
            membership.joined_at = now
            membership.revoked_at = None
            membership.revoked_by = None
            membership.save()

            invitation.status = Invitation.Status.ACCEPTED
            invitation.accepted_by = user
            invitation.accepted_at = now
            invitation.save(update_fields=["status", "accepted_by", "accepted_at", "updated_at"])

            audit.record(
                action="tenant.invitation.accepted",
                actor=user,
                tenant=tenant,
                target=invitation,
                after={"email": invitation.email, "role": invitation.role,
                       "account_created": account_created},
            )

        return Response(
            {
                "detail": "Invitation accepted. You can now log in." if account_created
                else "Invitation accepted.",
                "tenant_id": str(tenant.pk),
                "tenant_name": _tenant_name(tenant),
                "role": membership.role,
                "account_created": account_created,
            },
            status=http.HTTP_201_CREATED,
        )
