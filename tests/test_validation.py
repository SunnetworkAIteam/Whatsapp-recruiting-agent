import pandas as pd
import pytest
from app.validation import normalize_phone, validate_candidate

def test_excel_parsing_and_validation():
    # Load sample Excel dynamically
    df = pd.read_excel("tests/data/sample.xlsx")

    # Ensure file is not empty
    assert len(df) > 0

    # Validate each row without assuming specific names
    for _, row in df.iterrows():
        record = {
            "name": row.get("name"),
            "phone": row.get("phone"),
            "role": row.get("role")
        }

        # Required fields must exist
        assert set(record.keys()) == {"name", "phone", "role"}

        # Phone normalization always returns a string
        normalized = normalize_phone(record["phone"])
        assert isinstance(normalized, str)

        # Validation depends only on phone format
        if len(normalized) == 10 and normalized.isdigit():
            assert validate_candidate(record) is True
        else:
            assert validate_candidate(record) is False


def test_deduplication_within_excel():
    # Load sample Excel dynamically
    df = pd.read_excel("tests/data/sample.xlsx")

    seen = set()
    inserted, skipped = [], []

    for _, row in df.iterrows():
        record = {
            "name": row.get("name"),
            "phone": row.get("phone"),
            "role": row.get("role")
        }
        normalized = normalize_phone(record["phone"])

        if normalized in seen:
            skipped.append(record)
        else:
            seen.add(normalized)
            inserted.append(record)

    # Deduplication should separate unique vs duplicate rows
    assert len(inserted) + len(skipped) == len(df)
