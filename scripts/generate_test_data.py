"""Generates a randomly composed data contract + matching negotiation
scenarios (see README.md). Usage: generate_test_data.py [--seed N] [--verify]
"""

import argparse
import asyncio
import json
import os
import random
import sys
from dataclasses import dataclass
from typing import Any, Callable

from faker import Faker

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

DEFAULT_CONTRACT_OUT = "generated/contracts/generated_contract.json"
DEFAULT_DATA_DIR = "generated/data"


@dataclass
class FieldTemplate:
    name: str
    json_type: str  # JSON Schema "type": "string" | "integer" | "number" | "boolean"
    description: str
    minimum: float | None
    fmt: str | None
    generate: Callable[[Faker], Any]


# Candidate fields grouped by role; build_field_set picks one per role.
ROLE_POOLS: dict[str, list[FieldTemplate]] = {
    "int_with_min": [
        FieldTemplate("order_id", "integer", "Positive integer identifier for the order", 1, None,
                      lambda fake: fake.random_int(min=1, max=9000)),
        FieldTemplate("customer_id", "integer", "Positive integer identifier for the customer", 1, None,
                      lambda fake: fake.random_int(min=1, max=9000)),
        FieldTemplate("quantity", "integer", "Number of units ordered, must be at least one", 1, None,
                      lambda fake: fake.random_int(min=1, max=500)),
        FieldTemplate("warehouse_number", "integer", "Positive integer identifier for the warehouse", 1, None,
                      lambda fake: fake.random_int(min=1, max=200)),
    ],
    "number_with_min": [
        FieldTemplate("unit_price", "number", "Price per unit, must be positive", 0.01, None,
                      lambda fake: round(fake.pyfloat(min_value=1, max_value=500, right_digits=2), 2)),
        FieldTemplate("total_amount", "number", "Total charged amount, must be positive", 0.01, None,
                      lambda fake: round(fake.pyfloat(min_value=1, max_value=2000, right_digits=2), 2)),
        FieldTemplate("account_balance", "number", "Current account balance, must be non-negative", 0.0, None,
                      lambda fake: round(fake.pyfloat(min_value=0, max_value=10000, right_digits=2), 2)),
        FieldTemplate("discount_rate", "number", "Discount applied, must be non-negative", 0.0, None,
                      lambda fake: round(fake.pyfloat(min_value=0, max_value=50, right_digits=2), 2)),
    ],
    "boolean": [
        FieldTemplate("is_active", "boolean", "Whether the record is currently active", None, None,
                      lambda fake: fake.pybool()),
        FieldTemplate("is_verified", "boolean", "Whether the associated account is verified", None, None,
                      lambda fake: fake.pybool()),
        FieldTemplate("is_flagged", "boolean", "Whether the record has been flagged for review", None, None,
                      lambda fake: fake.pybool()),
    ],
    "string": [
        FieldTemplate("full_name", "string", "Full name associated with the record", None, None,
                      lambda fake: fake.name()),
        FieldTemplate("email", "string", "Contact e-mail address", None, None,
                      lambda fake: fake.email()),
        FieldTemplate("country_code", "string", "ISO country code", None, None,
                      lambda fake: fake.country_code()),
        FieldTemplate("warehouse_code", "string", "Short warehouse code", None, None,
                      lambda fake: fake.lexify(text="???").upper()),
    ],
    "date_time": [
        FieldTemplate("created_at", "string", "ISO 8601 formatted date-time the record was created", None,
                      "date-time", lambda fake: fake.date_time_between(start_date="-60d", end_date="-30d")
                                                     .strftime("%Y-%m-%dT%H:%M:%SZ")),
        FieldTemplate("updated_at", "string", "ISO 8601 formatted date-time the record was last updated", None,
                      "date-time", lambda fake: fake.date_time_between(start_date="-30d", end_date="now")
                                                     .strftime("%Y-%m-%dT%H:%M:%SZ")),
        FieldTemplate("last_login_at", "string", "ISO 8601 formatted date-time of the last login", None,
                      "date-time", lambda fake: fake.date_time_between(start_date="-30d", end_date="now")
                                                     .strftime("%Y-%m-%dT%H:%M:%SZ")),
    ],
}

REQUIRED_ROLES = ("int_with_min", "number_with_min", "boolean", "string", "date_time")


