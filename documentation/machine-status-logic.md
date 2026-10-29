# Machine Status Detection Logic — `fetch_machine_status()`

## Overview

The goal of this function is to determine, **in real time**, which of the 50 AGIE EDM machines
are currently **RUNNING** (have a tray loaded) and which are **IDLE** (empty).

The source of truth is the table `silver_quality.b2b_machine_status`, which records every
tray movement event across the production floor. Each row represents one event with the
following key fields:

| Field | Description |
|---|---|
| `MACHINE` | Physical machine name (e.g. `AGIE 07`). Exit events are recorded as `AGIE UNKNOWN`. |
| `STATE` | `Entrada` (tray entered) or `Saída` (tray exited) |
| `STAGE` | Production stage: `02 - Gravadora` (Laser) or `03 - Agie` (EDM) |
| `SERIAL_NUMBER` | Tray identifier — the key used to link entries and exits |
| `TYPE_NUMBER` | Part number being processed |
| `DATE` | Event timestamp |
| `QUANTITY` | Number of pieces in the tray |

---

## Core Problem: Exit Events Have No Machine Reference

Exit (`Saída`) events are **always recorded under `AGIE UNKNOWN`** — the machine name is
lost at the time of exit registration. This means we cannot directly query
"which machine did this tray leave?".

**Solution:** use the `SERIAL_NUMBER` as the link between an `Entrada` (which has the
machine) and its corresponding `Saída` (which does not). A tray is still **inside** a
machine when its `Entrada` has no subsequent `Saída` for the same `SERIAL_NUMBER`.

---

## Step-by-Step Logic

### Step 1 — `all_machines`
Build the canonical list of all 50 physical AGIE machines from the restrictions table
(`silver_type_number.nozzle_restrictions`). This ensures every machine always appears in
the output, even if it has no recent events.

### Step 2 — `all_entradas`
Collect all `Entrada` events at stage `03 - Agie` within the **trusted data window**
(from `2026-04-25` onwards).

**Filters applied:**
- `STATE = 'Entrada'` — only entry events
- `MACHINE <> 'AGIE UNKNOWN'` and `MACHINE IS NOT NULL` — only events with a real machine
- `STAGE = '03 - Agie'` — only EDM stage entries
- `DATE >= '2026-04-25'` — data before this date is considered dirty/unreliable
- `QUANTITY <> 0` — ignore zero-quantity test records
- `SERIAL_NUMBER NOT LIKE '%[a-z]%'` — ignore test/dummy serials with lowercase characters

### Step 3 — `current_pn_per_machine`
For each machine, identify the **TYPE_NUMBER of the most recent `Entrada`**. This defines
the "current production session" — i.e., what part the machine is currently set up to run.

**Why this is needed:** a machine may have ghost trays from a previous production session
(different TYPE_NUMBER) that never had a `Saída` registered due to data entry errors. These
must not be mistaken for currently loaded trays.

> **Rule:** if an older tray with a *different* TYPE_NUMBER has no exit record, it is
> treated as a phantom and ignored. Only trays matching the machine's current TYPE_NUMBER
> are considered.

### Step 4 — `active_candidates`
Find all trays of the **current session's TYPE_NUMBER** that are **still inside** the
machine.

A tray is considered **still inside** if:
1. It matches the machine's current TYPE_NUMBER (from Step 3)
2. Its `Entrada` occurred within the **last 24 hours** (ghost timeout — see scenarios below)
3. There is **no subsequent event at all** for that `SERIAL_NUMBER` after the `Entrada` date

Point (3) uses `NOT EXISTS` checking **any event** after the `Entrada`, without filtering
by stage or state. This is intentional — see the "Missed Exit" scenario below.

All remaining candidates are ranked by `event_date DESC` per machine.

### Step 5 — `active`
Select only the **most recent** tray still inside each machine (`rn = 1`).

This is the tray whose `entry_datetime`, `part_number_id`, and `serial_number` are
displayed on the dashboard for that machine.

### Step 6 — `prev_completed`
For each machine, find the TYPE_NUMBER of the **last completed run** — i.e., the most
recent `Entrada` at `03 - Agie` for which a `Saída` at `03 - Agie` exists **after** that
specific `Entrada`'s date.

This is used by the frontend to show what the machine was running before its current job
(useful for changeover detection).

### Step 7 — Final `SELECT`
`LEFT JOIN` all 50 machines against `active` and `prev`. Machines with no active tray
receive `NULL` for all state fields and are treated as `IDLE` by the application.

---

## Scenarios Covered

### ✅ Normal Operation
```
Entrada 03 - Agie  @ 10:00  (AGIE 07, SN: 0001234)
Saída   03 - Agie  @ 14:30  (AGIE UNKNOWN, SN: 0001234)
```
The `NOT EXISTS` check finds the `Saída` at 14:30 > 10:00 → tray is **out** → machine is IDLE.

---

### ✅ Tray Still Being Processed (RUNNING)
```
Entrada 03 - Agie  @ 12:00  (AGIE 03, SN: 0001132149)
— no Saída yet —
```
`NOT EXISTS` finds nothing after 12:00 → tray is **still inside** → machine is RUNNING.

---

