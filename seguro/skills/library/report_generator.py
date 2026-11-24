import os
from typing import List
from seguro.tools.registry import registry
from seguro.tools.office import excel_create_table_and_chart

@registry.register(
    name="skill_report_generator",
    description="Generates an executive Excel spreadsheet report with tabular metrics and embedded visual bar chart on the user Desktop.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Title of the report"},
            "headers": {"type": "array", "items": {"type": "string"}, "description": "Column names"},
            "rows": {"type": "array", "items": {"type": "array"}, "description": "Data rows"},
            "filename": {"type": "string", "description": "Output Excel filename (e.g. 'sales_report.xlsx')"}
        },
        "required": ["title", "headers", "rows"]
    }
)
def skill_report_generator(
    title: str,
    headers: List[str],
    rows: List[list],
    filename: str = "analysis_report.xlsx"
) -> dict:
    return excel_create_table_and_chart(
        headers=headers,
        rows=rows,
        filename=filename,
        chart_title=title
    )
