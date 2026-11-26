"""Builds the PROJECT B.A.N.T.A.Y. vehicle size-classification workbook.
Usage: python tools/make_vehicle_dataset.py <output.xlsx>

All derived columns are Excel formulas (they recalculate when you enter real measurements).
Typical dimensions are APPROXIMATE published/commonly quoted values for popular Philippine models,
NOT measurements taken in Ormoc. Every placeholder row is marked PLACEHOLDER until replaced.
"""
import random
import sys

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

FONT = "Arial"
BLUE, BLACK, GREEN = "0000FF", "000000", "008000"
YELLOW = PatternFill("solid", fgColor="FFFF00")
LIGHT_YELLOW = PatternFill("solid", fgColor="FFF7C2")
HEAD = PatternFill("solid", fgColor="252321")        # project charcoal
SUB = PatternFill("solid", fgColor="F4EBDD")         # project cream
thin = Side(style="thin", color="D9CFC0")
BOX = Border(left=thin, right=thin, top=thin, bottom=thin)


def f(color=BLACK, bold=False, size=10, italic=False):
    return Font(name=FONT, color=color, bold=bold, size=size, italic=italic)


# ----------------------------------------------------------------------------- class reference data
# (code, label, display name, example models, W, L, H, tolerance, fill factor, zone buffer,
#  speed min, speed max, body at scan height, source/notes)
CLASSES = [
    (1, "bicycle", "Bicycle", "City / mountain bike, 26-29 in wheels", 0.60, 1.75, 1.05, 0.10, 0.35, 0.0, 8, 20,
     "PARTIAL - wheel rims and rider legs only (sparse)", "Typical adult bicycle dimensions; frame and wheel size vary."),
    (2, "motorcycle", "Motorcycle (underbone / scooter / standard)", "Honda TMX 125 Alpha, Honda XRM 125, Yamaha Mio", 0.73, 1.95, 1.08, 0.10, 0.55, 0.0, 20, 60,
     "YES - engine, legs, wheels", "Approx. manufacturer specs of common PH 110-155 cc models (about 1.9-2.0 x 0.7-0.8 x 1.0-1.1 m)."),
    (3, "habal_habal", "Habal-habal (passenger motorcycle)", "Motorcycle with extended seat / side planks", 0.90, 2.10, 1.10, 0.20, 0.60, 0.25, 20, 50,
     "YES - riders' legs widen the profile", "Locally modified; width depends on planks and passengers. HIGHEST uncertainty: measure locally."),
    (4, "tricycle_sidecar", "Tricycle (motorcycle + sidecar)", "Ormoc-style tricycle with covered sidecar", 1.55, 2.40, 1.70, 0.15, 0.75, 0.25, 15, 35,
     "YES - sidecar body", "Body styles vary by LGU/TODA; dimensions are a general estimate. Measure several local units."),
    (5, "multicab", "Multicab / mini utility", "Suzuki Multicab / Scrum-type mini truck", 1.40, 3.20, 1.80, 0.10, 0.85, 0.25, 20, 50,
     "YES - cab and bed sides", "Common in Leyte; approx. kei-truck size (about 3.2-3.4 x 1.4 x 1.8 m). Added as a common local type."),
    (6, "sedan_hatchback", "Sedan / hatchback", "Toyota Vios (sedan), Toyota Wigo (hatchback)", 1.70, 4.10, 1.50, 0.12, 0.85, 0.5, 20, 80,
     "YES - doors / bumper", "Vios about 4.43 x 1.74 x 1.48 m; Wigo about 3.66 x 1.60 x 1.52 m (approx. brochure values). Typical value is between them."),
    (7, "suv_auv_mpv", "SUV / AUV / MPV", "Toyota Fortuner, Innova, Avanza; Mitsubishi Montero Sport", 1.83, 4.70, 1.80, 0.08, 0.85, 0.5, 20, 80,
     "YES - doors / bumper", "Fortuner about 4.80 x 1.86 x 1.84; Innova about 4.74 x 1.83 x 1.80; Avanza about 4.39 x 1.73 x 1.70 m (approx.)."),
    (8, "van", "Van", "Toyota Hiace Commuter, Mitsubishi L300 FB", 1.80, 5.00, 2.10, 0.15, 0.90, 0.75, 20, 70,
     "YES - side panels", "Hiace Commuter about 5.38 x 1.88 x 2.29; L300 FB about 4.7 x 1.7 x 2.0 m (approx.)."),
    (9, "pickup", "Pickup truck", "Toyota Hilux, Ford Ranger, Nissan Navara", 1.86, 5.30, 1.82, 0.05, 0.80, 0.75, 20, 80,
     "YES - doors and bed sides", "Hilux about 5.33 x 1.86 x 1.82; Ranger about 5.37 x 1.92 x 1.88 m (approx.)."),
    (10, "jeepney", "Jeepney (PUJ)", "Traditional jeepney; modern PUJ is larger", 1.90, 6.00, 2.10, 0.12, 0.85, 0.75, 15, 50,
     "YES - body sides", "Traditional PUJ about 5.5-6.5 m long. Modern PUJ about 7 x 2 x 2.7 m: record it separately in Remarks. Added as a common local type."),
    (11, "light_truck", "Light / delivery truck", "Isuzu NHR/NLR (ELF), Mitsubishi Canter with box", 1.90, 5.50, 2.60, 0.20, 0.90, 1.0, 20, 60,
     "YES - box / chassis", "Varies with body (dropside, box, aluminum van); about 4.7-6.1 x 1.7-2.0 x 2.2-3.0 m."),
    (12, "bus", "Bus", "Provincial / city bus (Leyte-Samar routes)", 2.50, 11.50, 3.30, 0.10, 0.92, 1.5, 20, 60,
     "YES - body", "Typical 11-12 m two-axle bus, about 2.5 m wide (common max width), 3.2-3.6 m tall."),
]
N = len(CLASSES)
CR_FIRST, CR_LAST = 6, 6 + N - 1                     # Class_Reference data rows


