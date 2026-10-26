from django.urls import path

from .views import (
    BatchCodeDeactivateView,
    CodeDeactivateView,
    CodeListView,
    IssuanceJobDetailView,
    IssuanceJobExportView,
    IssuanceJobListCreateView,
)

urlpatterns = [
    path("issuance/jobs", IssuanceJobListCreateView.as_view(), name="issuance-job-list"),
    path("issuance/jobs/<uuid:pk>", IssuanceJobDetailView.as_view(), name="issuance-job-detail"),
    path("issuance/jobs/<uuid:pk>/export", IssuanceJobExportView.as_view(), name="issuance-job-export"),
    path("issuance/codes", CodeListView.as_view(), name="issuance-code-list"),
    path("issuance/codes/<str:code_str>/deactivate", CodeDeactivateView.as_view(), name="code-deactivate"),
    path("issuance/batches/<uuid:batch_pk>/deactivate", BatchCodeDeactivateView.as_view(), name="batch-deactivate"),
]
