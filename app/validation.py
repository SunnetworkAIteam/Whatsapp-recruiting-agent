import re
from supabase import Client

def normalize_phone(phone) -> str:
    """
    Normalize phone numbers:
    - Convert to string
    - Remove non-digits
    - Keep last 10 digits
    """
    phone_str = str(phone)
    digits = re.sub(r"\D", "", phone_str)
    return digits[-10:] if len(digits) >= 10 else digits

def validate_candidate(record: dict) -> bool:
    """
    Validate candidate record:
    - Must contain name, phone, role, with real (non-blank) VALUES --
      not just present keys. A blank name previously passed this check
      (only checked key presence) and silently inserted with name="",
      which then fell back to a generic placeholder in outreach
      messages instead of ever being caught and surfaced to HR.
    - Phone must normalize to 10 digits
    """
    required = {"name", "phone", "role"}
    if not required.issubset(record.keys()):
        return False

    if not str(record.get("name") or "").strip():
        return False
    if not str(record.get("role") or "").strip():
        return False

    normalized = normalize_phone(record["phone"])
    return len(normalized) == 10 and normalized.isdigit()

def is_duplicate(supabase: Client, normalized_phone: str) -> bool:
    """
    Check if phone already exists in Supabase candidates table.
    """
    result = supabase.table("candidates").select("phone").eq("phone", normalized_phone).execute()
    return len(result.data) > 0