### ✅ Multiple Trays of Same PN Loaded Simultaneously
```
Entrada 03 - Agie  @ 09:55  (AGIE 03, SN: 0001132154)  ← still inside
Entrada 03 - Agie  @ 12:13  (AGIE 03, SN: 0001132149)  ← still inside (rn=1, shown)
```
Both are `active_candidates`. The most recent (`rn=1`) is shown on the dashboard.
The machine is RUNNING as long as **at least one** tray has no exit.

---

### ✅ Most Recent Tray Exited but Earlier One Still Inside
```
Entrada 03 - Agie  @ 12:13  (AGIE 03, SN: 0001132149)  ← still inside
Entrada 03 - Agie  @ 14:56  (AGIE 03, SN: 0000982656)  ← exited at 15:20
Saída   03 - Agie  @ 15:20  (AGIE UNKNOWN, SN: 0000982656)
```
`0000982656` is excluded (has a Saída after its Entrada).
`0001132149` has no exit → becomes `rn=1` → machine is still RUNNING. ✅

---

### ✅ Serial Reuse (Same Serial, Different Runs)
```
Entrada 03 - Agie  @ 2026-04-10  (AGIE 03, SN: 0000975499)  ← old run
Saída   03 - Agie  @ 2026-04-11  (AGIE UNKNOWN, SN: 0000975499)
...
Entrada 03 - Agie  @ 2026-04-28 18:37  (AGIE 07, SN: 0000975499)  ← new run
```
The old `Saída` from April 11 is at `DATE < 2026-04-28 18:37`, so the `NOT EXISTS`
condition (`s.DATE > ae.event_date`) correctly ignores it. The new `Entrada` has no
subsequent exit → machine is RUNNING. ✅

---

### ✅ Missed Exit — Tray Returned to Laser Without Registering Saída
```
Entrada 03 - Agie  @ 18:37  (AGIE 07, SN: 0000975499)
— no Saída at 03 - Agie —
Entrada 02 - Gravadora @ 07:54 next day (AGIE UNKNOWN, SN: 0000975499)
```
There is no `Saída` at `03 - Agie`, but the tray clearly left the machine because it
re-entered `02 - Gravadora` afterwards.

The `NOT EXISTS` check has **no stage filter** — it detects the `Entrada` at `02 - Gravadora`
as a subsequent event (`DATE > ae.event_date`) and correctly marks the tray as **out**
→ machine is IDLE. ✅

---

### ✅ Ghost Tray — Different TYPE_NUMBER with No Exit
```
Entrada 03 - Agie  @ 01:28  (AGIE 03, SN: 0001281328, PN: 0433175518)  ← ghost
— no Saída —
Entrada 03 - Agie  @ 09:55  (AGIE 03, SN: 0001132154, PN: 0433172203)  ← current session
```
`current_pn_per_machine` identifies `0433172203` as the current PN.
`0001281328` has a different PN (`0433175518`) → excluded by the `INNER JOIN` in
`active_candidates` → ignored. ✅

---

### ✅ Ghost Tray — Same TYPE_NUMBER, Entry Older Than 24 Hours
```
Entrada 03 - Agie  @ 2026-04-25 13:44  (AGIE 02, SN: 0000997396, PN: 0433172144)
— no Saída —
Entrada 03 - Agie  @ 2026-04-28 14:56  (AGIE 02, SN: 0000997396, PN: 0433172144)
```
Even if the PN matches, a tray with no exit registered after 24+ hours is considered a
data entry error. The `DATEADD(HOUR, -24, GETDATE())` filter in `active_candidates`
drops any `Entrada` older than 24 hours that has no corresponding exit. ✅

---

## Summary of Protection Layers

| Layer | Applied In | Protects Against |
|---|---|---|
| Trusted data window (`DATE >= 2026-04-25`) | `all_entradas` | Dirty historical data with missing events |
| Current PN session (`INNER JOIN current_pn_per_machine`) | `active_candidates` | Ghost trays from a prior TYPE_NUMBER run |
| Temporal `NOT EXISTS` (any subsequent event) | `active_candidates` | Serial reuse, missed exits, returns to Gravadora |
| 24-hour ghost timeout (`DATEADD(HOUR, -24, ...)`) | `active_candidates` | Same-PN ghost trays with no data-quality exit |
| Per-row temporal `EXISTS` for `prev_completed` | `prev_completed` | Misattributing a prior run's exit to a new Entrada |

---

## Output Fields Per Machine

| Field | Type | Description |
|---|---|---|
| `machine_id` | string | Physical machine name, e.g. `AGIE 07` |
| `machine_type` | string \| null | Process type, e.g. `AGIE OP2` (null if idle) |
| `part_number_id` | string \| null | TYPE_NUMBER currently loaded (null if idle) |
| `prev_part_number_id` | string \| null | TYPE_NUMBER of the last completed run |
| `quantity` | number \| null | Pieces in the current tray |
| `serial_number` | string \| null | Tray serial number (`rn=1` candidate) |
| `event_date` | datetime \| null | Timestamp of the current tray's Entrada |
| `stage` | string \| null | Always `03 - Agie` when RUNNING |

The `entry_datetime` is then used by the application layer (`fetch_current_state`) to
compute `elapsed_sec`, `start_sec`, `end_sec`, and `percent_complete` based on the
machine's cycle time for that TYPE_NUMBER.
