"""In-process simulated Modbus TCP->RTU gateway (pymodbus server, 127.0.0.1).

Unit 2 = Dimplex WPM (H register map incl. the H write registers), unit 1 =
an unrelated decoy device. Every request PDU is recorded as
``(device_id, function_code, address)`` so tests can prove routing, the exact
registers touched, and the absence of unintended writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket

from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusServerContext,
    ModbusSparseDataBlock,
)
from pymodbus.server import ModbusTcpServer

UNIT_DIMPLEX = 2
UNIT_DECOY = 1
WRITE_FCS = {5, 6, 15, 16}

# H map (LWPM 410 manual 08/2023 §6.1, §6.5) + H write registers (§6.3).
H_VALUES = {
    13: 0,        # fault: none
    14: 2,        # status: heat pump on - heating
    27: 0xFF9C,   # outdoor -10.0 °C
    28: 300,      # return setpoint 30.0
    29: 285,      # return 28.5
    30: 480,      # dhw 48.0
    31: 321,      # flow 32.1
    40: 480,      # dhw setpoint (read) 48.0
    41: 12,       # source outlet 1.2
    94: 0,        # lock: none
    129: 19,      # HC1 parallel shift raw 19 = 0 K
    134: 1,       # operating mode 1 = Auto
    149: 48,      # DHW setpoint 48 °C
}
DECOY_VALUES = {1: 999, 27: 999, 129: 7, 134: 3, 149: 70}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SimGateway:
    def __init__(self, dimplex=None, decoy=None, ignore_writes=False, coils=None):
        """``ignore_writes``: the device acknowledges FC06 but keeps its old value
        (as a controller that rejects/clamps silently would) -> read-back mismatch.
        ``coils``: optional {address: bool} coil map for unit 2 (FC01 reads).
        """
        self.port = free_port()
        self.requests: list[tuple[int, int, int | None]] = []
        self.ignore_writes = ignore_writes
        # Shadow of unit 2's holding registers (kept in step with the server).
        self.values = dict(H_VALUES if dimplex is None else dimplex)
        dimplex_ctx = {"hr": ModbusSparseDataBlock(dict(self.values))}
        if coils is not None:
            dimplex_ctx["co"] = ModbusSparseDataBlock(dict(coils))
        devices = {
            UNIT_DIMPLEX: ModbusDeviceContext(**dimplex_ctx),
            UNIT_DECOY: ModbusDeviceContext(
                hr=ModbusSparseDataBlock(dict(DECOY_VALUES if decoy is None else decoy))
            ),
        }
        self.server = ModbusTcpServer(
            ModbusServerContext(devices=devices, single=False),
            address=("127.0.0.1", self.port),
            trace_pdu=self._trace,
        )
        self._task = None

    def _trace(self, sending, pdu):
        if not sending:
            self.requests.append((pdu.dev_id, pdu.function_code, getattr(pdu, "address", None)))
            if pdu.function_code == 6 and pdu.dev_id == UNIT_DIMPLEX:
                if self.ignore_writes:
                    pdu.registers = [self.values.get(pdu.address, 0)]
                elif pdu.address in self.values:
                    self.values[pdu.address] = pdu.registers[0]
        return pdu

    async def start(self):
        self._task = asyncio.create_task(self.server.serve_forever())
        for _ in range(50):
            with contextlib.suppress(OSError):
                _r, w = await asyncio.open_connection("127.0.0.1", self.port)
                w.close()
                await w.wait_closed()
                break
            await asyncio.sleep(0.05)
        return self

    async def stop(self):
        await self.server.shutdown()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    def writes(self):
        return [r for r in self.requests if r[1] in WRITE_FCS]

    def register(self, address: int) -> int:
        return self.values[address]