def rng_ref(col):
    return f"Class_Reference!${col}${CR_FIRST}:${col}${CR_LAST}"


def lookup(col, label_cell):
    return f"INDEX({rng_ref(col)},MATCH({label_cell},{rng_ref('B')},0))"


def header_row(ws, row, headers, widths=None):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(name=FONT, bold=True, color="F4EBDD", size=10)
        c.fill = HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BOX
        if widths:
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
    ws.row_dimensions[row].height = 42


def title(ws, text, sub):
    ws["A1"] = text
    ws["A1"].font = Font(name=FONT, bold=True, size=15, color="252321")
    ws["A2"] = sub
    ws["A2"].font = f(italic=True, color="A34F36")


def build(path):
    wb = Workbook()

    # ========================================================================= README
    rd = wb.active
    rd.title = "README"
    title(rd, "PROJECT B.A.N.T.A.Y. - Vehicle Size Classification Dataset",
          "Reference sizes and training records for classifying nearby vehicles from distance / LiDAR measurements")
    lines = [
        ("What this workbook is", True),
        ("A template for building a labeled vehicle-size dataset for BANTAY's machine-learning classifier, with "
         "realistic PLACEHOLDER rows you replace with your own measurements from Ormoc City.", False),
        ("", False),
        ("Sheets", True),
        ("Assumptions - global settings (LiDAR scan height, base warning/caution distances, time-to-collision thresholds).", False),
        ("Class_Reference - one row per vehicle type: typical size, tolerance, min/max limits, areas, size tier, proximity zones.", False),
        ("Training_Data - one row per observed vehicle: measurements, LiDAR readings, label, and automatic checks.", False),
        ("", False),
        ("Colour legend", True),
        ("Yellow fill = cells for YOU to fill in or replace with measured data.", False),
        ("Blue text = hard-coded input or placeholder value (replace with real data; it is not a measurement).", False),
        ("Black text = formula (do not type over it; it recalculates automatically).", False),
        ("Green text = value linked from another sheet.", False),
        ("", False),
        ("How to record a real vehicle (one row in Training_Data)", True),
        ("1. Change Data_Status from PLACEHOLDER to MEASURED (or VERIFIED once double-checked).", False),
        ("2. Pick Class_Label from the dropdown; Class_Code fills in automatically.", False),
        ("3. Measure with a tape, in METRES: Front_Width_m = widest point EXCLUDING mirrors; Side_Length_m = bumper to bumper; "
         "Height_m = ground to highest fixed point (roof, cover or rider's head for habal-habal: note which in Remarks).", False),
        ("4. From BANTAY: Distance_to_Sensor_m, Bearing_deg, Closing_Speed_kph (speed TOWARD the sensor, not road speed) and the LiDAR_* columns "
         "(Live Scan > click the object). Leave blank what you did not measure.", False),
        ("5. Check the *_Check columns: CHECK means the value is outside the class's typical range. Re-measure, "
         "or keep it and explain in Remarks (real vehicles do vary).", False),
        ("6. Delete placeholder rows (or keep them filtered out) before training. Never train on PLACEHOLDER rows as if they were real.", False),
        ("", False),
        ("Honesty notes", True),
        ("Typical sizes are APPROXIMATE values commonly quoted in manufacturer brochures for popular Philippine models, rounded. "
         "They are NOT measurements taken in Ormoc. Verify against the actual vehicles you observe.", False),
        ("Habal-habal and tricycle sizes vary a lot between builders and TODAs. Treat them as rough starting points.", False),
        ("Fill factors (how much of the width x height rectangle is solid) and zone buffers are ASSUMPTIONS for this "
         "template. Adjust them in Class_Reference once you have data.", False),
        ("BANTAY's LiDAR is 2D: it measures one horizontal slice at the scan height, so it sees length/width "
         "at that height, never true height. Height here comes from the tape measure.", False),
        ("Proximity thresholds (2 m warning, 4 m caution, 1.5 s / 3.0 s time-to-collision) come from the BANTAY research proposal.", False),
    ]
    r = 4
    for text, bold in lines:
        c = rd.cell(row=r, column=1, value=text)
        c.font = f(bold=bold, size=11 if bold else 10, color="252321" if bold else BLACK)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    rd.column_dimensions["A"].width = 130

    # ========================================================================= Assumptions
    asm = wb.create_sheet("Assumptions")
    title(asm, "Assumptions", "Global settings used by the formulas (blue = editable input)")
    header_row(asm, 4, ["Setting", "Value", "Unit", "Source / note"], [40, 14, 10, 90])
    settings = [
        ("LiDAR scan height above road", 0.60, "m", "Default BANTAY digital-twin height. Set to your actual mounting height."),
        ("Base WARNING distance", 2.0, "m", "BANTAY research proposal: Zone 1 = 0-2 m."),
        ("Base CAUTION distance", 4.0, "m", "BANTAY research proposal: Zone 2 = 2-4 m."),
        ("Time-to-collision WARNING", 1.5, "s", "BANTAY research proposal (Hayward 1972 TTC; severe conflict)."),
        ("Time-to-collision CAUTION", 3.0, "s", "BANTAY research proposal (slight conflict)."),
    ]
    for i, (name, val, unit, note) in enumerate(settings):
        row = 5 + i
        asm.cell(row=row, column=1, value=name).font = f()
        c = asm.cell(row=row, column=2, value=val)
        c.font = f(BLUE, bold=True); c.fill = YELLOW; c.number_format = "0.00"
        asm.cell(row=row, column=3, value=unit).font = f()
        asm.cell(row=row, column=4, value=note).font = f(italic=True)
        for col in range(1, 5):
            asm.cell(row=row, column=col).border = BOX
    A_SCAN, A_WARN, A_CAUT, A_TTCW, A_TTCC = "Assumptions!$B$5", "Assumptions!$B$6", "Assumptions!$B$7", "Assumptions!$B$8", "Assumptions!$B$9"

    # ========================================================================= Class_Reference
    cr = wb.create_sheet("Class_Reference")
    title(cr, "Class_Reference - typical vehicle sizes (Philippines)",
          "Blue = approximate typical values / assumptions to verify. Black = formulas. Units: metres, m^2, km/h.")
    cr["A3"] = "Edit the blue cells as you collect real measurements; min/max limits, areas, tiers and zones update automatically."
    cr["A3"].font = f(italic=True)
    cr_headers = ["Class_Code", "Class_Label", "Display_Name", "Example_Models_PH", "Typ_Front_Width_m", "Typ_Side_Length_m",
                  "Typ_Height_m", "Tolerance_pct", "Width_Min_m", "Width_Max_m", "Length_Min_m", "Length_Max_m", "Height_Min_m",
                  "Height_Max_m", "Frontal_Fill_Factor", "Est_Frontal_Area_m2", "Footprint_Area_m2", "Side_Area_m2",
                  "Length_Width_Ratio", "Size_Tier", "Zone_Buffer_m", "Warning_Zone_m", "Caution_Zone_m",
                  "Typ_Speed_Min_kph", "Typ_Speed_Max_kph", "Visible_At_Scan_Height", "Source_Notes"]
    header_row(cr, 5, cr_headers, [9, 17, 30, 38, 11, 11, 11, 10, 10, 10, 10, 10, 10, 10, 10, 11, 11, 11, 10, 9, 10, 10, 10, 10, 10, 34, 70])
    for i, c in enumerate(CLASSES):
        row = CR_FIRST + i
        code, label, disp, ex, W, L, H, tol, fill, buf, smin, smax, vis, note = c
        inputs = {1: code, 2: label, 3: disp, 4: ex, 5: W, 6: L, 7: H, 8: tol, 15: fill, 21: buf, 24: smin, 25: smax, 26: vis, 27: note}
        for col, v in inputs.items():
            cell = cr.cell(row=row, column=col, value=v)
            cell.font = f(BLUE if col not in (1, 2, 3) else BLACK, bold=col == 2)
        formulas = {
            9: f"=ROUND(E{row}*(1-H{row}),2)", 10: f"=ROUND(E{row}*(1+H{row}),2)",
            11: f"=ROUND(F{row}*(1-H{row}),2)", 12: f"=ROUND(F{row}*(1+H{row}),2)",
            13: f"=ROUND(G{row}*(1-H{row}),2)", 14: f"=ROUND(G{row}*(1+H{row}),2)",
            16: f"=ROUND(E{row}*G{row}*O{row},2)", 17: f"=ROUND(E{row}*F{row},2)", 18: f"=ROUND(F{row}*G{row},2)",
            19: f"=IF(E{row}>0,ROUND(F{row}/E{row},2),\"\")",
            20: f"=IF(Q{row}<2,\"S\",IF(Q{row}<6,\"M\",IF(Q{row}<12,\"L\",\"XL\")))",
            22: f"=ROUND({A_WARN}+U{row},2)", 23: f"=ROUND({A_CAUT}+2*U{row},2)",
        }
        for col, v in formulas.items():
            cell = cr.cell(row=row, column=col, value=v)
            cell.font = f(GREEN if col in (22, 23) else BLACK)
        for col in range(1, 28):
            cell = cr.cell(row=row, column=col)
            cell.border = BOX
            cell.alignment = Alignment(vertical="top", wrap_text=col in (3, 4, 26, 27))
        for col in (5, 6, 7, 9, 10, 11, 12, 13, 14, 16, 17, 18, 19, 21, 22, 23):
            cr.cell(row=row, column=col).number_format = "0.00"
        cr.cell(row=row, column=8).number_format = "0%"
        cr.cell(row=row, column=15).number_format = "0.00"
    note_row = CR_LAST + 2
    notes = [
        "Size_Tier from footprint (width x length): S < 2 m^2, M < 6 m^2, L < 12 m^2, XL above. Thresholds are an assumption.",
        "Warning_Zone = base warning (Assumptions) + Zone_Buffer; Caution_Zone = base caution + 2 x Zone_Buffer. Buffers are assumptions: larger vehicles get earlier alerts.",
        "Est_Frontal_Area = width x height x fill factor (share of the bounding rectangle that is solid). Fill factors are assumptions.",
        "Types added beyond the request because they are common on Leyte roads: multicab, jeepney.",
    ]
    for k, t in enumerate(notes):
        cr.cell(row=note_row + k, column=1, value=t).font = f(italic=True)
    cr.freeze_panes = "C6"
    cr["H6"].comment = Comment("Tolerance: +/- share of the typical value treated as normal for this class. Assumption.", "BANTAY")
    cr["O6"].comment = Comment("Fill factor: fraction of width x height rectangle that is solid. Assumption, not measured.", "BANTAY")

    # ========================================================================= Training_Data
    td = wb.create_sheet("Training_Data")
    title(td, "Training_Data - one row per observed vehicle",
          "PLACEHOLDER rows show the expected format; replace them with measured data. Yellow = fill in. Black = automatic.")
    td_headers = ["Sample_ID", "Data_Status", "Date", "Location", "Class_Label", "Class_Code", "Example_Model",
                  "Front_Width_m", "Side_Length_m", "Height_m", "Est_Frontal_Area_m2", "Footprint_Area_m2", "Side_Area_m2",
                  "Length_Width_Ratio", "Max_Dimension_m", "Min_Dimension_m", "Width_Check", "Length_Check", "Height_Check",
                  "Distance_to_Sensor_m", "Bearing_deg", "Closing_Speed_kph", "Proximity_Zone", "TTC_s", "Risk_Level",
                  "Scan_Height_m", "LiDAR_Visible_Length_m", "LiDAR_Point_Count", "LiDAR_Segments", "Measured_By", "Remarks"]
    header_row(td, 4, td_headers, [10, 13, 12, 20, 17, 8, 24, 10, 10, 10, 11, 11, 11, 10, 11, 11, 9, 9, 9, 11, 9, 10, 12, 9, 10, 9, 11, 10, 9, 13, 48])
    INPUT_COLS = {1, 2, 3, 4, 5, 7, 8, 9, 10, 20, 21, 22, 27, 28, 29, 30, 31}

    rnd = random.Random(2026)
    rows = []
    n_id = 1
    for c in CLASSES:
        code, label, disp, ex, W, L, H, tol, fill, buf, smin, smax, vis, note = c
        models = [m.strip() for m in ex.replace(";", ",").split(",")]
        for k in range(4):
            j = lambda v: round(v * (1 + rnd.uniform(-0.6, 0.6) * tol), 2)   # stay inside the tolerance band
            rows.append({
                1: f"S{n_id:04d}", 2: "PLACEHOLDER", 3: None, 4: "Ormoc City, Leyte", 5: label,
                7: models[k % len(models)], 8: j(W), 9: j(L), 10: j(H),
                20: round(rnd.uniform(1.0, 9.0), 1), 21: round(rnd.uniform(-90, 90)), 22: round(rnd.uniform(0, 0.35 * smax)),   # speed TOWARD the sensor, not road speed
                30: None,
                31: "PLACEHOLDER: typical value with small variation. Replace with tape/LiDAR measurement.",
            })
            n_id += 1
    first, last_ph = 5, 5 + len(rows) - 1
    last = last_ph + 100                                  # 100 empty rows ready for real data

    for r in range(first, last + 1):
        data = rows[r - first] if r <= last_ph else {1: f"S{n_id + (r - last_ph - 1):04d}"}
        for col, v in data.items():
            if v is not None:
                td.cell(row=r, column=col, value=v)
        label = f"$E{r}"
        fx = {
            6: f'=IF(E{r}="","",IFERROR({lookup("A", label)},"?"))',
            11: f'=IF(OR(H{r}="",J{r}="",E{r}=""),"",IFERROR(ROUND(H{r}*J{r}*{lookup("O", label)},2),""))',
            12: f'=IF(OR(H{r}="",I{r}=""),"",ROUND(H{r}*I{r},2))',
            13: f'=IF(OR(I{r}="",J{r}=""),"",ROUND(I{r}*J{r},2))',
            14: f'=IF(OR(H{r}="",I{r}=""),"",IF(H{r}>0,ROUND(I{r}/H{r},2),""))',
            15: f'=IF(COUNT(H{r}:J{r})=0,"",MAX(H{r}:J{r}))',
            16: f'=IF(COUNT(H{r}:J{r})=0,"",MIN(H{r}:J{r}))',
            17: f'=IF(OR(H{r}="",E{r}=""),"",IFERROR(IF(AND(H{r}>={lookup("I", label)},H{r}<={lookup("J", label)}),"OK","CHECK"),""))',
            18: f'=IF(OR(I{r}="",E{r}=""),"",IFERROR(IF(AND(I{r}>={lookup("K", label)},I{r}<={lookup("L", label)}),"OK","CHECK"),""))',
            19: f'=IF(OR(J{r}="",E{r}=""),"",IFERROR(IF(AND(J{r}>={lookup("M", label)},J{r}<={lookup("N", label)}),"OK","CHECK"),""))',
            23: f'=IF(OR(T{r}="",E{r}=""),"",IFERROR(IF(T{r}<={lookup("V", label)},"WARNING",IF(T{r}<={lookup("W", label)},"CAUTION","CLEAR")),""))',
            24: f'=IF(OR(T{r}="",V{r}=""),"",IF(V{r}>0,ROUND(T{r}/(V{r}/3.6),2),""))',
            25: f'=IF(W{r}="","",IF(OR(W{r}="WARNING",AND(X{r}<>"",X{r}<={A_TTCW})),"WARNING",IF(OR(W{r}="CAUTION",AND(X{r}<>"",X{r}<={A_TTCC})),"CAUTION","CLEAR")))',
            26: f"={A_SCAN}",
        }
        for col, v in fx.items():
            td.cell(row=r, column=col, value=v)
        for col in range(1, len(td_headers) + 1):
            cell = td.cell(row=r, column=col)
            cell.border = BOX
            if col in INPUT_COLS:
                cell.fill = YELLOW if r > last_ph else LIGHT_YELLOW
                cell.font = f(BLUE if r <= last_ph else BLACK)
            else:
                cell.font = f(GREEN if col == 26 else BLACK)
        for col in (8, 9, 10, 11, 12, 13, 14, 15, 16, 20, 24, 26, 27):
            td.cell(row=r, column=col).number_format = "0.00"
        td.cell(row=r, column=3).number_format = "yyyy-mm-dd"

    # dropdowns
    dv_label = DataValidation(type="list", formula1=f"={rng_ref('B')}", allow_blank=True,
                              error="Pick a label from Class_Reference.", errorTitle="Unknown class")
    dv_status = DataValidation(type="list", formula1='"PLACEHOLDER,MEASURED,VERIFIED"', allow_blank=True)
    td.add_data_validation(dv_label); td.add_data_validation(dv_status)
    dv_label.add(f"E{first}:E{last}"); dv_status.add(f"B{first}:B{last}")
    td.freeze_panes = "F5"
    td.auto_filter.ref = f"A4:{get_column_letter(len(td_headers))}{last}"
    td["E4"].comment = Comment("Choose from the dropdown (labels come from Class_Reference).", "BANTAY")
    td["H4"].comment = Comment("Widest point EXCLUDING side mirrors, metres.", "BANTAY")
    td["J4"].comment = Comment("Tape measure: ground to highest fixed point. The 2D LiDAR cannot measure height.", "BANTAY")
    td["T4"].comment = Comment("From BANTAY Live Scan (distance from the LiDAR to the object's centre).", "BANTAY")
    td["V4"].comment = Comment("Speed TOWARD the sensor (km/h), from BANTAY's track speed and heading. "
                               "A vehicle passing parallel has ~0 closing speed even at 60 km/h. Used for time-to-collision.", "BANTAY")
    td["AA4"].comment = Comment("From BANTAY: the object's length as seen in the scan slice (Live Scan > Selected object > Max extent).", "BANTAY")

    # summary under the header on Class_Reference: how many real rows per class (excluding placeholders)
    s = CR_LAST + 8
    cr.cell(row=s, column=1, value="Real (non-placeholder) samples per class - for checking dataset balance").font = f(bold=True)
    header_row(cr, s + 1, ["Class_Code", "Class_Label", "Measured", "Verified", "Placeholders"])
    for i in range(N):
        row = s + 2 + i
        cr.cell(row=row, column=1, value=f"=A{CR_FIRST + i}").font = f()
        cr.cell(row=row, column=2, value=f"=B{CR_FIRST + i}").font = f()
        for col, status in ((3, "MEASURED"), (4, "VERIFIED"), (5, "PLACEHOLDER")):
            cr.cell(row=row, column=col,
                    value=f'=COUNTIFS(Training_Data!$E${first}:$E${last},B{row},Training_Data!$B${first}:$B${last},"{status}")').font = f(GREEN)
        for col in range(1, 6):
            cr.cell(row=row, column=col).border = BOX
    cr.row_dimensions[s + 1].height = 30

    for ws in wb.worksheets:
        ws.sheet_view.showGridLines = ws.title != "README"
    wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    print(f"saved {path}: {len(rows)} placeholder rows (rows {first}-{last_ph}), empty rows to {last}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "Project_BANTAY_Vehicle_Size_Dataset.xlsx")
