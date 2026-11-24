import os
from collections import Counter

from seguro.tools.registry import registry, _parse_list
from seguro.safety.gate import safety_gate


def _rows(table):
    rows = _parse_list(table)
    return [(_parse_list(r) if isinstance(r, str) else list(r)) for r in rows]


@registry.register(
    name="chart_png",
    description=(
        "Renders a bar chart PNG from a table (first row = headers). By default plots column 2 against column 1; "
        "with count_by=<column name> it counts rows per value of that column (e.g. articles per source)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "table": {"type": "array", "items": {"type": "array"}, "description": "Rows, first row is the header"},
            "filename": {"type": "string", "description": "Output .png name or path (bare names go to the Desktop)"},
            "title": {"type": "string", "description": "Chart title"},
            "count_by": {"type": "string", "description": "Optional header name to count rows by"}
        },
        "required": ["table", "filename"]
    }
)
def chart_png(table: list, filename: str, title: str = "", count_by: str = "") -> dict:
    import matplotlib
    matplotlib.use("Agg")  # no GUI backend: never opens a window or blocks
    import matplotlib.pyplot as plt
    from seguro.tools.files import normalize_user_path

    rows = _rows(table)
    if len(rows) < 2:
        return {"ok": False, "error": "table needs a header row and at least one data row"}
    header, data = [str(h) for h in rows[0]], rows[1:]
    if count_by:
        if count_by not in header:
            return {"ok": False, "error": f"count_by '{count_by}' is not a column. Columns: {header}"}
        idx = header.index(count_by)
        counts = Counter(str(r[idx]) for r in data if len(r) > idx)
        labels, values, ylabel = list(counts), list(counts.values()), "count"
    else:
        if len(header) < 2:
            return {"ok": False, "error": "need at least 2 columns (label, value) or use count_by"}
        labels, values = [], []
        for r in data:
            try:
                values.append(float(str(r[1]).replace(",", "")))
                labels.append(str(r[0]))
            except (ValueError, IndexError):
                return {"ok": False, "error": f"column '{header[1]}' must be numeric; bad row {r}"}
        ylabel = header[1]

    name = filename if filename.lower().endswith(".png") else filename + ".png"
    out_path = normalize_user_path(name)
    ok, reason = safety_gate.check_path(out_path)
    if not ok:
        return {"ok": False, "error": reason}

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)
    ax.bar(labels, values, color="#5b6cff")
    ax.set_title(title or (f"Rows per {count_by}" if count_by else f"{header[1]} by {header[0]}"))
    ax.set_ylabel(ylabel)
    ax.tick_params(axis="x", labelrotation=30)
    fig.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return {"ok": True, "path": out_path, "bars": len(labels), "labels": labels, "values": values}
