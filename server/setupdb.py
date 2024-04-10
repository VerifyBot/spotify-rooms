import sqlite3

# create the sqlite database file and connect to it
conn = sqlite3.connect('database.db')

cur = conn.cursor()

# setup the users table
cur.execute("""DROP TABLE IF EXISTS users;""")
cur.execute("""
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL,
  password TEXT NOT NULL
);
""")

cur.close()
conn.close()
