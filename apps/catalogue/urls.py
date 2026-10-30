from django.urls import path

from .views import (
    BatchDetailView,
    BatchListCreateView,
    BatchRecallView,
    BrandDetailView,
    BrandListCreateView,
    BulkImportJobDetailView,
    BulkImportView,
    IndustryListView,
    ProductCorrectionRequestView,
    ProductWithdrawView,
    ProductActivityView,
    ProductConsumerPreviewView,
    ProductDetailView,
    ProductImageDeleteView,
    ProductImageUploadView,
    ProductListCreateView,
    ProductSubmitView,
    RegulatoryRefListCreateView,
)

urlpatterns = [
    path("catalogue/brands", BrandListCreateView.as_view(), name="brand-list"),
    path("catalogue/brands/<uuid:pk>", BrandDetailView.as_view(), name="brand-detail"),
    path("catalogue/products", ProductListCreateView.as_view(), name="product-list"),
    path("catalogue/products/<uuid:pk>", ProductDetailView.as_view(), name="product-detail"),
    path("catalogue/products/<uuid:pk>/submit", ProductSubmitView.as_view(), name="product-submit"),
    path("catalogue/products/<uuid:pk>/withdraw", ProductWithdrawView.as_view(), name="product-withdraw"),
    path(
        "catalogue/products/<uuid:pk>/correction-request",
        ProductCorrectionRequestView.as_view(),
        name="product-correction-request",
    ),
    path("catalogue/industries", IndustryListView.as_view(), name="industry-list"),
    path("catalogue/products/<uuid:pk>/activity", ProductActivityView.as_view(), name="product-activity"),
    path(
        "catalogue/products/<uuid:pk>/consumer-preview",
        ProductConsumerPreviewView.as_view(),
        name="product-consumer-preview",
    ),
    path(
        "catalogue/products/<uuid:product_pk>/regulatory-refs",
        RegulatoryRefListCreateView.as_view(),
        name="regulatory-ref-list",
    ),
    path(
        "catalogue/products/<uuid:product_pk>/batches",
        BatchListCreateView.as_view(),
        name="batch-list",
    ),
    path(
        "catalogue/products/<uuid:product_pk>/batches/<uuid:pk>",
        BatchDetailView.as_view(),
        name="batch-detail",
    ),
    path(
        "catalogue/products/<uuid:product_pk>/batches/<uuid:pk>/recall",
        BatchRecallView.as_view(),
        name="batch-recall",
    ),
    path("catalogue/import", BulkImportView.as_view(), name="bulk-import"),
    path("catalogue/import/<uuid:pk>", BulkImportJobDetailView.as_view(), name="bulk-import-detail"),
    path("catalogue/images", ProductImageUploadView.as_view(), name="product-image-upload"),
    path("catalogue/images/<uuid:pk>", ProductImageDeleteView.as_view(), name="product-image-delete"),
]
