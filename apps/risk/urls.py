from django.urls import path

from .views import (
    CaseDetailView,
    CaseListCreateView,
    ConsumerReportView,
    RiskFlagListView,
    RiskFlagReviewView,
)

urlpatterns = [
    path("reports", ConsumerReportView.as_view(), name="consumer-report"),
    path("risk/flags", RiskFlagListView.as_view(), name="risk-flag-list"),
    path("risk/flags/<uuid:pk>/review", RiskFlagReviewView.as_view(), name="risk-flag-review"),
    path("risk/cases", CaseListCreateView.as_view(), name="risk-case-list"),
    path("risk/cases/<uuid:pk>", CaseDetailView.as_view(), name="risk-case-detail"),
]
