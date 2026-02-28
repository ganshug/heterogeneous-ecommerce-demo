"""
IBM Power E-Cart — Shopping Cart Application
Runs on Intel (x86_64), connects to IBM Db2 on IBM Power (ppc64le).

Provides:
  - Browser UI  : GET /        — full HTML e-commerce shopping cart
  - REST API    : /products, /cart, /orders, /arch, /health, /ready
"""

import os
import time
import platform
import html
from contextlib import contextmanager
from flask import Flask, jsonify, request, redirect, url_for

# ibm_db_dbi provides a PEP-249 DB-API 2.0 interface to IBM Db2
import ibm_db
import ibm_db_dbi

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Database connection configuration
# ---------------------------------------------------------------------------
DB2_HOST     = os.environ.get("DB2_HOST",     "db2-service.db2-shop-demo.svc.cluster.local")
DB2_PORT     = os.environ.get("DB2_PORT",     "50000")
DB2_DATABASE = os.environ.get("DB2_DATABASE", "shopdb")
DB2_USER     = os.environ.get("DB2_USER",     "db2inst1")
DB2_PASSWORD = os.environ.get("DB2_PASSWORD", "Db2ShopDemo2024!")

DB2_CONN_STR = (
    f"DATABASE={DB2_DATABASE};"
    f"HOSTNAME={DB2_HOST};"
    f"PORT={DB2_PORT};"
    f"PROTOCOL=TCPIP;"
    f"UID={DB2_USER};"
    f"PWD={DB2_PASSWORD};"
)


# ---------------------------------------------------------------------------
# Database helpers — using ibm_db_dbi (PEP-249 cursor interface)
# ---------------------------------------------------------------------------

def _connect_with_retry(retries=5, delay=5):
    """Open an ibm_db_dbi connection, retrying on failure."""
    last_exc = Exception("Could not connect to Db2")
    for attempt in range(retries):
        try:
            conn = ibm_db_dbi.connect(DB2_CONN_STR, "", "")
            return conn
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                print(f"Db2 connection attempt {attempt + 1} failed: {exc}. Retrying in {delay}s...")
                time.sleep(delay)
    raise last_exc


@contextmanager
def get_db():
    """Context manager: open a Db2 connection, always close it."""
    conn = _connect_with_retry()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _q(value):
    """Escape single quotes for Db2 SQL string literals."""
    return str(value).replace("'", "''")


def _create_table_if_not_exists(cur, create_sql, table_name):
    """Execute CREATE TABLE, ignoring SQL0601N (table already exists in Db2)."""
    try:
        cur.execute(create_sql)
    except Exception as exc:
        if "SQL0601N" in str(exc) or "-601" in str(exc):
            pass  # Table already exists — that's fine
        else:
            raise


