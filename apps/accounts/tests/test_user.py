import pytest

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_create_user_normalises_email_and_hashes_password():
    user = User.objects.create_user("Jane.Doe@Example.COM", "a-long-test-password")
    assert user.email == "jane.doe@example.com"
    assert user.check_password("a-long-test-password")
    assert user.password != "a-long-test-password"
    assert not user.is_staff and not user.is_superuser


def test_create_user_requires_email():
    with pytest.raises(ValueError):
        User.objects.create_user("", "a-long-test-password")


def test_create_superuser_sets_flags():
    admin = User.objects.create_superuser("root@example.com", "a-long-test-password")
    assert admin.is_staff and admin.is_superuser


def test_primary_key_is_uuid_not_sequential():
    user = User.objects.create_user("u@example.com", "a-long-test-password")
    assert len(str(user.pk)) == 36
