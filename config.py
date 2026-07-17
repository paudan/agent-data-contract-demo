STRATEGY_MAP = {
    "01_compatible.json": "standard",
    "02_missing_all.json": "standard",
    "03_missing_some.json": "standard",
    "04_redundant_fields.json": "standard",
    "05_invalid_types.json": "standard",
    "06_nan_values.json": "standard",
    "07_gradual_fix.json": "gradual",
    "08_unfixable.json": "partial",
}

# 08 is intentionally unfixable under "partial"; every other scenario succeeds.
EXPECTED_SUCCESS = {name: name != "08_unfixable.json" for name in STRATEGY_MAP}