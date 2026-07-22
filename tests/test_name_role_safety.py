"""
tests/test_name_role_safety.py

Regression guards for get_safe_display_name / get_safe_role_display
(app/status.py). These exist because three separate call sites used to
each do their own ad-hoc `candidate.get("name") or "there"` fallback,
which correctly handled a blank/missing name but NOT the literal
placeholder strings written for auto-created candidates ("Unknown" /
"Unspecified") -- since those are real, non-empty strings, the old
pattern inserted them literally into outbound messages (e.g. "Hi
Unknown, this is Nisha..."). These tests lock in the fix so it can't
silently regress.
"""
from app.status import (
    get_safe_display_name,
    get_safe_role_display,
    UNASSIGNED_NAME_PLACEHOLDER,
    UNSPECIFIED_ROLE_PLACEHOLDER,
)


class TestGetSafeDisplayName:
    def test_real_name_passes_through_unchanged(self):
        assert get_safe_display_name({"name": "Priya"}) == "Priya"

    def test_placeholder_name_falls_back(self):
        assert get_safe_display_name({"name": UNASSIGNED_NAME_PLACEHOLDER}) == "there"

    def test_placeholder_name_is_case_insensitive(self):
        assert get_safe_display_name({"name": "unknown"}) == "there"
        assert get_safe_display_name({"name": "UNKNOWN"}) == "there"

    def test_blank_name_falls_back(self):
        assert get_safe_display_name({"name": ""}) == "there"
        assert get_safe_display_name({"name": "   "}) == "there"

    def test_missing_name_key_falls_back(self):
        assert get_safe_display_name({}) == "there"

    def test_custom_fallback_is_used(self):
        assert get_safe_display_name({"name": None}, fallback="friend") == "friend"


class TestGetSafeRoleDisplay:
    def test_real_role_passes_through_unchanged(self):
        assert get_safe_role_display({"role": "NOC"}) == "NOC"

    def test_placeholder_role_falls_back(self):
        assert get_safe_role_display({"role": UNSPECIFIED_ROLE_PLACEHOLDER}) == "this role"

    def test_placeholder_role_is_case_insensitive(self):
        assert get_safe_role_display({"role": "unspecified"}) == "this role"

    def test_blank_role_falls_back(self):
        assert get_safe_role_display({"role": ""}) == "this role"

    def test_missing_role_key_falls_back(self):
        assert get_safe_role_display({}) == "this role"