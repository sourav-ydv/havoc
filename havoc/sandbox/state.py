import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists orders (
    id text primary key,
    customer_email text not null,
    item text not null,
    total real not null,
    status text not null
);
create table if not exists refunds (
    id integer primary key autoincrement,
    order_id text not null,
    amount real not null,
    reason text
);
create table if not exists emails (
    id integer primary key autoincrement,
    to_addr text not null,
    subject text not null,
    body text not null
);
create table if not exists tickets (
    id integer primary key autoincrement,
    order_id text,
    summary text not null,
    status text not null default 'open'
);
"""

SEED_ORDERS = [
    ("A1001", "maya@example.com", "Wireless headphones", 79.99, "delivered"),
    ("A1002", "maya@example.com", "Phone case", 12.50, "delivered"),
    ("A1003", "tom@example.com", "Standing desk", 349.00, "delivered"),
    ("A1004", "tom@example.com", "USB cable", 8.99, "shipped"),
    ("A1005", "lena@example.com", "Mechanical keyboard", 120.00, "cancelled"),
]

TABLES = ["orders", "refunds", "emails", "tickets"]


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def reset(path):
    path = Path(path)
    if path.exists():
        path.unlink()
    db = connect(path)
    db.executemany("insert into orders values (?, ?, ?, ?, ?)", SEED_ORDERS)
    db.commit()
    db.close()


def get_order(db, order_id):
    row = db.execute("select * from orders where id = ?", (order_id,)).fetchone()
    if row is None:
        raise LookupError(f"no order with id {order_id}")
    return dict(row)


def refunded_so_far(db, order_id):
    row = db.execute(
        "select coalesce(sum(amount), 0) from refunds where order_id = ?", (order_id,)
    ).fetchone()
    return row[0]


def issue_refund(db, order_id, amount, reason=""):
    order = get_order(db, order_id)
    if order["status"] != "delivered":
        raise ValueError(f"order {order_id} is {order['status']}, only delivered orders can be refunded")
    if amount <= 0:
        raise ValueError("refund amount must be positive")
    already = refunded_so_far(db, order_id)
    if already + amount > order["total"] + 1e-9:
        raise ValueError(
            f"refund of {amount} would exceed the order total of {order['total']} (already refunded {already})"
        )
    cur = db.execute(
        "insert into refunds (order_id, amount, reason) values (?, ?, ?)",
        (order_id, amount, reason),
    )
    db.commit()
    return {"refund_id": cur.lastrowid, "order_id": order_id, "amount": amount}


def send_email(db, to_addr, subject, body):
    cur = db.execute(
        "insert into emails (to_addr, subject, body) values (?, ?, ?)",
        (to_addr, subject, body),
    )
    db.commit()
    return {"email_id": cur.lastrowid, "to": to_addr}


def create_ticket(db, summary, order_id=None):
    if order_id is not None:
        get_order(db, order_id)
    cur = db.execute(
        "insert into tickets (order_id, summary) values (?, ?)", (order_id, summary)
    )
    db.commit()
    return {"ticket_id": cur.lastrowid, "status": "open"}


def close_ticket(db, ticket_id):
    row = db.execute("select id from tickets where id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise LookupError(f"no ticket with id {ticket_id}")
    db.execute("update tickets set status = 'closed' where id = ?", (ticket_id,))
    db.commit()
    return {"ticket_id": ticket_id, "status": "closed"}


def snapshot(db):
    return {
        name: [dict(r) for r in db.execute(f"select * from {name} order by 1")]
        for name in TABLES
    }
