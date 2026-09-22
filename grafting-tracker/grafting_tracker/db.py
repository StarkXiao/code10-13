"""SQLite 存储层：表结构定义与基础读写。"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cultivar (            -- 接穗品种
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    species TEXT NOT NULL DEFAULT '',            -- 树种，如 苹果/梨
    note    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rootstock (           -- 砧木
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS plot (                -- 苗床/地块
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS graft_batch (         -- 嫁接批次
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no     TEXT NOT NULL UNIQUE,           -- 批次号
    cultivar_id  INTEGER NOT NULL REFERENCES cultivar(id),
    rootstock_id INTEGER NOT NULL REFERENCES rootstock(id),
    method       TEXT NOT NULL,                  -- 嫁接方法：切接/劈接/舌接/T形芽接…
    graft_date   TEXT NOT NULL,                  -- ISO 日期 YYYY-MM-DD
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    plot_id      INTEGER REFERENCES plot(id),
    operator     TEXT NOT NULL DEFAULT '',
    scion_source TEXT NOT NULL DEFAULT '',       -- 穗条来源
    note         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS survival_survey (     -- 成活调查（同批次可多次，取最新）
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES graft_batch(id),
    survey_date TEXT NOT NULL,
    alive_count INTEGER NOT NULL CHECK (alive_count >= 0),
    surveyor    TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    UNIQUE (batch_id, survey_date)
);

CREATE TABLE IF NOT EXISTS env_record (          -- 逐日温湿度（按苗床）
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plot_id      INTEGER NOT NULL REFERENCES plot(id),
    record_date  TEXT NOT NULL,
    temp_avg     REAL,
    temp_max     REAL,
    temp_min     REAL,
    humidity_avg REAL,
    UNIQUE (plot_id, record_date)
);

CREATE TABLE IF NOT EXISTS scion_spec (          -- 品种穗条参数与来年目标
    cultivar_id      INTEGER PRIMARY KEY REFERENCES cultivar(id),
    grafts_per_scion REAL NOT NULL DEFAULT 4.0,  -- 每根穗条可嫁接株数
    target_plants    INTEGER                     -- 来年目标成品苗；NULL 按历史量推算
);

CREATE INDEX IF NOT EXISTS idx_batch_combo   ON graft_batch (cultivar_id, rootstock_id);
CREATE INDEX IF NOT EXISTS idx_survey_batch  ON survival_survey (batch_id);
CREATE INDEX IF NOT EXISTS idx_env_plot_date ON env_record (plot_id, record_date);
"""

_NAME_TABLES = {"cultivar", "rootstock", "plot"}


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开（必要时创建）数据库连接。"""
    if str(db_path) != ":memory:":
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        db_path = path
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """建表（幂等）并返回连接。"""
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def get_or_create(conn: sqlite3.Connection, table: str, name: str, **fields) -> int:
    """按名称查字典表，不存在则插入，返回 id。"""
    if table not in _NAME_TABLES:
        raise ValueError(f"未知字典表: {table}")
    row = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cols = ["name", *fields.keys()]
    cur = conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
        (name, *fields.values()),
    )
    return cur.lastrowid


def add_batch(conn: sqlite3.Connection, batch_no: str, cultivar_id: int,
              rootstock_id: int, method: str, graft_date: str, quantity: int,
              plot_id: int | None = None, operator: str = "",
              scion_source: str = "", note: str = "") -> int:
    """录入嫁接批次，返回批次 id。"""
    if quantity <= 0:
        raise ValueError(f"嫁接株数必须为正数: {quantity}")
    cur = conn.execute(
        "INSERT INTO graft_batch (batch_no, cultivar_id, rootstock_id, method,"
        " graft_date, quantity, plot_id, operator, scion_source, note)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (batch_no, cultivar_id, rootstock_id, method, graft_date, quantity,
         plot_id, operator, scion_source, note),
    )
    return cur.lastrowid


def add_survey(conn: sqlite3.Connection, batch_id: int, survey_date: str,
               alive_count: int, surveyor: str = "", note: str = "") -> int:
    """录入成活调查；同一批次同一天重复录入时覆盖。"""
    row = conn.execute("SELECT quantity FROM graft_batch WHERE id = ?",
                       (batch_id,)).fetchone()
    if row is None:
        raise ValueError(f"批次不存在: id={batch_id}")
    if alive_count < 0 or alive_count > row["quantity"]:
        raise ValueError(
            f"成活数 {alive_count} 超出合理范围 [0, {row['quantity']}]")
    cur = conn.execute(
        "INSERT INTO survival_survey (batch_id, survey_date, alive_count, surveyor, note)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT (batch_id, survey_date) DO UPDATE SET"
        "   alive_count = excluded.alive_count,"
        "   surveyor    = excluded.surveyor,"
        "   note        = excluded.note",
        (batch_id, survey_date, alive_count, surveyor, note),
    )
    return cur.lastrowid


def upsert_env(conn: sqlite3.Connection, plot_id: int, record_date: str,
               temp_avg: float, temp_max: float, temp_min: float,
               humidity_avg: float) -> None:
    """录入/覆盖某苗床某日的温湿度记录。"""
    conn.execute(
        "INSERT INTO env_record (plot_id, record_date, temp_avg, temp_max, temp_min, humidity_avg)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT (plot_id, record_date) DO UPDATE SET"
        "   temp_avg = excluded.temp_avg, temp_max = excluded.temp_max,"
        "   temp_min = excluded.temp_min, humidity_avg = excluded.humidity_avg",
        (plot_id, record_date, temp_avg, temp_max, temp_min, humidity_avg),
    )


def set_scion_spec(conn: sqlite3.Connection, cultivar_id: int,
                   grafts_per_scion: float | None = None,
                   target_plants: int | None = None) -> None:
    """设置品种穗条参数；未给出的字段保留原值。"""
    row = conn.execute(
        "SELECT grafts_per_scion AS g, target_plants AS t"
        " FROM scion_spec WHERE cultivar_id = ?", (cultivar_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO scion_spec (cultivar_id, grafts_per_scion, target_plants)"
            " VALUES (?,?,?)",
            (cultivar_id,
             grafts_per_scion if grafts_per_scion is not None else 4.0,
             target_plants),
        )
    else:
        conn.execute(
            "UPDATE scion_spec SET grafts_per_scion = ?, target_plants = ?"
            " WHERE cultivar_id = ?",
            (grafts_per_scion if grafts_per_scion is not None else row["g"],
             target_plants if target_plants is not None else row["t"],
             cultivar_id),
        )
