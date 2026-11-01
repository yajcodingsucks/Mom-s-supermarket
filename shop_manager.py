"""
Shop Manager - a simple supermarket-style inventory & sales app.

Pages:  Scan | Sell | Products | Revenue | Profit | Credit

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
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import messagebox, ttk

# ----------------------------------------------------------------- settings --
APP_NAME = "Shop Manager"
CURRENCY = "Rs"          # change to "$", "€", "₹" ... as you like
LOW_STOCK = 5            # products at or below this quantity show in red
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop.db")


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
        CREATE TABLE IF NOT EXISTS credit_payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
            amount REAL NOT NULL,
            paid_at TEXT NOT NULL,
            note TEXT DEFAULT ''
        );
        """)
        self.conn.commit()

    # ---- products
    def product_by_barcode(self, code):
        return self.conn.execute("SELECT * FROM products WHERE barcode=?", (code,)).fetchone()

    def product(self, pid):
        return self.conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()

    def products(self, search=""):
        s = f"%{search}%"
        return self.conn.execute(
            "SELECT * FROM products WHERE name LIKE ? OR barcode LIKE ? OR supplier LIKE ? "
            "ORDER BY name COLLATE NOCASE", (s, s, s)).fetchall()

    def save_product(self, d, pid=None):
        vals = (d["barcode"], d["name"], d["cost_price"], d["sell_price"],
                d["quantity"], d["purchase_date"], d["supplier"])
        with self.conn:
            if pid is None:
                self.conn.execute(
                    "INSERT INTO products(barcode,name,cost_price,sell_price,quantity,purchase_date,supplier) "
                    "VALUES (?,?,?,?,?,?,?)", vals)
            else:
                self.conn.execute(
                    "UPDATE products SET barcode=?,name=?,cost_price=?,sell_price=?,quantity=?,"
                    "purchase_date=?,supplier=? WHERE id=?", vals + (pid,))

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
                if p["quantity"] < it["qty"]:
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

    def customer_history(self, cid):
        return self.conn.execute(
            "SELECT sold_at AS at, 'Credit sale #' || id AS what, total AS amount FROM sales "
            "WHERE customer_id=? AND payment='credit' "
            "UNION ALL SELECT paid_at, 'Payment' || CASE WHEN note<>'' THEN ' - ' || note ELSE '' END, -amount "
            "FROM credit_payments WHERE customer_id=? ORDER BY 1 DESC", (cid, cid)).fetchall()


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
    """Entry that fires callback(code) on Enter, plus Camera / Find buttons."""

    def __init__(self, master, app, callback, label="Scan or type barcode:"):
        super().__init__(master)
        self.app, self.callback = app, callback
        ttk.Label(self, text=label).pack(side="left")
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=30, font=("Segoe UI", 13))
        self.entry.pack(side="left", padx=6)
        self.entry.bind("<Return>", lambda e: self.fire(self.var.get()))
        ttk.Button(self, text="📷 Camera", command=self.camera).pack(side="left", padx=2)
        ttk.Button(self, text="🔍 Find by name", command=self.find).pack(side="left", padx=2)

    def fire(self, code):
        code = (code or "").strip()
        self.var.set("")
        if code:
            self.callback(code)
        self.focus()

    def camera(self):
        self.fire(scan_with_camera(self))

    def find(self):
        p = ProductPicker(self, self.app.db).result
        if p:
            self.fire(p["barcode"])

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
    sb.pack(side="right", fill="y")
    return frame, tree


class Dialog(tk.Toplevel):
    """Base modal dialog."""

    def __init__(self, master, title):
        super().__init__(master)
        self.title(title)
        self.transient(master.winfo_toplevel())
        self.resizable(False, False)
        self.result = None
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def run(self):
        self.grab_set()
        self.wait_window()
        return self.result


