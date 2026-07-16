"""
Google Sheets + Drive ingestion for candidate data.

Replaces the Excel upload path. Each Google Sheet inside GOOGLE_DRIVE_FOLDER_ID
is treated as one role -- the sheet's name (e.g. "NOC", "AI", "SOC") becomes
the candidate's `role`.

Env vars required:
    GOOGLE_APPLICATION_CREDENTIALS=app/credentials/google_service_account.json
    GOOGLE_DRIVE_FOLDER_ID=<folder ID from the Drive URL>
"""

import os
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

CREDS_PATH = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS",
    "app/credentials/google_service_account.json",
)
FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")

# Confirmed schema columns (see project notes)
EXPECTED_COLUMNS = [
    "name", "phone", "location", "email", "current_company",
    "college_name", "source", "hiring_recruiter_name", "remarks",
    "interview_score", "communication", "overall_feedback",
    "recruitment_status", "feedback", "final_status",
]

# Maps common human-friendly header variants (lowercased) to schema column names.
HEADER_ALIASES = {
    "phone no": "phone",
    "phone number": "phone",
    "current company": "current_company",
    "college name": "college_name",
    "hiring recruiter name": "hiring_recruiter_name",
    "recruiter name": "hiring_recruiter_name",
    "interview score": "interview_score",
    "communication feedback (5)": "communication",
    "communication feedback": "communication",
    "overall feedback (5)": "overall_feedback",
    "recruitment status": "recruitment_status",
    "final status": "final_status",
}


def _normalize_header(raw_header: str) -> str:
    cleaned = raw_header.strip().lower()
    return HEADER_ALIASES.get(cleaned, cleaned)


def _fix_phone_value(value):
    """
    With valueRenderOption=UNFORMATTED_VALUE, numeric cells come back as
    Python int/float rather than a display-formatted string. Convert that
    raw value into a clean phone number string.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def get_credentials():
    return service_account.Credentials.from_service_account_file(
        CREDS_PATH, scopes=SCOPES
    )


def list_sheets_in_folder(drive_service, folder_id):
    """List all Google Sheets files inside a given Drive folder."""
    query = (
        f"'{folder_id}' in parents "
        "and mimeType='application/vnd.google-apps.spreadsheet' "
        "and trashed=false"
    )
    results = drive_service.files().list(
        q=query,
        fields="files(id, name)",
        pageSize=100,
    ).execute()
    return results.get("files", [])


def read_sheet_rows(sheets_service, spreadsheet_id, range_name="A1:Z1000"):
    """Read raw rows from a spreadsheet, avoiding lossy display formatting."""
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()
    return result.get("values", [])


def rows_to_records(rows, role: str):
    """
    Convert raw sheet rows (header row first) into a list of candidate dicts.
    Adds `role` to every record based on the sheet name.
    """
    if not rows:
        return []

    header = [_normalize_header(h) for h in rows[0]]
    records = []
    for row in rows[1:]:
        padded = row + [""] * (len(header) - len(row))
        record = dict(zip(header, padded))

        # Strip leading/trailing whitespace from every text field. Manual
        # entry in Sheets easily introduces stray spaces (e.g. "Usha "
        # instead of "Usha"), which then silently breaks any exact-match
        # grouping or comparison downstream (recruiter grouping, dedup,
        # display) without ever showing up as an obvious error -- fixing
        # it once here, at the source, means every field is protected
        # rather than patching each symptom separately as it's noticed.
        record = {
            key: (value.strip() if isinstance(value, str) else value)
            for key, value in record.items()
        }

        if "phone" in record:
            record["phone"] = _fix_phone_value(record["phone"])

        # Skip fully blank rows
        if not any(str(v).strip() for v in record.values()):
            continue

        record["role"] = role
        records.append(record)

    return records


def fetch_all_candidates_from_sheets():
    """
    Main entry point: connects to Drive, finds every sheet in the configured
    folder, reads its rows, and returns a flat list of candidate dicts
    (one dict per row, each tagged with its sheet's name as `role`).

    Returns (records, warnings) where warnings is a list of strings
    describing any per-sheet issues encountered (missing columns, etc.)
    that the caller may want to surface to HR/the dashboard.
    """
    if not FOLDER_ID:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_ID is not set in the environment.")

    creds = get_credentials()
    drive_service = build("drive", "v3", credentials=creds)
    sheets_service = build("sheets", "v4", credentials=creds)

    sheets = list_sheets_in_folder(drive_service, FOLDER_ID)
    all_records = []
    warnings = []

    for sheet in sheets:
        rows = read_sheet_rows(sheets_service, sheet["id"])
        if not rows:
            warnings.append(f"Sheet '{sheet['name']}' is empty, skipped.")
            continue

        header = [_normalize_header(h) for h in rows[0]]
        missing = [c for c in ("name", "phone") if c not in header]
        if missing:
            warnings.append(
                f"Sheet '{sheet['name']}' is missing required column(s) "
                f"{missing}, skipped."
            )
            continue

        records = rows_to_records(rows, role=sheet["name"])
        all_records.extend(records)

    return all_records, warnings