def init_db():
    """Initialize the Db2 database schema for the e-cart."""
    with get_db() as conn:
        cur = conn.cursor()

        # Products table
        _create_table_if_not_exists(cur, """
            CREATE TABLE products (
                id          INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name        VARCHAR(255) NOT NULL,
                description VARCHAR(1000),
                price       DECIMAL(10,2) NOT NULL DEFAULT 0.00,
                stock       INTEGER NOT NULL DEFAULT 0,
                category    VARCHAR(100) DEFAULT 'General',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Cart items table
        _create_table_if_not_exists(cur, """
            CREATE TABLE cart_items (
                id         INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                product_id INTEGER NOT NULL,
                quantity   INTEGER NOT NULL DEFAULT 1,
                added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Orders table
        _create_table_if_not_exists(cur, """
            CREATE TABLE orders (
                id           INTEGER NOT NULL GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                total_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
                status       VARCHAR(50) NOT NULL DEFAULT 'pending',
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Seed sample products if table is empty
        cur.execute("SELECT COUNT(*) FROM products")
        row = cur.fetchone()
        if row and row[0] == 0:
            seed_products = [
                ("IBM Power10 Server S1022", "Dual-socket POWER10 server — ppc64le architecture, ideal for AI/ML workloads", 49999.99, 10, "Servers"),
                ("IBM Fusion HCI Node", "Hyper-converged infrastructure node for OpenShift — NVMe + 25GbE", 89999.99, 5, "HCI"),
                ("Red Hat OpenShift Subscription", "Enterprise Kubernetes platform subscription (1 year, unlimited nodes)", 1299.99, 100, "Software"),
                ("IBM Db2 Enterprise License", "Enterprise database license for IBM Power — includes HA and partitioning", 4999.99, 50, "Software"),
                ("NVMe SSD 3.84TB U.2", "High-speed NVMe storage for HCI nodes — 7GB/s read", 1299.99, 30, "Storage"),
                ("25GbE Dual-Port NIC", "High-performance 25GbE network adapter for OCP nodes", 399.99, 75, "Networking"),
                ("IBM Storage Scale License", "Parallel file system license for IBM Fusion HCI", 2499.99, 20, "Software"),
                ("DDR5 128GB RDIMM", "Server memory module for IBM Power10 — DDR5 4800MHz", 899.99, 40, "Memory"),
            ]
            for p in seed_products:
                cur.execute(
                    "INSERT INTO products (name, description, price, stock, category) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (p[0], p[1], p[2], p[3], p[4])
                )

        cur.close()
    print("Db2 e-cart database initialized successfully.")


# Initialize DB on startup
with app.app_context():
    try:
        init_db()
    except Exception as exc:
        print(f"WARNING: Could not initialize Db2 at startup: {exc}")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _esc(value):
    """HTML-escape a value for safe rendering."""
    return html.escape(str(value)) if value is not None else ""


def get_db_info():
    """Get Db2 connection status and version string."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT SERVICE_LEVEL FROM SYSIBMADM.ENV_INST_INFO")
            row = cur.fetchone()
            cur.close()
            version = row[0] if row else "Unknown"
        return True, f"IBM Db2 {version}"
    except Exception as exc:
        return False, str(exc)[:80]


def get_products():
    """Fetch all products from Db2."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, name, description, price, stock, category "
                "FROM products ORDER BY category, id"
            )
            rows = cur.fetchall()
            cur.close()
        return rows, None
    except Exception as exc:
        return [], str(exc)


def get_cart():
    """Fetch all cart items with product details from Db2."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.id, p.name, p.price, c.quantity,
                       DECIMAL(p.price * c.quantity, 10, 2) AS subtotal,
                       c.product_id, p.category
                FROM cart_items c
                JOIN products p ON c.product_id = p.id
                ORDER BY c.added_at DESC
            """)
            rows = cur.fetchall()
            cur.close()
        return rows, None
    except Exception as exc:
        return [], str(exc)


def get_cart_total():
    """Calculate total price of all items in cart."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT COALESCE(SUM(DECIMAL(p.price * c.quantity, 10, 2)), 0)
                FROM cart_items c
                JOIN products p ON c.product_id = p.id
            """)
            row = cur.fetchone()
            cur.close()
            return float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# HTML template — E-Cart UI
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IBM Power E-Cart — Heterogeneous HCP Demo</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0a0e1a; color: #e0e0e0; min-height: 100vh; }}
    header {{ background: linear-gradient(135deg, #0d1b2a 0%, #0a0e1a 100%); border-bottom: 2px solid #0062ff; padding: 16px 32px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }}
    .logo {{ display: flex; align-items: center; gap: 12px; }}
    .logo-icon {{ font-size: 2rem; }}
    header h1 {{ font-size: 1.4rem; color: #fff; }}
    header h1 span {{ color: #0062ff; }}
    header h1 .sub {{ font-size: 0.75rem; color: #555; display: block; font-weight: 400; margin-top: 2px; }}
    .cart-badge {{ background: #0062ff; color: #fff; border-radius: 20px; padding: 8px 18px; font-size: 0.9rem; font-weight: 700; white-space: nowrap; }}
    .arch-banner {{ display: flex; gap: 12px; padding: 12px 32px; background: #111827; border-bottom: 1px solid #1e2535; flex-wrap: wrap; }}
    .arch-card {{ background: #1a2235; border-radius: 8px; padding: 10px 16px; border-left: 4px solid; flex: 1; min-width: 160px; }}
    .arch-card.power {{ border-color: #4caf50; }}
    .arch-card.intel {{ border-color: #0062ff; }}
    .arch-card.db {{ border-color: #ff9800; }}
    .arch-card h3 {{ font-size: 0.65rem; text-transform: uppercase; letter-spacing: 1px; color: #666; margin-bottom: 3px; }}
    .arch-card .value {{ font-size: 0.88rem; font-weight: 700; }}
    .arch-card.power .value {{ color: #4caf50; }}
    .arch-card.intel .value {{ color: #0062ff; }}
    .arch-card.db .value {{ color: #ff9800; }}
    .arch-card .sub {{ font-size: 0.68rem; color: #555; margin-top: 2px; }}
    .main {{ display: flex; gap: 20px; max-width: 1280px; margin: 20px auto; padding: 0 20px; align-items: flex-start; }}
    .products-panel {{ flex: 2.5; }}
    .cart-panel {{ flex: 1; min-width: 280px; position: sticky; top: 20px; }}
    .card {{ background: #1a2235; border-radius: 10px; padding: 18px; margin-bottom: 18px; border: 1px solid #1e2535; }}
    .card h2 {{ font-size: 0.95rem; color: #fff; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #1e2535; display: flex; align-items: center; gap: 8px; }}
    .category-label {{ font-size: 0.7rem; color: #555; font-weight: 400; margin-left: auto; }}
    .product-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }}
    .product-card {{ background: #111827; border-radius: 8px; padding: 14px; border: 1px solid #1e2535; transition: border-color 0.2s, transform 0.1s; }}
    .product-card:hover {{ border-color: #0062ff; transform: translateY(-1px); }}
    .product-card .cat-tag {{ font-size: 0.65rem; color: #0062ff; background: #0a1628; border-radius: 4px; padding: 2px 7px; display: inline-block; margin-bottom: 6px; }}
    .product-card h3 {{ font-size: 0.85rem; color: #fff; margin-bottom: 5px; line-height: 1.3; }}
    .product-card .desc {{ font-size: 0.72rem; color: #666; margin-bottom: 10px; line-height: 1.4; }}
    .product-card .price {{ font-size: 1.05rem; font-weight: 700; color: #0062ff; margin-bottom: 3px; }}
    .product-card .stock {{ font-size: 0.68rem; margin-bottom: 10px; }}
    .btn {{ padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 0.82rem; font-weight: 600; transition: all 0.2s; }}
    .btn-primary {{ background: #0062ff; color: #fff; width: 100%; }}
    .btn-primary:hover {{ background: #0050d0; }}
    .btn-primary:disabled {{ opacity: 0.35; cursor: not-allowed; }}
    .btn-danger {{ background: transparent; color: #f44336; border: 1px solid #f44336; padding: 4px 10px; font-size: 0.75rem; }}
    .btn-danger:hover {{ background: #f44336; color: #fff; }}
    .btn-success {{ background: #4caf50; color: #fff; width: 100%; padding: 12px; font-size: 0.95rem; border-radius: 8px; }}
    .btn-success:hover {{ background: #388e3c; }}
    .btn-outline {{ background: transparent; color: #888; border: 1px solid #2a2f3e; }}
    .btn-outline:hover {{ border-color: #888; color: #e0e0e0; }}
    .flash {{ padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; font-size: 0.82rem; }}
    .flash.success {{ background: #0d2a0d; border: 1px solid #4caf50; color: #4caf50; }}
    .flash.error {{ background: #2a0d0d; border: 1px solid #f44336; color: #f44336; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th {{ text-align: left; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.5px; color: #555; padding: 6px 8px; border-bottom: 1px solid #1e2535; }}
    td {{ padding: 8px 8px; border-bottom: 1px solid #111827; font-size: 0.82rem; vertical-align: middle; }}
    tr:last-child td {{ border-bottom: none; }}
    .cart-total-row {{ display: flex; justify-content: space-between; align-items: center; padding: 12px 0 8px; border-top: 1px solid #1e2535; margin-top: 8px; }}
    .cart-total-row .label {{ font-size: 0.85rem; color: #888; }}
    .cart-total-row .amount {{ font-size: 1.25rem; font-weight: 700; color: #0062ff; }}
    .empty {{ text-align: center; color: #444; padding: 20px; font-size: 0.82rem; }}
    .badge {{ display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 0.65rem; font-weight: 700; }}
    .badge-power {{ background: #0d2a0d; color: #4caf50; }}
    .badge-intel {{ background: #0a1628; color: #0062ff; }}
    .qty-wrap {{ display: flex; align-items: center; gap: 6px; margin-bottom: 8px; }}
    .qty-input {{ width: 48px; background: #0a0e1a; border: 1px solid #1e2535; border-radius: 4px; padding: 4px 8px; color: #e0e0e0; font-size: 0.8rem; text-align: center; outline: none; }}
    .qty-label {{ font-size: 0.68rem; color: #666; }}
    .form-field {{ margin-bottom: 10px; }}
    .form-field label {{ display: block; font-size: 0.7rem; color: #666; margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .form-field input {{ width: 100%; background: #0a0e1a; border: 1px solid #1e2535; border-radius: 6px; padding: 8px 12px; color: #e0e0e0; font-size: 0.82rem; outline: none; transition: border-color 0.2s; }}
    .form-field input:focus {{ border-color: #0062ff; }}
    .form-row {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .form-row .form-field {{ flex: 1; min-width: 100px; }}
    .orders-count {{ font-size: 0.75rem; color: #555; }}
    footer {{ text-align: center; padding: 16px; color: #333; font-size: 0.72rem; margin-top: 8px; border-top: 1px solid #111827; }}
  </style>
</head>
<body>
  <header>
    <div class="logo">
      <span class="logo-icon">&#128722;</span>
      <div>
        <h1>IBM Power <span>E-Cart</span>
          <span class="sub">Heterogeneous OCP Demo &mdash; Intel App + IBM Power Db2</span>
        </h1>
      </div>
    </div>
    <span class="cart-badge">&#128722; {cart_count} item{cart_plural} &nbsp;|&nbsp; ${cart_total:.2f}</span>
  </header>

  <div class="arch-banner">
    <div class="arch-card intel">
      <h3>App Server (This Pod)</h3>
      <div class="value">x86_64 &mdash; Intel</div>
      <div class="sub">{app_node}</div>
    </div>
    <div class="arch-card power">
      <h3>Database (IBM Power)</h3>
      <div class="value">ppc64le &mdash; IBM Power</div>
      <div class="sub">db2-service &bull; IBM Db2</div>
    </div>
    <div class="arch-card db">
      <h3>Db2 Status</h3>
      <div class="value" style="color:{db_color};">{db_status}</div>
      <div class="sub">{db_version}</div>
    </div>
  </div>

  <div class="main">
    <!-- Products Panel -->
    <div class="products-panel">
      {flash}

      <div class="card">
        <h2>&#128230; Product Catalog
          <span class="category-label">Stored in IBM Db2 on IBM Power (ppc64le)</span>
        </h2>
        <div class="product-grid">
          {product_cards}
        </div>
      </div>

      <!-- Add Product Form -->
      <div class="card">
        <h2>&#43; Add New Product</h2>
        <form method="POST" action="/ui/products">
          <div class="form-row">
            <div class="form-field" style="flex:3;">
              <label>Product Name *</label>
              <input type="text" name="name" placeholder="e.g. IBM Power10 Server" required maxlength="255">
            </div>
            <div class="form-field" style="flex:1;">
              <label>Category</label>
              <input type="text" name="category" placeholder="Servers" maxlength="100">
            </div>
          </div>
          <div class="form-row">
            <div class="form-field" style="flex:2;">
              <label>Description</label>
              <input type="text" name="description" placeholder="Product description" maxlength="1000">
            </div>
            <div class="form-field" style="flex:1;">
              <label>Price ($) *</label>
              <input type="text" name="price" placeholder="999.99" required maxlength="20">
            </div>
            <div class="form-field" style="flex:1;">
              <label>Stock</label>
              <input type="text" name="stock" placeholder="10" maxlength="10">
            </div>
          </div>
          <button type="submit" class="btn btn-primary" style="width:auto;padding:8px 22px;">&#128190; Add Product to Inventory</button>
        </form>
      </div>
    </div>

    <!-- Cart + Info Panel -->
    <div class="cart-panel">
      <div class="card">
        <h2>&#128722; Your Cart</h2>
        {cart_table}
        <div class="cart-total-row">
          <span class="label">Total</span>
          <span class="amount">${cart_total:.2f}</span>
        </div>
        {checkout_btn}
      </div>

      <!-- Orders Summary -->
      <div class="card">
        <h2>&#9989; Orders <span class="orders-count">({order_count} placed)</span></h2>
        {orders_table}
      </div>

      <!-- Architecture Info -->
      <div class="card">
        <h2>&#127760; Cross-Architecture</h2>
        <table>
          <tr><th>Component</th><th>Arch</th><th>Role</th></tr>
          <tr>
            <td style="font-size:0.78rem;">E-Cart App</td>
            <td><span class="badge badge-intel">x86_64</span></td>
            <td style="font-size:0.72rem;color:#555;">App Logic</td>
          </tr>
          <tr>
            <td style="font-size:0.78rem;">IBM Db2</td>
            <td><span class="badge badge-power">ppc64le</span></td>
            <td style="font-size:0.72rem;color:#555;">Data Store</td>
          </tr>
        </table>
        <div style="margin-top:10px;font-size:0.7rem;color:#444;line-height:1.6;">
          Intel app writes cart & orders to IBM Db2 on IBM Power via Kubernetes ClusterIP DNS.
        </div>
      </div>
    </div>
  </div>

  <footer>
    IBM Power E-Cart &mdash; Intel (x86_64) App + IBM Power (ppc64le) Db2 on OpenShift Hosted Control Plane &bull; IBM Fusion HCI Demo
  </footer>
</body>
</html>"""


def build_product_cards(products):
    """Build HTML product cards for the e-cart grid."""
    if not products:
        return '<div class="empty">No products yet. Add your first product below!</div>'
    cards = []
    for p in products:
        pid, pname, pdesc, pprice, pstock = p[0], p[1], p[2] or "", float(p[3]), int(p[4])
        pcategory = p[5] if len(p) > 5 else "General"
        stock_color = "#4caf50" if pstock > 5 else ("#ff9800" if pstock > 0 else "#f44336")
        stock_label = f"{pstock} in stock" if pstock > 0 else "Out of stock"
        disabled = 'disabled' if pstock == 0 else ''
        cards.append(f"""
          <div class="product-card">
            <span class="cat-tag">{_esc(pcategory)}</span>
            <h3>{_esc(pname)}</h3>
            <div class="desc">{_esc(pdesc)}</div>
            <div class="price">${pprice:,.2f}</div>
            <div class="stock" style="color:{stock_color};">{stock_label}</div>
            <form method="POST" action="/ui/cart/add">
              <input type="hidden" name="product_id" value="{pid}">
              <div class="qty-wrap">
                <span class="qty-label">Qty:</span>
                <input type="text" name="quantity" value="1" class="qty-input" maxlength="3">
              </div>
              <button type="submit" class="btn btn-primary" {disabled}>
                &#43; Add to Cart
              </button>
            </form>
          </div>""")
    return "".join(cards)


def build_cart_table(cart_items):
    """Build HTML cart table."""
    if not cart_items:
        return '<div class="empty">Your cart is empty.</div>'
    rows = []
    for item in cart_items:
        cid = item[0]
        pname = item[1]
        qty = int(item[3])
        subtotal = float(item[4])
        rows.append(f"""
          <tr>
            <td style="font-size:0.78rem;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
                title="{_esc(pname)}">{_esc(pname)}</td>
            <td style="color:#888;text-align:center;">{qty}</td>
            <td style="color:#0062ff;white-space:nowrap;">${subtotal:,.2f}</td>
            <td>
              <form method="POST" action="/ui/cart/{cid}/remove" style="display:inline;">
                <button type="submit" class="btn btn-danger"
                  onclick="return confirm('Remove from cart?')">&#10005;</button>
              </form>
            </td>
          </tr>""")
    return f"""<table>
      <tr><th>Product</th><th>Qty</th><th>Total</th><th></th></tr>
      {"".join(rows)}
    </table>"""


def build_orders_table(orders):
    """Build HTML orders summary table."""
    if not orders:
        return '<div class="empty" style="padding:12px;">No orders yet.</div>'
    rows = []
    for o in orders[:5]:  # Show last 5 orders
        oid, total, status, created = o[0], float(o[1]), o[2], str(o[3])[:16]
        status_color = "#4caf50" if status == "completed" else "#ff9800"
        rows.append(f"""
          <tr>
            <td style="color:#555;">#{oid}</td>
            <td style="color:#0062ff;">${total:,.2f}</td>
            <td style="color:{status_color};font-size:0.72rem;">{_esc(status)}</td>
            <td style="color:#444;font-size:0.68rem;">{_esc(created)}</td>
          </tr>""")
    return f"""<table>
      <tr><th>#</th><th>Total</th><th>Status</th><th>Date</th></tr>
      {"".join(rows)}
    </table>"""


def get_orders():
    """Fetch recent orders from Db2."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, total_amount, status, created_at "
                "FROM orders ORDER BY id DESC FETCH FIRST 10 ROWS ONLY"
            )
            rows = cur.fetchall()
            cur.close()
        return rows, None
    except Exception as exc:
        return [], str(exc)


# ---------------------------------------------------------------------------
# Browser UI routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Main e-cart UI."""
    flash_msg  = _esc(request.args.get("msg", ""))
    flash_type = request.args.get("type", "success")
    if flash_type not in ("success", "error"):
        flash_type = "success"
    flash_html = f'<div class="flash {flash_type}">{flash_msg}</div>' if flash_msg else ""

    db_ok, db_version = get_db_info()
    db_status = "Connected ✓" if db_ok else "Disconnected ✗"
    db_color  = "#4caf50" if db_ok else "#f44336"

    products, _ = get_products()
    cart_items, _ = get_cart()
    orders, _ = get_orders()
    cart_total = get_cart_total()
    cart_count = sum(int(i[3]) for i in cart_items)

    product_cards = build_product_cards(products)
    cart_table    = build_cart_table(cart_items)
    orders_table  = build_orders_table(orders)
    checkout_btn  = (
        '<form method="POST" action="/ui/checkout" style="margin-top:10px;">'
        '<button type="submit" class="btn btn-success">&#9989; Place Order</button>'
        '</form>'
        if cart_items else ""
    )

    page = HTML_TEMPLATE.format(
        app_node=_esc(os.environ.get("NODE_NAME", "unknown")),
        db_status=db_status,
        db_color=db_color,
        db_version=_esc(db_version[:80]) if db_ok else "",
        flash=flash_html,
        product_cards=product_cards,
        cart_table=cart_table,
        orders_table=orders_table,
        cart_total=cart_total,
        cart_count=cart_count,
        cart_plural="s" if cart_count != 1 else "",
        checkout_btn=checkout_btn,
        order_count=len(orders),
    )
    return page, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/ui/products", methods=["POST"])
def ui_add_product():
    """Add a new product to the Db2 catalog."""
    name        = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    price_str   = request.form.get("price", "0").strip()
    stock_str   = request.form.get("stock", "0").strip()
    category    = request.form.get("category", "General").strip() or "General"
    if not name:
        return redirect(url_for("index", msg="Product name is required.", type="error"))
    try:
        price = float(price_str)
        stock = int(stock_str) if stock_str else 0
    except ValueError:
        return redirect(url_for("index", msg="Invalid price or stock value.", type="error"))
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO products (name, description, price, stock, category) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, description, price, stock, category)
            )
            cur.close()
        return redirect(url_for("index",
                                msg=f"Product '{name}' added to IBM Db2 on IBM Power ✓",
                                type="success"))
    except Exception as exc:
        return redirect(url_for("index", msg=f"Error: {exc}", type="error"))


