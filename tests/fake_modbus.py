"""Minimal raw Modbus TCP device for fault injection (127.0.0.1 only, no pymodbus).

Implements FC03 (read holding registers) and FC06 (write single register) and
records every request frame as ``(unit, function_code, address)``. Faults:

* ``drop_fc06_response`` – apply the write but never answer (lost response);
* ``fc06_delay`` – answer FC06 only after this many seconds;
* ``fail_reads`` – answer every FC03 with exception code 4 (device failure).

Independent of the pymodbus version under test, so the same fault behaviour
is reproduced for every client version.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct


class FakeModbusDevice:
    def __init__(self, registers: dict[int, int], unit: int = 2) -> None:
        self.registers = dict(registers)
        self.unit = unit
        self.frames: list[tuple[int, int, int]] = []
        self.connections = 0  # accepted TCP connections
        self.drop_fc06_response = False
        self.fc06_delay = 0.0
        self.fail_reads = False
        self.port = 0
        self._server: asyncio.base_events.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    async def start(self) -> FakeModbusDevice:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            for w in list(self._writers):
                w.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    def fc(self, code: int) -> list[tuple[int, int, int]]:
        return [f for f in self.frames if f[1] == code]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._writers.add(writer)
        self.connections += 1
        try:
            while True:
                header = await reader.readexactly(7)
                tid, _pid, length, unit = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                fc = pdu[0]
                addr = struct.unpack(">H", pdu[1:3])[0] if len(pdu) >= 3 else -1
                self.frames.append((unit, fc, addr))
                reply = await self._reply(unit, fc, pdu)
                if reply is None:
                    continue
                writer.write(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()

    async def _reply(self, unit: int, fc: int, pdu: bytes) -> bytes | None:
        if unit != self.unit:
            return bytes([fc | 0x80, 0x0B])  # gateway target failed to respond
        if fc == 3:
            addr, count = struct.unpack(">HH", pdu[1:5])
            if self.fail_reads:
                return bytes([0x83, 0x04])
            values = [self.registers.get(a) for a in range(addr, addr + count)]
            if any(v is None for v in values):
                return bytes([0x83, 0x02])
            return bytes([3, 2 * count]) + b"".join(struct.pack(">H", v) for v in values)
        if fc == 6:
            addr, value = struct.unpack(">HH", pdu[1:5])
            if addr not in self.registers:
                return bytes([0x86, 0x02])
            self.registers[addr] = value
            if self.drop_fc06_response:
                return None
            if self.fc06_delay:
                await asyncio.sleep(self.fc06_delay)
            return bytes([6]) + pdu[1:5]
        return bytes([fc | 0x80, 0x01])
