from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

User = get_user_model()

_MAX_PASSWORD_LEN = 128  # hashing-DoS prevention [EC-027]
_MAX_EMAIL_LEN = 254


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=_MAX_EMAIL_LEN)
    password = serializers.CharField(write_only=True, min_length=10, max_length=_MAX_PASSWORD_LEN)
    full_name = serializers.CharField(max_length=255, default="", allow_blank=True)

    def validate_email(self, value):
        return value.lower().strip()

    def validate_password(self, value):
        # validate_password raises ValidationError which DRF wraps in 400.
        # Called only for new users (existing-email path skips this).
        validate_password(value)
        return value


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=_MAX_EMAIL_LEN)
    password = serializers.CharField(write_only=True, max_length=_MAX_PASSWORD_LEN)


class MFATokenSerializer(serializers.Serializer):
    token = serializers.RegexField(r"^\d{6}$", help_text="6-digit TOTP code.")


class ProfileUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=255)  # trimmed and non-blank by default


class UserSerializer(serializers.ModelSerializer):
    mfa_enabled = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "is_staff", "mfa_enabled"]

    @extend_schema_field(serializers.BooleanField())
    def get_mfa_enabled(self, obj):
        from django_otp.plugins.otp_totp.models import TOTPDevice

        return TOTPDevice.objects.filter(user=obj, confirmed=True).exists()
