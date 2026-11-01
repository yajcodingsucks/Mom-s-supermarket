"""
Shop Manager - a simple supermarket-style inventory & sales app.

Pages:  Scan & Sell | Products | Revenue | Profit | Credit

Run with:   python shop_manager.py
Data is saved in "shop.db" next to this file (created automatically).

Barcode input works three ways:
  1. Type the barcode in any scan box and press Enter
     (a USB barcode scanner does exactly this, so it will just work later).
  2. Phone as scanner (e.g. a "barcode to PC" style app that types into your laptop).
  3. Laptop webcam: click "Camera" (needs:  pip install opencv-python pyzbar).
"""

import os
import sqlite3
import sys
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

# ----------------------------------------------------------------- settings --
APP_NAME = "Shop Manager"
CURRENCY = "Rs"          # change to "$", "€", "₹" ... as you like
LOW_STOCK = 5            # products at or below this quantity show in red
# colours (blue & white theme)
BLUE = "#1565c0"
DARK = "#0d47a1"
MID = "#90caf9"
LIGHT = "#e3f2fd"
WHITE = "#ffffff"
TEXT = "#000000"
GRID = "#b7c9e2"       # table grid lines
GREEN = "#1b5e20"      # payments / paid off
GREEN_BG = "#c8e6c9"

# The database lives next to the app: next to ShopManager.exe when packaged
# (e.g. on the pendrive), or next to this .py file when run with Python.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "shop.db")


def money(x):
    return f"{CURRENCY} {x or 0:,.2f}"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------- database --
