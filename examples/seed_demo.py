from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).parent


def seed_customers() -> None:
    path = ROOT / "customers.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS customers;

            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                city TEXT NOT NULL,
                signup_date TEXT NOT NULL,
                password_hash TEXT
            );

            INSERT INTO customers (id, name, status, city, signup_date, password_hash) VALUES
                (1, 'Asha Mehta', 'active', 'Mumbai', '2026-06-04', 'blocked-demo-value'),
                (2, 'Rohan Singh', 'inactive', 'Delhi', '2026-05-12', 'blocked-demo-value'),
                (3, 'Neha Rao', 'active', 'Bengaluru', '2026-06-19', 'blocked-demo-value');
            """
        )


def seed_orders() -> None:
    path = ROOT / "orders.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS orders;
            DROP TABLE IF EXISTS order_items;
            DROP TABLE IF EXISTS products;

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                total_amount REAL NOT NULL,
                created_at TEXT NOT NULL,
                card_token TEXT
            );

            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL
            );

            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL
            );

            INSERT INTO orders (id, customer_id, status, total_amount, created_at, card_token) VALUES
                (101, 1, 'completed', 1200.0, '2026-06-20', 'blocked-demo-value'),
                (102, 1, 'refunded', 450.0, '2026-06-22', 'blocked-demo-value'),
                (103, 3, 'completed', 780.0, '2026-07-01', 'blocked-demo-value');

            INSERT INTO products (id, name, category) VALUES
                (201, 'Analytics Pro', 'software'),
                (202, 'Support Plus', 'service');

            INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES
                (301, 101, 201, 1, 1200.0),
                (302, 102, 202, 3, 150.0),
                (303, 103, 201, 1, 780.0);
            """
        )


def main() -> None:
    seed_customers()
    seed_orders()
    print("Seeded examples/customers.db and examples/orders.db")


if __name__ == "__main__":
    main()