@app.route("/ui/cart/add", methods=["POST"])
def ui_add_to_cart():
    """Add a product to the shopping cart in Db2."""
    product_id = request.form.get("product_id", "").strip()
    qty_str    = request.form.get("quantity", "1").strip()
    try:
        qty = max(1, int(qty_str))
    except ValueError:
        qty = 1
    try:
        with get_db() as conn:
            cur = conn.cursor()
            # Check if product already in cart
            cur.execute(
                "SELECT id, quantity FROM cart_items WHERE product_id = ?",
                (product_id,)
            )
            row = cur.fetchone()
            if row:
                new_qty = int(row[1]) + qty
                cur.execute(
                    "UPDATE cart_items SET quantity = ? WHERE id = ?",
                    (new_qty, row[0])
                )
            else:
                cur.execute(
                    "INSERT INTO cart_items (product_id, quantity) VALUES (?, ?)",
                    (product_id, qty)
                )
            cur.close()
        return redirect(url_for("index", msg="Item added to cart ✓", type="success"))
    except Exception as exc:
        return redirect(url_for("index", msg=f"Error: {exc}", type="error"))


@app.route("/ui/cart/<int:cart_id>/remove", methods=["POST"])
def ui_remove_from_cart(cart_id):
    """Remove an item from the shopping cart."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM cart_items WHERE id = ?", (cart_id,))
            cur.close()
        return redirect(url_for("index", msg="Item removed from cart ✓", type="success"))
    except Exception as exc:
        return redirect(url_for("index", msg=f"Error: {exc}", type="error"))


@app.route("/ui/checkout", methods=["POST"])
def ui_checkout():
    """Place an order — moves cart items to orders table in Db2."""
    try:
        total = get_cart_total()
        if total == 0:
            return redirect(url_for("index", msg="Cart is empty.", type="error"))
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO orders (total_amount, status) VALUES (?, ?)",
                (total, "completed")
            )
            cur.execute("DELETE FROM cart_items")
            cur.close()
        return redirect(url_for("index",
                                msg=f"Order placed! Total: ${total:,.2f} ✓",
                                type="success"))
    except Exception as exc:
        return redirect(url_for("index", msg=f"Checkout error: {exc}", type="error"))


# ---------------------------------------------------------------------------
# REST API routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/ready")
def ready():
    """Readiness probe — single fast connection attempt, no retry."""
    try:
        conn = ibm_db_dbi.connect(DB2_CONN_STR, "", "")
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM SYSIBM.SYSDUMMY1")
        cur.close()
        conn.close()
        return jsonify({"status": "ready", "db": "connected"}), 200
    except Exception as exc:
        return jsonify({"status": "not ready", "db": str(exc)}), 503


@app.route("/arch")
def arch_info():
    db_ok, db_version = get_db_info()
    return jsonify({
        "heterogeneous_demo": {
            "app_server": {
                "role": "IBM Power E-Cart Application Server",
                "architecture": platform.machine(),
                "platform": platform.platform(),
                "node": os.environ.get("NODE_NAME", "unknown"),
                "pod": os.environ.get("POD_NAME", "unknown"),
                "arch_label": "x86_64 (Intel)",
            },
            "database": {
                "role": "IBM Db2 Community Edition",
                "architecture": "ppc64le (IBM Power)",
                "host": "db2-service.db2-shop-demo.svc.cluster.local",
                "port": 50000,
                "connected": db_ok,
                "db2_version": db_version,
                "image": "cp.icr.io/cp/db2/db2u:latest",
            },
        }
    })


@app.route("/products", methods=["GET"])
def get_products_api():
    products, err = get_products()
    if err:
        return jsonify({"error": err}), 500
    items = [
        {"id": p[0], "name": p[1], "description": p[2],
         "price": float(p[3]), "stock": int(p[4]),
         "category": p[5] if len(p) > 5 else "General"}
        for p in products
    ]
    return jsonify({"products": items, "count": len(items)}), 200


@app.route("/cart", methods=["GET"])
def get_cart_api():
    cart_items, err = get_cart()
    if err:
        return jsonify({"error": err}), 500
    items = [
        {"cart_id": c[0], "product_name": c[1], "price": float(c[2]),
         "quantity": int(c[3]), "subtotal": float(c[4]), "product_id": c[5]}
        for c in cart_items
    ]
    return jsonify({"cart": items, "total": get_cart_total(), "count": len(items)}), 200


@app.route("/orders", methods=["GET"])
def get_orders_api():
    orders, err = get_orders()
    if err:
        return jsonify({"error": err}), 500
    result = [
        {"id": o[0], "total_amount": float(o[1]),
         "status": o[2], "created_at": str(o[3])}
        for o in orders
    ]
    return jsonify({"orders": result, "count": len(result)}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)