def build_field_set(rng: random.Random, max_extra_fields: int = 3) -> tuple[list[FieldTemplate], dict[str, FieldTemplate]]:
    """Randomly composes the field set: one guaranteed field per
    REQUIRED_ROLES plus 0..max_extra_fields more for variety.
    Returns (field_list, role -> chosen FieldTemplate).
    """
    roles: dict[str, FieldTemplate] = {role: rng.choice(ROLE_POOLS[role]) for role in REQUIRED_ROLES}
    chosen = list(roles.values())
    chosen_names = {f.name for f in chosen}

    remaining = [t for pool in ROLE_POOLS.values() for t in pool if t.name not in chosen_names]
    rng.shuffle(remaining)
    num_extra = rng.randint(0, min(max_extra_fields, len(remaining)))
    chosen.extend(remaining[:num_extra])
    rng.shuffle(chosen)

    return chosen, roles


def build_contract_schema(fields: list[FieldTemplate], min_items: int, title: str, description: str) -> dict:
    properties = {}
    required = []
    for field in fields:
        prop = {"type": field.json_type, "description": field.description}
        if field.minimum is not None:
            prop["minimum"] = field.minimum
        if field.fmt is not None:
            prop["format"] = field.fmt
        properties[field.name] = prop
        required.append(field.name)

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": title,
        "description": description,
        "type": "array",
        "minItems": min_items,
        "items": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def generate_record(fields: list[FieldTemplate], fake: Faker) -> dict:
    return {field.name: field.generate(fake) for field in fields}


def generate_dataset(fields: list[FieldTemplate], fake: Faker, num_records: int) -> list[dict]:
    return [generate_record(fields, fake) for _ in range(num_records)]


def _stringify(value) -> str:
    """Turns a valid value into a same-content but wrong-typed string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _below_minimum(value: float, minimum: float) -> float:
    """A number guaranteed strictly less than `minimum`."""
    return minimum - abs(value) - 1


def _random_redundant_field(fields: list[FieldTemplate], fake: Faker) -> tuple[str, Any]:
    """A field name/value pair guaranteed not to collide with the schema."""
    existing_names = {f.name for f in fields}
    while True:
        key = f"{fake.word()}_{fake.random_int(min=0, max=999)}"
        if key not in existing_names:
            return key, fake.word()


# --- Scenario builders: (fields, roles, fake, num_records) -> record list ---

def scenario_compatible(fields, roles, fake, num_records):
    return generate_dataset(fields, fake, num_records)


def scenario_missing_all(fields, roles, fake, num_records):
    data = generate_dataset(fields, fake, num_records)
    target = roles["string"].name
    for record in data:
        del record[target]
    return data


def scenario_missing_some(fields, roles, fake, num_records):
    data = generate_dataset(fields, fake, num_records)
    del data[-1][roles["date_time"].name]
    return data


def scenario_redundant_fields(fields, roles, fake, num_records):
    data = generate_dataset(fields, fake, num_records)
    for record in data:
        key, value = _random_redundant_field(fields, fake)
        record[key] = value
    return data


def scenario_invalid_types(fields, roles, fake, num_records):
    data = generate_dataset(fields, fake, num_records)
    record = data[0]
    for role in ("boolean", "int_with_min"):
        name = roles[role].name
        record[name] = _stringify(record[name])
    return data


def scenario_nan_values(fields, roles, fake, num_records):
    data = generate_dataset(fields, fake, num_records)
    target = roles["number_with_min"].name
    data[0][target] = "NaN"
    data[1][target] = None
    return data


def scenario_gradual_fix(fields, roles, fake, num_records):
    """One record with all 4 fixable error types on 4 distinct fields; only
    fully valid after the "gradual" strategy's 5th submission (see README.md)."""
    data = generate_dataset(fields, fake, num_records)
    record = data[0]

    bool_name = roles["boolean"].name
    record[bool_name] = _stringify(record[bool_name])  # type error

    int_field = roles["int_with_min"]
    record[int_field.name] = _below_minimum(record[int_field.name], int_field.minimum)  # minimum error

    del record[roles["number_with_min"].name]  # required error

    key, value = _random_redundant_field(fields, fake)
    record[key] = value  # additionalProperties error

    return data


def scenario_unfixable(fields, roles, fake, num_records):
    """A lone minimum violation "partial" refuses to fix -- never converges."""
    data = generate_dataset(fields, fake, num_records)
    field = roles["number_with_min"]
    data[0][field.name] = _below_minimum(data[0][field.name], field.minimum)
    return data


