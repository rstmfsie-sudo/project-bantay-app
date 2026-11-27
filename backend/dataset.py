"""Object sizes from the researcher's workbook dataset/Project_BANTAY_Vehicle_Size_Dataset.xlsx.

Only the Class_Reference sheet is read: typical (length, width) per row, grouped into the app's categories.
The workbook's Training_Data rows are tape measurements, not LiDAR features, and every row is still
PLACEHOLDER, so none are used as labels. A row whose label mentions pedestrian/human/person/walker becomes
"pedestrian"; bicycle -> bicycle; motorcycle and habal-habal -> motorcycle; every other vehicle -> car.
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

FILE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "dataset" / "Project_BANTAY_Vehicle_Size_Dataset.xlsx"
TWO_WHEELERS = {"bicycle": "bicycle", "motorcycle": "motorcycle", "habal_habal": "motorcycle"}
PEOPLE = ("pedestrian", "human", "person", "walker")


def category(label: str) -> str:
    l = label.lower()
    return "pedestrian" if any(p in l for p in PEOPLE) else TWO_WHEELERS.get(l, "car")


@lru_cache(maxsize=1)
def sizes() -> dict[str, list[tuple[str, float, float]]]:
    """{category: [(workbook label, length_m, width_m), ...]}; {} if the workbook is missing/unreadable."""
    try:
        import openpyxl
        ws = openpyxl.load_workbook(FILE, data_only=True, read_only=True)["Class_Reference"]
        rows = list(ws.iter_rows(values_only=True))
        h = next(i for i, r in enumerate(rows) if r and "Class_Label" in r)
        col = {n: i for i, n in enumerate(rows[h])}
        out: dict[str, list] = {}
        for r in rows[h + 1:]:
            if not r[col["Class_Label"]] or not isinstance(r[col["Typ_Side_Length_m"]], (int, float)):
                continue
            label = str(r[col["Class_Label"]])
            out.setdefault(category(label), []).append((label, float(r[col["Typ_Side_Length_m"]]),
                                                        float(r[col["Typ_Front_Width_m"]])))
        return out
    except Exception:
        return {}
