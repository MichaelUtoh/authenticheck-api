from django.urls import path

from .views import ATUssdView, GS1DigitalLinkView

urlpatterns = [
    # GS1 Digital Link: /01/{gtin}/21/{serial}
    path("01/<str:gtin>/21/<str:serial>", GS1DigitalLinkView.as_view(), name="gs1-digital-link"),
    # Africa's Talking USSD inbound
    path("integrations/africastalking/ussd", ATUssdView.as_view(), name="at-ussd"),
]
