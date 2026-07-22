"""
tests/test_google_sheets_parsing.py

Tests for the pure parsing/normalization functions in
app/google_sheets.py -- these run on every Sheets sync, so a bug here
silently corrupts candidate data at the source (e.g. the whitespace
bug that broke recruiter-name grouping until it was fixed).
"""
from app.google_sheets import _normalize_header, _fix_phone_value, rows_to_records


class TestNormalizeHeader:
    def test_lowercases_and_strips(self):
        assert _normalize_header("  Name  ") == "name"

    def test_applies_known_aliases(self):
        # Whatever aliases exist in HEADER_ALIASES should resolve --
        # this just confirms the mechanism works for at least the
        # trivial identity case if no alias is defined for a header.
        assert _normalize_header("phone") == "phone"


class TestFixPhoneValue:
    def test_none_becomes_empty_string(self):
        assert _fix_phone_value(None) == ""

    def test_float_becomes_int_string_no_decimal(self):
        # UNFORMATTED_VALUE from Sheets can return phone numbers as
        # floats (e.g. 9876543210.0) -- must not leak a ".0" suffix.
        assert _fix_phone_value(9876543210.0) == "9876543210"

    def test_int_becomes_string(self):
        assert _fix_phone_value(9876543210) == "9876543210"

    def test_string_is_stripped(self):
        assert _fix_phone_value("  9876543210  ") == "9876543210"


class TestRowsToRecords:
    def test_empty_rows_returns_empty_list(self):
        assert rows_to_records([], "NOC") == []

    def test_header_only_returns_empty_list(self):
        assert rows_to_records([["name", "phone", "role"]], "NOC") == []

    def test_basic_row_is_converted_to_dict_with_role(self):
        rows = [
            ["name", "phone", "role"],
            ["Priya", "9876543210", "Backend"],
        ]
        records = rows_to_records(rows, "NOC")
        assert records == [{"name": "Priya", "phone": "9876543210", "role": "NOC"}]

    def test_row_shorter_than_header_is_padded(self):
        # A row missing trailing columns (common when someone leaves
        # optional fields blank in Sheets) must not crash or misalign.
        rows = [
            ["name", "phone", "role", "notes"],
            ["Priya", "9876543210"],
        ]
        records = rows_to_records(rows, "NOC")
        assert records[0]["notes"] == ""

    def test_string_fields_are_whitespace_stripped(self):
        # Real bug this guards against: manual Sheets entry easily
        # introduces stray spaces (e.g. "Usha " instead of "Usha"),
        # which silently broke exact-match recruiter grouping.
        rows = [
            ["name", "phone", "hiring_recruiter_name"],
            [" Priya ", "9876543210", "Usha "],
        ]
        records = rows_to_records(rows, "NOC")
        assert records[0]["name"] == "Priya"
        assert records[0]["hiring_recruiter_name"] == "Usha"

    def test_fully_blank_row_is_skipped(self):
        rows = [
            ["name", "phone", "role"],
            ["", "", ""],
            ["Priya", "9876543210", ""],
        ]
        records = rows_to_records(rows, "NOC")
        assert len(records) == 1
        assert records[0]["name"] == "Priya"

    def test_phone_field_is_normalized_via_fix_phone_value(self):
        rows = [
            ["name", "phone"],
            ["Priya", 9876543210.0],
        ]
        records = rows_to_records(rows, "NOC")
        assert records[0]["phone"] == "9876543210"

    def test_role_is_set_from_argument_not_from_sheet(self):
        # The sheet name (passed in as `role`) is authoritative, even if
        # a "role" column happens to exist with different content.
        rows = [
            ["name", "phone", "role"],
            ["Priya", "9876543210", "SomethingElse"],
        ]
        records = rows_to_records(rows, "NOC")
        assert records[0]["role"] == "NOC"