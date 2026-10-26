from django.urls import path

from .views import VerifyView

urlpatterns = [
    path("verify/<str:code_str>", VerifyView.as_view(), name="verify-code"),
]
