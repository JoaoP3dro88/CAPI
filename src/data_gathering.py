from __future__ import annotations

import os
import time
import urllib
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args, **_kwargs) -> bool:
        # Optional dependency: environment variables still work without .env support.
        return False


CURRENT_DIR         = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent

# Load .env from common locations without overriding already-exported variables.
load_dotenv(REPO_ROOT / ".env")
load_dotenv(CURRENT_DIR / ".env")


def get_engine(
    server: str | None = None,
    database: str | None = None,
    username: str | None = None,
    password: str | None = None,
    driver: str | None = None,
):
    server = server or os.environ.get("SIM_DB_SERVER")
    database = database or os.environ.get("SIM_DB_NAME")
    username = username or os.environ.get("SIM_DB_USER")
    password = password or os.environ.get("SIM_DB_PASSWORD")
    driver = driver or os.environ.get("SIM_DB_DRIVER", "ODBC Driver 17 for SQL Server")

    missing = [key for key, value in {
        "SIM_DB_SERVER": server,
        "SIM_DB_NAME": database,
        "SIM_DB_USER": username,
        "SIM_DB_PASSWORD": password,
    }.items() if not value]
    if missing:
        raise ValueError(
            "Missing database configuration. Set env vars: " + ", ".join(missing)
        )

    connection_string = (
        f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};UID={username};PWD={password}"
        ";LoginTimeout=15;QueryTimeout=90"
    )
    connection_string = urllib.parse.quote_plus(connection_string)
    engine = create_engine(
        f"mssql+pyodbc:///?odbc_connect={connection_string}",
        pool_size=30,          # number of persistent connections in the pool
        max_overflow=50,       # extra connections allowed beyond pool_size
        pool_timeout=60,       # max seconds to wait for a connection from the pool
        pool_pre_ping=True,    # discard stale connections before using them
    )
    return engine


def _normalize_ids(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw).strip()
        if not value or value.lower() in {"nan", "none", "null"}:
            continue
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _normalize_optional_text(value) -> str | None:
    if value is None:
        return None
    if value != value:
        return None
    text_value = str(value).strip()
    if not text_value or text_value.lower() in {"nan", "none", "null"}:
        return None
    return text_value


def _machine_to_line(machine_id: str) -> str:
    parts = machine_id.strip().split()
    if len(parts) >= 2 and parts[0].upper() == "AGIE":
        try:
            num = int(parts[-1])
            if 1 <= num <= 10:
                return "Agie1"
            if 11 <= num <= 20:
                return "Agie2"
            if 21 <= num <= 30:
                return "Agie3"
            if 31 <= num <= 40:
                return "Agie4"
            if 41 <= num <= 50:
                return "Agie5"
        except ValueError:
            pass
    return "Unknown"


def get_eligible_part_numbers(df_restrictions: pd.DataFrame) -> set[str]:
    part_processes: dict[str, set[str]] = {}
    for row in df_restrictions.to_dict("records"):
        part_number_id = _normalize_optional_text(row.get("part_number_id"))
        process_type = _normalize_optional_text(row.get("process_type"))
        if not part_number_id or not process_type:
            continue
        part_processes.setdefault(part_number_id, set()).add(process_type.upper())
    return {
        part_number_id
        for part_number_id, processes in part_processes.items()
        if "LASER" in processes and "EDM" in processes
    }