class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            cost_price REAL NOT NULL DEFAULT 0,
            sell_price REAL NOT NULL DEFAULT 0,
            quantity INTEGER NOT NULL DEFAULT 0,
            purchase_date TEXT,
            supplier TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS customers(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            phone TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sales(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sold_at TEXT NOT NULL,
            total REAL NOT NULL,
            profit REAL NOT NULL,
            payment TEXT NOT NULL,               -- 'cash' or 'credit'
            customer_id INTEGER REFERENCES customers(id)
        );
        CREATE TABLE IF NOT EXISTS sale_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id INTEGER NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
            product_id INTEGER REFERENCES products(id) ON DELETE SET NULL,
            product_name TEXT NOT NULL,
            qty INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            unit_cost REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_cash(
            day TEXT PRIMARY KEY,                -- YYYY-MM-DD
            opening REAL DEFAULT 0,              -- money in the drawer at the start
            taken_out REAL DEFAULT 0,            -- money taken out (expenses, supplier...)
            counted REAL,                        -- money counted at the end
            note TEXT DEFAULT '',
            saved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS credit_payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            paid_at TEXT NOT NULL,
            note TEXT DEFAULT ''
        );
        """)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(products)")]
        if "description" not in cols:   # added in a later version
            self.conn.execute("ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''")
        if "quick" not in cols:         # show as a quick button on Scan & Sell
            self.conn.execute("ALTER TABLE products ADD COLUMN quick INTEGER DEFAULT 0")
        self.conn.commit()

    # ---- products
    def quick_products(self):
        return self.conn.execute("SELECT * FROM products WHERE quick=1 ORDER BY name COLLATE NOCASE").fetchall()

    def product_by_barcode(self, code):
        return self.conn.execute("SELECT * FROM products WHERE barcode=?", (code,)).fetchone()

    def product(self, pid):
        return self.conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()

    def products(self, search=""):
        s = f"%{search}%"
        return self.conn.execute(
            "SELECT * FROM products WHERE name LIKE ? OR barcode LIKE ? OR supplier LIKE ? "
            "OR description LIKE ? ORDER BY name COLLATE NOCASE", (s, s, s, s)).fetchall()

    def suggest(self, text, limit=8):
        """Products whose barcode starts with, or name/description contains, the text."""
        return self.conn.execute(
            "SELECT * FROM products WHERE barcode LIKE ? OR name LIKE ? OR description LIKE ? "
            "ORDER BY (barcode = ?) DESC, (name LIKE ?) DESC, name COLLATE NOCASE LIMIT ?",
            (f"{text}%", f"%{text}%", f"%{text}%", text, f"{text}%", limit)).fetchall()

    def save_product(self, d, pid=None):
        vals = (d["barcode"], d["name"], d["cost_price"], d["sell_price"],
                d["quantity"], d["purchase_date"], d["supplier"], d.get("description") or "",
                int(d.get("quick") or 0))
        with self.conn:
            if pid is None:
                self.conn.execute(
                    "INSERT INTO products(barcode,name,cost_price,sell_price,quantity,purchase_date,supplier,"
                    "description,quick) VALUES (?,?,?,?,?,?,?,?,?)", vals)
            else:
                self.conn.execute(
                    "UPDATE products SET barcode=?,name=?,cost_price=?,sell_price=?,quantity=?,"
                    "purchase_date=?,supplier=?,description=?,quick=? WHERE id=?", vals + (pid,))

    def delete_product(self, pid):
        with self.conn:
            self.conn.execute("DELETE FROM products WHERE id=?", (pid,))

    def restock(self, pid, qty, cost=None, bought=None):
        with self.conn:
            self.conn.execute("UPDATE products SET quantity = quantity + ? WHERE id=?", (qty, pid))
            if cost is not None:
                self.conn.execute("UPDATE products SET cost_price=? WHERE id=?", (cost, pid))
            if bought:
                self.conn.execute("UPDATE products SET purchase_date=? WHERE id=?", (bought, pid))

    # ---- sales
    def record_sale(self, cart, payment, customer_id=None):
        """cart: list of dicts {product_id, qty, price}. Returns (sale_id, total)."""
        with self.conn:
            total = profit = 0.0
            rows = []
            for it in cart:
                p = self.product(it["product_id"])
                if p is None:
                    raise ValueError("A product in the cart no longer exists.")
                if p["quantity"] < it["qty"] and not it.get("short"):
                    raise ValueError(f"Not enough stock for {p['name']} (only {p['quantity']} left).")
                total += it["qty"] * it["price"]
                profit += it["qty"] * (it["price"] - p["cost_price"])
                rows.append((p, it["qty"], it["price"]))
            cur = self.conn.execute(
                "INSERT INTO sales(sold_at,total,profit,payment,customer_id) VALUES (?,?,?,?,?)",
                (now_str(), total, profit, payment, customer_id))
            sid = cur.lastrowid
            for p, q, price in rows:
                self.conn.execute(
                    "INSERT INTO sale_items(sale_id,product_id,product_name,qty,unit_price,unit_cost) "
                    "VALUES (?,?,?,?,?,?)", (sid, p["id"], p["name"], q, price, p["cost_price"]))
                self.conn.execute("UPDATE products SET quantity = quantity - ? WHERE id=?", (q, p["id"]))
        return sid, total

    # ---- one day's figures
    def day_summary(self, day):
        s = self.conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS total, COALESCE(SUM(profit),0) AS profit, "
            "COALESCE(SUM(CASE WHEN payment='cash' THEN total END),0) AS cash, "
            "COALESCE(SUM(CASE WHEN payment='credit' THEN total END),0) AS credit "
            "FROM sales WHERE date(sold_at)=?", (day,)).fetchone()
        paid = self.conn.execute("SELECT COALESCE(SUM(amount),0) FROM credit_payments WHERE date(paid_at)=?",
                                 (day,)).fetchone()[0]
        return dict(s) | {"credit_paid": paid}

    def get_day(self, day):
        return self.conn.execute("SELECT * FROM daily_cash WHERE day=?", (day,)).fetchone()

    def previous_count(self, day):
        r = self.conn.execute("SELECT counted FROM daily_cash WHERE day<? AND counted IS NOT NULL "
                              "ORDER BY day DESC LIMIT 1", (day,)).fetchone()
        return r[0] if r else None

    def save_day(self, day, opening, taken_out, counted, note):
        with self.conn:
            self.conn.execute(
                "INSERT INTO daily_cash(day,opening,taken_out,counted,note,saved_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET opening=excluded.opening, taken_out=excluded.taken_out, "
                "counted=excluded.counted, note=excluded.note, saved_at=excluded.saved_at",
                (day, opening, taken_out, counted, note, now_str()))

    def void_sale(self, sid):
        """Delete a sale and put its items back in stock."""
        with self.conn:
            for it in self.sale_items(sid):
                if it["product_id"] is not None:
                    self.conn.execute("UPDATE products SET quantity = quantity + ? WHERE id=?",
                                      (it["qty"], it["product_id"]))
            self.conn.execute("DELETE FROM sales WHERE id=?", (sid,))

    def sales(self, start, end):
        return self.conn.execute(
            "SELECT s.*, c.name AS customer, "
            "(SELECT SUM(qty) FROM sale_items i WHERE i.sale_id=s.id) AS items "
            "FROM sales s LEFT JOIN customers c ON c.id=s.customer_id "
            "WHERE date(s.sold_at) BETWEEN ? AND ? ORDER BY s.sold_at DESC", (start, end)).fetchall()

    def sale_items(self, sid):
        return self.conn.execute("SELECT * FROM sale_items WHERE sale_id=?", (sid,)).fetchall()

    def top_products(self, start, end, order="qty", limit=30):
        order_by = "qty DESC, revenue DESC" if order == "qty" else "revenue DESC, qty DESC"
        return self.conn.execute(
            "SELECT MAX(i.product_name) AS name, SUM(i.qty) AS qty, SUM(i.qty*i.unit_price) AS revenue, "
            "SUM(i.qty*(i.unit_price-i.unit_cost)) AS profit, MAX(p.quantity) AS stock "
            "FROM sale_items i JOIN sales s ON s.id=i.sale_id LEFT JOIN products p ON p.id=i.product_id "
            "WHERE date(s.sold_at) BETWEEN ? AND ? "
            f"GROUP BY COALESCE(i.product_id, i.product_name) ORDER BY {order_by} LIMIT ?",
            (start, end, limit)).fetchall()

    def profit_by_product(self, start, end):
        return self.conn.execute(
            "SELECT i.product_name AS name, SUM(i.qty) AS qty, SUM(i.qty*i.unit_price) AS revenue, "
            "SUM(i.qty*i.unit_cost) AS cost FROM sale_items i JOIN sales s ON s.id=i.sale_id "
            "WHERE date(s.sold_at) BETWEEN ? AND ? GROUP BY i.product_name "
            "ORDER BY (SUM(i.qty*i.unit_price) - SUM(i.qty*i.unit_cost)) DESC", (start, end)).fetchall()

    # ---- customers / credit
    def customers(self):
        return self.conn.execute(
            "SELECT c.*, "
            "COALESCE((SELECT SUM(total) FROM sales s WHERE s.customer_id=c.id AND s.payment='credit'),0) AS owed, "
            "COALESCE((SELECT SUM(amount) FROM credit_payments p WHERE p.customer_id=c.id),0) AS paid "
            "FROM customers c ORDER BY c.name COLLATE NOCASE").fetchall()

    def add_customer(self, name, phone=""):
        with self.conn:
            return self.conn.execute("INSERT INTO customers(name,phone) VALUES (?,?)",
                                     (name, phone)).lastrowid

    def add_payment(self, cid, amount, note=""):
        with self.conn:
            self.conn.execute("INSERT INTO credit_payments(customer_id,amount,paid_at,note) VALUES (?,?,?,?)",
                              (cid, amount, now_str(), note))

    def customer(self, cid):
        return self.conn.execute(
            "SELECT c.*, "
            "COALESCE((SELECT SUM(total) FROM sales s WHERE s.customer_id=c.id AND s.payment='credit'),0) AS owed, "
            "COALESCE((SELECT SUM(amount) FROM credit_payments p WHERE p.customer_id=c.id),0) AS paid "
            "FROM customers c WHERE c.id=?", (cid,)).fetchone()

    def customer_history(self, cid):
        return self.conn.execute(
            "SELECT sold_at AS at, 'sale' AS kind, id AS ref, total AS amount, '' AS note FROM sales "
            "WHERE customer_id=? AND payment='credit' "
            "UNION ALL SELECT paid_at, 'payment', id, amount, note FROM credit_payments "
            "WHERE customer_id=? ORDER BY 1 DESC", (cid, cid)).fetchall()


# ----------------------------------------------------------- webcam scanner --
def scan_with_camera(parent):
    """Open the webcam and return the first barcode it sees (or None)."""
    try:
        import cv2
        from pyzbar import pyzbar
    except ImportError:
        messagebox.showinfo(
            "Camera scanning",
            "Camera scanning needs two extra packages.\n\nOpen a terminal and run:\n\n"
            "    pip install opencv-python pyzbar\n\n"
            "(On a Mac you may also need:  brew install zbar)\n\n"
            "Until then, just type the barcode and press Enter.", parent=parent)
        return None
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        messagebox.showerror("Camera", "Couldn't open the webcam.", parent=parent)
        return None
    code = None
    title = "Hold the barcode up to the camera  (press Q to cancel)"
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        found = pyzbar.decode(frame)
        if found:
            code = found[0].data.decode("utf-8", "ignore")
            break
        cv2.imshow(title, frame)
        if (cv2.waitKey(1) & 0xFF) in (ord("q"), ord("Q"), 27):
            break
    cap.release()
    cv2.destroyAllWindows()
    if code:
        parent.bell()
    return code


# ------------------------------------------------------------ small widgets --
class ScanBox(ttk.Frame):
    """Barcode box: scan or type + Enter. Matching products appear in a list below while typing."""

    def __init__(self, master, app, callback, label="Scan or type barcode / name:"):
        super().__init__(master)
        self.app, self.callback = app, callback
        ttk.Label(self, text=label, font=("Segoe UI", 13, "bold")).pack(side="left")
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=24, font=("Segoe UI", 18))
        self.entry.pack(side="left", padx=6)
        ttk.Button(self, text="📷 Camera", command=self.camera).pack(side="left", padx=2)

        # floating list of matching products, shown under the box
        self.drop = ttk.Frame(self.winfo_toplevel(), relief="solid", borderwidth=1)
        self.list = ttk.Treeview(self.drop, columns=("Product", "Price", "Stock", "Barcode"),
                                 show="headings", height=6, selectmode="browse")
        for c, w, anchor in [("Product", 290, "w"), ("Price", 110, "e"), ("Stock", 70, "e"), ("Barcode", 150, "w")]:
            self.list.heading(c, text=c)
            self.list.column(c, width=w, anchor=anchor, stretch=False)
        self.list.pack(fill="both", expand=True)
        self.matches = {}

        self.var.trace_add("write", lambda *a: self.update_list())
        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Down>", self.to_list)
        self.entry.bind("<Escape>", self.on_escape)
        self.entry.bind("<FocusOut>", lambda e: self.after(200, self.maybe_hide))
        self.list.bind("<ButtonRelease-1>", self.on_click)
        self.list.bind("<Return>", self.choose_selected)
        self.list.bind("<Escape>", self.on_escape)
        self.list.bind("<FocusOut>", lambda e: self.after(200, self.maybe_hide))

    # ---- suggestion list
    def update_list(self):
        text = self.var.get().strip()
        if not text:
            return self.hide()
        rows = self.app.db.suggest(text)
        self.list.delete(*self.list.get_children())
        self.matches = {}
        for p in rows:
            name = p["name"] + (f"  ({p['description']})" if p["description"] else "")
            self.list.insert("", "end", iid=str(p["id"]),
                             values=(name, money(p["sell_price"]), p["quantity"], p["barcode"]))
            self.matches[str(p["id"])] = p
        if rows:
            self.list.configure(height=min(len(rows), 6))
            self.drop.place(in_=self, x=0, rely=1.0, y=2)
            self.drop.lift()
        else:
            self.hide()

    def hide(self):
        self.drop.place_forget()

    def maybe_hide(self):
        try:
            f = self.focus_get()
        except (KeyError, tk.TclError):
            f = None
        if f not in (self.entry, self.list):
            self.hide()

    def on_enter(self, event=None):
        text = self.var.get().strip()
        if text:
            p = self.app.db.product_by_barcode(text)
            if p:
                self.fire(p["barcode"])
            elif self.matches:
                first = self.list.get_children()[0]
                self.fire(self.matches[first]["barcode"])
            else:
                self.fire(text)
        return "break"

    def to_list(self, event=None):
        kids = self.list.get_children()
        if kids and self.drop.winfo_ismapped():
            self.list.focus_set()
            self.list.selection_set(kids[0])
            self.list.focus(kids[0])
        return "break"

    def on_click(self, event):
        row = self.list.identify_row(event.y)
        if row in self.matches:
            self.fire(self.matches[row]["barcode"])

    def choose_selected(self, event=None):
        sel = self.list.selection()
        if sel:
            self.fire(self.matches[sel[0]]["barcode"])
        return "break"

    def on_escape(self, event=None):
        self.hide()
        self.var.set("")
        self.focus()
        return "break"

    # ----
    def fire(self, code):
        code = (code or "").strip()
        self.hide()
        self.var.set("")
        if code:
            self.callback(code)
        self.focus()

    def camera(self):
        self.fire(scan_with_camera(self))

    def focus(self):
        self.entry.focus_set()


class PeriodBar(ttk.Frame):
    """Date range selector with quick presets."""

    def __init__(self, master, on_change):
        super().__init__(master)
        self.on_change = on_change
        self.start, self.end = tk.StringVar(), tk.StringVar()
        for text, key in [("Today", "today"), ("Last 7 days", "week"),
                          ("This month", "month"), ("All time", "all")]:
            ttk.Button(self, text=text, command=lambda k=key: self.preset(k)).pack(side="left", padx=2)
        ttk.Label(self, text="   From").pack(side="left")
        ttk.Entry(self, textvariable=self.start, width=11).pack(side="left", padx=3)
        ttk.Label(self, text="To").pack(side="left")
        ttk.Entry(self, textvariable=self.end, width=11).pack(side="left", padx=3)
        ttk.Button(self, text="Apply", command=self.on_change).pack(side="left", padx=4)
        ttk.Label(self, text="(YYYY-MM-DD)", foreground="gray").pack(side="left")
        self.preset("month", notify=False)

    def preset(self, key, notify=True):
        t = date.today()
        s = {"today": t, "week": t - timedelta(days=6),
             "month": t.replace(day=1), "all": date(2000, 1, 1)}[key]
        self.start.set(s.isoformat())
        self.end.set(t.isoformat())
        if notify:
            self.on_change()

    def get(self):
        try:
            s = date.fromisoformat(self.start.get().strip())
            e = date.fromisoformat(self.end.get().strip())
        except ValueError:
            messagebox.showerror("Dates", "Please write dates as YYYY-MM-DD, e.g. 2026-11-01.")
            return None
        return s.isoformat(), e.isoformat()


_HEADER_H = [None]  # height of a table's heading row, measured once


def add_gridlines(tree):
    """Draw Excel-style grid lines over a Treeview (it has none of its own)."""
    lines, state = [], {"job": None, "busy": False}

    def redraw():
        state["job"] = None
        if state["busy"] or not tree.winfo_ismapped():
            return
        state["busy"] = True
        try:
            if _HEADER_H[0] is None:
                iid = tree.insert("", 0, values=())
                tree.update_idletasks()
                bb = tree.bbox(iid)
                tree.delete(iid)
                if not bb:
                    return
                _HEADER_H[0] = bb[1]
            for line in lines:
                line.destroy()
            lines.clear()
            w, h, top = tree.winfo_width(), tree.winfo_height(), _HEADER_H[0]
            rh = int(float(ttk.Style(tree).lookup("Treeview", "rowheight") or 34))
            y = top + rh
            while y < h - 1:                                   # row lines
                f = tk.Frame(tree, bg=GRID, bd=0, highlightthickness=0)
                f.place(x=0, y=y - 1, width=w, height=1)
                lines.append(f)
                y += rh
            x = 0
            for c in tree["columns"][:-1]:                    # column lines
                x += int(tree.column(c, "width"))
                f = tk.Frame(tree, bg=GRID, bd=0, highlightthickness=0)
                f.place(x=x - 1, y=top, width=1, height=max(h - top, 0))
                lines.append(f)
        finally:
            state["busy"] = False

    def schedule(event=None):
        if state["job"] is None:
            state["job"] = tree.after(30, redraw)

    tree.bind("<Configure>", schedule, add="+")
    tree.bind("<Map>", schedule, add="+")
    tree.bind("<ButtonRelease-1>", schedule, add="+")   # after dragging a column wider


def make_tree(master, columns, widths, height=15):
    frame = ttk.Frame(master)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    for c, w in zip(columns, widths):
        tree.heading(c, text=c)
        anchor = "e" if any(k in c for k in ("Price", "Total", "Qty", "Revenue", "Cost",
                                              "Profit", "Paid", "Balance", "Owed", "Amount",
                                              "Items", "Subtotal", "Stock")) else "w"
        tree.column(c, width=w, anchor=anchor)
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side="left", fill="both", expand=True)
    add_gridlines(tree)
    sb.pack(side="right", fill="y")
    return frame, tree


class Dialog(tk.Toplevel):
    """Base modal dialog."""

    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.configure(bg=WHITE)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def center(self):
        """Place the popup in the middle of the screen."""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max((self.winfo_screenwidth() - w) // 2, 0)
        y = max((self.winfo_screenheight() - h) // 2 - 30, 0)
        self.geometry(f"+{x}+{y}")

    def run(self):
        self.center()
        self.lift()
        self.grab_set()
        self.wait_window()
        return self.result


class ProductDialog(Dialog):
    FIELDS = [("barcode", "Barcode (leave blank if it has none)"), ("name", "Product name"),
              ("description", "Description (e.g. 1L bottle, 500g)"), ("cost_price", "Cost price (what you paid)"),
              ("sell_price", "Selling price"), ("quantity", "Quantity in stock"),
              ("purchase_date", "Date bought (YYYY-MM-DD)"), ("supplier", "Supplier (optional)")]

    def __init__(self, master, product=None, barcode=""):
        super().__init__(master, "Edit product" if product else "New product")
        self.vars = {}
        defaults = dict(product) if product else {
            "barcode": barcode, "name": "", "description": "", "cost_price": "", "sell_price": "",
            "quantity": "", "purchase_date": date.today().isoformat(), "supplier": ""}
        for r, (key, label) in enumerate(self.FIELDS):
            ttk.Label(self.body, text=label).grid(row=r, column=0, sticky="w", pady=3)
            v = tk.StringVar(value="" if defaults.get(key) is None else str(defaults[key]))
            e = ttk.Entry(self.body, textvariable=v, width=32)
            e.grid(row=r, column=1, pady=3, padx=(8, 0))
            self.vars[key] = v
            if r == (1 if barcode or product else 0):
                e.focus_set()
        self.quick = tk.BooleanVar(value=bool(defaults.get("quick")))
        tk.Checkbutton(self.body, text="Show as a quick button on Scan & Sell\n(for items without a barcode)",
                       variable=self.quick, font=("Segoe UI", 12, "bold"), bg=WHITE, activebackground=WHITE,
                       justify="left").grid(row=len(self.FIELDS), column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(self.body, text="Save", command=self.save).grid(row=len(self.FIELDS) + 1, column=1,
                                                                   sticky="e", pady=(10, 0))
        self.bind("<Return>", lambda e: self.save())
        self.result = self.run()

    def save(self):
        d = {k: v.get().strip() for k, v in self.vars.items()}
        d["quick"] = int(self.quick.get())
        if not d["name"]:
            return messagebox.showerror("Missing", "The product name is required.", parent=self)
        if not d["barcode"]:   # loose item: make an internal code for it
            d["barcode"] = "Q" + datetime.now().strftime("%y%m%d%H%M%S")
        try:
            d["cost_price"] = float(d["cost_price"] or 0)
            d["sell_price"] = float(d["sell_price"] or 0)
            d["quantity"] = int(d["quantity"] or 0)
            if d["purchase_date"]:
                date.fromisoformat(d["purchase_date"])
        except ValueError:
            return messagebox.showerror("Check values",
                                        "Prices must be numbers, quantity a whole number,\n"
                                        "and the date like 2026-11-01.", parent=self)
        if d["sell_price"] < d["cost_price"]:
            if not messagebox.askyesno("Selling at a loss?",
                                       "Selling price is lower than cost price. Save anyway?", parent=self):
                return
        self.result = d
        self.destroy()


class ProductPicker(Dialog):
    """Search products by name (handy when there's no barcode / no scanner)."""

    def __init__(self, master, db):
        super().__init__(master, "Find product")
        self.db = db
        self.q = tk.StringVar()
        e = ttk.Entry(self.body, textvariable=self.q, width=40)
        e.pack(fill="x")
        e.focus_set()
        self.q.trace_add("write", lambda *a: self.fill())
        f, self.tree = make_tree(self.body, ("Name", "Price", "Stock", "Barcode"), (220, 90, 60, 130), 12)
        f.pack(fill="both", expand=True, pady=8)
        self.tree.bind("<Double-1>", lambda e: self.choose())
        self.bind("<Return>", lambda e: self.choose())
        ttk.Button(self.body, text="Select", command=self.choose).pack(anchor="e")
        self.fill()
        self.result = self.run()

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        for p in self.db.products(self.q.get()):
            self.tree.insert("", "end", iid=str(p["id"]),
                             values=(p["name"], money(p["sell_price"]), p["quantity"], p["barcode"]))
        kids = self.tree.get_children()
        if kids:
            self.tree.selection_set(kids[0])

    def choose(self):
        sel = self.tree.selection()
        if sel:
            self.result = self.db.product(int(sel[0]))
            self.destroy()


class SimpleForm(Dialog):
    """Generic small form: fields = [(key, label, default)]."""

    def __init__(self, master, title, fields):
        super().__init__(master, title)
        self.vars = {}
        for r, (key, label, default) in enumerate(fields):
            ttk.Label(self.body, text=label).grid(row=r, column=0, sticky="w", pady=3)
            v = tk.StringVar(value=default)
            e = ttk.Entry(self.body, textvariable=v, width=28)
            e.grid(row=r, column=1, padx=(8, 0), pady=3)
            if r == 0:
                e.focus_set()
            self.vars[key] = v
        ttk.Button(self.body, text="OK", command=self.ok).grid(row=len(fields), column=1, sticky="e", pady=(10, 0))
        self.bind("<Return>", lambda e: self.ok())
        self.result = self.run()

    def ok(self):
        self.result = {k: v.get().strip() for k, v in self.vars.items()}
        self.destroy()


def big(master, text="", size=14, bold=False, **kw):
    return ttk.Label(master, text=text, font=("Segoe UI", size, "bold" if bold else "normal"), **kw)


# --------------------------------------------------------------------- pages --
def big_button(master, text, command, bg=LIGHT, fg=TEXT, size=16, **kw):
    """Large, easy-to-see button."""
    return tk.Button(master, text=text, command=command, bg=bg, fg=fg, activebackground=bg,
                     activeforeground=fg, font=("Segoe UI", size, "bold"), padx=18, pady=8,
                     relief="flat", bd=0, highlightthickness=1, highlightbackground=BLUE, cursor="hand2", **kw)


def ask_sell_anyway(parent, name, available):
    return messagebox.askyesno(
        "Not enough stock in the system",
        f"The system shows only {max(available, 0)} {name} left.\n\n"
        "If you have more on the shelf that you haven't added yet,\n"
        "you can sell it anyway (remember to use Add stock later).\n\n"
        "Sell anyway?", parent=parent)


class QuantityDialog(Dialog):
    """Popup after scanning a product for sale: shows its details and asks how many."""

    def __init__(self, master, p, in_cart=0, short_ok=False):
        super().__init__(master, "How many?")
        self.p = p
        self.short = short_ok
        self.max = p["quantity"] - in_cart
        b = self.body
        big(b, p["name"], 24, True).pack(anchor="w")
        if p["description"]:
            big(b, p["description"], 15).pack(anchor="w")
        big(b, f"{money(p['sell_price'])} each", 26, True, foreground=BLUE).pack(anchor="w", pady=(8, 0))
        stock = f"In stock: {p['quantity']}" + (f"   ({in_cart} already in this sale)" if in_cart else "")
        big(b, stock, 13, foreground="gray").pack(anchor="w", pady=(2, 12))

        big(b, "Quantity:", 16, True).pack(anchor="w")
        row = ttk.Frame(b)
        row.pack(anchor="w")
        big_button(row, "−", lambda: self.step(-1), size=22, width=3).pack(side="left")
        self.qty = tk.StringVar(value="1")
        self.entry = tk.Entry(row, textvariable=self.qty, font=("Segoe UI", 28, "bold"), width=5,
                              justify="center", relief="solid", bd=1)
        self.entry.pack(side="left", padx=10, ipady=4)
        big_button(row, "+", lambda: self.step(1), size=22, width=3).pack(side="left")

        self.line = big(b, "", 18, True)
        self.line.pack(anchor="w", pady=12)
        self.qty.trace_add("write", lambda *a: self.update_line())
        btns = ttk.Frame(b)
        btns.pack(fill="x")
        big_button(btns, "✔  Add to sale", self.ok, bg=DARK, fg=WHITE, size=18).pack(side="left", fill="x", expand=True)
        big_button(btns, "Cancel", self.destroy, size=18).pack(side="left", padx=(10, 0))
        self.bind("<Return>", lambda e: self.ok())
        self.update_line()
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.result = self.run()

    def step(self, d):
        try:
            q = int(self.qty.get())
        except ValueError:
            q = 0
        self.qty.set(str(max(1, q + d)))

    def update_line(self):
        try:
            q = int(self.qty.get())
        except ValueError:
            return self.line.config(text="")
        self.line.config(text=f"Total for this item:  {money(q * self.p['sell_price'])}")

    def ok(self):
        try:
            q = int(self.qty.get())
            if q < 1:
                raise ValueError
        except ValueError:
            return messagebox.showwarning("Quantity", "Type a whole number, like 1 or 3.", parent=self)
        if q > self.max and not self.short:
            self.bell()
            if not ask_sell_anyway(self, self.p["name"], self.max):
                return
            self.short = True
        self.result = (q, self.short)
        self.destroy()


class CheckoutDialog(Dialog):
    """Popup: shows the total, asks how much the customer gave, shows the change."""

    def __init__(self, master, app, total):
        super().__init__(master, "Sell")
        self.app, self.total = app, total
        b = self.body
        big(b, "Total to pay", 16).pack(anchor="w")
        big(b, money(total), 34, True).pack(anchor="w", pady=(0, 14))

        big(b, "Customer gave:", 16).pack(anchor="w")
        row = ttk.Frame(b)
        row.pack(anchor="w", fill="x")
        self.given = tk.StringVar()
        self.entry = tk.Entry(row, textvariable=self.given, font=("Segoe UI", 26), width=10)
        self.entry.pack(side="left")
        big_button(row, "Exact amount", lambda: self.given.set(f"{total:.2f}"), size=13).pack(side="left", padx=10)

        self.change = big(b, "", 30, True)
        self.change.pack(anchor="w", pady=14)
        self.given.trace_add("write", lambda *a: self.update_change())

        cf = ttk.Frame(b)
        cf.pack(anchor="w", fill="x", pady=(0, 14))
        self.credit = tk.BooleanVar()
        self.credit_btn = big_button(cf, "☐  Pay later (credit)", self.flip_credit, size=17)
        self.credit_btn.pack(side="left")
        self.cust_map = {}
        self.customer = ttk.Combobox(cf, state="disabled", width=16, font=("Segoe UI", 16))
        self.customer.pack(side="left", padx=10, ipady=6)
        self.new_btn = big_button(cf, "➕ New customer", self.new_customer, size=14, state="disabled")
        self.new_btn.pack(side="left")
        self.load_customers()

        btns = ttk.Frame(b)
        btns.pack(fill="x")
        big_button(btns, "✔  Confirm sale", self.confirm, bg=DARK, fg=WHITE, size=18).pack(
            side="left", fill="x", expand=True)
        big_button(btns, "Cancel", self.destroy, size=18).pack(side="left", padx=(10, 0))

        self.bind("<Return>", lambda e: self.confirm())
        self.entry.focus_set()
        self.result = self.run()

    def load_customers(self):
        self.cust_map = {c["name"]: c["id"] for c in self.app.db.customers()}
        self.customer["values"] = list(self.cust_map)

    def update_change(self):
        try:
            g = float(self.given.get())
        except ValueError:
            return self.change.config(text="")
        diff = g - self.total
        if diff >= -0.005:
            self.change.config(text=f"Give back:  {money(max(diff, 0))}", foreground=BLUE)
        else:
            self.change.config(text=f"Still needs:  {money(-diff)}", foreground=TEXT)

    def flip_credit(self):
        self.credit.set(not self.credit.get())
        self.toggle()

    def toggle(self):
        credit = self.credit.get()
        self.credit_btn.config(text="☑  Pay later (credit)" if credit else "☐  Pay later (credit)",
                               bg=DARK if credit else LIGHT, fg=WHITE if credit else TEXT,
                               activebackground=DARK if credit else LIGHT,
                               activeforeground=WHITE if credit else TEXT)
        self.customer.config(state="readonly" if credit else "disabled")
        self.new_btn.config(state="normal" if credit else "disabled")
        self.entry.config(state="disabled" if credit else "normal")
        if credit:
            self.change.config(text="Goes on the customer's credit", foreground="")
        else:
            self.update_change()

    def new_customer(self):
        name = self.app.new_customer(self)
        if name:
            self.load_customers()
            self.customer.set(name)

    def confirm(self):
        if self.credit.get():
            name = self.customer.get()
            cid = self.cust_map.get(name)
            if cid is None:
                return messagebox.showwarning("Customer", "Choose (or add) the customer.", parent=self)
            self.result = {"payment": "credit", "customer_id": cid, "name": name, "given": None}
        else:
            try:
                g = float(self.given.get())
            except ValueError:
                return messagebox.showwarning("Amount", "Type how much the customer gave,\n"
                                                        "or press 'Exact amount'.", parent=self)
            if g < self.total - 0.005:
                return messagebox.showwarning("Not enough", f"The customer still needs to give "
                                                            f"{money(self.total - g)}.", parent=self)
            self.result = {"payment": "cash", "customer_id": None, "name": None, "given": g}
        self.destroy()


class StockInDialog(Dialog):
    """Receive stock: scan a barcode, the product appears, type how many arrived, save."""

    def __init__(self, master, app, product=None):
        super().__init__(master, "Add stock")
        self.app = app
        self.pid = None
        b = self.body
        self.scan = ScanBox(b, app, self.load_code)
        self.scan.pack(anchor="w")
        self.name = big(b, "Scan the product you received", 20, True)
        self.name.pack(anchor="w", pady=(14, 0))
        self.info = big(b, "", 13, foreground="gray")
        self.info.pack(anchor="w", pady=(2, 10))

        form = ttk.Frame(b)
        form.pack(anchor="w")
        self.qty, self.cost = tk.StringVar(), tk.StringVar()
        self.date = tk.StringVar(value=date.today().isoformat())
        self.entries = []
        for r, (label, var) in enumerate([("Quantity received", self.qty),
                                          ("New cost price (blank = keep same)", self.cost),
                                          ("Date bought", self.date)]):
            ttk.Label(form, text=label, font=("Segoe UI", 13)).grid(row=r, column=0, sticky="w", pady=4)
            e = tk.Entry(form, textvariable=var, font=("Segoe UI", 16), width=14, state="disabled")
            e.grid(row=r, column=1, padx=10, pady=4)
            e.bind("<Return>", lambda ev: self.save())
            self.entries.append(e)
        ttk.Label(b, text="Tip: scan the same item again to add 1 more, or type the quantity.",
                  foreground="gray").pack(anchor="w", pady=(6, 0))

        self.status = big(b, "", 13, foreground=BLUE)
        self.status.pack(anchor="w", pady=8)
        btns = ttk.Frame(b)
        btns.pack(fill="x")
        self.save_btn = big_button(btns, "✔  Save stock", self.save, bg=DARK, fg=WHITE,
                                   size=16, state="disabled")
        self.save_btn.pack(side="left", fill="x", expand=True)
        big_button(btns, "Done", self.destroy, size=16).pack(side="left", padx=(10, 0))

        if product:
            self.load(product)
            self.after_idle(lambda: (self.entries[0].focus_set(), self.entries[0].select_range(0, "end")))
        else:
            self.scan.focus()
        self.run()

    def set_enabled(self, on):
        for e in self.entries:
            e.config(state="normal" if on else "disabled")
        self.save_btn.config(state="normal" if on else "disabled")

    def load(self, p, qty=""):
        self.pid = p["id"]
        self.name.config(text=p["name"])
        self.info.config(text=f"Barcode {p['barcode']}  ·  In stock now: {p['quantity']}  ·  "
                              f"Cost {money(p['cost_price'])}  ·  Sells at {money(p['sell_price'])}")
        self.set_enabled(True)
        self.qty.set(qty)
        self.cost.set("")

    def load_code(self, code):
        p = self.app.db.product_by_barcode(code)
        if p:
            self.app.bell()
            if self.pid == p["id"]:          # same item scanned again -> one more
                try:
                    self.qty.set(str(int(self.qty.get() or 0) + 1))
                except ValueError:
                    self.qty.set("1")
                return
            if self.pid is not None and self.qty.get().strip():
                if not messagebox.askyesno("Not saved", f"You haven't saved the stock for "
                                                        f"{self.name.cget('text')}.\nSwitch anyway?",
                                           parent=self):
                    return
            self.load(p, qty="1")
            self.status.config(text="")
            return
        self.app.bell()
        if not messagebox.askyesno("Not found", f"No product with barcode {code}.\n"
                                                "Add it as a new product?", parent=self):
            return
        d = ProductDialog(self, barcode=code).result
        if not d:
            return
        try:
            self.app.db.save_product(d)
        except sqlite3.IntegrityError:
            return messagebox.showerror("Duplicate", "That barcode already exists.", parent=self)
        self.status.config(text=f"New product added: {d['name']} ({d['quantity']} in stock)",
                           foreground=BLUE)

    def save(self):
        if self.pid is None:
            return
        try:
            qty = int(self.qty.get())
            if qty <= 0:
                raise ValueError
            cost = float(self.cost.get()) if self.cost.get().strip() else None
            bought = self.date.get().strip() or None
            if bought:
                date.fromisoformat(bought)
        except ValueError:
            return messagebox.showerror("Check values", "Quantity must be a whole number above 0,\n"
                                                        "cost a number, and the date like 2026-11-01.",
                                        parent=self)
        self.app.db.restock(self.pid, qty, cost, bought)
        p = self.app.db.product(self.pid)
        self.app.bell()
        self.status.config(text=f"Saved: +{qty} {p['name']}  →  now {p['quantity']} in stock",
                           foreground=BLUE)
        self.pid = None
        self.name.config(text="Scan the next product")
        self.info.config(text="")
        self.qty.set("")
        self.cost.set("")
        self.set_enabled(False)
        self.scan.focus()


class SaleDoneDialog(Dialog):
    """Second popup after a sale: how much money to give back (or credit added)."""

    def __init__(self, master, total, payment, given=None, name=None):
        super().__init__(master, "Sale done")
        b = self.body
        big(b, "✔  Sale done!", 20, True, foreground=GREEN).pack()
        if payment == "cash":
            change = max(given - total, 0)
            big(b, "Give back to the customer:", 18).pack(pady=(16, 0))
            big(b, money(change), 46, True, foreground=BLUE).pack(pady=(0, 6))
            sub = (f"Total {money(total)}   ·   Customer gave {money(given)}" if change > 0.005
                   else "Exact amount, nothing to give back")
        else:
            big(b, f"Added to {name}'s credit:", 18).pack(pady=(16, 0))
            big(b, money(total), 46, True, foreground=BLUE).pack(pady=(0, 6))
            sub = "They can pay it back later from the Credit page"
        big(b, sub, 13, foreground="gray").pack()
        ok = big_button(b, "OK", self.destroy, bg=DARK, fg=WHITE, size=20, width=12)
        ok.pack(pady=(18, 0))
        self.bind("<Return>", lambda e: self.destroy())
        self.after_idle(ok.focus_set)
        self.run()


class ScanSellPage(ttk.Frame):
    """Scan products: see the price, they go into the cart, then press SELL."""

    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        self.cart = {}    # product_id -> {product_id, name, price, qty, stock}
        self.last = None  # id of the last scanned product

        self.scan = ScanBox(self, app, self.add_code)
        self.scan.pack(anchor="w")

        # last scanned product, in big letters
        card = ttk.Frame(self, padding=(16, 10), relief="groove")
        card.pack(fill="x", pady=10)
        card.columnconfigure(0, weight=1)
        self.name = big(card, "Scan a product", 22, True)
        self.name.grid(row=0, column=0, sticky="w")
        self.price = big(card, "", 28, True, foreground=BLUE)
        self.price.grid(row=0, column=1, sticky="e")
        self.stock = big(card, "", 14)
        self.stock.grid(row=1, column=0, sticky="w")
        tools = ttk.Frame(card)
        tools.grid(row=1, column=1, sticky="e")
        self.edit_btn = ttk.Button(tools, text="Edit product", command=self.edit, state="disabled")
        self.edit_btn.pack(side="left", padx=(0, 6))
        self.restock_btn = ttk.Button(tools, text="📦 Add stock", command=self.restock)
        self.restock_btn.pack(side="left")

        # quick buttons (items without barcodes)
        self.quick = ttk.Frame(self)
        self.quick.pack(fill="x", pady=(0, 10))

        # cart
        f, self.tree = make_tree(self, ("Product", "Price", "Qty", "Subtotal"), (380, 130, 70, 140), 8)
        f.pack(fill="both", expand=True)

        row = ttk.Frame(self)
        row.pack(fill="x", pady=10)
        big_button(row, "+1", lambda: self.change(1), size=18).pack(side="left")
        big_button(row, "−1", lambda: self.change(-1), size=18).pack(side="left", padx=8)
        big_button(row, "Remove", self.remove).pack(side="left")
        big_button(row, "Clear all", self.clear).pack(side="left", padx=8)
        self.total_lbl = big(row, money(0), 26, True)
        self.total_lbl.pack(side="right")
        big(row, "TOTAL: ", 18).pack(side="right")

        big_button(self, "SELL", self.checkout, bg=DARK, fg=WHITE, size=26).pack(fill="x")

    # ---- product display
    def show(self, p):
        if p is None:
            self.last = None
            self.name.config(text="Scan a product")
            self.price.config(text="")
            self.stock.config(text="")
            self.edit_btn.config(state="disabled")
            return
        self.last = p["id"]
        self.name.config(text=p["name"] + (f"  ·  {p['description']}" if p["description"] else ""))
        self.price.config(text=money(p["sell_price"]))
        low = p["quantity"] <= LOW_STOCK
        warn = "   ⚠ ADD STOCK (sold more than recorded)" if p["quantity"] < 0 else "   ⚠ LOW STOCK"
        self.stock.config(text=f"In stock: {p['quantity']}" + (warn if low else ""),
                          foreground=TEXT, font=("Segoe UI", 14, "bold" if low else "normal"))
        self.edit_btn.config(state="normal")
        self.restock_btn.config(state="normal")

    def build_quick(self):
        for w in self.quick.winfo_children():
            w.destroy()
        items = self.app.db.quick_products()
        per_row = 6
        for i, p in enumerate(items):
            b = big_button(self.quick, f"{p['name']}\n{money(p['sell_price'])}",
                           lambda code=p["barcode"]: (self.add_code(code), self.scan.focus()), size=13,
)
            b.grid(row=i // per_row, column=i % per_row, sticky="nsew", padx=3, pady=3)
        for c in range(per_row):
            self.quick.columnconfigure(c, weight=1 if items else 0, uniform="q" if items else "")

    def refresh(self):
        self.build_quick()
        for pid in list(self.cart):
            p = self.app.db.product(pid)
            if p is None:
                del self.cart[pid]
            else:
                self.cart[pid].update(name=p["name"], price=p["sell_price"], stock=p["quantity"])
        self.show(self.app.db.product(self.last) if self.last else None)
        self.redraw()
        self.scan.focus()

    def edit(self):
        self.app.edit_product(self, self.last)
        self.refresh()

    def restock(self):
        self.app.restock_product(self, self.last)
        self.refresh()

    # ---- cart
    def add_code(self, code):
        p = self.app.db.product_by_barcode(code)
        if not p:
            self.app.bell()
            if not messagebox.askyesno("Not found", f"No product with barcode {code}.\n"
                                                    "Add it as a new product?"):
                return
            d = ProductDialog(self, barcode=code).result
            if not d:
                return
            try:
                self.app.db.save_product(d)
            except sqlite3.IntegrityError:
                return messagebox.showerror("Duplicate", "That barcode already exists.")
            p = self.app.db.product_by_barcode(d["barcode"])
        self.show(p)
        self.app.bell()
        item = self.cart.get(p["id"])
        in_cart = item["qty"] if item else 0
        short = bool(item and item.get("short"))
        if in_cart >= p["quantity"] and not short:
            if not ask_sell_anyway(self, p["name"], p["quantity"] - in_cart):
                return
            short = True
        res = QuantityDialog(self, p, in_cart, short_ok=short).result
        if not res:
            return
        q, short = res
        if item:
            item["qty"] += q
            item["short"] = item.get("short") or short
        else:
            self.cart[p["id"]] = {"product_id": p["id"], "name": p["name"], "price": p["sell_price"],
                                  "qty": q, "stock": p["quantity"], "short": short}
        self.redraw(select=p["id"])

    def selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def change(self, delta):
        pid = self.selected()
        if pid is None:
            return messagebox.showinfo("Select", "Click a product in the list first.")
        it = self.cart[pid]
        if delta > 0 and it["qty"] + delta > it["stock"] and not it.get("short"):
            if not ask_sell_anyway(self, it["name"], it["stock"] - it["qty"]):
                return
            it["short"] = True
        it["qty"] += delta
        if it["qty"] <= 0:
            del self.cart[pid]
        self.redraw(select=pid)

    def remove(self):
        pid = self.selected()
        if pid is None:
            return messagebox.showinfo("Select", "Click a product in the list first.")
        del self.cart[pid]
        self.redraw()

    def clear(self):
        if self.cart and not messagebox.askyesno("Clear", "Remove everything from the list?"):
            return
        self.cart.clear()
        self.redraw()

    def total(self):
        return sum(i["qty"] * i["price"] for i in self.cart.values())

    def redraw(self, select=None):
        self.tree.delete(*self.tree.get_children())
        for pid, i in self.cart.items():
            self.tree.insert("", "end", iid=str(pid),
                             values=(i["name"], money(i["price"]), i["qty"], money(i["qty"] * i["price"])))
        if select is not None and str(select) in self.tree.get_children():
            self.tree.selection_set(str(select))
        self.total_lbl.config(text=money(self.total()))

    def checkout(self):
        if not self.cart:
            return messagebox.showinfo("Empty", "Scan some products first.")
        d = CheckoutDialog(self, self.app, self.total()).result
        if not d:
            return self.scan.focus()
        try:
            sid, total = self.app.db.record_sale(list(self.cart.values()), d["payment"], d["customer_id"])
        except ValueError as e:
            return messagebox.showerror("Can't complete sale", str(e))
        SaleDoneDialog(self, total, d["payment"], d["given"], d["name"])
        self.cart.clear()
        self.refresh()


class TodayPage(ttk.Frame):
    """One day at a glance, and balancing the cash drawer."""

    def __init__(self, master, app):
        super().__init__(master, padding=14)
        self.app = app
        self.day = date.today()
        self.loading = False

        nav = ttk.Frame(self)
        nav.pack(fill="x")
        big_button(nav, "◀  Previous day", lambda: self.move(-1), size=13).pack(side="left")
        self.day_lbl = big(nav, "", 20, True)
        self.day_lbl.pack(side="left", padx=20)
        big_button(nav, "Next day  ▶", lambda: self.move(1), size=13).pack(side="left")
        big_button(nav, "Today", self.go_today, bg=DARK, fg=WHITE, size=13).pack(side="left", padx=10)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, pady=(12, 0))
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.LabelFrame(body, text="  Cash drawer  ", padding=14)
        right.pack(side="left", fill="y", padx=(14, 0))

        # sales figures
        cards = ttk.Frame(left)
        cards.pack(fill="x")
        self.cards = {}
        spec = [("total", "Total sales"), ("cash", "Cash sales"), ("credit", "Sold on credit"),
                ("credit_paid", "Credit paid back today"), ("profit", "Profit"), ("n", "Number of sales")]
        for i, (key, label) in enumerate(spec):
            c = ttk.Frame(cards, padding=10, relief="groove")
            c.grid(row=i // 3, column=i % 3, sticky="nsew", padx=(0, 8), pady=(0, 8))
            ttk.Label(c, text=label, foreground="gray").pack(anchor="w")
            self.cards[key] = big(c, "-", 18, True)
            self.cards[key].pack(anchor="w")
        for col in range(3):
            cards.columnconfigure(col, weight=1, uniform="c")
        ttk.Label(left, text="What was sold this day").pack(anchor="w", pady=(6, 2))
        f, self.tree = make_tree(left, ("Product", "Qty sold", "Revenue"), (300, 100, 140), 8)
        f.pack(fill="both", expand=True)

        # cash drawer
        self.opening, self.taken, self.counted, self.note = (tk.StringVar() for _ in range(4))
        def entry_row(label, var, hint=""):
            ttk.Label(right, text=label, font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(6, 0))
            e = tk.Entry(right, textvariable=var, font=("Segoe UI", 18), width=14, relief="solid", bd=1)
            e.pack(anchor="w", ipady=3)
            if hint:
                ttk.Label(right, text=hint, foreground="gray").pack(anchor="w")
            return e
        entry_row("Money in drawer at the start", self.opening)
        self.opening_hint = ttk.Label(right, text="", foreground="gray")
        self.opening_hint.pack(anchor="w")
        entry_row("Money taken out (expenses, supplier...)", self.taken)
        ttk.Label(right, text="+ cash sales  + credit paid back", foreground="gray").pack(anchor="w", pady=(8, 0))
        ttk.Label(right, text="Should be in the drawer now:", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        self.expected_lbl = big(right, "-", 24, True, foreground=BLUE)
        self.expected_lbl.pack(anchor="w")
        self.counted_entry = entry_row("Money you counted now", self.counted)
        self.result_lbl = big(right, "", 18, True)
        self.result_lbl.pack(anchor="w", pady=(10, 4))
        entry_row("Note (optional)", self.note)
        big_button(right, "💾  Save today's count", self.save, bg=DARK, fg=WHITE, size=15).pack(fill="x", pady=(12, 0))
        self.saved_lbl = ttk.Label(right, text="", foreground="gray")
        self.saved_lbl.pack(anchor="w", pady=(4, 0))
        for v in (self.opening, self.taken, self.counted):
            v.trace_add("write", lambda *a: self.recalc())

    def move(self, days):
        self.day += timedelta(days=days)
        self.refresh()

    def go_today(self):
        self.day = date.today()
        self.refresh()

    @staticmethod
    def num(s):
        s = (s or "").strip()
        if not s:
            return 0.0
        try:
            return float(s)
        except ValueError:
            return None

    def refresh(self):
        d = self.day.isoformat()
        label = "Today" if self.day == date.today() else self.day.strftime("%A")
        self.day_lbl.config(text=f"{label}  ·  {self.day.strftime('%d %B %Y')}")
        s = self.app.db.day_summary(d)
        self.summary = s
        for key in ("total", "cash", "credit", "credit_paid", "profit"):
            self.cards[key].config(text=money(s[key]))
        self.cards["n"].config(text=str(s["n"]))

        self.tree.delete(*self.tree.get_children())
        for r in self.app.db.profit_by_product(d, d):
            self.tree.insert("", "end", values=(r["name"], r["qty"], money(r["revenue"])))

        self.loading = True
        saved = self.app.db.get_day(d)
        if saved:
            self.opening.set(f"{saved['opening'] or 0:g}")
            self.taken.set(f"{saved['taken_out']:g}" if saved["taken_out"] else "")
            self.counted.set("" if saved["counted"] is None else f"{saved['counted']:g}")
            self.note.set(saved["note"] or "")
            self.opening_hint.config(text="")
            self.saved_lbl.config(text=f"Last saved {saved['saved_at']}")
        else:
            prev = self.app.db.previous_count(d)
            self.opening.set(f"{prev:g}" if prev is not None else "")
            self.opening_hint.config(text="(filled in from the last count - change it if needed)"
                                     if prev is not None else "")
            self.taken.set("")
            self.counted.set("")
            self.note.set("")
            self.saved_lbl.config(text="Not saved yet for this day")
        self.loading = False
        self.recalc()

    def recalc(self):
        if self.loading:
            return
        s = self.summary
        opening, taken, counted = self.num(self.opening.get()), self.num(self.taken.get()), self.num(self.counted.get())
        if opening is None or taken is None:
            self.expected_lbl.config(text="check the numbers")
            return self.result_lbl.config(text="")
        expected = opening - taken + s["cash"] + s["credit_paid"]
        self.expected_lbl.config(text=money(expected))
        if counted is None or not self.counted.get().strip():
            return self.result_lbl.config(text="")
        diff = counted - expected
        if abs(diff) < 0.005:
            self.result_lbl.config(text="✔  Balanced!", foreground=GREEN)
        elif diff < 0:
            self.result_lbl.config(text=f"Short by {money(-diff)}", foreground=TEXT)
        else:
            self.result_lbl.config(text=f"Extra {money(diff)}", foreground=TEXT)

    def save(self):
        opening, taken = self.num(self.opening.get()), self.num(self.taken.get())
        counted = self.num(self.counted.get()) if self.counted.get().strip() else None
        if opening is None or taken is None or (self.counted.get().strip() and counted is None):
            return messagebox.showerror("Check the numbers", "Please type the amounts as numbers, like 1500.")
        self.app.db.save_day(self.day.isoformat(), opening, taken, counted, self.note.get().strip())
        self.refresh()


class TopSellersDialog(Dialog):
    """Page showing the best-selling products for a chosen period."""

    def __init__(self, master, app):
        super().__init__(master, "Top selling products")
        self.app = app
        self.resizable(True, True)
        self.minsize(900, 600)
        self.order = "qty"
        b = self.body
        head = ttk.Frame(b)
        head.pack(fill="x")
        big(head, "🏆  Top selling products", 22, True).pack(side="left")
        big_button(head, "←  Back to products", self.destroy, size=13).pack(side="right")

        self.period = PeriodBar(b, self.refresh)
        self.period.pack(anchor="w", pady=(12, 8))

        sort = ttk.Frame(b)
        sort.pack(anchor="w", pady=(0, 8))
        ttk.Label(sort, text="Rank by:", font=("Segoe UI", 13, "bold")).pack(side="left", padx=(0, 8))
        self.by_qty = big_button(sort, "Most sold (quantity)", lambda: self.set_order("qty"), size=13)
        self.by_qty.pack(side="left")
        self.by_money = big_button(sort, "Most money (revenue)", lambda: self.set_order("revenue"), size=13)
        self.by_money.pack(side="left", padx=8)

        cols = ("Rank", "Product", "Qty sold", "Revenue", "Profit", "In stock now")
        f, self.tree = make_tree(b, cols, (80, 320, 110, 140, 140, 130), 12)
        f.pack(fill="both", expand=True)
        self.tree.tag_configure("top3", font=("Segoe UI", 13, "bold"))
        self.empty = ttk.Label(b, text="", foreground="gray")
        self.empty.pack(anchor="w", pady=(6, 0))
        self.set_order("qty")
        self.run()

    def set_order(self, order):
        self.order = order
        for btn, key in ((self.by_qty, "qty"), (self.by_money, "revenue")):
            on = key == order
            btn.config(bg=DARK if on else LIGHT, fg=WHITE if on else TEXT,
                       activebackground=DARK if on else LIGHT, activeforeground=WHITE if on else TEXT)
        self.refresh()

    def refresh(self):
        rng = self.period.get()
        if not rng:
            return
        self.tree.delete(*self.tree.get_children())
        medals = {1: "🥇 1", 2: "🥈 2", 3: "🥉 3"}
        rows = self.app.db.top_products(*rng, order=self.order)
        for n, r in enumerate(rows, 1):
            stock = "-" if r["stock"] is None else r["stock"]
            self.tree.insert("", "end", tags=("top3",) if n <= 3 else (),
                             values=(medals.get(n, str(n)), r["name"], r["qty"], money(r["revenue"]),
                                     money(r["profit"]), stock))
        self.empty.config(text="" if rows else "No sales in this period yet.")


class ProductsPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Label(top, text="Search:").pack(side="left")
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *a: self.refresh())
        ttk.Entry(top, textvariable=self.q, width=30).pack(side="left", padx=6)
        for text, cmd in [("➕ Add product", self.add), ("✏ Edit", self.edit),
                          ("📦 Add stock (scan)", self.restock), ("🗑 Delete", self.delete),
                          ("🏆 Top sellers", lambda: TopSellersDialog(self, self.app))]:
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=2)

        cols = ("Barcode", "Name", "Cost Price", "Sell Price", "Stock", "Bought", "Supplier")
        f, self.tree = make_tree(self, cols, (130, 230, 100, 100, 70, 100, 140), 18)
        f.pack(fill="both", expand=True, pady=10)
        self.tree.tag_configure("low", foreground=TEXT, font=("Segoe UI", 13, "bold"))
        self.tree.bind("<Double-1>", lambda e: self.edit())
        self.summary = ttk.Label(self, text="")
        self.summary.pack(anchor="w")

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        value = items = low = 0
        for p in self.app.db.products(self.q.get()):
            is_low = p["quantity"] <= LOW_STOCK
            self.tree.insert("", "end", iid=str(p["id"]), tags=("low",) if is_low else (),
                             values=(p["barcode"], p["name"], money(p["cost_price"]), money(p["sell_price"]),
                                     p["quantity"], p["purchase_date"] or "", p["supplier"] or ""))
            value += p["cost_price"] * p["quantity"]
            items += p["quantity"]
            low += is_low
        self.summary.config(text=f"{len(self.tree.get_children())} products  ·  {items} items in stock  ·  "
                                 f"stock value at cost {money(value)}  ·  {low} low on stock (bold)")

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select", "Select a product first.")
            return None
        return int(sel[0])

    def add(self):
        d = ProductDialog(self).result
        if d:
            try:
                self.app.db.save_product(d)
            except sqlite3.IntegrityError:
                messagebox.showerror("Duplicate", "A product with that barcode already exists.")
            self.refresh()

    def edit(self):
        pid = self.selected()
        if pid:
            self.app.edit_product(self, pid)
            self.refresh()

    def restock(self):
        sel = self.tree.selection()
        self.app.restock_product(self, int(sel[0]) if sel else None)
        self.refresh()

    def delete(self):
        pid = self.selected()
        if pid and messagebox.askyesno("Delete", "Delete this product? (Past sales are kept.)"):
            self.app.db.delete_product(pid)
            self.refresh()


class RevenuePage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        self.period = PeriodBar(self, self.refresh)
        self.period.pack(anchor="w")
        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=12)
        self.cards = {}
        for key, label in [("total", "Total revenue"), ("cash", "Cash sales"),
                           ("credit", "Credit sales"), ("count", "Number of sales")]:
            c = ttk.Frame(cards, padding=10, relief="groove")
            c.pack(side="left", padx=(0, 10), fill="x", expand=True)
            ttk.Label(c, text=label, foreground="gray").pack(anchor="w")
            self.cards[key] = big(c, "-", 18, True)
            self.cards[key].pack(anchor="w")
        cols = ("Sale #", "Date & time", "Items", "Payment", "Customer", "Total")
        f, self.tree = make_tree(self, cols, (70, 170, 70, 90, 180, 120), 15)
        f.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", lambda e: self.details())
        row = ttk.Frame(self)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="View items", command=self.details).pack(side="left")
        ttk.Button(row, text="Void sale (undo)", command=self.void).pack(side="left", padx=6)

    def refresh(self):
        rng = self.period.get()
        if not rng:
            return
        self.tree.delete(*self.tree.get_children())
        total = cash = credit = 0
        rows = self.app.db.sales(*rng)
        for s in rows:
            self.tree.insert("", "end", iid=str(s["id"]),
                             values=(s["id"], s["sold_at"], s["items"] or 0, s["payment"].title(),
                                     s["customer"] or "", money(s["total"])))
            total += s["total"]
            if s["payment"] == "credit":
                credit += s["total"]
            else:
                cash += s["total"]
        self.cards["total"].config(text=money(total))
        self.cards["cash"].config(text=money(cash))
        self.cards["credit"].config(text=money(credit))
        self.cards["count"].config(text=str(len(rows)))

    def sel(self):
        s = self.tree.selection()
        return int(s[0]) if s else None

    def details(self):
        sid = self.sel()
        if sid is None:
            return
        lines = [f"{i['qty']} × {i['product_name']} @ {money(i['unit_price'])} = {money(i['qty'] * i['unit_price'])}"
                 for i in self.app.db.sale_items(sid)]
        messagebox.showinfo(f"Sale #{sid}", "\n".join(lines) or "No items")

    def void(self):
        sid = self.sel()
        if sid is None:
            return messagebox.showinfo("Select", "Select a sale first.")
        if messagebox.askyesno("Void sale", f"Delete sale #{sid} and put its items back in stock?"):
            self.app.db.void_sale(sid)
            self.refresh()


class ProfitPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        self.period = PeriodBar(self, self.refresh)
        self.period.pack(anchor="w")
        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=12)
        self.cards = {}
        for key, label in [("rev", "Revenue"), ("cost", "Cost of goods sold"),
                           ("profit", "Gross profit"), ("margin", "Profit margin")]:
            c = ttk.Frame(cards, padding=10, relief="groove")
            c.pack(side="left", padx=(0, 10), fill="x", expand=True)
            ttk.Label(c, text=label, foreground="gray").pack(anchor="w")
            self.cards[key] = big(c, "-", 18, True)
            self.cards[key].pack(anchor="w")
        ttk.Label(self, text="Profit by product (best first)").pack(anchor="w")
        cols = ("Product", "Qty sold", "Revenue", "Cost", "Profit", "Margin")
        f, self.tree = make_tree(self, cols, (260, 80, 120, 120, 120, 80), 14)
        f.pack(fill="both", expand=True, pady=(4, 0))
        self.tree.tag_configure("loss", foreground=TEXT, font=("Segoe UI", 13, "bold"))

    def refresh(self):
        rng = self.period.get()
        if not rng:
            return
        self.tree.delete(*self.tree.get_children())
        rev = cost = 0
        for r in self.app.db.profit_by_product(*rng):
            p = r["revenue"] - r["cost"]
            m = f"{p / r['revenue'] * 100:.1f}%" if r["revenue"] else "-"
            self.tree.insert("", "end", tags=("loss",) if p < 0 else (),
                             values=(r["name"], r["qty"], money(r["revenue"]), money(r["cost"]), money(p), m))
            rev += r["revenue"]
            cost += r["cost"]
        profit = rev - cost
        self.cards["rev"].config(text=money(rev))
        self.cards["cost"].config(text=money(cost))
        self.cards["profit"].config(text=money(profit), foreground=BLUE if profit >= 0 else TEXT)
        self.cards["margin"].config(text=f"{profit / rev * 100:.1f}%" if rev else "-")


