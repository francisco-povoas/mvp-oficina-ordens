import json
from pathlib import Path

from jsonschema import Draft202012Validator

CONTRACTS = Path("contracts")
OPENAPI_PATH = CONTRACTS / "openapi" / "internal-v1.openapi.json"

EXPECTED = {
    "/auth/token": "post",
    "/orders/{order_id}/quote-context": "get",
    "/orders/{order_id}/payment-context": "get"
}
EXPECTED_SCOPES = {
    "/orders/{order_id}/quote-context": [
        "orders:quote-context:read"
    ],
    "/orders/{order_id}/payment-context": [
        "orders:payment-context:read"
    ]
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_internal_openapi_is_versioned_and_marks_rest_integrations() -> None:
    document = _load(OPENAPI_PATH)
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "1.1.0"
    assert set(document["paths"]) == set(EXPECTED)

    for path, method in EXPECTED.items():
        operation = document["paths"][path][method]
        if path != "/auth/token":
            assert operation["x-implementation-status"] == "implemented-stage-2-rest"
            assert operation["x-required-scopes"] == EXPECTED_SCOPES[path]
            assert operation["security"] == [{"serviceBearer": []}]
            assert "501" not in operation["responses"]

    payment_parameters = document["paths"][
        "/orders/{order_id}/payment-context"
    ]["get"]["parameters"]
    references = {item["$ref"] for item in payment_parameters}
    assert "#/components/parameters/ApprovedQuoteId" in references
    assert "#/components/parameters/ApprovedQuoteVersion" in references
    assert "#/components/parameters/ApprovedTotal" in references


def test_openapi_materializes_error_correlation_and_idempotency_contracts() -> None:
    components = _load(OPENAPI_PATH)["components"]
    assert components["parameters"]["CorrelationId"]["required"] is True
    assert components["parameters"]["IdempotencyKey"]["required"] is True
    assert components["schemas"]["Problem"]["additionalProperties"] is False
    Draft202012Validator.check_schema(components["schemas"]["Problem"])

