"""Pure, framework-free data-contract validation and repair logic, shared
by the ADK agents and the A2A layer. The repair engine is schema-driven
(see README.md), not tied to any particular field name.
"""

import json
import math
import os
from faker import Faker
from jsonschema import Draft7Validator

from agents.telemetry import get_tracer

DEFAULT_SCHEMA_PATH = "contracts/forecasting_contract.json"

_fake = Faker()
_tracer = get_tracer()


def load_schema(schema_path: str) -> dict:
    """Loads and parses the JSON schema data contract from disk."""
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Data contract schema not found at: {schema_path}")

    with open(schema_path, 'r') as f:
        return json.load(f)


def validate_data(data, schema_path):
    """Validates a list of records against the JSON schema contract.
    Returns (is_valid, annotated_data, global_errors)."""
    with _tracer.start_as_current_span("validate_data") as span:
        span.set_attribute("schema_path", schema_path)
        is_valid, annotated_data, global_errors = _validate_data_impl(data, schema_path)
        span.set_attribute("is_valid", is_valid)
        span.set_attribute("global_error_count", len(global_errors))
        return is_valid, annotated_data, global_errors


def _validate_data_impl(data, schema_path):
    schema = load_schema(schema_path)

    validator = Draft7Validator(schema)
    annotated_data = []
    global_errors = []

    if not isinstance(data, list):
        return False, [], ["Input data must be a list of records."]

    for record in data:
        if isinstance(record, dict):
            annotated_data.append(record.copy())
        else:
            annotated_data.append({"_raw_value": record})

    for record in annotated_data:
        if isinstance(record, dict):
            record["_validation_meta"] = {
                "valid": True,
                "errors": []
            }

    errors = list(validator.iter_errors(data))

    for error in errors:
        path = list(error.path)

        if len(path) == 0:
            global_errors.append(error.message)
            continue

        record_idx = path[0]
        if record_idx >= len(annotated_data):
            global_errors.append(error.message)
            continue

        record = annotated_data[record_idx]
        if not isinstance(record, dict) or "_validation_meta" not in record:
            continue

        field = None
        error_type = error.validator

        if len(path) > 1:
            field = path[1]
        else:
            if error.validator == "required":
                allowed_properties = schema.get("items", {}).get("required", [])
                missing = [f for f in allowed_properties if f not in data[record_idx]]
                for m in missing:
                    record["_validation_meta"]["valid"] = False
                    record["_validation_meta"]["errors"].append({
                        "field": m,
                        "error_type": "required",
                        "message": f"Field '{m}' is missing but required by the contract."
                    })
                continue
            elif error.validator == "additionalProperties":
                allowed_properties = schema.get("items", {}).get("properties", {}).keys()
                extra = [f for f in data[record_idx].keys() if f not in allowed_properties]
                for e in extra:
                    record["_validation_meta"]["valid"] = False
                    record["_validation_meta"]["errors"].append({
                        "field": e,
                        "error_type": "additionalProperties",
                        "message": f"Field '{e}' is not allowed by the contract."
                    })
                continue

        record["_validation_meta"]["valid"] = False
        record["_validation_meta"]["errors"].append({
            "field": field,
            "error_type": error_type,
            "message": error.message
        })

    is_valid = len(global_errors) == 0 and all(
        isinstance(r, dict) and r.get("_validation_meta", {}).get("valid", True)
        for r in annotated_data
    )

    return is_valid, annotated_data, global_errors


def generate_supplier_chat_message(is_valid, annotated_data, global_errors):
    if is_valid:
        return "Hi! I have successfully validated your dataset against the contract schema. All records are compliant. Here are your forecasting predictions (all set to 1.0)."

    error_summary = []
    error_count = len(global_errors)
    for record in annotated_data:
        if isinstance(record, dict) and "_validation_meta" in record:
            meta = record["_validation_meta"]
            if not meta["valid"]:
                for err in meta["errors"]:
                    error_count += 1
                    desc = f"'{err['field']}' ({err['error_type']})" if err.get('field') else err['error_type']
                    if desc not in error_summary:
                        error_summary.append(desc)

    issues_str = ", ".join(error_summary[:3])
    if len(error_summary) > 3:
        issues_str += ", etc."

    return f"Hello. I reviewed your submission, but it violates our data contract. I found {error_count} error(s) (including: {issues_str}). I have annotated the records with validation metadata. Please correct them and resubmit."


def generate_client_chat_message(prev_response, current_call):
    if not prev_response:
        return "Hello Forecasting Service! I am submitting a new batch of sales records for forecasting. Please validate the data and return the forecasts."

    status = prev_response.get("status")
    if status == "success":
        return "Excellent! I have received the forecasts. Thank you for the service."

    if status == "max_turns_exceeded":
        return ("Understood. It seems we could not reach a compliant dataset within the "
                "negotiation's turn limit. Ending this session.")

    error_count = len(prev_response.get("global_errors", []))
    for record in prev_response.get("data", []):
        if isinstance(record, dict) and "_validation_meta" in record:
            meta = record["_validation_meta"]
            if not meta["valid"]:
                error_count += len(meta.get("errors", []))

    return f"Thank you for the validation feedback. I noted {error_count} issue(s) in the previous run. I have applied our data repair routines to fix them. Here is the corrected dataset. Let me know if everything is compliant now."


# --- Schema-driven repair engine: nothing below depends on a field name. ---

def _is_nan_like(val) -> bool:
    if val is None:
        return True
    if isinstance(val, float) and math.isnan(val):
        return True
    if isinstance(val, str) and val.strip().lower() == "nan":
        return True
    return False


