# OpenROAD Python API Cheatsheet

OpenROAD provides two Python modules: `odb` and `openroad`.

---

## Setup & I/O

### `openroad` module

| Function / Class            | Description                          |
| --------------------------- | ------------------------------------ |
| `openroad.Tech()`           | Create technology object             |
| `openroad.Design(tech)`     | Create design object (requires Tech) |
| `openroad.Timing(design)`   | Create timing/STA object             |
| `tech.readLef(file)`        | Read LEF file                        |
| `tech.readLiberty(file)`    | Read Liberty library (for timing)    |
| `design.readDb(file)`       | Read `.odb` database                 |
| `design.readDef(file)`      | Read DEF file                        |
| `design.readVerilog(file)`  | Read Verilog netlist                 |
| `design.writeDb(file)`      | Write `.odb` database                |
| `design.writeDef(file)`     | Write DEF file                       |
| `design.getBlock()`         | Get `dbBlock`                        |
| `design.getDb()`            | Get `dbDatabase`                     |
| `design.getTech()`          | Get `Tech`                           |
| `design.link()`             | Link design to database              |
| `design.isSequential(inst)` | Check if instance is sequential      |
| `design.isInClock(iterm)`   | Check if terminal is on clock net    |
| `design.isBuffer(master)`   | Check if cell is a buffer            |
| `design.isInverter(master)` | Check if cell is an inverter         |
| `design.micronToDBU(value)` | Convert microns to DBU               |
| `design.evalTclString(cmd)` | Execute a Tcl command                |

### `odb` Database Objects

| Function                     | Description           |
| ---------------------------- | --------------------- |
| `odb.read_db(file)`          | Read `.odb` database  |
| `odb.write_db(file, block)`  | Write `.odb` database |
| `odb.read_def(file)`         | Read DEF file         |
| `odb.write_def(file, block)` | Write DEF file        |
| `odb.read_lef(file, db)`     | Read LEF file         |
| `odb.dbDatabase.create()`    | Create new database   |

---

## Library & Cell Access

### `dbDatabase`

| Method                 | Description               |
| ---------------------- | ------------------------- |
| `findMaster(name)`     | Find cell master by name  |
| `findLib(name)`        | Find library by name      |
| `findTech(name)`       | Find technology by name   |
| `getLibs()`            | Get all libraries         |
| `getTechs()`           | Get all technologies      |
| `getNumberOfMasters()` | Total cell count          |
| `getDbuPerMicron()`    | Database units per micron |
| `beginEco()`           | Begin ECO session         |
| `commitEco()`          | Commit ECO changes        |
| `endEco()`             | End ECO session           |
| `undoEco()`            | Undo last ECO             |

### `dbLib`

| Method             | Description       |
| ------------------ | ----------------- |
| `getName()`        | Library name      |
| `getMasters()`     | All cell masters  |
| `findMaster(name)` | Find cell by name |
| `findSite(name)`   | Find site by name |

### `dbMaster`

| Method                       | Description           |
| ---------------------------- | --------------------- |
| `getName()`                  | Cell name             |
| `getWidth()` / `getHeight()` | Cell dimensions (DBU) |
| `getArea()`                  | Cell area (DBU²)      |
| `getMTerms()`                | All cell pins         |
| `findMTerm(name)`            | Find pin by name      |
| `isSequential()`             | Is flip-flop/latch    |
| `isCore()`                   | Is core cell          |
| `isFiller()`                 | Is filler cell        |
| `getSite()`                  | Placement site        |
| `getSymmetryX/Y/R90()`       | Symmetry flags        |
| `getEEQ()` / `getLEQ()`      | Equivalent master     |

---

## Instance Manipulation

### `dbInst`

| Method                                           | Description                               |
| ------------------------------------------------ | ----------------------------------------- |
| `getName()`                                      | Instance name                             |
| `getMaster()`                                    | Get `dbMaster`                            |
| `swapMaster(master)`                             | Replace cell with new master              |
| `getLocation()` / `setLocation(x, y)`            | Get/set placement origin                  |
| `getOrient()` / `setOrient(orient)`              | Get/set orientation (N/S/E/W/FN/FS/FE/FW) |
| `setLocationOrient(x, y, orient)`                | Set location and orientation              |
| `getPlacementStatus()` / `setPlacementStatus(s)` | Get/set status (PLACED/FIXED/UNPLACED)    |
| `getBBox()`                                      | Bounding box (`Rect`)                     |
| `getITerms()`                                    | All instance terminals                    |
| `getITerm(name)` / `findITerm(name)`             | Get terminal by name                      |
| `isPlaced()` / `isFixed()` / `isCore()`          | Placement/cell type checks                |
| `setDoNotTouch(bool)`                            | Set DNT flag                              |
| `rename(name)`                                   | Rename instance                           |
| `destroy()`                                      | Delete instance                           |
| `setHalo(l, b, r, t)`                            | Set keepout margin                        |

---

## Net Connectivity

### `dbNet`

