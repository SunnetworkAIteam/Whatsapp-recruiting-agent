import pandas as pd
from pathlib import Path

def test_excel_parsing():
    file_path = Path(__file__).parent / "data/sample.xlsx"
    df = pd.read_excel(file_path)
    assert "name" in df.columns
    assert "phone" in df.columns
    assert "role" in df.columns
    assert len(df) > 0
