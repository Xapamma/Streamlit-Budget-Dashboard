"""Build downloadable PDF and Excel copies of a monthly budget plan."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

HEADERS = ["Category", "Planned", "Actual", "Remaining"]


def build_budget_rows(
    month_start,
    category_allocations: dict[str, float],
    subcategory_limits_by_main: dict[str, dict[str, float]],
    actual_for,
) -> list[tuple[str, str, float, float, float]]:
    """Return (level, label, planned, actual, remaining) rows; level is 'main' or 'sub'.

    ``actual_for(main_category, path_or_none)`` returns the actual amount.
    """
    rows = []
    for main, planned in category_allocations.items():
        actual = actual_for(main, main)
        rows.append(("main", main, planned, actual, planned - actual))
        for sub, sub_planned in subcategory_limits_by_main.get(main, {}).items():
            sub_actual = actual_for(main, f"{main} :: {sub}")
            rows.append(("sub", sub, sub_planned, sub_actual, sub_planned - sub_actual))
    return rows


def _summary(month_start, rows, monthly_income):
    income = float(monthly_income or 0.0)
    spending = sum(r[2] for r in rows if r[0] == "main" and r[1] != "Income")
    return [
        ("Monthly income", income),
        ("Planned spending", spending),
        ("Unallocated", income - spending),
    ]


def _period(month_start) -> str:
    end = (month_start + pd.offsets.MonthEnd(0)).date()
    return f"{month_start:%B %Y} ({month_start:%m/%d/%Y} - {end:%m/%d/%Y})"


def build_excel(month_start, rows, monthly_income) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = f"{month_start:%B %Y}"[:31]
    money = '"$"#,##0.00;[Red]-"$"#,##0.00'
    header_fill = PatternFill("solid", fgColor="1F4E78")
    main_fill = PatternFill("solid", fgColor="DDEBF7")
    thin = Side(style="thin", color="BFBFBF")

    ws["A1"] = f"Budget Plan - {month_start:%B %Y}"
    ws["A1"].font = Font(size=16, bold=True)
    ws["A2"] = f"Period: {_period(month_start)}"
    ws["A3"] = f"Generated: {date.today():%m/%d/%Y}"

    row = 5
    for label, value in _summary(month_start, rows, monthly_income):
        ws.cell(row, 1, label).font = Font(bold=True)
        cell = ws.cell(row, 2, value)
        cell.number_format = money
        row += 1

    row += 1
    for col, header in enumerate(HEADERS, 1):
        cell = ws.cell(row, col, header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = ws.cell(row + 1, 1)
    row += 1

    for level, label, planned, actual, remaining in rows:
        is_main = level == "main"
        values = [label if is_main else f"    {label}", planned, actual, remaining]
        for col, value in enumerate(values, 1):
            cell = ws.cell(row, col, value)
            cell.border = Border(bottom=thin)
            if col > 1:
                cell.number_format = money
            if is_main:
                cell.font = Font(bold=True)
                cell.fill = main_fill
        row += 1

    ws.column_dimensions["A"].width = 38
    for letter_ in "BCD":
        ws.column_dimensions[letter_].width = 16
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def build_pdf(month_start, rows, monthly_income) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Budget Plan - {month_start:%B %Y}",
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"Budget Plan - {month_start:%B %Y}", styles["Title"]),
        Paragraph(f"Period: {_period(month_start)}", styles["Normal"]),
        Paragraph(f"Generated: {date.today():%m/%d/%Y}", styles["Normal"]),
        Spacer(1, 14),
    ]

    def money(value: float) -> str:
        return f"-${-value:,.2f}" if value < 0 else f"${value:,.2f}"

    summary = Table(
        [[label, money(value)] for label, value in _summary(month_start, rows, monthly_income)],
        colWidths=[2.5 * inch, 1.5 * inch],
        hAlign="LEFT",
    )
    summary.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
            ]
        )
    )
    story += [summary, Spacer(1, 16)]

    data = [HEADERS]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
    ]
    for index, (level, label, planned, actual, remaining) in enumerate(rows, 1):
        is_main = level == "main"
        data.append(
            [label if is_main else f"    {label}", money(planned), money(actual), money(remaining)]
        )
        if is_main:
            style += [
                ("BACKGROUND", (0, index), (-1, index), colors.HexColor("#DDEBF7")),
                ("FONTNAME", (0, index), (-1, index), "Helvetica-Bold"),
            ]
        if remaining < 0:
            style.append(("TEXTCOLOR", (3, index), (3, index), colors.red))
    table = Table(
        data,
        colWidths=[3 * inch, 1.3 * inch, 1.3 * inch, 1.3 * inch],
        repeatRows=1,
    )
    table.setStyle(TableStyle(style))
    story.append(table)

    doc.build(story)
    return buffer.getvalue()