# (filename, builder, repair_strategy, expected_success)
SCENARIOS = [
    ("01_compatible.json", scenario_compatible, "standard", True),
    ("02_missing_all.json", scenario_missing_all, "standard", True),
    ("03_missing_some.json", scenario_missing_some, "standard", True),
    ("04_redundant_fields.json", scenario_redundant_fields, "standard", True),
    ("05_invalid_types.json", scenario_invalid_types, "standard", True),
    ("06_nan_values.json", scenario_nan_values, "standard", True),
    ("07_gradual_fix.json", scenario_gradual_fix, "gradual", True),
    ("08_unfixable.json", scenario_unfixable, "partial", False),
]


async def verify(contract_path: str, data_dir: str, num_calls: int) -> bool:
    """Runs every scenario through SimulationCoordinator and checks it
    matches SCENARIOS' expectations. Requires a reachable Redis instance."""
    sys.path.insert(0, REPO_ROOT)
    from simulation import SimulationCoordinator

    coordinator = SimulationCoordinator(num_calls=num_calls, schema_path=contract_path)

    print("\n===== Verifying generated scenarios against the simulation engine =====")
    all_ok = True
    for filename, _builder, strategy, expected_success in SCENARIOS:
        with open(os.path.join(data_dir, filename)) as f:
            data = json.load(f)
        name = os.path.splitext(filename)[0]
        success, calls, _response = await coordinator.run_scenario(name, data, strategy)
        ok = success == expected_success
        all_ok &= ok
        print(f"{filename:25s} strategy={strategy:10s} success={str(success):5s} "
              f"(expected {str(expected_success):5s}) calls={calls} [{'OK' if ok else 'MISMATCH'}]")

    print("\nAll scenarios behaved as expected." if all_ok else "\nSome scenarios did NOT behave as expected.")
    return all_ok


def main():
    parser = argparse.ArgumentParser(
        description="Generate a randomly composed data contract and matching negotiation scenarios for simulation testing."
    )
    parser.add_argument("--contract-out", default=DEFAULT_CONTRACT_OUT,
                         help=f"Path to write the JSON schema contract to (default: {DEFAULT_CONTRACT_OUT}). "
                              "Pass contracts/forecasting_contract.json to regenerate the canonical fixture in place.")
    parser.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                         help=f"Directory to write the scenario JSON files to (default: {DEFAULT_DATA_DIR}). "
                              "Pass 'data' to regenerate the canonical fixtures in place.")
    parser.add_argument("--num-records", type=int, default=5,
                         help="Records per scenario; must be >= 2 (schema requires minItems=2). Default: 5.")
    parser.add_argument("--max-extra-fields", type=int, default=3,
                         help="Upper bound on how many extra (non-guaranteed-role) fields to randomly mix "
                              "into the contract, on top of the 5 always-present roles. Default: 3.")
    parser.add_argument("--min-items", type=int, default=2, help="Contract's minItems. Default: 2.")
    parser.add_argument("--title", default="RandomizedDataContract")
    parser.add_argument("--description", default="A randomly generated data contract for simulation testing.")
    parser.add_argument("--seed", type=int, default=None, help="Random seed, for reproducible generation.")
    parser.add_argument("--num-calls", type=int, default=5,
                         help="Turn budget (NUM_CALLS) used only when --verify is passed. Default: 5.")
    parser.add_argument("--verify", action="store_true",
                         help="After generating, run every scenario through the simulation engine and check "
                              "it behaves as expected (requires a reachable Redis instance).")
    args = parser.parse_args()

    if args.num_records < 2:
        parser.error("--num-records must be >= 2 (schema requires minItems=2)")

    rng = random.Random(args.seed)
    fake = Faker()
    if args.seed is not None:
        Faker.seed(args.seed)

    fields, roles = build_field_set(rng, max_extra_fields=args.max_extra_fields)
    schema = build_contract_schema(fields, min_items=args.min_items, title=args.title, description=args.description)

    contract_dir = os.path.dirname(args.contract_out)
    if contract_dir:
        os.makedirs(contract_dir, exist_ok=True)
    with open(args.contract_out, "w") as f:
        json.dump(schema, f, indent=2)
    field_summary = ", ".join(f"{f.name}:{f.json_type}" for f in fields)
    print(f"Wrote contract schema to {args.contract_out} ({len(fields)} fields: {field_summary})")

    os.makedirs(args.data_dir, exist_ok=True)
    for filename, builder, strategy, expected_success in SCENARIOS:
        data = builder(fields, roles, fake, args.num_records)
        path = os.path.join(args.data_dir, filename)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Wrote {path} (strategy={strategy}, expected_success={expected_success})")

    if args.verify:
        all_ok = asyncio.run(verify(args.contract_out, args.data_dir, args.num_calls))
        sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
