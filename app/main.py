from fastapi import FastAPI, UploadFile, File
import pandas as pd

app = FastAPI()

@app.post("/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    if not file.filename.endswith(".xlsx"):
        return {"error": "Only .xlsx files allowed"}
    
    df = pd.read_excel(file.file)
    required_cols = {"name", "phone", "role"}
    if not required_cols.issubset(df.columns):
        return {"error": "Missing required columns"}
    
    records = df.to_dict(orient="records")
    return {"parsed": records}
