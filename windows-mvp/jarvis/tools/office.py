"""Excel and Word through COM (needs Microsoft Office installed and the file open)."""

from .registry import ToolError, tool


def _app(prog_id: str, label: str):
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        return win32com.client.GetActiveObject(prog_id)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"{label} isn't running. Open the file first.") from e


@tool(
    "excel_read",
    "Read cells from the open Excel workbook. Empty range = the used area of the sheet.",
    {"range": {"type": "string", "description": "e.g. A1:D20"}, "sheet": {"type": "string"}},
    risk="read",
)
def excel_read(range: str = "", sheet: str = "") -> str:  # noqa: A002 - matches the tool schema
    xl = _app("Excel.Application", "Excel")
    wb = xl.ActiveWorkbook
    if wb is None:
        raise ToolError("No workbook is open in Excel.")
    ws = wb.Worksheets(sheet) if sheet else wb.ActiveSheet
    rng = ws.Range(range) if range else ws.UsedRange
    values = rng.Value
    if not isinstance(values, tuple):
        values = ((values,),)
    rows = ["\t".join("" if v is None else str(v) for v in row) for row in values[:200]]
    return f"{wb.Name} / {ws.Name} / {rng.Address}:\n" + "\n".join(rows)


@tool(
    "excel_write",
    "Write values into the open Excel workbook starting at a cell.",
    {
        "start_cell": {"type": "string", "description": "Top-left cell, e.g. B2"},
        "rows": {"type": "array", "items": {"type": "array", "items": {}}, "description": "2-D list of values"},
        "sheet": {"type": "string"},
    },
    required=["start_cell", "rows"],
)
def excel_write(start_cell: str, rows: list, sheet: str = "") -> str:
    xl = _app("Excel.Application", "Excel")
    wb = xl.ActiveWorkbook
    if wb is None:
        raise ToolError("No workbook is open in Excel.")
    ws = wb.Worksheets(sheet) if sheet else wb.ActiveSheet
    width = max(len(r) for r in rows)
    data = tuple(tuple(list(r) + [""] * (width - len(r))) for r in rows)
    start = ws.Range(start_cell)
    r0, c0 = start.Row, start.Column
    ws.Range(ws.Cells(r0, c0), ws.Cells(r0 + len(data) - 1, c0 + width - 1)).Value = data
    return f"Wrote {len(data)} rows x {width} columns at {start_cell}."


@tool("word_read", "Read the text of the active Word document.", risk="read")
def word_read() -> str:
    word = _app("Word.Application", "Word")
    if word.Documents.Count == 0:
        raise ToolError("No document is open in Word.")
    doc = word.ActiveDocument
    return f"{doc.Name}:\n{doc.Content.Text[:8000]}"


@tool(
    "word_append",
    "Add a paragraph and/or a table to the end of the active Word document.",
    {
        "text": {"type": "string"},
        "table": {"type": "array", "items": {"type": "array", "items": {}}, "description": "Optional 2-D list for a table"},
    },
)
def word_append(text: str = "", table: list | None = None) -> str:
    word = _app("Word.Application", "Word")
    if word.Documents.Count == 0:
        raise ToolError("No document is open in Word.")
    doc = word.ActiveDocument
    if text:
        doc.Content.InsertAfter("\n" + text)
    if table:
        width = max(len(r) for r in table)
        doc.Content.InsertParagraphAfter()
        rng = doc.Content
        rng.Collapse(0)  # wdCollapseEnd
        tbl = doc.Tables.Add(rng, len(table), width)
        tbl.Borders.Enable = True
        for r, row in enumerate(table, 1):
            for c, value in enumerate(row, 1):
                tbl.Cell(r, c).Range.Text = "" if value is None else str(value)
    return f"Added to {doc.Name}."