def fetch_production_seed_rows(
    engine,
    eligible_part_numbers: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    machine_rows: list[dict] = []
    for row in fetch_machine_status(engine).to_dict("records"):
        machine_id = _normalize_optional_text(row.get("machine_id"))
        part_number_id = _normalize_optional_text(row.get("part_number_id"))
        if not machine_id or not part_number_id:
            continue
        if eligible_part_numbers and part_number_id not in eligible_part_numbers:
            continue
        machine_rows.append(
            {
                "machine_id": machine_id,
                "part_number_id": part_number_id,
                "order_id": _normalize_optional_text(row.get("order_id")),
                "quantity": int(float(row.get("quantity") or 0)),
                "event_date": row.get("event_date"),
            }
        )

    buffer_rows: list[dict] = []
    for row in fetch_buffer_stock(engine).to_dict("records"):
        machine_id = _normalize_optional_text(row.get("machine_id"))
        part_number_id = _normalize_optional_text(row.get("part_number_id"))
        if not machine_id or not part_number_id:
            continue
        if eligible_part_numbers and part_number_id not in eligible_part_numbers:
            continue
        line_id = _machine_to_line(machine_id)
        if line_id == "Unknown":
            continue
        buffer_rows.append(
            {
                "line_id": line_id,
                "machine_id": machine_id,
                "serial_number": _normalize_optional_text(row.get("serial_number")),
                "part_number_id": part_number_id,
                "order_id": _normalize_optional_text(row.get("order_id")),
                "quantity": int(float(row.get("quantity") or 0)),
            }
        )

    return machine_rows, buffer_rows


def _build_in_filter(
    values: list[str] | None,
    *,
    column: str,
    parameter_prefix: str,
    connector: str,
) -> tuple[str, dict[str, str]]:
    normalized = _normalize_ids(values)
    if not normalized:
        return "", {}

    placeholders: list[str] = []
    params: dict[str, str] = {}
    for idx, value in enumerate(normalized):
        name = f"{parameter_prefix}_{idx}"
        placeholders.append(f":{name}")
        params[name] = value
    clause = f"{connector} {column} IN ({', '.join(placeholders)})"
    return clause, params


def _read_filter_csv(csv_path: str) -> tuple[list[str], list[str]]:
    df = pd.read_csv(
        csv_path,
        sep=None,
        engine="python",
        encoding="utf-8-sig",
        dtype={"order_id": str, "part_number_id": str},
    )
    df.columns = [c.strip() for c in df.columns]

    required = {"order_id", "part_number_id"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {sorted(missing)}. "
            f"Columns found: {list(df.columns)}"
        )

    order_ids = (
        df["order_id"].dropna().astype(str).str.strip().loc[lambda s: s.ne("")]
        .drop_duplicates().tolist()
    )
    part_number_ids = (
        df["part_number_id"].dropna().astype(str).str.strip().loc[lambda s: s.ne("")]
        .drop_duplicates().tolist()
    )
    return order_ids, part_number_ids


def fetch_orders(engine, order_ids: list[str] | None = None) -> pd.DataFrame:
    extra_filter, params = _build_in_filter(
        order_ids,
        column="ORDER_NUMBER",
        parameter_prefix="order_id",
        connector="AND",
    )

    query = f"""
    SELECT
        MATERIAL as part_number_id,
        QUANTITY_ORDER as real_qtty,
        ORDER_NUMBER as order_id,
        END_DATE as update_date
    FROM silver_orders.orders_10digit
    WHERE MATERIAL IN (
        SELECT DISTINCT TYPE_NUMBER
        FROM silver_type_number.nozzle_restrictions
    )
    {extra_filter}
    ORDER BY
        CASE
            WHEN END_DATE IS NOT NULL AND CAST(END_DATE AS date) = CAST(GETDATE() AS date) THEN 0
            WHEN END_DATE IS NOT NULL AND CAST(END_DATE AS date) < CAST(GETDATE() AS date) THEN 1
            WHEN END_DATE IS NOT NULL AND CAST(END_DATE AS date) > CAST(GETDATE() AS date) THEN 2
            ELSE 3
        END,
        END_DATE ASC,
        ORDER_NUMBER ASC
    """

    return pd.read_sql(text(query), engine, params=params)


def fetch_restrictions(engine, part_number_ids: list[str] | None = None) -> pd.DataFrame:
    part_filter, params = _build_in_filter(
        part_number_ids,
        column="X.part_number_id",
        parameter_prefix="part_number_id",
        connector="WHERE",
    )

    query = f"""
    WITH Machines AS (
        SELECT DISTINCT
            machine_type = CASE
                WHEN MACHINE LIKE 'MACHINE%' THEN RIGHT(MACHINE, 4)
                ELSE PROCESS
            END,
            MACHINE as machine_id,
            process_type = CASE
                WHEN PROCESS LIKE '%AGIE%' THEN 'EDM'
                WHEN PROCESS LIKE '%LM%' THEN 'LASER'
            END,
            line_id = CASE
                WHEN MACHINE LIKE 'AGIE%' THEN
                    CASE
                        WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 1 AND 10 THEN 'Agie1'
                        WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 11 AND 20 THEN 'Agie2'
                        WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 21 AND 30 THEN 'Agie3'
                        WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 31 AND 40 THEN 'Agie4'
                        WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 41 AND 50 THEN 'Agie5'
                    END
                WHEN MACHINE LIKE 'MACHINE%' THEN 'LaserM'
                ELSE NULL
            END
        FROM silver_type_number.nozzle_restrictions
    ),
    Restrictions AS (
        SELECT
            TYPE_NUMBER as part_number_id,
            machine_type = RIGHT(machine_id, 4),
            machine_id,
            process_time_seconds_per_part = CASE
                WHEN machine_id = 'MACHINE_3206' THEN 5.1
                WHEN machine_id = 'MACHINE_1541' THEN 5.1
                WHEN machine_id = 'MACHINE_1659' THEN 7.5
                WHEN machine_id = 'MACHINE_5056' THEN 8
                WHEN machine_id = 'MACHINE_9286' THEN 6
            END
        FROM (
            SELECT
                TYPE_NUMBER,
                MACHINE_3206,
                MACHINE_1541,
                MACHINE_1659,
                MACHINE_9286,
                MACHINE_5056
            FROM bronze_Excel.restriction_laser_marking_sheet1
        ) AS SourceTable
        UNPIVOT (
            Value FOR machine_id IN (MACHINE_3206, MACHINE_1541, MACHINE_1659, MACHINE_9286, MACHINE_5056)
        ) AS UnpivotTable
        WHERE Value = 1

        UNION ALL

        SELECT
            TYPE_NUMBER as part_number_id,
            PROCESS as machine_type,
            MACHINE as machine_id,
            PROCESS_TIME as process_time_seconds_per_part
        FROM gold.simulation_agie_process_time
    )
    SELECT
        X.part_number_id,
        X.machine_type,
        X.process_type,
        X.process_time_seconds_per_part
    FROM (
        SELECT
            R.part_number_id,
            R.machine_type,
            M.process_type,
            AVG(R.process_time_seconds_per_part) as process_time_seconds_per_part
        FROM Restrictions R
        LEFT JOIN Machines M
            ON R.machine_type = M.machine_type
            AND R.machine_id = M.machine_id
        GROUP BY
            R.part_number_id,
            R.machine_type,
            M.process_type
    ) X
    {part_filter}
    ORDER BY X.part_number_id, X.machine_type
    """

    return pd.read_sql(text(query), engine, params=params)


def fetch_machines(engine) -> pd.DataFrame:
    query = """
    SELECT DISTINCT
        machine_type = CASE
            WHEN MACHINE LIKE 'MACHINE%' THEN RIGHT(MACHINE, 4)
            ELSE PROCESS
        END,
        MACHINE as machine_id,
        process_type = CASE
            WHEN PROCESS LIKE '%AGIE%' THEN 'EDM'
            WHEN PROCESS LIKE '%LM%' THEN 'LASER'
        END,
        line_id = CASE
            WHEN MACHINE LIKE 'AGIE%' THEN
                CASE
                    WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 1 AND 10 THEN 'Agie1'
                    WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 11 AND 20 THEN 'Agie2'
                    WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 21 AND 30 THEN 'Agie3'
                    WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 31 AND 40 THEN 'Agie4'
                    WHEN CAST(SUBSTRING(MACHINE, CHARINDEX(' ', MACHINE) + 1, LEN(MACHINE)) AS INT) BETWEEN 41 AND 50 THEN 'Agie5'
                END
            WHEN MACHINE LIKE 'MACHINE%' THEN 'LaserM'
            ELSE NULL
        END
    FROM silver_type_number.nozzle_restrictions
    ORDER BY MACHINE ASC
    """
    return pd.read_sql(query, engine)


def fetch_destination_routing(engine) -> pd.DataFrame:
    """Fetch the TypeNumber → final market destination routing table.

    Returns one row per nozzle TypeNumber with its destination market.

    Columns returned:
        TYPE_NUMBER  – nozzle TYPE_NUMBER
        DEFINITION     – 'COM_HE' or 'SEM_HE'

    NOTE: Replace the stub query below with the real table/view once available.
    The stub returns an empty DataFrame so the feature works without data
    (all TypeNumbers default to 'SEM_HE' when not in the routing table).
    """
    
    query = """
    SELECT
        [TYPE_NUMBER]
        ,[DEFINITION]
    FROM 
        [AgieProductionPlan].[silver_quality].[b2b_type_number_he_status_view]
    """
    return pd.read_sql(text(query), engine)


def fetch_hard_stage_market(engine) -> pd.DataFrame:
    """Fetch Hard Stage Market inventory.

    Returns one row per nozzle part number with its associated body number and
    current Hard Stage stock quantity.

    Columns returned:
        part_number_id   – nozzle TYPE_NUMBER
        body_number_id   – body BODY_TYPE_NUMBER
        qtty_hardstage   – current Hard Stage stock (pieces)
        max_date_hardstage – last update timestamp for the Hard Stage stock
    """
    query = """
    SELECT
        TYPE_NUMBER        as part_number_id,
        BODY_TYPE_NUMBER   as body_number_id,
        QUANTITY_HARDSTAGE as qtty_hardstage,
        MAX_DATE_HARDSTAGE as max_date_hardstage
    FROM silver_production.inventory
    """
    return pd.read_sql(query, engine)


def fetch_machine_status(engine) -> pd.DataFrame:
    """Fetch the current status of ALL 50 AGIE machines.

    Returns one row per machine.  Machines with a tray currently loaded
    (Entrada without a matching Saída by SERIAL_NUMBER) are RUNNING.
    All other machines appear with NULL part_number_id / serial_number
    (treated as IDLE by the caller).

    Source of truth for the machine list: silver_type_number.nozzle_restrictions
    (50 machines, AGIE 01 – AGIE 50).

    Columns returned:
        machine_id          – e.g. "AGIE 08"
        machine_type        – PROCESS field, e.g. "AGIE OP2" (NULL if idle)
        part_number_id      – TYPE_NUMBER currently loaded (NULL if idle)
        prev_part_number_id – TYPE_NUMBER of last completed run (NULL if none)
        quantity            – batch quantity (NULL if idle)
        serial_number       – tray serial number (NULL if idle)
        event_date          – datetime of the Entrada event (NULL if idle)
        stage               – production stage (NULL if idle)
    """
    query = """
    WITH
    -- ── Performance fix: replace correlated NOT EXISTS / EXISTS with pre-computed ──
    -- aggregates. NOT EXISTS is O(n²) without an index; a GROUP BY + JOIN is O(n).
    -- max_event_per_serial: the most recent event (any stage) per serial.
    -- If a tray's Entrada IS that max event, no later event exists → tray still inside.
    -- Scoped to the trusted window (2026-04-25) so SQL Server scans only recent rows,
    -- not the entire historical table.
    max_event_per_serial AS (
        SELECT SERIAL_NUMBER, MAX(DATE) AS max_event_date
        FROM silver_quality.b2b_machine_status
        WHERE QUANTITY <> 0
          AND DATE >= '2026-04-25'
        GROUP BY SERIAL_NUMBER
    ),
    -- saida_agie_per_serial: the most recent Saída per serial number.
    -- If max_saida_date > Entrada.DATE then a completed Saída exists for that run.
    -- Also scoped to trusted window to avoid full historical scan.
    -- NOTE: Saída records do NOT have MACHINE or STAGE populated — the operator
    -- only registers the serial on exit. No STAGE filter here.
    saida_agie_per_serial AS (
        SELECT SERIAL_NUMBER, MAX(DATE) AS max_saida_date
        FROM silver_quality.b2b_machine_status
        WHERE STATE    = 'Sa\u00edda'
          AND QUANTITY <> 0
          AND DATE    >= '2026-04-25'
        GROUP BY SERIAL_NUMBER
    ),
    all_machines AS (
        -- Canonical list of all 50 physical AGIE machines
        SELECT DISTINCT MACHINE AS machine_id
        FROM silver_type_number.nozzle_restrictions
        WHERE MACHINE LIKE 'AGIE %'
    ),
    all_entradas AS (
        -- All Entrada events at 03 - Agie since 2026-04-25 (trusted window)
        SELECT
            MACHINE            AS machine_id,
            PROCESS            AS machine_type,
            TYPE_NUMBER        AS part_number_id,
            QUANTITY           AS quantity,
            SERIAL_NUMBER      AS serial_number,
            DATE               AS event_date,
            STAGE              AS stage
        FROM silver_quality.b2b_machine_status
        WHERE STATE   = 'Entrada'
          AND MACHINE <> 'AGIE UNKNOWN'
          AND MACHINE IS NOT NULL
          AND STAGE   = '03 - Agie'
          AND DATE   >= '2026-04-25'
          AND QUANTITY <> 0
          AND SERIAL_NUMBER NOT LIKE '%[a-z]%'
    ),
    last_entrada_per_machine AS (
        -- The single most recent Entrada per machine (since 2026-04-25).
        -- Ghost-entry fix: if the most recent Entrada HAS a Saída (completed),
        -- the machine is idle — even if older ghost entries exist without a Saída.
        -- This prevents stale ghosts from being reported as "currently running"
        -- when a newer, properly-closed run already occurred on the same machine.
        SELECT machine_id, MAX(event_date) AS last_entrada_date
        FROM all_entradas
        GROUP BY machine_id
    ),
    current_pn_per_machine AS (
        -- The TYPE_NUMBER of the most recent Entrada per machine defines the "current
        -- production session". Any older Entrada with a DIFFERENT TYPE_NUMBER that never
        -- got a Saída is considered a ghost/unregistered exit and is ignored.
        SELECT machine_id, part_number_id AS current_part_number_id
        FROM (
            SELECT machine_id, part_number_id,
                   ROW_NUMBER() OVER (PARTITION BY machine_id ORDER BY event_date DESC) AS rn
            FROM all_entradas
        ) t
        WHERE rn = 1
    ),
    active_candidates AS (
        -- Entradas that:
        --   1. Match the machine's current PN session (different-PN ghost trays are ignored)
        --   2. Have NO subsequent event (replaced NOT EXISTS with INNER JOIN on max_event_date)
        SELECT
            ae.machine_id, ae.machine_type, ae.part_number_id, ae.quantity,
            ae.serial_number, ae.event_date, ae.stage,
            ROW_NUMBER() OVER (
                PARTITION BY ae.machine_id
                ORDER BY ae.event_date DESC
            ) AS rn
        FROM all_entradas ae
        INNER JOIN current_pn_per_machine cp
               ON  cp.machine_id             = ae.machine_id
               AND cp.current_part_number_id  = ae.part_number_id
        INNER JOIN max_event_per_serial mx        -- replaces NOT EXISTS (O(n²) → O(n))
               ON  mx.SERIAL_NUMBER   = ae.serial_number
               AND mx.max_event_date  = ae.event_date  -- no later event → tray still inside
        INNER JOIN last_entrada_per_machine lm    -- ghost-entry fix: only the most recent
               ON  lm.machine_id       = ae.machine_id  -- entrada per machine is a candidate.
               AND lm.last_entrada_date = ae.event_date  -- older ghosts are ignored.
        WHERE ae.event_date >= DATEADD(HOUR, -24, GETDATE())  -- ghost timeout: 24h
    ),
    active AS (
        -- Most recent tray still inside the machine (among all currently-inside trays)
        SELECT machine_id, machine_type, part_number_id, quantity,
               serial_number, event_date, stage
        FROM active_candidates
        WHERE rn = 1
    ),
    prev_completed AS (
        -- Most recent Entrada per machine that DID have a matching Saída (completed run).
        -- Replaced correlated EXISTS with INNER JOIN on saida_agie_per_serial (O(n²) → O(n)).
        -- Scoped to trusted window to avoid scanning full historical table.
        SELECT
            e.MACHINE            AS machine_id,
            e.TYPE_NUMBER        AS prev_part_number_id,
            ROW_NUMBER() OVER (
                PARTITION BY e.MACHINE
                ORDER BY e.DATE DESC
            ) AS rn
        FROM silver_quality.b2b_machine_status e
        INNER JOIN saida_agie_per_serial sa       -- replaces EXISTS (O(n²) → O(n))
               ON  sa.SERIAL_NUMBER  = e.SERIAL_NUMBER
               AND sa.max_saida_date > e.DATE     -- a Saída exists after this Entrada
        WHERE e.STATE = 'Entrada'
          AND e.MACHINE <> 'AGIE UNKNOWN'
          AND e.MACHINE IS NOT NULL
          AND e.STAGE  = '03 - Agie'
          AND e.DATE  >= '2026-04-25'
          AND e.QUANTITY <> 0
          AND e.SERIAL_NUMBER NOT LIKE '%[a-z]%'
    ),
    prev AS (
        SELECT machine_id, prev_part_number_id
        FROM prev_completed
        WHERE rn = 1
    )
    -- LEFT JOIN so every machine appears; idle ones get NULLs
    SELECT
        m.machine_id,
        a.machine_type,
        a.part_number_id,
        p.prev_part_number_id,
        a.quantity,
        a.serial_number,
        a.event_date,
        a.stage
    FROM all_machines m
    LEFT JOIN active a ON m.machine_id = a.machine_id
    LEFT JOIN prev   p ON m.machine_id = p.machine_id
    ORDER BY TRY_CAST(SUBSTRING(m.machine_id, CHARINDEX(' ', m.machine_id)+1, LEN(m.machine_id)) AS INT)
    """
    return pd.read_sql(text(query), engine)


def fetch_buffer_stock(engine) -> pd.DataFrame:
    """Fetch trays currently waiting to enter AGIE (buffer between Laser and EDM).

    A tray is in the buffer if and only if:
      1. It has a Saída at 02 - Gravadora within the trusted window
      2. That Saída is the most recent one for that serial (current session)
      3. No subsequent event of ANY kind exists after that Saída
         (an Entrada at 03 - Agie means it already entered EDM;
          a new Entrada at 02 - Gravadora means it was re-queued)
      4. The Saída is within the last 24h (ghost timeout)

    These are the same fail-safe layers used in fetch_machine_status:
      - Serial reuse: temporal check prevents prior-run Saídas from counting
      - Missed exit: any subsequent event signals the tray left the buffer
      - Current session: only the most recent Saída per serial is considered
      - No ghost timeout: trays can legitimately stay in the buffer for more
        than 24h (e.g. weekend, shift gap) — only a new event removes them

    The MACHINE column of the Saída event identifies which AGIE corridor the
    tray is destined for (AGIE 01-10 → Agie1, etc.).

    Columns returned:
        serial_number   – tray serial
        part_number_id  – TYPE_NUMBER
        machine_id      – destination AGIE machine (from the Gravadora Saída)
        quantity        – pieces in the tray
        saida_gravadora – datetime of that Saída
    """
    query = """
    WITH
    -- ── Performance fix: replace correlated NOT EXISTS with pre-computed MAX ──
    -- Scoped to trusted window so SQL Server scans only recent rows.
    max_event_per_serial AS (
        SELECT SERIAL_NUMBER, MAX(DATE) AS max_event_date
        FROM silver_quality.b2b_machine_status
        WHERE QUANTITY <> 0
          AND DATE >= '2026-04-25'
        GROUP BY SERIAL_NUMBER
    ),
    all_gravadora_saidas AS (
        -- All Saída events at 02 - Gravadora since trusted window
        SELECT
            SERIAL_NUMBER,
            TYPE_NUMBER,
            MACHINE,
            QUANTITY,
            DATE
        FROM silver_quality.b2b_machine_status
        WHERE STATE    = 'Sa\u00edda'
          AND STAGE    = '02 - Gravadora'
          AND DATE    >= '2026-04-25'
          AND QUANTITY <> 0
          AND SERIAL_NUMBER NOT LIKE '%[a-z]%'
          AND MACHINE NOT LIKE '%SEM AGIE%'
    ),
    current_session_per_serial AS (
        -- Most recent Saída per serial. If the tray went through the laser
        -- multiple times (re-queued), only the latest exit is considered.
        SELECT SERIAL_NUMBER, TYPE_NUMBER, MACHINE, QUANTITY, DATE
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY SERIAL_NUMBER ORDER BY DATE DESC) AS rn
            FROM all_gravadora_saidas
        ) t
        WHERE rn = 1
    ),
    in_buffer AS (
        -- Saídas whose DATE equals the serial's max event date: no later event exists
        -- → tray is still in the buffer. Replaces NOT EXISTS (O(n²)) with JOIN (O(n)).
        -- QUANTITY guard: single laser tray never exceeds 500 pieces.
        SELECT
            cs.SERIAL_NUMBER,
            cs.TYPE_NUMBER,
            cs.MACHINE,
            cs.QUANTITY,
            cs.DATE
        FROM current_session_per_serial cs
        INNER JOIN max_event_per_serial mx        -- replaces NOT EXISTS (O(n²) → O(n))
               ON  mx.SERIAL_NUMBER  = cs.SERIAL_NUMBER
               AND mx.max_event_date = cs.DATE    -- no later event → tray still in buffer
        WHERE cs.QUANTITY <= 500
    )
    SELECT
        SERIAL_NUMBER  AS serial_number,
        TYPE_NUMBER    AS part_number_id,
        MACHINE        AS machine_id,
        QUANTITY       AS quantity,
        DATE           AS saida_gravadora
    FROM in_buffer
    ORDER BY DATE DESC
    """
    return pd.read_sql(text(query), engine)


def fetch_finished_goods(engine) -> pd.DataFrame:
    """Fetch trays currently in the finished goods supermarket (post-AGIE output).

    A tray is in finished goods if its LAST event (across ALL stages) is a
    Saída from '03 - Agie'. Uses b2b_supermarket_status as the slot registry
    but cross-checks with b2b_machine_status to ensure each serial is only
    counted once (most recent slot entry per serial).

    The SUPERMARKET column encodes the corridor: A→Agie1, B→Agie2, C→Agie3,
    D→Agie4, E→Agie5.

    Columns returned:
        part_number_id  – TYPE_NUMBER of the tray
        supermarket     – slot identifier, e.g. "A2"
        line_letter     – first character of SUPERMARKET (corridor letter)
        quantity        – piece count in the tray
        serial_number   – tray serial
        event_date      – datetime of the latest supermarket entry
    """
    query = """
    WITH last_machine_event AS (
        -- Most recent event per serial across all stages
        SELECT
            SERIAL_NUMBER,
            STAGE,
            STATE,
            ROW_NUMBER() OVER (
                PARTITION BY SERIAL_NUMBER
                ORDER BY DATE DESC
            ) AS rn
        FROM silver_quality.b2b_machine_status
        WHERE DATE >= '2026-04-25'
          AND QUANTITY <> 0
          AND SERIAL_NUMBER NOT LIKE '%[a-z]%'
    ),
    finished_serials AS (
        -- Serials whose last event is a Sa\u00edda from 03 - Agie
        SELECT SERIAL_NUMBER
        FROM last_machine_event
        WHERE rn = 1
          AND STAGE = '03 - Agie'
          AND STATE = 'Sa\u00edda'
    ),
    last_slot AS (
        -- Most recent supermarket slot entry per serial
        SELECT
            TYPE_NUMBER          AS part_number_id,
            SUPERMARKET          AS supermarket,
            LEFT(SUPERMARKET, 1) AS line_letter,
            QUANTITY             AS quantity,
            SERIAL_NUMBER        AS serial_number,
            DATE                 AS event_date,
            ROW_NUMBER() OVER (
                PARTITION BY SERIAL_NUMBER
                ORDER BY DATE DESC
            ) AS rn
        FROM silver_quality.b2b_supermarket_status
    )
    SELECT
        ls.part_number_id,
        ls.supermarket,
        ls.line_letter,
        ls.quantity,
        ls.serial_number,
        ls.event_date
    FROM last_slot ls
    INNER JOIN finished_serials fs ON ls.serial_number = fs.SERIAL_NUMBER
    WHERE ls.rn = 1
    ORDER BY ls.supermarket
    """
    return pd.read_sql(text(query), engine)


def fetch_data_last_updated(engine) -> str | None:
    """Return the most recent DT_UPDATE timestamp from b2b_machine_status.

    This reflects when the source data was last refreshed by the upstream
    ETL pipeline — independent of when the snapshot was generated.

    Returns an ISO-format string (UTC-naive, as stored in the DB) or None
    on error.
    """
    query = """
    SELECT TOP 1 DT_UPDATE
    FROM silver_quality.b2b_machine_status
    WHERE DATE >= '2026-04-25'
    ORDER BY DATE DESC
    """
    try:
        df = pd.read_sql(text(query), engine)
        if df.empty:
            return None
        raw = df.iloc[0, 0]
        if raw is None:
            return None
        return str(pd.Timestamp(raw).isoformat())
    except Exception:
        return None


def fetch_produced_today(engine) -> pd.DataFrame:
    """Fetch all pieces produced (left EDM) — no time filter.

    Reads ``silver_quality.b2b_machine_status`` for *Saída* events from the
    EDM stage (``03 - Agie``). No shift or date filter is applied.

    Columns returned:
        part_number_id  – TYPE_NUMBER that was produced
        produced_pieces – total QUANTITY leaving EDM
        last_event      – datetime of the most recent Saída for this PN
    """
    query = """
    SELECT
        TYPE_NUMBER    AS part_number_id,
        SUM(QUANTITY)  AS produced_pieces,
        MAX(DATE)      AS last_event
    FROM silver_quality.b2b_machine_status
    WHERE STATE = 'Sa\u00edda'
      AND STAGE = '03 - Agie'
      AND DATE >= '2026-04-25'
      AND QUANTITY <> 0
      AND SERIAL_NUMBER NOT LIKE '%[a-z]%'
    GROUP BY TYPE_NUMBER
    ORDER BY TYPE_NUMBER
    """
    return pd.read_sql(text(query), engine)


def fetch_laser_status(engine) -> pd.DataFrame:
    """Fetch trays currently being processed at the Laser Marker (02 - Gravadora).

    A tray is AT the laser if and only if:
      1. It has an Entrada at 02 - Gravadora within the trusted window
      2. That Entrada is the most recent one for that serial (current session)
      3. No subsequent event of ANY kind exists after that Entrada
         (a Saída from Gravadora, a new Entrada in AGIE, etc. all mean it left)
      4. The Entrada is within the last 24h (ghost timeout)

    These are the same fail-safe layers used in fetch_machine_status:
      - Serial reuse: temporal check prevents old Saídas from invalidating new Entradas
      - Missed exit: any subsequent event (regardless of stage) signals the tray left
      - Ghost timeout: Entradas older than 24h with no exit are discarded
      - Current PN session: only the most recent Entrada per serial is considered

    Returns one row per TYPE_NUMBER with:
        part_number_id   – TYPE_NUMBER
        waiting_trays    – trays currently being processed at the Laser
        waiting_pieces   – pieces currently at the Laser
        done_trays       – always 0 (done trays go to buffer, shown there)
        done_pieces      – always 0
    """
    query = """
    WITH
    -- ── Performance fix: replace correlated NOT EXISTS with pre-computed MAX ──
    -- Ghost timeout is 24h, so scoping to last 26h is safe and avoids a full scan.
    max_event_per_serial AS (
        SELECT SERIAL_NUMBER, MAX(DATE) AS max_event_date
        FROM silver_quality.b2b_machine_status
        WHERE QUANTITY <> 0
          AND DATE >= DATEADD(HOUR, -26, GETDATE())
        GROUP BY SERIAL_NUMBER
    ),
    all_gravadora_entradas AS (
        -- All Entrada events at 02 - Gravadora since trusted window
        SELECT
            SERIAL_NUMBER,
            TYPE_NUMBER,
            MACHINE,
            QUANTITY,
            DATE
        FROM silver_quality.b2b_machine_status
        WHERE STATE    = 'Entrada'
          AND STAGE    = '02 - Gravadora'
          AND DATE    >= '2026-04-25'
          AND QUANTITY <> 0
          AND SERIAL_NUMBER NOT LIKE '%[a-z]%'
    ),
    current_session_per_serial AS (
        -- Most recent Entrada per serial defines the current laser session.
        -- If the serial re-entered the Gravadora (e.g. after a rework), only
        -- the latest entry is considered — prior sessions are ignored.
        SELECT SERIAL_NUMBER, TYPE_NUMBER, MACHINE, QUANTITY, DATE
        FROM (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY SERIAL_NUMBER ORDER BY DATE DESC) AS rn
            FROM all_gravadora_entradas
        ) t
        WHERE rn = 1
    ),
    active_at_laser AS (
        -- Entradas whose DATE equals the serial's max event date: no later event exists
        -- → tray is still at the laser. Replaces NOT EXISTS (O(n²)) with JOIN (O(n)).
        -- Ghost timeout: entries older than 24h are discarded.
        SELECT
            cs.SERIAL_NUMBER,
            cs.TYPE_NUMBER,
            cs.MACHINE,
            cs.QUANTITY,
            cs.DATE
        FROM current_session_per_serial cs
        INNER JOIN max_event_per_serial mx        -- replaces NOT EXISTS (O(n²) → O(n))
               ON  mx.SERIAL_NUMBER  = cs.SERIAL_NUMBER
               AND mx.max_event_date = cs.DATE    -- no later event → tray still at laser
        WHERE cs.DATE >= DATEADD(HOUR, -24, GETDATE())
    )
    SELECT
        TYPE_NUMBER    AS part_number_id,
        COUNT(*)       AS waiting_trays,
        SUM(QUANTITY)  AS waiting_pieces,
        0              AS done_trays,
        0              AS done_pieces
    FROM active_at_laser
    GROUP BY TYPE_NUMBER
    """
    df = pd.read_sql(text(query), engine)
    df = df[df["waiting_trays"] > 0]
    return df.reset_index(drop=True)


def fetch_cycle_times(engine) -> pd.DataFrame:
    """Fetch EDM cycle time per (TYPE_NUMBER, MACHINE) from the process time table.

    Returns the exact PROCESS_TIME for each PN+machine combination.
    Used as fallback when no completed runs exist in the last 24h.

    Columns returned:
        part_number_id  – TYPE_NUMBER
        machine_id      – MACHINE (e.g. "AGIE 21")
        cycle_time_sec  – PROCESS_TIME in seconds per piece
    """
    query = """
    SELECT
        TYPE_NUMBER   AS part_number_id,
        MACHINE       AS machine_id,
        PROCESS_TIME  AS cycle_time_sec
    FROM gold.simulation_agie_process_time
    """
    return pd.read_sql(text(query), engine)


def fetch_machine_oee(engine, oee_hours: int = 168) -> dict[str, float]:
    """Calculate real OEE per machine from completed runs in the last `oee_hours` hours.

    Logic
    -----
    Find all SERIAL_NUMBERs that had ANY event (Entrada or Saída) in the last
    `oee_hours`. For each serial that has BOTH an Entrada and a Saída (completed
    run), calculate OEE — even if the Entrada itself is older than the window.

    For each completed run:
        actual_time_sec  = Saída.DATE - Entrada.DATE  (seconds)
        ideal_time_sec   = QUANTITY * cycle_time_sec  (from simulation table)
        run_oee          = ideal_time_sec / actual_time_sec

    Machine OEE = average of run_oee across all qualifying completed runs.
    Clamped to [0, 1].

    Returns
    -------
    dict mapping machine_id → oee_value (float 0-1), only for machines that
    have at least one completed run in the window.
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(hours=oee_hours)).strftime("%Y-%m-%d %H:%M:%S")
    # Search for Entradas up to 3x the OEE window back — covers long machine cycles
    # while avoiding a full-history scan (the original O(n²) bottleneck).
    entrada_cutoff = (datetime.now() - timedelta(hours=oee_hours * 3)).strftime("%Y-%m-%d %H:%M:%S")

    # Approach: start from Saídas in the window (completed runs), then for each
    # Saída use ROW_NUMBER() to pick its one immediately preceding Entrada.
    # This eliminates the cartesian product (old: entradas × saidas per serial).
    query = f"""
    WITH recent_saidas AS (
        -- Completed runs (Saídas) within the OEE window
        SELECT SERIAL_NUMBER, DATE AS saida_date
        FROM silver_quality.b2b_machine_status
        WHERE STATE = 'Saída'
          AND STAGE = '03 - Agie'
          AND DATE >= '{cutoff}'
          AND QUANTITY <> 0
          AND SERIAL_NUMBER NOT LIKE '%[a-z]%'
    ),
    matched_entradas AS (
        -- For each Saída, find the last Entrada that occurred before it.
        -- Bounded to {oee_hours * 3}h back to avoid a full-history scan.
        SELECT
            e.MACHINE, e.TYPE_NUMBER, e.SERIAL_NUMBER, e.QUANTITY,
            e.DATE        AS entrada_date,
            rs.saida_date AS saida_date,
            ROW_NUMBER() OVER (
                PARTITION BY e.SERIAL_NUMBER, rs.saida_date
                ORDER BY e.DATE DESC
            ) AS rn
        FROM silver_quality.b2b_machine_status e
        INNER JOIN recent_saidas rs ON rs.SERIAL_NUMBER = e.SERIAL_NUMBER
        WHERE e.STATE     = 'Entrada'
          AND e.STAGE     = '03 - Agie'
          AND e.MACHINE  <> 'AGIE UNKNOWN'
          AND e.MACHINE  IS NOT NULL
          AND e.QUANTITY <> 0
          AND e.SERIAL_NUMBER NOT LIKE '%[a-z]%'
          AND e.DATE >= '{entrada_cutoff}'
          AND e.DATE  < rs.saida_date
    )
    SELECT
        MACHINE       AS machine_id,
        TYPE_NUMBER   AS part_number_id,
        SERIAL_NUMBER AS serial_number,
        QUANTITY      AS quantity,
        entrada_date,
        saida_date,
        DATEDIFF(SECOND, entrada_date, saida_date) AS actual_sec
    FROM matched_entradas
    WHERE rn = 1
      AND DATEDIFF(SECOND, entrada_date, saida_date) > 0
    """
    df_runs = pd.read_sql(text(query), engine)

    if df_runs.empty:
        return {}

    # Load cycle times keyed by (part_number_id, machine_id)
    df_ct = fetch_cycle_times(engine)
    ct_map: dict[tuple, float] = {
        (str(r["part_number_id"]).strip(), str(r["machine_id"]).strip()): float(r["cycle_time_sec"])
        for r in df_ct.to_dict("records")
    }
    # Reindex as (machine_id, part_number_id) → secs for lookup_cycle_time
    ct_map_sim: dict[tuple, float] = {
        (mid, pn): secs for (pn, mid), secs in ct_map.items() if secs and secs > 0
    }
    # Global average fallback (used only when lookup_cycle_time also fails)
    all_ct_values = list(ct_map_sim.values())
    global_avg_ct: float = sum(all_ct_values) / len(all_ct_values) if all_ct_values else 60.0

    from cycle_time_utils import lookup_cycle_time

    # Calculate OEE per run, then average per machine
    machine_oee_runs: dict[str, list[float]] = {}

    for row in df_runs.to_dict("records"):
        mid        = str(row["machine_id"]).strip()
        pnid       = str(row["part_number_id"]).strip()
        quantity   = float(row["quantity"] or 0)
        actual_sec = float(row["actual_sec"] or 0)

        if actual_sec <= 0 or quantity <= 0:
            continue

        # Ideal time = how long it SHOULD take at standard cycle time
        # Hierarchy: exact → closest PN same machine → closest PN any machine → global avg
        try:
            cycle_t, _ = lookup_cycle_time(pnid, mid, ct_map_sim, use_fallback=True)
        except ValueError:
            cycle_t = global_avg_ct

        ideal_sec = quantity * cycle_t
        run_oee   = min(1.0, ideal_sec / actual_sec)

        machine_oee_runs.setdefault(mid, []).append(run_oee)

    # Average OEE per machine
    return {
        mid: sum(runs) / len(runs)
        for mid, runs in machine_oee_runs.items()
        if runs
    }


def _build_final_markets(
    df_fg: pd.DataFrame,
    df_orders: pd.DataFrame,
    routing_map: dict[str, str],
    market_timeout_hours: int,
    now,
) -> tuple[list[dict], list[dict]]:
    """Split finished goods into HE and PACKAGING markets, applying order
    completion and timeout logic.

    Parameters
    ----------
    df_fg : DataFrame
        Output of fetch_finished_goods() — one row per tray in the supermarket.
    df_orders : DataFrame
        Output of fetch_orders() — all open orders.
    routing_map : dict
        part_number_id → 'HE' | 'PACKAGING'  (from fetch_destination_routing).
        TypeNumbers NOT in the map default to 'PACKAGING'.
    market_timeout_hours : int
        After this many hours without an open order a TypeNumber is hidden.
    now : datetime
        Current datetime for timeout calculation.

    Returns
    -------
    he_market, packaging_market  — each a list of FinalMarketItem dicts:
        {
          part_number_id: str,
          total_pieces: int,
          oldest_entry_date: str | None,   # ISO — used for timeout display
          pending_orders: [                # open orders sorted oldest-first
            { order_id, quantity, due_date, remaining_needed }
          ]
        }
    """
    from datetime import timedelta

    # Build open-orders lookup: part_number_id → list sorted by due_date ASC (oldest first)
    orders_by_pn: dict[str, list[dict]] = {}
    for row in df_orders.to_dict("records"):
        pnid = str(row.get("part_number_id") or "").strip()
        oid  = str(row.get("order_id") or "").strip()
        qty  = int(float(row.get("real_qtty") or 0))
        due  = str(row.get("update_date") or "").strip()[:10]  # keep YYYY-MM-DD
        if not pnid or not oid:
            continue
        orders_by_pn.setdefault(pnid, []).append({
            "order_id": oid,
            "quantity": qty,
            "due_date": due,
        })
    # Sort each PN's orders oldest-first
    for pnid in orders_by_pn:
        orders_by_pn[pnid].sort(key=lambda o: o["due_date"] or "")

    # Accumulate pieces per TypeNumber + track oldest_entry_date
    pn_pieces: dict[str, int] = {}
    pn_oldest_date: dict[str, object] = {}  # datetime | None

    for row in df_fg.to_dict("records"):
        pnid   = str(row.get("part_number_id") or "").strip()
        qty    = int(float(row.get("quantity") or 0))
        ev     = row.get("event_date")
        if not pnid:
            continue
        pn_pieces[pnid]       = pn_pieces.get(pnid, 0) + qty
        # Track the oldest entry date (earliest = first tray arrived)
        try:
            ts = pd.Timestamp(ev)
            if ts.tzinfo is not None:
                ts = ts.tz_localize(None)
            cur = pn_oldest_date.get(pnid)
            pn_oldest_date[pnid] = ts if cur is None else min(cur, ts)
        except Exception:
            pass

    timeout_td = timedelta(hours=market_timeout_hours)
    he_items: list[dict] = []
    pkg_items: list[dict] = []

    for pnid, total in pn_pieces.items():
        orders = list(orders_by_pn.get(pnid, []))  # copy — will mutate
        oldest_dt = pn_oldest_date.get(pnid)
        oldest_iso = oldest_dt.isoformat() if oldest_dt else None

        # ── Apply order-completion logic ──────────────────────────────────
        # Walk orders oldest-first: if total_pieces >= order.quantity,
        # "fulfil" it (subtract from running total, remove order), keep remainder.
        remaining = total
        for order in list(orders):  # iterate over a copy; we'll rebuild below
            if remaining >= order["quantity"]:
                remaining -= order["quantity"]
                orders.remove(order)  # order fulfilled → disappears
            else:
                break  # not enough pieces to fulfil even this order yet

        # ── Timeout logic ─────────────────────────────────────────────────
        # If no open orders remain AND all pieces entered the market more than
        # market_timeout_hours ago → hide this TypeNumber entirely.
        if not orders and oldest_dt is not None:
            age = (now - oldest_dt).total_seconds()
            if age >= timeout_td.total_seconds():
                continue  # skip — timed out

        # ── Build pending_orders with remaining_needed ────────────────────
        pending = []
        acc = remaining
        for order in orders:
            needed = max(0, order["quantity"] - acc)
            pending.append({
                "order_id":        order["order_id"],
                "quantity":        order["quantity"],
                "due_date":        order["due_date"],
                "remaining_needed": needed,
            })
            acc = max(0, acc - order["quantity"])

        item = {
            "part_number_id":   pnid,
            "total_pieces":     remaining,
            "oldest_entry_date": oldest_iso,
            "pending_orders":   pending,
        }

        dest = routing_map.get(pnid, "PACKAGING").upper()
        if dest == "HE":
            he_items.append(item)
        else:
            pkg_items.append(item)

    # Sort each market: TypeNumbers with open orders first (by oldest due_date), then by PN
    def _sort_key(item):
        if item["pending_orders"]:
            return (0, item["pending_orders"][0]["due_date"] or "", item["part_number_id"])
        return (1, "", item["part_number_id"])

    he_items.sort(key=_sort_key)
    pkg_items.sort(key=_sort_key)

    return he_items, pkg_items


def fetch_current_state(engine, oee_hours: int = 168, off_hours: int = 168, market_timeout_hours: int = 24,
                        default_test_min: int = 60) -> dict:
    """Build the live snapshot from real DB data.

    Parameters
    ----------
    oee_hours : int
        Window in hours for OEE calculation (completed runs).
    off_hours : int
        Machines with no completed run in the last `off_hours` are marked OFF (inactive).

    No shift or time filtering — data is shown as-is from the DB.

    Returned dict keys
    ------------------
    machines         – list of EDM machine dicts
    laser_summary    – list of {part_number_id, waiting_trays, done_trays, ...}
    stock_levels     – list of per-line buffer stock dicts
    finished_goods   – list of per-line finished goods dicts
    backlog_orders   – list of part_number_ids in-process but not yet produced
    produced_orders  – list of produced-order dicts (one per PN)
    """
    from datetime import datetime
    from pathlib import Path

    now = datetime.now()

    # ── 0. Load changeover lookup (CSV) for setup/test activity detection ─────
    _changeover_lookup = None
    _changeover_default_sec = 20 * 60  # 20 min default
    _test_default_sec = default_test_min * 60  # configurable, default 60 min
    try:
        from simulation import load_changeovers
        _co_path = Path(__file__).resolve().parent.parent / "data" / "changeover_data.csv"
        if _co_path.exists():
            _changeover_lookup = load_changeovers(_co_path)
    except Exception:
        _changeover_lookup = None

    def _get_activity(is_running: bool, has_oee_history: bool, elapsed_sec: float,
                      prev_pnid: str, curr_pnid: str) -> tuple[str, float, float]:
        """
        Returns (activity, changeover_sec, test_sec).

        If machine is not running → ('inactive' or 'idle', 0, 0).
        If running and prev_pnid differs from curr_pnid and changeover lookup available:
            - elapsed < changeover_sec              → 'changeover' (SETUP)
            - elapsed < changeover_sec + test_sec   → 'test'
            - elapsed >= changeover_sec + test_sec  → 'process'
        If running and same PN or no lookup → 'process'.
        """
        if not is_running:
            activity = "inactive" if not has_oee_history else "idle"
            return activity, 0.0, 0.0

        # Determine changeover + test durations
        changeover_sec = 0.0
        test_sec = 0.0

        if _changeover_lookup and prev_pnid and curr_pnid and prev_pnid != curr_pnid:
            durations = _changeover_lookup.get(prev_pnid, curr_pnid)
            changeover_sec = durations.edm_min * 60.0
            test_sec = durations.edm_test_min * 60.0

            # Fallback to default if no entry in CSV (durations will be 0,0,0)
            if changeover_sec == 0.0 and test_sec == 0.0:
                changeover_sec = _changeover_default_sec
                test_sec = _test_default_sec

        if changeover_sec > 0 or test_sec > 0:
            if elapsed_sec < changeover_sec:
                return "changeover", changeover_sec, test_sec
            elif elapsed_sec < changeover_sec + test_sec:
                return "test", changeover_sec, test_sec

        return "process", changeover_sec, test_sec

    # ── 1-3. Fire all independent DB queries in parallel ─────────────────────
    # Each query is independent — running them concurrently means total latency
    # = slowest single query, instead of the sum of all queries.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _t0 = time.monotonic()

    def _fetch_oee_main():
        return fetch_machine_oee(engine, oee_hours=oee_hours)

    def _fetch_oee_off():
        if off_hours != oee_hours:
            return fetch_machine_oee(engine, oee_hours=off_hours)
        return None  # signal: reuse main result

    def _fetch_produced():
        return fetch_produced_today(engine)

    def _fetch_machines():
        return fetch_machine_status(engine)

    def _fetch_laser():
        return fetch_laser_status(engine)

    def _fetch_buffer():
        return fetch_buffer_stock(engine)

    def _fetch_fg():
        return fetch_finished_goods(engine)

    def _fetch_orders_bg():
        return fetch_orders(engine)

    def _fetch_routing():
        return fetch_destination_routing(engine)

    _tasks = {
        "oee_main":  _fetch_oee_main,
        "oee_off":   _fetch_oee_off,
        "produced":  _fetch_produced,
        "machines":  _fetch_machines,
        "laser":     _fetch_laser,
        "buffer":    _fetch_buffer,
        "fg":        _fetch_fg,
        "orders":    _fetch_orders_bg,
        "routing":   _fetch_routing,
    }

    _results: dict[str, object] = {}
    _query_times: dict[str, float] = {}

    def _timed(name: str, fn):
        t0 = time.monotonic()
        result = fn()
        _query_times[name] = round(time.monotonic() - t0, 2)
        return result

    with ThreadPoolExecutor(max_workers=len(_tasks)) as _pool:
        _futures = {_pool.submit(_timed, name, fn): name for name, fn in _tasks.items()}
        for _future in as_completed(_futures):
            name = _futures[_future]
            try:
                _results[name] = _future.result()
            except Exception as _exc:
                print(f"[fetch_current_state] WARNING: query '{name}' failed: {_exc}")
                _results[name] = None

    _t1 = time.monotonic()
    _times_str = " | ".join(f"{k}={v}s" for k, v in sorted(_query_times.items(), key=lambda x: -x[1]))
    print(f"[fetch_current_state] parallel DB fetch completed in {_t1 - _t0:.2f}s  →  {_times_str}")

    machine_oee_map: dict = _results.get("oee_main") or {}
    _oee_off_result = _results.get("oee_off")
    machine_off_map: dict = _oee_off_result if _oee_off_result is not None else machine_oee_map

    # NOTE: DO NOT use `result or default` for DataFrames — pandas raises
    # ValueError("The truth value of a DataFrame is ambiguous") on bool().
    # Always use explicit `is not None` checks.
    _empty_df = pd.DataFrame()
    df_produced_raw = _results["produced"] if _results.get("produced") is not None else _empty_df
    df_machines     = _results["machines"] if _results.get("machines") is not None else _empty_df
    _df_laser       = _results["laser"]    if _results.get("laser")    is not None else _empty_df
    _df_buffer      = _results["buffer"]   if _results.get("buffer")   is not None else _empty_df
    _df_fg          = _results["fg"]       if _results.get("fg")       is not None else _empty_df
    _df_orders_bg   = _results["orders"]   if _results.get("orders")   is not None else _empty_df
    _df_routing     = _results["routing"]  if _results.get("routing")  is not None else _empty_df

    def _to_elapsed_sec(dt) -> float:
        """Seconds elapsed since the machine's last Entrada — independent of shift."""
        if dt is None:
            return 0.0
        try:
            ts = pd.Timestamp(dt)
            if ts.tzinfo is not None:
                ts = ts.tz_localize(None)
            diff = (now - ts).total_seconds()
            return max(0.0, diff)
        except Exception:
            return 0.0

    machines_list = []

    for row in df_machines.to_dict("records"):
        mid          = str(row.get("machine_id")          or "").strip()
        mtype        = str(row.get("machine_type")         or "").strip()
        pnid         = _normalize_optional_text(row.get("part_number_id"))  # NULL → machine is idle
        prev_pnid    = str(row.get("prev_part_number_id")  or "").strip()
        ev           = row.get("event_date")
        elapsed_sec  = _to_elapsed_sec(ev)
        is_running   = bool(pnid)

        oee_value: float | None = machine_oee_map.get(mid)
        has_oee_history = machine_off_map.get(mid) is not None

        activity, changeover_sec, test_sec = _get_activity(
            is_running, has_oee_history, elapsed_sec, prev_pnid, pnid
        )

        # Store entry_datetime as ISO string so callers can compute start_sec/end_sec
        entry_iso = None
        if ev is not None:
            try:
                ts = pd.Timestamp(ev)
                if ts.tzinfo is not None:
                    ts = ts.tz_localize(None)
                entry_iso = ts.isoformat()
            except Exception:
                pass

        machines_list.append({
            "machine_id":   mid,
            "process_type": "EDM",
            "line_id":      _machine_to_line(mid),
            "machine_type": mtype or None,
            "static_oee":   oee_value,
            "state": {
                "activity":       activity,
                "order_id":       None,
                "part_number_id": pnid,
                "product_type":   pnid,
                "tray_id":        str(row.get("serial_number") or "").strip() or None,
                "pieces":         (lambda q: int(float(q)) if q is not None and q == q else None)(row.get("quantity")),
                "entry_datetime": entry_iso,
                "start_sec":      None,  # enriched externally once cycle_times are available
                "end_sec":        None,  # enriched externally once cycle_times are available
                "elapsed_sec":    elapsed_sec if is_running else None,
                "current_oee":    oee_value,
                "prev_part_number_id": prev_pnid or None,
                "changeover_sec": changeover_sec if is_running else None,
                "test_sec":       test_sec if is_running else None,
            },
        })

    # ── 4. Laser summary ─────────────────────────────────────────────────────
    df_laser = _df_laser  # already fetched in parallel above
    laser_summary = []
    for row in df_laser.to_dict("records"):
        laser_summary.append({
            "part_number_id": str(row.get("part_number_id") or "").strip(),
            "waiting_trays":  int(row.get("waiting_trays") or 0),
            "done_trays":     int(row.get("done_trays") or 0),
            "waiting_pieces": int(float(row.get("waiting_pieces") or 0)),
            "done_pieces":    int(float(row.get("done_pieces") or 0)),
        })

    # ── 5a. Buffer Stock — saíram da Laser, ainda não entraram na AGIE ─────────
    df_buffer = _df_buffer  # already fetched in parallel above

    _machine_to_line_id = lambda mid: _machine_to_line(mid)  # reuse helper above

    buffer_stock: dict[str, dict] = {}
    for row in df_buffer.to_dict("records"):
        mid    = str(row.get("machine_id") or "").strip()
        pnid   = str(row.get("part_number_id") or "").strip()
        qty    = int(float(row.get("quantity") or 0))
        serial = str(row.get("serial_number") or "").strip()
        line_id = _machine_to_line(mid) if mid and mid != "AGIE UNKNOWN" else "Unknown"

        if line_id not in buffer_stock:
            buffer_stock[line_id] = {"line_id": line_id, "trays": 0, "parts": 0, "tray_details": []}
        buffer_stock[line_id]["trays"] += 1
        buffer_stock[line_id]["parts"] += qty
        buffer_stock[line_id]["tray_details"].append({
            "tray_id":        serial,
            "order_id":       None,
            "part_number_id": pnid,
            "product_type":   pnid,
            "pieces":         qty,
        })

    stock_levels = sorted(buffer_stock.values(), key=lambda x: x["line_id"])

    # ── 5b. Finished Goods — saíram das AGIEs, estão no supermarket de saída ──
    df_fg = _df_fg  # already fetched in parallel above

    _letter_to_line = {
        "A": "Agie1", "B": "Agie2", "C": "Agie3", "D": "Agie4", "E": "Agie5",
    }

    finished_goods: dict[str, dict] = {}
    for row in df_fg.to_dict("records"):
        letter  = str(row.get("line_letter") or "").strip().upper()
        line_id = _letter_to_line.get(letter, f"Agie_{letter}")
        pnid    = str(row.get("part_number_id") or "").strip()
        qty     = int(float(row.get("quantity") or 0))
        serial  = str(row.get("serial_number") or "").strip()
        slot    = str(row.get("supermarket") or "").strip()

        if line_id not in finished_goods:
            finished_goods[line_id] = {"line_id": line_id, "trays": 0, "parts": 0, "tray_details": []}
        finished_goods[line_id]["trays"] += 1
        finished_goods[line_id]["parts"] += qty
        finished_goods[line_id]["tray_details"].append({
            "tray_id":        serial or slot,
            "order_id":       None,
            "part_number_id": pnid,
            "product_type":   pnid,
            "pieces":         qty,
            "supermarket":    slot,
        })

    finished_goods_levels = sorted(finished_goods.values(), key=lambda x: x["line_id"])

    # ── 6. Produced this shift (one entry per PN) ────────────────────────────
    produced_orders = []
    produced_pn_set: set[str] = set()
    for row in df_produced_raw.to_dict("records"):
        pnid = str(row.get("part_number_id") or "").strip()
        qty  = int(float(row.get("produced_pieces") or 0))
        if not pnid:
            continue
        produced_pn_set.add(pnid)
        produced_orders.append({
            "order_id":        None,
            "part_number_id":  pnid,
            "product_type":    pnid,   # no CRIN/PLV — PN is used as type key
            "quantity":        qty,
            "trays":           max(1, round(qty / 150)),
            "produced_pieces": qty,
            "completed_trays": max(1, round(qty / 150)),
            "completion_pct":  1.0,
        })

    # ── 7. Backlog — todas as ordens abertas em orders_10digit ──────────────
    #
    # Todas as linhas da tabela são ordens abertas — basta buscá-las todas.

    df_orders = _df_orders_bg  # already fetched in parallel above
    backlog_orders: list[str] = []
    seen: set[str] = set()
    for row in df_orders.to_dict("records"):
        oid = str(row.get("order_id") or "").strip()
        if oid and oid not in seen:
            seen.add(oid)
            backlog_orders.append(oid)

    # ── 8. Final markets (HE + PACKAGING) ────────────────────────────────────
    # fetch_destination_routing returns TYPE_NUMBER + DEFINITION ('COM_HE' | 'SEM_HE')
    df_routing = _df_routing  # already fetched in parallel above
    routing_map: dict[str, str] = {}
    for r in df_routing.to_dict("records"):
        pnid       = str(r.get("TYPE_NUMBER") or "").strip()
        definition = str(r.get("DEFINITION") or "").strip().upper()
        if not pnid:
            continue
        # COM_HE  → HE market
        # SEM_HE  → PACKAGING market (default)
        routing_map[pnid] = "HE" if definition == "COM_HE" else "PACKAGING"

    he_market, packaging_market = _build_final_markets(
        df_fg=df_fg,
        df_orders=df_orders,
        routing_map=routing_map,
        market_timeout_hours=market_timeout_hours,
        now=now,
    )

    return {
        "machines":             machines_list,
        "laser_summary":        laser_summary,
        "stock_levels":         stock_levels,         # buffer: saíram da Laser, ainda na fila da AGIE
        "finished_goods":       finished_goods_levels, # finished goods: saíram das AGIEs (legado)
        "he_market":            he_market,            # TypeNumbers destinados à HE
        "packaging_market":     packaging_market,     # TypeNumbers destinados a Packaging
        "backlog_orders":       backlog_orders,
        "produced_orders":      produced_orders,
    }



def fetch_all_filtered(
    engine,
    csv_path: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Fetch all data needed for simulation and live view.

    Returns:
        df_orders        – orders from silver_orders.orders_10digit
        df_restrictions  – machine/part restrictions and process times
        df_machines      – machine list with line_id and process_type
        df_hard_stage    – Hard Stage Market inventory (body + nozzle stock)
    """
    if csv_path:
        order_ids, part_number_ids = _read_filter_csv(csv_path)
    else:
        order_ids, part_number_ids = None, None

    df_orders = fetch_orders(engine, order_ids=order_ids)
    df_restrictions = fetch_restrictions(engine, part_number_ids=part_number_ids)
    df_machines = fetch_machines(engine)
    df_hard_stage = fetch_hard_stage_market(engine)

    return df_orders, df_restrictions, df_machines, df_hard_stage
