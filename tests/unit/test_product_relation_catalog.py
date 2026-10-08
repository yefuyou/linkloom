from __future__ import annotations

from linkloom.ui.product import ProductApplication


def test_product_application_default_catalog_contains_frozen_vendor_relation(tmp_path) -> None:
    app = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=None)
    try:
        result = app.relation_resolver.resolve("uses vendor")
        assert result.canonical_relation == "uses vendor"
    finally:
        app.close()
