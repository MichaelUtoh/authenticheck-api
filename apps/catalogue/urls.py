from django.urls import path

from .views import (
    BatchDetailView,
    BatchListCreateView,
    BrandDetailView,
    BrandListCreateView,
    BulkImportJobDetailView,
    BulkImportView,
    ProductDetailView,
    ProductListCreateView,
    RegulatoryRefListCreateView,
)

urlpatterns = [
    path("catalogue/brands", BrandListCreateView.as_view(), name="brand-list"),
    path("catalogue/brands/<uuid:pk>", BrandDetailView.as_view(), name="brand-detail"),
    path("catalogue/products", ProductListCreateView.as_view(), name="product-list"),
    path("catalogue/products/<uuid:pk>", ProductDetailView.as_view(), name="product-detail"),
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
    path("catalogue/import", BulkImportView.as_view(), name="bulk-import"),
    path("catalogue/import/<uuid:pk>", BulkImportJobDetailView.as_view(), name="bulk-import-detail"),
]