class CustomerTab(ttk.Frame):
    """One customer's own page: balance, payments and everything they bought on credit."""

    def __init__(self, master, page, cid):
        super().__init__(master, padding=14)
        self.page, self.app, self.cid = page, page.app, cid
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.name = big(top, "", 22, True)
        self.name.pack(side="left")
        self.phone = big(top, "", 13)
        self.phone.pack(side="left", padx=14)
        big_button(top, "✖  Close", self.close, size=12).pack(side="right")

        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=12)
        self.cards = {}
        for key, label in [("owed", "Total bought on credit"), ("paid", "Total paid back"),
                           ("bal", "Still owes")]:
            c = ttk.Frame(cards, padding=10, relief="groove")
            c.pack(side="left", padx=(0, 10), fill="x", expand=True)
            ttk.Label(c, text=label, foreground="gray").pack(anchor="w")
            self.cards[key] = big(c, "-", 20, True)
            self.cards[key].pack(anchor="w")

        btns = ttk.Frame(self)
        btns.pack(fill="x", pady=(0, 10))
        big_button(btns, "💵  Record payment", self.pay, bg=DARK, fg=WHITE).pack(side="left")
        big_button(btns, "🔍  See items bought", self.details).pack(side="left", padx=8)

        ttk.Label(self, text="History, newest first. Double-click a sale to see what was bought.").pack(anchor="w")
        f, self.tree = make_tree(self, ("Date & time", "Details", "Amount"), (180, 500, 150), 11)
        f.pack(fill="both", expand=True, pady=(4, 0))
        self.tree.bind("<Double-1>", lambda e: self.details())
        self.tree.tag_configure("paid", background=GREEN_BG, foreground=GREEN, font=("Segoe UI", 13, "bold"))
        self.refresh()

    def refresh(self):
        c = self.app.db.customer(self.cid)
        if c is None:
            return self.close()
        bal = c["owed"] - c["paid"]
        self.name.config(text=c["name"])
        self.phone.config(text=f"📞 {c['phone']}" if c["phone"] else "")
        self.cards["owed"].config(text=money(c["owed"]))
        self.cards["paid"].config(text=money(c["paid"]))
        if c["owed"] > 0 and bal <= 0.005:
            self.cards["bal"].config(text="✔ Paid off", foreground=GREEN)
        else:
            self.cards["bal"].config(text=money(bal), foreground=TEXT)
        if str(self) in self.page.nb.tabs():
            self.page.nb.tab(self, text=c["name"])

        self.tree.delete(*self.tree.get_children())
        for r in self.app.db.customer_history(self.cid):
            if r["kind"] == "sale":
                items = ", ".join(f"{i['qty']}× {i['product_name']}" for i in self.app.db.sale_items(r["ref"]))
                if len(items) > 60:
                    items = items[:57] + "..."
                details = f"Bought on credit (sale #{r['ref']}): {items}"
                amount = f"+ {money(r['amount'])}"
            else:
                details = "Paid back" + (f" - {r['note']}" if r["note"] else "")
                amount = f"− {money(r['amount'])}"
            self.tree.insert("", "end", iid=f"{r['kind']}-{r['ref']}", values=(r["at"], details, amount),
                             tags=("paid",) if r["kind"] == "payment" else ())

    def pay(self):
        name = self.name.cget("text")
        d = SimpleForm(self, f"Payment from {name}",
                       [("amount", "Amount paid", ""), ("note", "Note (optional)", "")]).result
        if not d:
            return
        try:
            amt = float(d["amount"])
            if amt <= 0:
                raise ValueError
        except ValueError:
            return messagebox.showerror("Amount", "Enter a number above 0.")
        self.app.db.add_payment(self.cid, amt, d["note"])
        self.page.refresh()

    def details(self):
        sel = self.tree.selection()
        if not sel:
            return messagebox.showinfo("Select", "Click a sale in the history first.")
        kind, ref = sel[0].split("-")
        if kind != "sale":
            return messagebox.showinfo("Payment", "This line is a payment, not a sale.")
        lines = [f"{i['qty']} × {i['product_name']} @ {money(i['unit_price'])} = "
                 f"{money(i['qty'] * i['unit_price'])}" for i in self.app.db.sale_items(int(ref))]
        messagebox.showinfo(f"Sale #{ref}", "\n".join(lines) or "No items")

    def close(self):
        self.page.close_tab(self.cid)