| Method                          | Description                       |
| ------------------------------- | --------------------------------- |
| `getName()`                     | Net name                          |
| `getITerms()`                   | Connected instance terminals      |
| `getBTerms()`                   | Connected block terminals (ports) |
| `getSigType()`                  | Signal type (SIGNAL/POWER/GROUND) |
| `isSpecial()` / `isConnected()` | Net property checks               |
| `setDoNotTouch(bool)`           | Set DNT flag                      |
| `create(block, name)`           | Create new net                    |
| `destroy()`                     | Destroy net                       |
| `rename(name)`                  | Rename net                        |
| `mergeNet(other)`               | Merge with another net            |

### `dbITerm` (Instance Terminal)

| Method                                 | Description                      |
| -------------------------------------- | -------------------------------- |
| `getName()`                            | Terminal name (D, Q, CK, etc.)   |
| `getNet()`                             | Connected `dbNet`                |
| `getMTerm()`                           | Master terminal (pin definition) |
| `getInst()`                            | Parent `dbInst`                  |
| `getIoType()`                          | INPUT / OUTPUT / INOUT           |
| `isInputSignal()` / `isOutputSignal()` | Direction checks                 |
| `isClocked()`                          | Is clock terminal                |
| `isConnected()`                        | Has connection                   |
| `connect(net)`                         | Connect to a net                 |
| `disconnect()`                         | Disconnect from net              |

### `dbBTerm` (Block/Port Terminal)

| Method        | Description   |
| ------------- | ------------- |
| `getName()`   | Port name     |
| `getNet()`    | Connected net |
| `getIoType()` | I/O type      |
| `getBlock()`  | Parent block  |

---

## Block-Level Iteration

### `dbBlock`

| Method                                           | Description            |
| ------------------------------------------------ | ---------------------- |
| `getName()`                                      | Block name             |
| `getInsts()`                                     | All instances          |
| `getNets()`                                      | All nets               |
| `getITerms()`                                    | All instance terminals |
| `getBTerms()`                                    | All block terminals    |
| `getMasters()`                                   | Masters used in block  |
| `getRows()`                                      | Placement rows         |
| `getCoreArea()` / `getDieArea()`                 | Core/die area (`Rect`) |
| `findNet(name)` / `findInst(name)`               | Find by name           |
| `makeNewInstName(base)` / `makeNewNetName(base)` | Generate unique names  |
| `micronsToDbu(value)`                            | Convert microns to DBU |
| `getMinRoutingLayer()` / `getMaxRoutingLayer()`  | Routing layer range    |

---

## `openroad.Timing`

### Construction

```python
timing = openroad.Timing(design)
```

### Slack, Arrival, Slew

| Method                          | Description                      |
| ------------------------------- | -------------------------------- |
| `getPinSlack(iterm, min_max)`   | Get timing slack at a pin        |
| `getPinArrival(iterm, min_max)` | Get signal arrival time at a pin |
| `getPinSlew(iterm, min_max)`    | Get signal slew at a pin         |
| `getNetCap(net, min_max)`       | Get net capacitance              |
| `getPortCap(bterm, min_max)`    | Get port capacitance             |
| `getMaxCapLimit(pin)`           | Max capacitance limit for a pin  |
| `getMaxSlewLimit(pin)`          | Max slew limit for a pin         |

### Timing Constants

| Constant     | Description       |
| ------------ | ----------------- |
| `Timing.Min` | Min (fast) corner |
| `Timing.Max` | Max (slow) corner |

### Clocks & Endpoints

| Method                                        | Description                            |
| --------------------------------------------- | -------------------------------------- |
| `findClocksMatching(pattern, regexp, nocase)` | Find clocks by pattern                 |
| `isEndpoint(iterm)`                           | Check if terminal is a timing endpoint |
| `getCorners()`                                | Get all timing corners (returns tuple) |
| `findCorner(name)`                            | Find corner by name                    |
| `cmdCorner()`                                 | Get current command corner             |

### Power & Equivalence

| Method                       | Description                            |
| ---------------------------- | -------------------------------------- |
| `staticPower(inst, corner)`  | Static (leakage) power for instance    |
| `dynamicPower(inst, corner)` | Dynamic (switching) power for instance |
| `equivCells(master)`         | Get equivalent cells for a master      |
| `makeEquivCells()`           | Build equivalence classes              |

---

## Tech & Corners

### `openroad.Tech`

| Method                           | Description             |
| -------------------------------- | ----------------------- |
| `readLef(file)`                  | Read LEF                |
| `readLiberty(file)`              | Read Liberty            |
| `getTech()`                      | Get underlying `dbTech` |
| `getDB()`                        | Get `dbDatabase`        |
| `getSta()`                       | Get STA engine          |
| `nominalProcess/Temp/Voltage()`  | Nominal PVT values      |
| `timeScale/capacitanceScale/...` | Unit scale factors      |

---

## Design Utilities

| Method                           | Description                           |
| -------------------------------- | ------------------------------------- |
| `design.getResizer()`            | Get resizer engine (cell replacement) |
| `design.getReplace()`            | Get replacement engine                |
| `design.getOpendp()`             | Get detailed placer                   |
| `design.getTritonCts()`          | Get clock tree synthesizer            |
| `design.getGlobalRouter()`       | Get global router                     |
| `design.getTritonRoute()`        | Get detailed router                   |
| `design.getITermName(iterm)`     | Get formatted iTerm name              |

---