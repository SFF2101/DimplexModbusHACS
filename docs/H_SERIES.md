# WPM 2006/2007 H-software support (LWPM 410 Modbus RTU)

Status: **read-only monitoring implemented; no heat-pump writes on H.**
Branch point: upstream `nb3rt/DimplexModbusHACS` `a76937c3513627de2898d9514909722ffc5cf06c`.

Target example: Dimplex ILK 8TE (Sixmadun SMLIK-XP-8E), WPM 2007, firmware
`WPM_H_H54`, LWPM 410 Modbus RTU card, reached through an RTU→TCP gateway
(unit id 2, 19200 8N1).

## 1. Evidence

| Ref | Source | Use |
|---|---|---|
| E1 | Glen Dimplex, *Modbus RTU Anbindung — Projektierungs- und Installationshandbuch* (LWPM 410), version 08/2023, ch. 6 "Datenpunktliste" (§6.1 Betriebsdaten p. 15–17) | H read addresses |
| E2 | Dimplex wiki "Modbus TCP – Systemstatus" (id 3303833601): Status/Sperr/Störmeldungen per software L/M · J · H | H addresses 14 / 94 / 13 |
| E3 | Dimplex wiki "Statusmeldungen", "Sperrmeldungen", "Störmeldungen" (H/J columns) | H value enumerations |
| E4 | Dimplex wiki "Modbus TCP – Betriebsdaten" (J/L/M column) | proves J, L and M share addresses 1/2/53/3/58/5/7/9/54 |

**Column attribution.** E1 §6.1 lists two addresses per temperature row under
the headers `WPM_J | WPM_L | WPM_M | WPM_H`. E4 shows J, L and M share the first
address, so the second address is the H address. E2 independently gives the H
column for status/lock/fault (14/94/13), which matches E1's third value. The
owner's commissioning read (FC03 register 27 → raw 118 = 11.8 °C) is consistent
with register 27 = outdoor temperature on H. E1 was read through a text
extraction of the PDF (not the original layout); the acceptance test in §5
verifies every mapped value against the controller display before any reliance.

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
operating mode / party / holiday registers, SG Ready, measured power/energy
(5168/5170/5182, 5096–5129), inverter frequency (114, reverse-engineered),
sensor-error register. Reason: no unambiguous H address in E1/E2.

## 4. Writable parameters on H — catalogue (NO entity implemented)

`writes_supported("H")` is `False`: no number/select/climate/SG-Ready entity is
created on H even with the control gate on. The upstream write table uses NWPM
Modbus-TCP addresses (5015, 5036–5048, 5145, 5167 …) that are **not** the
LWPM 410 RTU addresses on any software version. E1 does list H-candidate
addresses, but its settings tables have four software columns and only three
printed values per row, so the H column cannot be attributed with certainty:

| Parameter (E1 §6.3) | Printed values | H candidate | Status |
|---|---|---|---|
| Betriebsmodus 0..5 | 142 / 222 / 134 | 134? | UNVERIFIED |
| HK1 Parallelverschiebung 0..38 (−19..+19 K) | … / 129 | 129? | UNVERIFIED |
| HK1 Heizkurvenendpunkt 20..70 °C | … / 142 | 142? | UNVERIFIED (collides with a J/L value) |
| HK1 Festwertsolltemperatur 18..60 °C | 164 / 244 / 130 | 130? | UNVERIFIED |
| Warmwasser Solltemperatur | … / 149 | 149? | UNVERIFIED |
| Warmwasser Hysterese 2..15 K | … / 131 | 131? | UNVERIFIED |
| Partystunden 0..72 / Urlaubstage 0..150 | … / 135 / 136 | 135 / 136? | UNVERIFIED |
| HK1 Raumtemperatur, Hysterese | 46/21, 47/22 | none | NOT AVAILABLE on H |
| Smart Grid coils 3/4, Smart_Grid_extern 374 | L20.2–L23.7 / ≥M1.3 | none | NOT AVAILABLE on H |
| Time programmes (192/272, select-then-write) | J/L only | none | NOT AVAILABLE on H |

E1 gives no EEPROM write-endurance or write-on-change guidance for H. Before any
H write entity is built, obtain the original PDF table (or Dimplex
confirmation, ferndiagnose@dimplex.de) for the H column and verify each address
by read-only comparison with the controller display.

**Tariff relevance.** On H the only documented, low-risk lever for economical
operation is the hardware EVU/utility-lock input (not Modbus) and, once
verified, operating mode / HC1 parallel shift / DHW setpoint. Tariff logic must
stay outside this integration (pure control primitives only).

## 5. Installation & verification plan (requires separate owner approval)

1. **Backup** Home Assistant (full) and note the gateway configuration.
2. Install via HACS → *Custom repositories* → this fork, category *Integration*,
   pinned to the reviewed commit (or copy `custom_components/dimplex_wpm` from
   that commit). Restart HA.
3. Add integration *Dimplex WPM*: host = gateway IP, port = gateway TCP port,
   **unit id = 2**, profile = *Dimplex WPM (generic)*, **software version = H**,
   scan interval 60 s, timeout 5 s. Step 2: no optional modules, all meters off.
   Leave *Enable control/write entities* **off**.
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
7. Control tests and tariff optimisation are separate, later authorisations.