class ProductDialog(Dialog):
    FIELDS = [("barcode", "Barcode"), ("name", "Product name"), ("cost_price", "Cost price (what you paid)"),
              ("sell_price", "Selling price"), ("quantity", "Quantity in stock"),
              ("purchase_date", "Date bought (YYYY-MM-DD)"), ("supplier", "Supplier (optional)")]

    def __init__(self, master, product=None, barcode=""):
        super().__init__(master, "Edit product" if product else "New product")
        self.vars = {}
        defaults = dict(product) if product else {
            "barcode": barcode, "name": "", "cost_price": "", "sell_price": "",
            "quantity": "", "purchase_date": date.today().isoformat(), "supplier": ""}
        for r, (key, label) in enumerate(self.FIELDS):
            ttk.Label(self.body, text=label).grid(row=r, column=0, sticky="w", pady=3)
            v = tk.StringVar(value="" if defaults.get(key) is None else str(defaults[key]))
            e = ttk.Entry(self.body, textvariable=v, width=32)
            e.grid(row=r, column=1, pady=3, padx=(8, 0))
            self.vars[key] = v
            if r == (1 if barcode or product else 0):
                e.focus_set()
        ttk.Button(self.body, text="Save", command=self.save).grid(row=len(self.FIELDS), column=1,
                                                                   sticky="e", pady=(10, 0))
        self.bind("<Return>", lambda e: self.save())
        self.result = self.run()

    def save(self):
        d = {k: v.get().strip() for k, v in self.vars.items()}
        if not d["barcode"] or not d["name"]:
            return messagebox.showerror("Missing", "Barcode and name are required.", parent=self)
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
class ScanPage(ttk.Frame):
    """Scan a product to see its price and quantity."""

    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        self.current = None
        self.scan = ScanBox(self, app, self.lookup)
        self.scan.pack(anchor="w")
        card = ttk.Frame(self, padding=20, relief="groove")
        card.pack(fill="x", pady=20)
        self.name = big(card, "Scan a product to see its details", 22, True)
        self.name.pack(anchor="w")
        self.price = big(card, "", 30, True, foreground="#1a7f37")
        self.price.pack(anchor="w", pady=(10, 0))
        self.qty = big(card, "", 18)
        self.qty.pack(anchor="w", pady=(6, 0))
        self.extra = big(card, "", 11, foreground="gray")
        self.extra.pack(anchor="w", pady=(6, 0))
        btns = ttk.Frame(self)
        btns.pack(anchor="w")
        self.edit_btn = ttk.Button(btns, text="Edit product", command=self.edit, state="disabled")
        self.edit_btn.pack(side="left", padx=(0, 6))
        self.restock_btn = ttk.Button(btns, text="Add stock", command=self.restock, state="disabled")
        self.restock_btn.pack(side="left")

    def refresh(self):
        if self.current:
            self.show(self.app.db.product(self.current))
        self.scan.focus()

    def lookup(self, code):
        p = self.app.db.product_by_barcode(code)
        if p:
            self.app.bell()
            self.show(p)
        elif messagebox.askyesno("Not found", f"No product with barcode {code}.\nAdd it as a new product?"):
            d = ProductDialog(self, barcode=code).result
            if d:
                try:
                    self.app.db.save_product(d)
                except sqlite3.IntegrityError:
                    return messagebox.showerror("Duplicate", "That barcode already exists.")
                self.show(self.app.db.product_by_barcode(d["barcode"]))

    def show(self, p):
        if p is None:
            self.current = None
            self.name.config(text="Scan a product to see its details")
            for w in (self.price, self.qty, self.extra):
                w.config(text="")
            self.edit_btn.config(state="disabled")
            self.restock_btn.config(state="disabled")
            return
        self.current = p["id"]
        self.name.config(text=p["name"])
        self.price.config(text=money(p["sell_price"]))
        low = p["quantity"] <= LOW_STOCK
        self.qty.config(text=f"In stock: {p['quantity']}" + ("   ⚠ low stock" if low else ""),
                        foreground="#c62828" if low else "")
        self.extra.config(text=f"Barcode {p['barcode']}  ·  Cost {money(p['cost_price'])}  ·  "
                               f"Bought {p['purchase_date'] or '-'}  ·  Supplier {p['supplier'] or '-'}")
        self.edit_btn.config(state="normal")
        self.restock_btn.config(state="normal")

    def edit(self):
        self.app.edit_product(self, self.current)
        self.refresh()

    def restock(self):
        self.app.restock_product(self, self.current)
        self.refresh()


