"""
Standalone test script for Google Sheets + Drive integration.
Run this against your TEST Drive folder before wiring into app/main.py.

Setup:
    pip install google-api-python-client google-auth

Env vars needed (add to .env):
    GOOGLE_APPLICATION_CREDENTIALS=app/credentials/google_service_account.json
    GOOGLE_DRIVE_FOLDER_ID=<your test folder's ID from the Drive URL>

Usage:
    python scripts/test_gsheets.py
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

# Columns confirmed for the candidates sheet
EXPECTED_COLUMNS = [
    "name", "phone", "location", "email", "current_company",
    "college_name", "source", "hiring_recruiter_name", "remarks",
    "interview_score", "communication", "overall_feedback",
    "recruitment_status", "feedback", "final_status",
]

# Maps common human-friendly header variants (lowercased) to schema column names.
# Add to this as new variants show up in real HR sheets.
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


def normalize_header(raw_header):
    """Lowercase/strip a header, then map through known aliases if present."""
    cleaned = raw_header.strip().lower()
    return HEADER_ALIASES.get(cleaned, cleaned)


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
        pageSize=50,
    ).execute()
    return results.get("files", [])


def read_sheet_rows(sheets_service, spreadsheet_id, range_name="A1:Z1000"):
    """Read all rows from the first sheet/tab of a spreadsheet."""
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
    ).execute()
    return result.get("values", [])


def rows_to_dicts(rows):
    """Convert raw rows (with header row first) into list of dicts."""
    if not rows:
        return []
    header = [normalize_header(h) for h in rows[0]]
    records = []
    for row in rows[1:]:
        # pad short rows so zip doesn't drop trailing empty cells
        padded = row + [""] * (len(header) - len(row))
        records.append(dict(zip(header, padded)))
    return records


def validate_headers(header_row):
    header = [normalize_header(h) for h in header_row]
    missing = [c for c in EXPECTED_COLUMNS if c not in header]
    extra = [c for c in header if c not in EXPECTED_COLUMNS]
    return missing, extra


def main():
    if not FOLDER_ID:
        raise SystemExit(
            "Missing GOOGLE_DRIVE_FOLDER_ID env var. "
            "Copy the folder ID from the Drive URL and set it."
        )

    creds = get_credentials()
    drive_service = build("drive", "v3", credentials=creds)
    sheets_service = build("sheets", "v4", credentials=creds)

    print(f"Looking for sheets in folder: {FOLDER_ID}\n")
    sheets = list_sheets_in_folder(drive_service, FOLDER_ID)

    if not sheets:
        print("No sheets found. Check that:")
        print("  1. The folder ID is correct")
        print("  2. The service account email has Editor/Viewer access to the folder")
        return

    print(f"Found {len(sheets)} sheet(s):")
    for s in sheets:
        print(f"  - {s['name']} ({s['id']})")
    print()

    for s in sheets:
        print(f"--- Reading: {s['name']} ---")
        rows = read_sheet_rows(sheets_service, s["id"])

        if not rows:
            print("  (empty sheet)")
            continue

        missing, extra = validate_headers(rows[0])
        if missing:
            print(f"  WARNING: missing expected columns: {missing}")
        if extra:
            print(f"  Note: extra columns not in schema: {extra}")

        records = rows_to_dicts(rows)
        print(f"  {len(records)} data row(s) found")
        for i, record in enumerate(records, start=1):
            print(f"  Row {i}: name={record.get('name')!r}, "
                  f"phone={record.get('phone')!r}, "
                  f"email={record.get('email')!r}")
        print()


if __name__ == "__main__":
    main()