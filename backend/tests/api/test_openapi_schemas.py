"""The draft payload is published in OpenAPI (no route declares it), so the frontend uses generated types."""

from rebate.modules.agreements import Payload
from tests.conftest import DbUrls
from tests.support import make_app


def test_draft_payload_schema_is_in_the_openapi_components(migrated_db: DbUrls) -> None:
    schemas = make_app(migrated_db.app).openapi()["components"]["schemas"]
    assert set(schemas["Payload"]["properties"]) == set(Payload.model_fields)
    assert {"SpaRules", "RebateRules", "Header", "Item", "TierIn", "CustomerRef"} <= set(schemas)
    refs = str(schemas["Payload"])
    assert "#/$defs" not in refs and "#/components/schemas/Header" in refs
