import hashlib
import json
from pathlib import Path
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = Path("contracts/vendor/financeiro/financial-events.v1.schema.json")
PRODUCER_SHA256 = "7e3f0e7d50d84285c9cd1714906922b5a66015cfb07322ca9a09e5969049e225"


def test_schema_financeiro_vendorizado_e_imutavel():
    raw = SCHEMA_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PRODUCER_SHA256
    schema = json.loads(raw)
    Draft202012Validator.check_schema(schema)
    envelope = {
        "message_id": str(uuid4()),
        "message_type": "PaymentConfirmed",
        "message_version": 1,
        "occurred_at": "2026-11-09T12:00:00Z",
        "correlation_id": str(uuid4()),
        "causation_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "saga_id": None,
        "payload": {
            "order_id": str(uuid4()),
            "quote_id": str(uuid4()),
            "quote_version": 2,
            "payment_id": str(uuid4()),
            "status": "CONFIRMED",
            "amount": "150.00",
            "confirmed_at": "2026-11-09T12:00:00Z",
        },
    }
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(envelope)
