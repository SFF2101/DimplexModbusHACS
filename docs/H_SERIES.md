# WPM 2006/2007 H-software support (LWPM 410 Modbus RTU)

Status: **read-only monitoring by default; three verified H controls behind the
control gate (default OFF): operating mode, HC1 parallel shift, DHW setpoint.**
Branch point: upstream `nb3rt/DimplexModbusHACS` `a76937c3513627de2898d9514909722ffc5cf06c`.

Target example: Dimplex ILK 8TE (Sixmadun SMLIK-XP-8E), WPM 2007, firmware
`WPM_H_H54`, LWPM 410 Modbus RTU card, reached through an RTU→TCP gateway
(unit id 2, 19200 8N1).

## 1. Evidence

| Ref | Source | Use |
|---|---|---|
| E1 | Glen Dimplex, *Modbus RTU Anbindung — Projektierungs- und Installationshandbuch* (LWPM 410), version 08/2023, 64 pages, PDF sha256 `4e3ce0b4680f3aa463ed6826e46e749e10fe84b8480b1d19eab91a4f9bae5390`; §4 function codes (p. 10), §6.1 Betriebsdaten (p. 15–16), §6.3 Einstellungen (p. 19–25) | H read and write addresses |
| E2 | Dimplex wiki "Modbus TCP – Systemstatus" (id 3303833601): Status/Sperr/Störmeldungen per software L/M · J · H | H addresses 14 / 94 / 13 |
| E3 | Dimplex wiki "Statusmeldungen", "Sperrmeldungen", "Störmeldungen" (H/J columns) | H value enumerations |
| E4 | Dimplex wiki "Modbus TCP – Betriebsdaten" (J/L/M column) | proves J, L and M share addresses 1/2/53/3/58/5/7/9/54 |

**Column attribution.** E1 §6.1 lists two addresses per temperature row under
the headers `WPM_J | WPM_L | WPM_M | WPM_H`. E4 shows J, L and M share the first
address, so the second address is the H address. E2 independently gives the H
column for status/lock/fault (14/94/13), which matches E1's third value. The
owner's commissioning read (FC03 register 27 → raw 118 = 11.8 °C) is consistent
with register 27 = outdoor temperature on H. E1 has since been inspected in its
original layout (rendered pages): §6.1 and §6.3 print a dedicated `WPM_H`
column, which confirms the attribution. The acceptance test in §5 still
verifies every mapped value against the controller display before reliance.

## 2. H read map (implemented)

All FC03 holding registers, int16 two's complement, 0.1 °C (E1 §6.1).

| Entity key | H | J/L/M | Evidence |
|---|---|---|---|
| outdoor_temperature (R1) | **27** | 1 | E1, E4 |
| return_setpoint_temperature | **28** | 53 | E1, E4 |
| return_temperature (R2) | **29** | 2 | E1, E4 |
| dhw_temperature (R3) | **30** | 3 | E1, E4 |
| flow_temperature (R9) | **31** | 5 | E1, E4 |
| hc2_setpoint_temperature (module HC2/3) | **32** | 54 | E1, E4 |
| hc2_temperature (R5, module HC2/3) | **33** | 9 | E1, E4 |
| dhw_setpoint_temperature | **40** | 58 | E1, E4 |
| source_outlet_temperature (R6) | **41** | 7 | E1, E4 |
| status_code / status_text | **14** | 103 (J: 43) | E2, E3 |
| fault_code / fault_text | **13** | 105 (J: 42) | E2, E3 |
| lock_code / lock_text | **94** | 104 (J: 59) | E2, E3 |

Value maps for H (E3): status uses the H/J table with value 29 = "Passive
cooling"; faults use the H/J table **without** codes 1/2 (N17.1/N17.2 are
L/M-column values); lock code 1 = "Outdoor temperature" (was "Reserved").

Polling on H reads only contiguous runs of documented addresses — default
core install: `13+2, 27+5, 40+2, 94+1` (4 FC03 requests per poll). No FC04, no
coils, no gap bridging, no capability probes. Connection validation reads
register 27 (not 1).

## 3. Not mapped on H (excluded → entity not created, register never read)

Everything else: source inlet (R24, "-" in E1), room temperature/humidity,
HC3, passive cooling, solar, ventilation, runtimes, inputs/outputs (coils),
party / holiday registers, SG Ready, measured power/energy
(5168/5170/5182, 5096–5129), inverter frequency (114, reverse-engineered),
sensor-error register. Reason: no H address in E1/E2, or not in the approved
scope (E1 also documents H room temperature/humidity 36–39, HC3 34/35 and passive
cooling 42/43; these are reported only, not mapped).

## 4. Writable parameters on H

E1 §4 (p. 10): writes use FC06 *Write Single Register* (FC05 for coils); no
FC16. E1 §6.3 tables have the columns `WPM_J bis WPM_L22.9 | ab WPM_L23 WPM_M |
WPM_H` (address ranges 1…127 | 1…207 | –). In the original layout a merged cell
spans the J and L23/M columns where both share an address, so rows with three
printed values map 1:1 to the three columns and the third value is the H
address. The NWPM Modbus-TCP table (`WRITE_REGISTERS`: 5015, 5036–5048, 5145,
SG Ready 5167 …) is never used on H.

### 4.1 Implemented (behind *Enable control/write entities*, default OFF)