class SellPage(ttk.Frame):
    """Point of sale: scan items into a cart and check out."""

    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        self.cart = {}  # product_id -> {product_id, name, price, qty, stock}
        self.scan = ScanBox(self, app, self.add_code)
        self.scan.pack(anchor="w")

        f, self.tree = make_tree(self, ("Product", "Price", "Qty", "Subtotal"), (340, 120, 70, 130), 12)
        f.pack(fill="both", expand=True, pady=10)

        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Button(row, text="+1", width=4, command=lambda: self.change(1)).pack(side="left")
        ttk.Button(row, text="−1", width=4, command=lambda: self.change(-1)).pack(side="left", padx=4)
        ttk.Button(row, text="Remove", command=self.remove).pack(side="left")
        ttk.Button(row, text="Clear cart", command=self.clear).pack(side="left", padx=4)
        self.total_lbl = big(row, money(0), 22, True)
        self.total_lbl.pack(side="right")
        big(row, "TOTAL: ", 16).pack(side="right")

        pay = ttk.LabelFrame(self, text="Payment", padding=10)
        pay.pack(fill="x", pady=10)
        self.payment = tk.StringVar(value="cash")
        ttk.Radiobutton(pay, text="Cash", value="cash", variable=self.payment,
                        command=self.toggle).grid(row=0, column=0, sticky="w")
        ttk.Label(pay, text="Amount given:").grid(row=0, column=1, padx=(20, 4))
        self.given = tk.StringVar()
        self.given.trace_add("write", lambda *a: self.update_change())
        self.given_entry = ttk.Entry(pay, textvariable=self.given, width=12)
        self.given_entry.grid(row=0, column=2)
        self.change_lbl = ttk.Label(pay, text="")
        self.change_lbl.grid(row=0, column=3, padx=10)

        ttk.Radiobutton(pay, text="Credit (pay later)", value="credit", variable=self.payment,
                        command=self.toggle).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(pay, text="Customer:").grid(row=1, column=1, padx=(20, 4), pady=(6, 0))
        self.customer = ttk.Combobox(pay, state="disabled", width=24)
        self.customer.grid(row=1, column=2, columnspan=2, sticky="w", pady=(6, 0))
        self.new_cust_btn = ttk.Button(pay, text="New customer", command=self.new_customer, state="disabled")
        self.new_cust_btn.grid(row=1, column=4, padx=6, pady=(6, 0))

        ttk.Button(self, text="✔  Complete sale", command=self.checkout).pack(anchor="e", ipadx=10, ipady=4)

    def refresh(self):
        self.load_customers()
        # refresh stock numbers for items already in the cart
        for pid in list(self.cart):
            p = self.app.db.product(pid)
            if p is None:
                del self.cart[pid]
            else:
                self.cart[pid]["stock"] = p["quantity"]
        self.redraw()
        self.scan.focus()

    def load_customers(self):
        self.cust_map = {c["name"]: c["id"] for c in self.app.db.customers()}
        self.customer["values"] = list(self.cust_map)

    def toggle(self):
        credit = self.payment.get() == "credit"
        self.customer.config(state="readonly" if credit else "disabled")
        self.new_cust_btn.config(state="normal" if credit else "disabled")
        self.given_entry.config(state="disabled" if credit else "normal")

    def new_customer(self):
        cid_name = self.app.new_customer(self)
        if cid_name:
            self.load_customers()
            self.customer.set(cid_name)

    def add_code(self, code):
        p = self.app.db.product_by_barcode(code)
        if not p:
            self.app.bell()
            return messagebox.showwarning("Not found", f"No product with barcode {code}.\n"
                                                       "Add it on the Products page first.")
        item = self.cart.get(p["id"])
        in_cart = item["qty"] if item else 0
        if in_cart + 1 > p["quantity"]:
            self.app.bell()
            return messagebox.showwarning("Out of stock", f"Only {p['quantity']} of {p['name']} in stock.")
        if item:
            item["qty"] += 1
        else:
            self.cart[p["id"]] = {"product_id": p["id"], "name": p["name"], "price": p["sell_price"],
                                  "qty": 1, "stock": p["quantity"]}
        self.app.bell()
        self.redraw(select=p["id"])

    def selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def change(self, delta):
        pid = self.selected()
        if pid is None:
            return
        it = self.cart[pid]
        if delta > 0 and it["qty"] + delta > it["stock"]:
            return messagebox.showwarning("Out of stock", f"Only {it['stock']} of {it['name']} in stock.")
        it["qty"] += delta
        if it["qty"] <= 0:
            del self.cart[pid]
        self.redraw(select=pid)

    def remove(self):
        pid = self.selected()
        if pid is not None:
            del self.cart[pid]
            self.redraw()

    def clear(self):
        self.cart.clear()
        self.given.set("")
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
        self.update_change()

    def update_change(self):
        try:
            g = float(self.given.get())
        except ValueError:
            return self.change_lbl.config(text="")
        diff = g - self.total()
        self.change_lbl.config(text=f"Change: {money(diff)}" if diff >= 0 else f"Short by {money(-diff)}",
                               foreground="" if diff >= 0 else "#c62828")

    def checkout(self):
        if not self.cart:
            return messagebox.showinfo("Empty", "The cart is empty.")
        cid = None
        if self.payment.get() == "credit":
            cid = self.cust_map.get(self.customer.get())
            if cid is None:
                return messagebox.showwarning("Customer", "Choose (or add) the customer for this credit sale.")
        try:
            sid, total = self.app.db.record_sale(list(self.cart.values()), self.payment.get(), cid)
        except ValueError as e:
            return messagebox.showerror("Can't complete sale", str(e))
        msg = f"Sale #{sid} saved.\nTotal: {money(total)}"
        if self.payment.get() == "credit":
            msg += f"\nAdded to {self.customer.get()}'s credit."
        messagebox.showinfo("Done", msg)
        self.clear()
        self.payment.set("cash")
        self.customer.set("")
        self.toggle()
        self.scan.focus()


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
                          ("📦 Add stock", self.restock), ("🗑 Delete", self.delete)]:
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=2)

        cols = ("Barcode", "Name", "Cost Price", "Sell Price", "Stock", "Bought", "Supplier")
        f, self.tree = make_tree(self, cols, (130, 230, 100, 100, 70, 100, 140), 18)
        f.pack(fill="both", expand=True, pady=10)
        self.tree.tag_configure("low", foreground="#c62828")
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
                                 f"stock value at cost {money(value)}  ·  {low} low on stock (red)")

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
        pid = self.selected()
        if pid:
            self.app.restock_product(self, pid)
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
        self.tree.tag_configure("loss", foreground="#c62828")

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
        self.cards["profit"].config(text=money(profit), foreground="#1a7f37" if profit >= 0 else "#c62828")
        self.cards["margin"].config(text=f"{profit / rev * 100:.1f}%" if rev else "-")


class CreditPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=16)
        self.app = app
        top = ttk.Frame(self)
        top.pack(fill="x")
        for text, cmd in [("➕ New customer", self.add), ("💵 Record payment", self.pay),
                          ("📜 History", self.history)]:
            ttk.Button(top, text=text, command=cmd).pack(side="left", padx=(0, 4))
        self.outstanding = big(top, "", 14, True, foreground="#c62828")
        self.outstanding.pack(side="right")
        cols = ("Customer", "Phone", "Total credit", "Paid", "Balance owed")
        f, self.tree = make_tree(self, cols, (220, 130, 130, 130, 140), 18)
        f.pack(fill="both", expand=True, pady=10)
        self.tree.tag_configure("owes", foreground="#c62828")
        self.tree.bind("<Double-1>", lambda e: self.history())

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        out = 0
        for c in self.app.db.customers():
            bal = c["owed"] - c["paid"]
            out += max(bal, 0)
            self.tree.insert("", "end", iid=str(c["id"]), tags=("owes",) if bal > 0.005 else (),
                             values=(c["name"], c["phone"], money(c["owed"]), money(c["paid"]), money(bal)))
        self.outstanding.config(text=f"Total owed to you: {money(out)}")

    def sel(self):
        s = self.tree.selection()
        if not s:
            messagebox.showinfo("Select", "Select a customer first.")
            return None
        return int(s[0])

    def add(self):
        self.app.new_customer(self)
        self.refresh()

    def pay(self):
        cid = self.sel()
        if cid is None:
            return
        name = self.tree.item(str(cid))["values"][0]
        d = SimpleForm(self, f"Payment from {name}",
                       [("amount", "Amount paid", ""), ("note", "Note (optional)", "")]).result
        if not d:
            return
        try:
            amt = float(d["amount"])
            if amt <= 0:
                raise ValueError
        except ValueError:
            return messagebox.showerror("Amount", "Enter a positive number.")
        self.app.db.add_payment(cid, amt, d["note"])
        self.refresh()

    def history(self):
        cid = self.sel()
        if cid is None:
            return
        name = self.tree.item(str(cid))["values"][0]
        rows = self.app.db.customer_history(cid)
        lines = [f"{r['at']}   {r['what']:<28} {'+' if r['amount'] > 0 else '−'}{money(abs(r['amount']))}"
                 for r in rows]
        messagebox.showinfo(f"{name} - history", "\n".join(lines) or "No credit activity yet.")


# ----------------------------------------------------------------------- app --
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.db = DB(DB_PATH)
        self.title(APP_NAME)
        self.geometry("1050x700")
        self.minsize(900, 600)
        style = ttk.Style(self)
        if "clam" in style.theme_names() and os.name != "nt":
            style.theme_use("clam")
        style.configure("TNotebook.Tab", padding=(16, 8), font=("Segoe UI", 11))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.pages = []
        for cls, title in [(ScanPage, "🔎 Scan"), (SellPage, "🛒 Sell"), (ProductsPage, "📦 Products"),
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

    def restock_product(self, parent, pid):
        p = self.db.product(pid)
        if not p:
            return
        d = SimpleForm(parent, f"Add stock - {p['name']}",
                       [("qty", "Quantity received", ""),
                        ("cost", "New cost price (blank = keep)", ""),
                        ("date", "Date bought", date.today().isoformat())]).result
        if not d:
            return
        try:
            qty = int(d["qty"])
            cost = float(d["cost"]) if d["cost"] else None
            if d["date"]:
                date.fromisoformat(d["date"])
        except ValueError:
            return messagebox.showerror("Check values", "Quantity must be a whole number, cost a number, "
                                                        "date like 2026-11-01.")
        self.db.restock(pid, qty, cost, d["date"] or None)

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