def _value_matches_schema(field_schema: dict, val) -> bool:
    """Whether `val` satisfies `field_schema`. Rejects NaN/Infinity first --
    Draft7Validator alone lets float('nan') pass a numeric minimum check."""
    if isinstance(val, float) and not math.isfinite(val):
        return False
    return Draft7Validator(field_schema).is_valid(val)


def _coerce_scalar(json_type: str, val):
    """Best-effort coercion of `val` to `json_type`. Returns (ok, value);
    ok is False when no sensible coercion exists."""
    if _is_nan_like(val):
        return False, None

    if json_type == "integer":
        try:
            return True, int(float(val))
        except (TypeError, ValueError):
            return False, None
    if json_type == "number":
        try:
            return True, float(val)
        except (TypeError, ValueError):
            return False, None
    if json_type == "boolean":
        if isinstance(val, bool):
            return True, val
        if isinstance(val, str):
            return True, val.strip().lower() in ("true", "1", "yes")
        if isinstance(val, (int, float)):
            return True, bool(val)
        return False, None
    if json_type == "string":
        # Stringifying an arbitrary type would just produce meaningless text.
        return (True, val) if isinstance(val, str) else (False, None)
    return False, None


def generate_value_for_schema(field_schema: dict, fake: Faker | None = None):
    """Generates a fresh Faker value satisfying `field_schema`."""
    fake = fake or _fake
    json_type = field_schema.get("type")
    fmt = field_schema.get("format")
    minimum = field_schema.get("minimum")

    if json_type == "integer":
        lo = int(math.ceil(minimum)) if minimum is not None else 0
        return fake.random_int(min=lo, max=lo + 1000)
    if json_type == "number":
        lo = float(minimum) if minimum is not None else 0.0
        return round(lo + fake.pyfloat(min_value=0, max_value=1000, right_digits=2), 2)
    if json_type == "boolean":
        return fake.pybool()
    if json_type == "string":
        if fmt == "date-time":
            return fake.date_time_between(start_date="-30d", end_date="now").strftime("%Y-%m-%dT%H:%M:%SZ")
        return fake.word()
    # Not expected for this contract's scalar fields, but don't crash the
    # negotiation over an unrecognized schema type.
    return None


def repair_value(field_schema: dict, current_value, err_type: str, fake: Faker | None = None):
    """Tries the least destructive fix (coerce type / abs() for minimum),
    verifies it against `field_schema`, else generates a fresh value."""
    fake = fake or _fake
    json_type = field_schema.get("type")
    candidate = None

    if err_type == "type":
        ok, coerced = _coerce_scalar(json_type, current_value)
        candidate = coerced if ok else None
    elif err_type == "minimum":
        ok, coerced = _coerce_scalar(json_type, current_value)
        if ok and isinstance(coerced, (int, float)):
            minimum = field_schema.get("minimum")
            candidate = abs(coerced) if minimum is not None and coerced < minimum else coerced
    # err_type == "required" (or anything else): no existing value to work
    # from, candidate stays None and a fresh value is generated below.

    if candidate is not None and _value_matches_schema(field_schema, candidate):
        return candidate
    return generate_value_for_schema(field_schema, fake)


def should_fix_error(repair_strategy: str, err_type: str, current_call: int) -> bool:
    if repair_strategy == "standard":
        return True
    elif repair_strategy == "none":
        return False
    elif repair_strategy == "partial":
        if err_type == "minimum":
            return False
        return True
    elif repair_strategy == "gradual":
        # Call 1: fix additional properties (redundant)
        # Call 2: fix type errors
        # Call 3: fix missing (required) fields
        # Call 4: fix minimum values
        # Call 5: fix any format/other errors
        if current_call == 1 and err_type == "additionalProperties":
            return True
        elif current_call == 2 and err_type in ("type", "additionalProperties"):
            return True
        elif current_call == 3 and err_type in ("required", "type", "additionalProperties"):
            return True
        elif current_call >= 4:
            return True
        return False
    return True


def fix_data(annotated_data: list, current_call: int, repair_strategy: str = "standard",
             schema: dict | None = None) -> list:
    """Applies the repair strategy to annotated (invalid) records. `schema`
    (from `load_schema`) drives repairs; optional only if never needed."""
    with _tracer.start_as_current_span("fix_data") as span:
        span.set_attribute("repair_strategy", repair_strategy)
        span.set_attribute("current_call", current_call)
        invalid_count = sum(
            1 for r in annotated_data
            if isinstance(r, dict) and not r.get("_validation_meta", {}).get("valid", True)
        )
        span.set_attribute("invalid_record_count", invalid_count)
        return _fix_data_impl(annotated_data, current_call, repair_strategy, schema)


def _fix_data_impl(annotated_data: list, current_call: int, repair_strategy: str, schema: dict | None) -> list:
    properties = (schema or {}).get("items", {}).get("properties", {})

    fixed_records = []
    for record in annotated_data:
        if not isinstance(record, dict):
            fixed_records.append(record)
            continue

        clean_rec = {k: v for k, v in record.items() if k != "_validation_meta"}
        meta = record.get("_validation_meta", {"valid": True, "errors": []})

        if meta["valid"]:
            fixed_records.append(clean_rec)
            continue

        errors = meta.get("errors", [])
        for error in errors:
            field = error.get("field")
            err_type = error.get("error_type")

            if not should_fix_error(repair_strategy, err_type, current_call):
                continue

            if err_type == "additionalProperties" and field:
                if field in clean_rec:
                    del clean_rec[field]
                continue

            if not field or field not in properties:
                continue

            if err_type in ("required", "type", "minimum"):
                clean_rec[field] = repair_value(properties[field], clean_rec.get(field), err_type)

        fixed_records.append(clean_rec)

    return fixed_records
