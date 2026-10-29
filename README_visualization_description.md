# Visualization Requirements (Dashboard + Real‑Time Playback)

This document defines **what must be visualized** and **how data is exchanged** (JSON schema). It is intended for handoff to Figma for UI/UX design and later implementation.

---

## 1) Goals

### 1.1 Primary goals
- Provide a **clear operational overview** of the production simulation.
- Show **machine utilization over time** and **order progression**.
- Allow a **step‑by‑step playback** of the simulation state (real‑time visualization).

### 1.2 Output formats
- **Static dashboard**: summary + timeline + tables.
- **Real‑time playback**: step‑based view of current plant state.

---

## 2) Static Dashboard – Required Sections

### 2.1 KPI Summary (Top of page)
Show these KPIs as cards with icons and trend placeholders:
- Total production time (sec / min)
- Total trays processed
- Average machine utilization (OEE proxy)
- Orders count
- Machines count
- Lines count
- Total changeover time (sec)
- Total test time (sec)
- Total processing time (sec)

### 2.2 Timeline / Gantt (Main chart)
**Chart type**: horizontal stacked bars (time on X, machines on Y), grouped by zones.
- X axis: time (minutes)
- Y axis: machine list (each bar row is a machine)
- Facets / sections: 
  - Laser Marking area
  - EDM lines (each line separated)
- Bar segments must show:
  - Order processing time per machine
  - Changeover time
  - Test/release time
- Bar labels:
  - For processing: Order ID
  - For changeover/test: activity label
- Machine label should include **OEE** (e.g., “EDM-1 (OEE 76.8%)”).

### 2.3 Stock State (End-of-day)
**Chart type**: bar chart per EDM line
- X: EDM line
- Y: trays in stock at end
- Each stock bar should allow drill‑down to the trays list with **part_number_id** (and order_id).

### 2.4 Orders Produced (Table)
Show each order with:
- order_id
- part_number_id
- product_type
- quantity
- trays
- produced_pieces

### 2.5 Filters (Optional but recommended)
- Process type (LASER / EDM)
- Line ID (EDM lines)
- Order ID
- Product type
- Machine ID

---

## 3) Real‑Time Playback – Required Sections

### 3.1 Playback Controls
- Play / Pause
- Step slider
- Step time (configurable, default 60 sec)
- Current time indicator (min)

### 3.2 Plant Layout (Left‑to‑Right flow)
**Areas shown in order**:
1) Backlog (orders not started)
2) Laser Marking Machines
3) Stock LM/EDM (per EDM line)
4) EDM Machines (grouped by line, parallel cells)
5) Produced Orders

### 3.3 Backlog Panel
- List of orders not started at the current time step.

### 3.4 Laser Marking Panel
- Each machine as a card/cell.
- Shows current activity:
  - IDLE, changeover, test, or processing
  - If processing: order_id + product_type
- Show machine OEE.

### 3.5 Stock Panel
- Show per‑line stock count (trays).
- If possible, include mini list of top N trays including **order_id** and **part_number_id**.

### 3.6 EDM Panel
- Machines grouped by line.
- Each machine as a card/cell.
- Shows current activity (same rules as LM).

### 3.7 Produced Orders Panel
- List of orders completed by current time.

---

## 4) Data Exchange (JSON)

> **Note**: The JSON payloads below are **examples**. Real datasets can include **50+ machines** and many orders. The visualization must scale accordingly (virtualized lists, filtering, and grouping by line/process are recommended).

### 4.1 Static Dashboard Payload (Example)

**File / endpoint**: `outputs/dashboard.json` or `GET /api/dashboard`

```json
{
  "kpis": {
    "total_time_sec": 0,
    "total_trays": 0,
    "average_utilization": 0.0,
    "orders_count": 0,
    "machines_count": 0,
    "lines_count": 0,
    "total_changeover_sec": 0,
    "total_test_sec": 0,
    "total_process_sec": 0
  },
  "machines": [
    {
      "machine_id": "EDM-1",
      "process_type": "EDM",
      "line_id": "AGIE-1",
      "machine_type": "Q1",
      "oee": 0.768
    }
  ],
  "timeline": [
    {
      "machine_id": "EDM-1",
      "process_type": "EDM",
      "line_id": "AGIE-1",
      "tray_id": 1,
      "order_id": "ORD-001",
      "part_number_id": "PN-100",
      "product_type": "CRIN",
      "activity": "process",
      "start_sec": 120.0,
      "end_sec": 360.0
    }
  ],
  "stock_final": [
    {
      "line_id": "AGIE-1",
      "trays": 0,
      "tray_details": [
        { "tray_id": 12, "order_id": "ORD-001", "part_number_id": "PN-100", "product_type": "CRIN" }
      ]
    }
  ],
  "orders": [
    {
      "order_id": "ORD-001",
      "part_number_id": "PN-100",
      "product_type": "CRIN",
      "quantity": 280,
      "trays": 2,
      "produced_pieces": 300
    }
  ]
}
```

### 4.2 Real‑Time Playback Payload (Example)

**File / endpoint**: `outputs/realtime.json` or `GET /api/realtime?step_sec=60`

```json
{
  "metadata": {
    "step_sec": 60,
    "total_time_sec": 7200,
    "generated_at": "2026-02-04T10:00:00Z"
  },
  "frames": [
    {
      "time_sec": 0,
      "backlog_orders": ["ORD-001", "ORD-002"],
      "produced_orders": [],
      "stock_levels": [
        {
          "line_id": "AGIE-1",
          "trays": 0,
          "tray_details": [
            { "tray_id": 12, "order_id": "ORD-001", "part_number_id": "PN-100", "product_type": "CRIN" }
          ]
        },
        {
          "line_id": "AGIE-2",
          "trays": 0,
          "tray_details": []
        }
      ],
      "machines": [
        {
          "machine_id": "LASER-1",
          "process_type": "LASER",
          "line_id": "LaserM",
          "machine_type": "LASER",
          "oee": 0.317,
          "state": {
            "activity": "process",
            "order_id": "ORD-001",
            "part_number_id": "PN-100",
            "product_type": "CRIN",
            "tray_id": 1,
            "start_sec": 0,
            "end_sec": 120
          }
        }
      ]
    }
  ]
}
```

### 4.3 Data Types (Strict)
- `order_id`, `part_number_id`, `product_type`, `machine_id`, `line_id`, `machine_type`, `process_type`, `activity` → string
- `quantity`, `trays`, `produced_pieces` → integer
- `start_sec`, `end_sec`, `time_sec`, `total_time_sec`, `step_sec` → float
- `oee`, `average_utilization` → float (0.0 to 1.0)

### 4.4 Activity values (enum)
- `process`
- `changeover`
- `test`
- `idle`

---

## 5) Visual Design Guidelines

- Modern industrial style: clean, minimal, blue/gray palette.
- Cards with subtle shadows and rounded corners.
- Consistent iconography for process, changeover, test, idle.
- Highlight bottlenecks with warning color when:
  - Stock is near capacity
  - Long changeovers
  - Machine idle > threshold

---

## 6) Optional Enhancements

- KPI: throughput per hour
- KPI: average changeover per machine
- KPI: average waiting time in stock
- Toggle: show only EDM / only LASER
- Tooltips on bars with full order details

---

## 7) Implementation Notes

- The JSON schemas above should be **authoritative** for integration.
- Figma‑generated UI should consume the JSON payloads without data transformation.
- Only the data transport (file or API) should be swapped during integration.
