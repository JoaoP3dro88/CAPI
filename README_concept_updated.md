
# Production Simulation Model (Proof of Concept)

This document outlines the architecture for a discrete-event simulation model designed to replicate our production environment. The goal of this PoC is to build a "digital twin" that can run different scheduling strategies and measure their impact on key performance indicators (KPIs) like throughput and changeover time.

The simulation is built using Python, primarily leveraging the `simpy` library for event-based simulation logic.

## Core Classes (The "Heart" of the Simulation)

The model is built around an object-oriented structure to represent the physical components of the factory.

### `class Machine`

A universal class representing any piece of equipment in the factory.
-   `machine_id` (str): Invertury (e.g., `"AGIE-12"`, `"LASER-3"`).
-   `machine_type` (str): The type of the machine, e.g., `'Opcon'`, `'Q1'`, `'Q2'` or `'LASER'`.
-   `line_id` (str): The ID of the production line the machine belongs to (e.g., `AGIE-1` to `AGIE-5`, `LaserM`).
-   `status` (str): The current state of the machine (e.g., `'IDLE'`, `'PROCESSING'`, `'CHANGEOVER'`, `'TESTING'`).
-   `current_part_number` (str): The product type that is currently set up on the machine.
-   `tray_id` (str): The tray with parts

### `class Operator`

This class models the human resource constraint, as one operator is responsible for all changeovers on a single line.
-   `id` (str): A unique identifier (e.g., `"Q1-Line-1"`).
-   `line_id` (str): The line for which the operator is responsible.
-   `status` (str): The current state of the operator, either `'BUSY'` or `'IDLE'`.
-   `volume` (int): Number of part that can be transpot at once
-   `tray_id` (str): The tray with parts

### `class Tray`

This class models the tray with parts, that the oparator is moving in the production
-   `id` (str):
-   `volume` (int):
-   `part_number` (str):
-   `last_process` (str): 



### `class FactoryEnvironment`

The main class that orchestrates the entire simulation.
-   Contains lists of all `Machine` and `Operator` objects.
-   Manages the queue of upcoming production orders.
-   Controls the simulation clock and event loop using `simpy`.
-   Implements the scheduling logic (e.g., a simple FIFO strategy for the PoC).

### `class Stock`

The class represent the Stock volume between production
-   `stock_type` (str): Related to Process like Laser to Agie
-   `line_id` (str): Corresponding line
-   `max_volume` (int): Maximum number 
-   `actuel_volume` (int): Volume of a stock
-   `part_number` (dict): part number including the volume of parts

## Data Input Files (Configuration)

To ensure flexibility, all parameters are loaded from external `.csv` or `.xlsx` files. The model is not hard-coded.

### `machines.csv`
Defines all machines in the factory.
-   `process_type`: Production process type 
-   `machine_id`: Unique ID of the machine.
-   `machine_type`: The type of machine (`Q1` or `LASER`).
-   `line_id`: The production line it belongs to.

### `orders.csv`
Contains the list of production orders to be scheduled in the simulation.
-   `date`: Order date, when the production should like, but not when exactly it will be produced.
-   `order_id`: Daily generated order_id of one part.
-   `part_number_id`: Part number for the order.
-   `product_type`: The type of product to be manufactured--> Family e.g. 'CRIN'.
-   `quantity`: The number of pieces to produce.

### `changeover_data.csv`
Defines the time required for changeover and subsequent testing.
-   `from_type`: The previous product type.
-   `to_type`: The new product type.
-   `laser_changeover_duration_min`: The time required for the changeover for laser marking.
-   `edm_changeover_duration_min`: The time required for the physical changeover.
-   `edm_test_duration_min`: The time required for testing after the changeover (the "Release Time").

### `process_times.csv`
Specifies the processing time for each product on each machine.
-   `machine_id`: The ID of the machine.
-   `product_type`: The ID of the product.
-   `time_per_piece_sec`: The time in seconds to process a single piece.

### `restrictions.csv`
Lists all machine restrictions, defining which products cannot run on certain machines.
-   `process_type`: Process of the mashines e.g. LaserMarking, EDM, HE
-   `machine_type`: The ID of the machine.
-   `machine_id`: The ID of the machine.
-   `part_number_id`: The product type that is not allowed to run on this machine.


## Simulation Process Logic

The core logic for processing a single job on an `AGIE` machine is implemented as a sequence of events:

1.  A job (e.g., "100 pieces of Type B") is assigned to machine "AGIE-12" (which just finished "Type A").
2.  **Check:** The simulation checks if `part_number` ('B') is different from `current_part_number` ('A'). If so, a **changeover is required**.
3.  **Request Resource:** The `Machine` object requests the `Operator` assigned to its `line_id`. The machine's process will halt until the operator resource becomes available.
4.  **Operator Becomes Available:**
    a. The `Operator.status` is set to `'BUSY'`.
    b. The `Machine.status` is set to `'CHANGEOVER'`.
    c. The simulation waits for the `changeover_duration_min`.
    d. The `Machine.status` is updated to `'TESTING'`.
    e. The simulation waits for the `test_duration_min` (Release Time).
    f. **Release Resource:** The `Operator.status` is set back to `'IDLE'`, making it available for other tasks on the same line.
5.  **Start Production:**
    a. The `Machine.status` is set to `'PROCESSING'`.
    b. The `Machine.current_job_type` is updated to 'B'.
    c. The simulation waits for the total production time (`quantity * time_per_piece_sec`).
6.  **Completion:** The `Machine.status` is set to `'IDLE'`, and it is now ready to take on the next job.
7.  **Deliver to Stock:** Operater bring the part to the stock