class CreditPage(ttk.Frame):
    """List of customers; clicking one opens that customer's own tab."""

    def __init__(self, master, app):
        super().__init__(master, padding=10)
        self.app = app
        self.tabs = {}  # customer id -> CustomerTab
        self.nb = ttk.Notebook(self, style="Inner.TNotebook")
        self.nb.pack(fill="both", expand=True)

        home = ttk.Frame(self.nb, padding=12)
        self.nb.add(home, text="👥 All customers")
        top = ttk.Frame(home)
        top.pack(fill="x")
        big_button(top, "➕  New customer", self.add, bg=DARK, fg=WHITE).pack(side="left")
        self.outstanding = big(top, "", 15, True)
        self.outstanding.pack(side="right")
        ttk.Label(home, text="Click a customer to open their own page.").pack(anchor="w", pady=(10, 0))
        cols = ("Customer", "Phone", "Total credit", "Paid", "Balance owed")
        f, self.tree = make_tree(home, cols, (240, 140, 140, 140, 150), 14)
        f.pack(fill="both", expand=True, pady=6)
        self.tree.tag_configure("owes", foreground=TEXT, font=("Segoe UI", 13, "bold"))
        self.tree.tag_configure("clear", background=GREEN_BG, foreground=GREEN, font=("Segoe UI", 13, "bold"))
        self.tree.bind("<ButtonRelease-1>", self.on_click, add="+")

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        out = 0
        for c in self.app.db.customers():
            bal = c["owed"] - c["paid"]
            out += max(bal, 0)
            tag = ("owes",) if bal > 0.005 else ("clear",) if c["owed"] > 0 else ()
            self.tree.insert("", "end", iid=str(c["id"]), tags=tag,
                             values=(c["name"], c["phone"], money(c["owed"]), money(c["paid"]), money(bal)))
        self.outstanding.config(text=f"Total owed to you: {money(out)}")
        for t in list(self.tabs.values()):
            t.refresh()

    def on_click(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.open(int(row))

    def open(self, cid):
        if cid not in self.tabs:
            t = CustomerTab(self.nb, self, cid)
            self.nb.add(t, text=t.name.cget("text") or "Customer")
            self.tabs[cid] = t
        self.nb.select(self.tabs[cid])

    def close_tab(self, cid):
        t = self.tabs.pop(cid, None)
        if t is not None:
            self.nb.forget(t)
            t.destroy()
            self.nb.select(0)

    def add(self):
        name = self.app.new_customer(self)
        self.refresh()
        if name:
            for c in self.app.db.customers():
                if c["name"] == name:
                    self.open(c["id"])


# ----------------------------------------------------------------------- app --
def apply_theme(root):
    """Blue & white look, same on every computer."""
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=WHITE)
    style.configure(".", background=WHITE, foreground=TEXT, font=("Segoe UI", 11),
                    bordercolor=BLUE, lightcolor=WHITE, darkcolor=LIGHT, focuscolor=BLUE)
    style.configure("TFrame", background=WHITE)
    style.configure("TLabel", background=WHITE, foreground=TEXT, font=("Segoe UI", 11))
    style.configure("TLabelframe", background=WHITE, bordercolor=BLUE)
    style.configure("TLabelframe.Label", background=WHITE, foreground=BLUE, font=("Segoe UI", 12, "bold"))
    # main tabs
    style.configure("TNotebook", background=LIGHT, borderwidth=0, tabmargins=(6, 6, 6, 0))
    style.configure("TNotebook.Tab", background=LIGHT, foreground=TEXT, padding=(20, 10),
                    font=("Segoe UI", 13, "bold"), bordercolor=BLUE)
    style.map("TNotebook.Tab", background=[("selected", BLUE), ("active", MID)],
              foreground=[("selected", WHITE)])
    # customer tabs inside the Credit page
    style.configure("Inner.TNotebook", background=WHITE, borderwidth=0)
    style.configure("Inner.TNotebook.Tab", background=LIGHT, foreground=TEXT, padding=(14, 6),
                    font=("Segoe UI", 12, "bold"))
    style.map("Inner.TNotebook.Tab", background=[("selected", BLUE), ("active", MID)],
              foreground=[("selected", WHITE)])
    # buttons
    style.configure("TButton", background=BLUE, foreground=WHITE, font=("Segoe UI", 12, "bold"),
                    padding=(12, 8), borderwidth=0)
    style.map("TButton", background=[("disabled", "#b0bec5"), ("pressed", DARK), ("active", DARK)],
              foreground=[("disabled", "#eceff1")])
    # lists
    style.configure("Treeview", background=WHITE, fieldbackground=WHITE, foreground=TEXT,
                    rowheight=34, font=("Segoe UI", 13), bordercolor=BLUE)
    style.map("Treeview", background=[("selected", MID)], foreground=[("selected", TEXT)])
    style.configure("Treeview.Heading", background=BLUE, foreground=WHITE,
                    font=("Segoe UI", 12, "bold"), relief="flat")
    style.map("Treeview.Heading", background=[("active", DARK)])
    # inputs
    style.configure("TEntry", fieldbackground=WHITE, foreground=TEXT, bordercolor=BLUE,
                    insertcolor=TEXT, padding=4)
    style.configure("TCombobox", fieldbackground=WHITE, background=LIGHT, foreground=TEXT,
                    arrowcolor=BLUE, bordercolor=BLUE)
    style.map("TCombobox", fieldbackground=[("readonly", WHITE), ("disabled", "#eceff1")])
    style.configure("TRadiobutton", background=WHITE, font=("Segoe UI", 12))
    style.configure("Vertical.TScrollbar", background=LIGHT, troughcolor=WHITE,
                    arrowcolor=BLUE, bordercolor=LIGHT)
    root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 13))



