"""
"Legacy Inventory Console v2.1": a deliberately API-less, keyboard-driven data-entry app, like
the old line-of-business tools RPA exists for. Records go into a private SQLite file; the only way
to add one is through the form (SKU -> Tab -> Name -> Tab -> Qty -> Enter).
"""
import os
import sqlite3
import tkinter as tk
from tkinter import ttk

DB = os.path.join(os.path.expanduser("~"), "SeguroProjects", "legacy_inventory.db")


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS stock (id INTEGER PRIMARY KEY, sku TEXT, name TEXT, qty INTEGER, "
                "entered_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    return con


def main():
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hdesk = u32.OpenDesktopW("default", 0, False, 0x01FF)
        if hdesk:
            u32.SetThreadDesktop(hdesk)
    except Exception:
        pass
    root = tk.Tk()
    root.title("Legacy Inventory Console v2.1")
    root.geometry("520x300")
    root.configure(bg="#c0c0c0")
    style = ttk.Style()
    style.theme_use("classic")
    tk.Label(root, text="INVENTORY ENTRY - TERMINAL 04", bg="#000080", fg="white",
             font=("Courier New", 12, "bold")).pack(fill="x")
    form = tk.Frame(root, bg="#c0c0c0")
    form.pack(pady=12)
    entries = {}
    for i, label in enumerate(("SKU", "Item name", "Quantity")):
        tk.Label(form, text=label + ":", bg="#c0c0c0", font=("Courier New", 11)).grid(row=i, column=0, sticky="e", pady=4)
        e = tk.Entry(form, width=30, font=("Courier New", 11))
        e.grid(row=i, column=1, pady=4)
        entries[label] = e
    status = tk.Label(root, text="F1=Help  ENTER=Save record", bg="#c0c0c0", font=("Courier New", 10))
    count = tk.Label(root, bg="#c0c0c0", font=("Courier New", 10))

    def refresh_count():
        n = db().execute("SELECT COUNT(*) FROM stock").fetchone()[0]
        count.config(text=f"Records on file: {n}")

    def save(_event=None):
        sku, name, qty = (entries[k].get().strip() for k in ("SKU", "Item name", "Quantity"))
        if not sku or not name or not qty.isdigit():
            status.config(text="ERR 17: ALL FIELDS REQUIRED, QTY NUMERIC", fg="red")
            return "break"
        con = db()
        con.execute("INSERT INTO stock (sku, name, qty) VALUES (?, ?, ?)", (sku, name, int(qty)))
        con.commit()
        for e in entries.values():
            e.delete(0, "end")
        entries["SKU"].focus_set()
        status.config(text=f"RECORD {sku} SAVED", fg="darkgreen")
        refresh_count()
        return "break"

    root.bind("<Return>", save)
    tk.Button(root, text="Save (Enter)", command=save, width=14).pack()
    status.pack(pady=6)
    count.pack()
    refresh_count()
    entries["SKU"].focus_set()
    root.mainloop()


if __name__ == "__main__":
    main()
