
# Production Simulation Model (Sequence Description)

## Prompt details, simulation PoC ## 


### General:

- We want to have a simulation (for the PoC without any optimization) that simulates a process in a production plant for daily production sequence 
- Input of the simulation is a day list of orders, each order (order id) has a part number ids. THere can be two different order ids with the same part number id
- One order id has one part number id that needs to produced in a specific quantity
- As we don't optimize it at the first step and just want to check if the simulation is reaching a similar OEE as the reality. This list should be just fifo step by step without any logic for now that says which is the best order.
- There are several restictions for example some part number ids can only be processed by specific machines. More detailed information later. 
- Output/Goal would be the total time or the OEE (can be calulated with the total time) of the daily production sequence
- General, each order consist of a part number ID which has a quantity of parts that need to be produced for this part number ID
- Each Part Number ID has a specific tool configuration, that means if we do a changover from one PN ID to another, the changeover time depends on the PN ID changeover combination (its symetric (PN ID A --> PN ID B == PN ID B --> PN ID A))
- We have a change over time (more information later) between PN IDs, we assume no changeover time between trays within the same PN ID 
- The affected area consists of two production processes sections, called Laser Marking (LM) and other EDM, furthermore there is a stock floor between these sections
- The coarse process flow is: hard Stage Supermarket --> Laser Marking --> Stock LM/EDM --> EDM --> Finish
- **Hard Stage Market constraint (DB mode)**: The simulation can only produce part numbers that are currently present in the Hard Stage Supermarket inventory (`silver_production.inventory`). The available stock quantity per nozzle PN acts as a production budget — once consumed, that PN cannot be produced further. After all SAP orders for HSM-available PNs are fulfilled, the simulation continues producing the remaining HSM stock optimally (HSM continuation trays with `order_id = "HSM_{pn}"`). This ensures the simulation reflects real material availability.
- The simulation should be done using SimPy



### Details of the part numbers IDs (PN ID):
- Each part number ID (PN ID) has some specific characteristics:
    - There is a restriction of which PN ID is able to go in which EDM Machine, possible machine types: e.g. OP2, Q1, Q2
    - There is a restriction of which PN ID is able to go in which Laser Marking Machine: e.g. 3206, 1541, ...
    - The restriction for the machine can be found in machine_restriction.csv --> each possible machine for each process type (e.g. `EDM` or `LASER`) that is allowed for this part number id is listed in the csv
    - Parts are always proceeded in trays of 150 parts (we assume always 150 parts, if order number is not fitting in 150 steps --> round up and produce more parts)

- Maybe define changeover here as well



### Details of the production environment:
- Hard Stage Supermarket: In DB mode, the simulation reads the actual Hard Stage inventory from the database. Only part numbers present in the Hard Stage Market can be produced, and the available quantity per PN limits total production. In CSV mode (testing), the Hard Stage is assumed ideal (unlimited availability).
- The number of LM machines and corresponding lines you can extract from the file "machine.csv" filter for process_type = "LASER"
- The number of EDM  machines and corresponding lines you can extract from the file "machine.csv" filter for process_type = "EDM", for example EDM has 10 machines of type Q1 in line 1
- There is a stock between LM and EDM for each of EDM lines (more information ab out the stock: later)
- Each part number id can be processed in parallel: e.g. order quantity 300, 150 can be produced in machine id 1 and 150 in machine id 2 considering the restrictions of machines (see csv)
- Lines for each process type can work parallel: e.g. you can produce parts in machines of line 1 and line 2 at the same time


### Details Laser Marking Process:
- The cycle time for one tray for each PN ID in a specific machine is defined in the restrictions.csv under cycle_time
- Change over time for laser marking process between PN IDs is defined in the changeover.csv
- After finishing the parts they will be placed into the stocks (see later more information)


### Details Stock between LM and EDM:
- The stock is empty at beginning
- There is no minimum waiting time for trays once they arrive at the stock to leave the stock for the next process (EDM)
- Each EDM line has its own stock (if there are 5 lines for edm, then there are 5 stocks assigned, one assigned to each line --> this assigned stock can only be used by the corresponding line)
- All stocks have the same maximum tray capacity, 20 trays of 3000 parts


### Details EDM Process
- The cycle time for one tray for each PN ID in a specific machine is defined in the restrictions.csv under cycle_time
- Change over time for edm process between PN IDs is defined in the changeover.csv
- For the EDM process we have a additional release (test) time (defined in the changeover.csv). This time is after the change over time and the actual process time
- After finishing the parts they will be finished (consider order completed)



### Other Assumption:

- We assume no scrap
- In DB mode, the simulation starts from the real current state of machines and buffers (live initial state). Machines processing a PN not in the Hard Stage Market are allowed to finish their current work, but no new work is started for non-HSM PNs.
- When all SAP orders for HSM-available PNs are exhausted, the simulation continues producing the remaining HSM stock as optimized continuation production.



## Data Input Files (Configuration)

To ensure flexibility, all parameters are loaded from external `.csv` or `.xlsx` files. The model is not hard-coded.

### `orders.csv`
Contains the list of production orders to be scheduled in the simulation.
-   `order_id`: general order ID (unique)
-   `date`: date of the order
-   `part_number_id`: Part number for the order.
-   `product_type`: The type of product to be manufactured--> Family e.g. 'CRIN'. (not relevant for now)
-   `quantity`: The number of pieces to produce.

### `machines.csv`
Defines all machines in the factory.
-   `machine_id`: Unique ID of the machine.
-   `process_type`: The type of machine (`EDM` or `LASER`).
-   `machine_type`: OP2, Q1, ....
-   `line_id`: The production line it belongs to.

### `restrictions.csv`
Lists all part_ids
-   `part_number_id`: Part number id
-   `process_type`: The type of machine (`EDM` or `LASER`).
-   `machine_type`: The ID of the machine.


### `changeover_data.csv`
Defines the time required for changeover and subsequent testing --> symetric from one PN ID to the other.
-   `type_a`: PN ID A
-   `type_b`: PN ID B
-   `changeover_laser_duration_min`: The time required for the physical changeover for laser marking between PN IDs.
-   `changeover_edm_duration_min`: The time required for the physical changeover for EDM process between PN IDs.
-   `test_edm_duration_min`: The time required for testing after the changeover (the "Release Time") at the edm process.




# Constraints to add later:

- (Furthermore, there are operators which have specific task and can be busy or idle and which operates at 1. the Laser Marking Area 2. EDM Area)
- Tray size 75/150
- PN Family
- HE Process needed?
- Type of processing on part number id level: sequential or parallel
- Block due to scrap, in geral scrap
- Each stock has a maximum tray capacity, and can differ for each stock
- AGIE availability (machine broken or some workingpositions)
- Tools availability --> 