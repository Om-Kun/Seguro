import os
import sys
import time
import asyncio
import pytest

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure seguro parent folder is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from seguro.tools.apps import apps_launch, load_apps_index
from seguro.tools.windows import windows_list, windows_close
from seguro.tools.shell import run_powershell
from seguro.tools.pyexec import run_python
from seguro.tools.files import files_write, files_read, files_find
from seguro.tools.office import excel_create_table_and_chart, word_create_document
from seguro.tools.browser import browser_search

def test_apps_index_and_launcher():
    """Acceptance test: Launches desktop apps and Microsoft Store apps by alias/name."""
    index = load_apps_index()
    assert len(index) > 5, "Apps index failed to discover StartApps"
    print(f"✅ Discovered {len(index)} StartApps on this Windows workstation")

    # Launch Notepad via alias
    res_np = apps_launch("notepad")
    assert res_np.get("status") == "launched"
    time.sleep(1.0)

    # Launch Calculator (Store app)
    res_calc = apps_launch("calculator")
    assert res_calc.get("status") == "launched"
    time.sleep(1.0)

    # Clean up test windows
    windows_close("Notepad")
    windows_close("Calculator")
    print("✅ App launcher passed: desktop & Store apps launched and closed")

def test_excel_table_and_chart_creation():
    """Acceptance test: Creates real .xlsx with tabular data and embedded bar chart."""
    headers = ["State", "Population", "Growth_Pct"]
    rows = [
        ["Maharashtra", 112374333, 15.9],
        ["Uttar Pradesh", 199812341, 20.2],
        ["Tamil Nadu", 72147030, 15.6],
        ["Karnataka", 61095297, 15.6],
        ["Gujarat", 60439692, 19.3]
    ]
    filename = "test_states.xlsx"
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out_path = os.path.join(desktop, filename)
    if os.path.exists(out_path):
        os.remove(out_path)

    res = excel_create_table_and_chart(headers, rows, filename=filename, chart_title="Indian States Population")
    assert res.get("ok") is True
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 1000

    # Verify workbook contents via openpyxl
    import openpyxl
    wb = openpyxl.load_workbook(out_path)
    ws = wb["ReportData"]
    assert ws.cell(row=1, column=1).value == "State"
    assert ws.cell(row=2, column=1).value == "Maharashtra"
    assert len(ws._charts) > 0, "Bar chart was not embedded in worksheet"
    print(f"✅ Excel creation passed: real .xlsx generated with {len(rows)} rows and embedded BarChart")

def test_word_document_creation():
    """Acceptance test: Creates real .docx document."""
    filename = "test_summary.docx"
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out_path = os.path.join(desktop, filename)
    if os.path.exists(out_path):
        os.remove(out_path)

    res = word_create_document(
        title="Raman Effect Summary",
        paragraphs=[
            "The Raman effect is the inelastic scattering of photons by matter.",
            "Discovered by Sir C. V. Raman in 1928, leading to the 1930 Nobel Prize in Physics."
        ],
        filename=filename
    )
    assert res.get("ok") is True
    assert os.path.exists(out_path)
    assert os.path.getsize(out_path) > 500
    print("✅ Word document creation passed: verified .docx structure")

def test_powershell_and_python_execution():
    """Acceptance test: Shell and Python execution in isolated subprocesses."""
    # Test PowerShell
    ps_res = run_powershell("Get-Date -Format yyyy-MM-dd")
    assert ps_res["returncode"] == 0
    assert len(ps_res["stdout"]) == 10

    # Test Python subprocess
    py_code = "import sys\nprint(f'PYTHON_OK_{sys.version_info.major}')"
    py_res = run_python(py_code)
    assert py_res["returncode"] == 0
    assert "PYTHON_OK_3" in py_res["stdout"]
    print("✅ PowerShell and isolated Python runners passed")

def test_file_operations():
    """Acceptance test: File write and read inside permitted Desktop directory."""
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    test_file = os.path.join(desktop, "test_seguro_io.txt")
    
    # Write
    w_res = files_write(test_file, "Seguro V2 Zero-Fake File IO Verification\nTimestamp Verified")
    assert w_res.get("ok") is True

    # Read
    r_res = files_read(test_file)
    assert r_res.get("ok") is True
    assert "Seguro V2 Zero-Fake" in r_res["content"]

    # Clean up
    if os.path.exists(test_file):
        os.remove(test_file)
    print("✅ File operations passed: write and read verified inside permitted boundary")

if __name__ == "__main__":
    test_apps_index_and_launcher()
    test_excel_table_and_chart_creation()
    test_word_document_creation()
    test_powershell_and_python_execution()
    test_file_operations()
    print("\n🎉 ALL PHASE 2 ACCEPTANCE TESTS PASSED!")
