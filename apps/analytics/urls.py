from django.urls import path

from .views import CodeAnalyticsView, RiskAnalyticsView, ScanAnalyticsView

urlpatterns = [
    path("analytics/scans", ScanAnalyticsView.as_view(), name="analytics-scans"),
    path("analytics/codes", CodeAnalyticsView.as_view(), name="analytics-codes"),
    path("analytics/risk", RiskAnalyticsView.as_view(), name="analytics-risk"),
]