class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.db = DB(DB_PATH)
        self.title(APP_NAME)
        w, h = 1050, 700
        x = max((self.winfo_screenwidth() - w) // 2, 0)
        y = max((self.winfo_screenheight() - h) // 2 - 30, 0)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(900, 600)
        apply_theme(self)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.pages = []
        for cls, title in [(ScanSellPage, "🛒 Scan & Sell"), (TodayPage, "📅 Today"), (ProductsPage, "📦 Products"),
                           (RevenuePage, "💰 Revenue"), (ProfitPage, "📈 Profit"), (CreditPage, "📒 Credit")]:
            page = cls(self.nb, self)
            self.nb.add(page, text=title)
            self.pages.append(page)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.pages[self.nb.index("current")].refresh())
        self.after(100, self.pages[0].refresh)

    # shared actions used by several pages
    def edit_product(self, parent, pid):
        p = self.db.product(pid)
        if not p:
            return
        d = ProductDialog(parent, product=p).result
        if d:
            try:
                self.db.save_product(d, pid)
            except sqlite3.IntegrityError:
                messagebox.showerror("Duplicate", "Another product already uses that barcode.")

    def restock_product(self, parent, pid=None):
        StockInDialog(parent, self, self.db.product(pid) if pid else None)

    def new_customer(self, parent):
        d = SimpleForm(parent, "New customer", [("name", "Name", ""), ("phone", "Phone (optional)", "")]).result
        if not d or not d["name"]:
            return None
        try:
            self.db.add_customer(d["name"], d["phone"])
        except sqlite3.IntegrityError:
            messagebox.showerror("Exists", "A customer with that name already exists.")
            return None
        return d["name"]


if __name__ == "__main__":
    App().mainloop()