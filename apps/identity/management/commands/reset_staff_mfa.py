"""Recovery for a platform staff member who lost their authenticator when no other staff user can
reset it through the API (POST /api/v1/platform/staff/{id}/reset-mfa)."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.audit import services as audit
from apps.identity.models import AuthSession
from apps.identity.services import session_service


class Command(BaseCommand):
    help = "Remove a staff user's authenticators and sign them out. They re-enrol at next login."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, email, **options):
        User = get_user_model()
        try:
            user = User.objects.get(email=email.strip().lower(), is_staff=True)
        except User.DoesNotExist:
            raise CommandError("No staff user with that email.") from None

        removed = TOTPDevice.objects.filter(user=user).delete()[0]
        revoked = session_service.revoke_all_sessions(user, AuthSession.RevocationReason.MFA_RESET)
        audit.record(
            action="auth.mfa.reset",
            target=user,
            metadata={"via": "management_command", "devices_removed": removed},
        )
        self.stdout.write(
            f"Reset MFA for {user.email}: {removed} device(s) removed, {revoked} session(s) revoked. "
            "They will be asked to set up an authenticator at their next login."
        )
