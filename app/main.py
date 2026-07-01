from fastapi import FastAPI, UploadFile, File
import pandas as pd
from supabase import create_client
from app.validation import validate_candidate, normalize_phone, is_duplicate

app = FastAPI()

# Supabase client (replace with your .env values)
SUPABASE_URL = "https://ljvxxtkbbjskfscchabc.supabase.co"
SUPABASE_KEY = "sb_secret_vVgq-WpxlPlyza5aekaMVw_kuMIt72K"
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


app = FastAPI()

@app.post("/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    if not file.filename.endswith(".xlsx"):
        return {"error": "Only .xlsx files allowed"}

    file.file.seek(0)  # reset pointer
    try:
        df = pd.read_excel(file.file, dtype={"phone": str})
    except Exception as e:
        return {"error": f"Could not parse Excel file: {str(e)}"}

    required_cols = {"name", "phone", "role"}

    if not required_cols.issubset(df.columns.str.lower()):
        return {"error": "Missing required columns (need name, phone, role)"}

    records = df.to_dict(orient="records")
    inserted, skipped = [], []

    for record in records:
        if not validate_candidate(record):
            skipped.append({"reason": "invalid", "record": record})
            continue

        normalized = normalize_phone(record["phone"])
        if is_duplicate(supabase, normalized):
            skipped.append({"reason": "duplicate", "record": record})
            continue

        supabase.table("candidates").insert({
            "name": record["name"],
            "phone": normalized,
            "role": record["role"]
        }).execute()
        inserted.append(record)

    return {"inserted": inserted, "skipped": skipped}
    
    df = pd.read_excel(file.file)
    required_cols = {"name", "phone", "role"}
    if not required_cols.issubset(df.columns):
        return {"error": "Missing required columns"}
    
    records = df.to_dict(orient="records")
    return {"parsed": records}