| Entity key | H reg | E1 cell | Type / range (E1) | Integration range / encoding |
|---|---|---|---|---|
| `set_operating_mode` (select) | **134** | p. 24, §6.3.3 *Modus*, row *Betriebsmodus*: 142 \| 222 \| 134 | uint16 Register R/W, 0..5: 0 Sommer, 1 Auto, 2 Urlaub, 3 Party, 4 2.Wärmeerzeuger, 5 Kühlen | all 6 codes decoded; **only 0 Summer and 1 Auto writable** |
| `set_hc1_curve_offset` (number, K) | **129** | p. 19, §6.3.1 *1.Heizkreis*, row *Parallelverschiebung*: 163 \| 243 \| 129; code list p. 19–20 | uint16 Register R/W, 0..38 (0 = −19 K, 19 = 0 K, 38 = +19 K) | −19..+19 K, raw = K + 19 |
| `set_dhw_setpoint` (number, °C) | **149** | p. 25, §6.3.4 *Warmwasser*, row *Solltemperatur*: 174 \| 254 \| 149 | uint16 Register R/W, min "Solltemp. Minimal", max 85 °C | **30..60 °C**, integer °C |

Write path on H (`coordinator._write_verified`): the register must be one of
the three above and the raw value inside the integration range/allowed codes
(otherwise refused before any bus traffic); one FC03 pre-read (an unchanged
value is **not** rewritten); one FC06 write to the configured unit id; one FC03
read-back of the same register — a mismatch raises an error. No retries. No
write ever happens during setup, startup, polling or option changes; only on an
explicit user/service action. On H no thermostat (climate) and no SG Ready
entity is created.

Inferences / policy (not stated by E1):
* **Integer °C** for register 149 is inferred from `uint16` with integer range
  limits (unlike §6.1 values, no "0,1" scaling is printed). The read-back check
  detects a wrong scale or a silently clamped value.
* The H minimum for register 149 is the controller parameter *Solltemp.
  Minimal*, which has **no H address** in E1 (row *Solltemperatur Minimal*:
  – \| 352 \| –). The integration therefore uses a conservative 30..60 °C
  window; 60 °C also keeps the setpoint away from the 85 °C maximum.
* Operating modes 2–5 need further parameters (party hours, holiday days),
  a 2nd heat generator or cooling hardware; they are display-only here.
* **Write endurance:** E1 contains no EEPROM / write-cycle / write-frequency
  guidance. Mitigation: no automatic writes, unchanged values skipped, one
  register per action. Tariff automations must rate-limit their own writes.

### 4.2 Documented on H but NOT implemented

| Parameter (E1 §6.3) | H reg | Reason |
|---|---|---|
| HK1 Festwertsolltemperatur 18..60 °C (p. 20) | 130 | not in approved scope |
| HK1 Heizkurvenendpunkt 20..70 °C (p. 20) | 142 | not in approved scope |
| HK1 Raumtemperatur 15.0..30.0 °C (p. 20) | 21 | decimal range with `uint16`: scaling not stated |
| HK1 Hysterese 0.5..5.0 K (p. 20) | 22 | decimal range with `uint16`: scaling not stated |
| Solltemp. dyn. Kühlen 10..35 °C (p. 20) | 151 | cooling; not in scope |
| WW Hysterese 2..15 K (p. 25) | 131 | not in approved scope |
| Partystunden 0..72 / Urlaubstage 0..150 (p. 24) | 135 / 136 | not in approved scope |
| 2./3. HK settings (p. 21–22) | select-then-write | multi-step write; not in scope |
| Lüftung, Stoßlüften (p. 24) | – | no H address |
| Smart Grid / SG Ready | – | no H address |

**Tariff relevance.** On H the documented, low-risk levers are the hardware
EVU/utility-lock input (not Modbus) and the three controls above. Tariff logic
must stay outside this integration and needs separate authorisation.

## 5. Installation & verification plan (requires separate owner approval)

1. **Backup** Home Assistant (full) and note the gateway configuration.
2. Install via HACS → *Custom repositories* → this fork, category *Integration*,
   pinned to the reviewed commit (or copy `custom_components/dimplex_wpm` from
   that commit). Restart HA.
3. Add integration *Dimplex WPM*: host = gateway IP, port = gateway TCP port,
   **unit id = 2**, profile = *Dimplex WPM (generic)*, **software version = H**,
   scan interval 60 s, timeout 5 s. Step 2: no optional modules, all meters off.
   Leave *Enable control/write entities* **off** (default).
4. Expected entities: outdoor, flow, return, return setpoint, DHW, DHW
   setpoint, source outlet temperatures; status/lock/fault (code + text);
   derived ΔT; diagnostics. Nothing else.
5. **Acceptance (read-only):** compare each temperature and status with the WPM
   display within one poll; outdoor ≈ controller R1 (historical commissioning
   read: reg 27 raw 118 = 11.8 °C — baseline only, not a current value). Check
   *Diagnostics → meta.failed_read_chunks* = 0 and that the other gateway unit
   is unaffected. Any mismatch → disable the entry and report.
6. **Rollback:** delete the config entry, remove the HACS repository (or the
   copied folder), restart; restore the backup if anything else changed.
7. Control tests (enabling the gate, any write to registers 129/134/149) and
   tariff optimisation are separate, later authorisations. When approved, first
   compare the read-back of 129/134/149 with the controller display while still
   not writing; then one single write per control with display confirmation.
   Expected extra entities with the gate on: *Operating mode* (select),
   *HC1 curve offset* and *DHW setpoint* (numbers). Nothing else.

## 6. Offline validation (no device)

* `pytest tests` — HA-free unit/regression tests (J/L/M frozen snapshot,
  H read/write tables, pymodbus mock gateway).
* `pytest tests_ha -o asyncio_mode=auto` — genuine Home Assistant runtime
  (pytest-homeassistant-custom-component): config flow, entry setup,
  coordinator, entity platforms and service calls against an in-process
  pymodbus TCP server on 127.0.0.1 (sockets restricted to loopback).
