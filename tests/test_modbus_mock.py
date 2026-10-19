"""Offline Modbus TCP mock tests (in-process pymodbus server, no real device).

Simulates a TCP->RTU gateway carrying two units: unit 2 = Dimplex WPM (H map),
unit 1 = an unrelated decoy device. Every request the server receives is
recorded so tests can assert that only reads (FC03/FC01) ever happen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket

from conftest import load
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusServerContext,
    ModbusSparseDataBlock,
)
from pymodbus.server import ModbusTcpServer
import pytest

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

reg = load("registers")
mc = load("modbus_client")

WRITE_FCS = {5, 6, 15, 16}
UNIT_DIMPLEX = 2
UNIT_DECOY = 1

# H-map holding registers for the simulated Dimplex (unit 2).
H_VALUES = {
    13: 0,        # fault: none
    14: 2,        # status: heat pump on - heating
    27: 0xFF9C,   # outdoor -10.0 °C (negative, two's complement)
    28: 300,      # return setpoint 30.0
    29: 285,      # return 28.5
    30: 480,      # dhw 48.0
    31: 321,      # flow 32.1
    40: 500,      # dhw setpoint 50.0
    41: 12,       # source outlet 1.2
    94: 0,        # lock: none
}
DECOY_VALUES = {27: 999, 1: 999}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockGateway:
    def __init__(self, dimplex=None, decoy=None, coils=None):
        """``coils``: optional {address: bool} coil map for unit 2 (FC01)."""
        self.port = _free_port()
        self.requests: list[tuple[int, int]] = []  # (device_id, function_code)
        # (device_id, function_code, address, count) for read-plan assertions.
        self.reads: list[tuple[int, int, int | None, int | None]] = []
        dimplex_ctx = {"hr": ModbusSparseDataBlock(dict(dimplex or H_VALUES))}
        if coils is not None:
            dimplex_ctx["co"] = ModbusSparseDataBlock(dict(coils))
        devices = {
            UNIT_DIMPLEX: ModbusDeviceContext(**dimplex_ctx),
            UNIT_DECOY: ModbusDeviceContext(hr=ModbusSparseDataBlock(dict(decoy or DECOY_VALUES))),
        }
        self.server = ModbusTcpServer(
            ModbusServerContext(devices=devices, single=False),
            address=("127.0.0.1", self.port),
            trace_pdu=self._trace,
        )
        self._task = None

    def _trace(self, sending, pdu):
        if not sending:
            self.requests.append((pdu.dev_id, pdu.function_code))
            self.reads.append((pdu.dev_id, pdu.function_code,
                               getattr(pdu, "address", None), getattr(pdu, "count", None)))
        return pdu

    async def __aenter__(self):
        self._task = asyncio.create_task(self.server.serve_forever())
        for _ in range(50):
            with contextlib.suppress(OSError):
                _r, w = await asyncio.open_connection("127.0.0.1", self.port)
                w.close()
                break
            await asyncio.sleep(0.05)
        return self

    async def __aexit__(self, *exc):
        await self.server.shutdown()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task

    def function_codes(self) -> set[int]:
        return {fc for _dev, fc in self.requests}


def _h_plan(modules=frozenset(reg.CORE_MODULES)):
    specs = reg.active_registers(
        version="H", enabled_modules=modules, capabilities=frozenset(), include_re=False
    )
    return specs, reg.build_read_plan(specs, [], "H", set(), cluster_gap=reg.read_cluster_gap("H"))


def run(coro):
    return asyncio.run(coro)


def test_unit_id_routed_to_device_2():
    """Regression for the pymodbus>=3.10 ``device_id`` rename (would hit unit 1)."""
    async def go():
        async with MockGateway() as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            data = await client.read_holding_registers(27, 1)
            await client.close()
            return data, gw.requests
    data, requests = run(go())
    assert data == [0xFF9C]
    assert requests and all(dev == UNIT_DIMPLEX for dev, _fc in requests)


def test_unit_kwargs_rejects_unknown_signature():
    client = mc.DimplexModbusClient("127.0.0.1", 1, UNIT_DIMPLEX, 1)

    def old(address, count, slave=1): ...

    def new(address, count, device_id=1): ...

    def unknown(address, count): ...

    assert client._unit_kwargs(old) == {"slave": UNIT_DIMPLEX}
    assert client._unit_kwargs(new) == {"device_id": UNIT_DIMPLEX}
    with pytest.raises(TypeError):
        client._unit_kwargs(unknown)


def test_h_plan_full_cycle_decodes_and_reads_only():
    async def go():
        async with MockGateway() as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            specs, plan = _h_plan()
            holding, coils, failed = await mc.execute_read_plan(client, plan)
            await client.close()
            return specs, holding, coils, failed, gw.function_codes()
    specs, holding, coils, failed, fcs = run(go())
    assert failed == 0 and coils == {}
    values = {s.key: reg.decode_value(s, holding[s.resolve_address("H")]) for s in specs
              if not s.enum}
    assert values["outdoor_temperature"] == -10.0
    assert values["return_setpoint_temperature"] == 30.0
    assert values["return_temperature"] == 28.5
    assert values["flow_temperature"] == 32.1
    assert values["dhw_temperature"] == 48.0
    assert values["source_outlet_temperature"] == 1.2
    assert values["status_code"] == 2 and values["fault_code"] == 0 and values["lock_code"] == 0
    assert fcs == {3}  # FC03 only: no FC04, no coils, no writes


def test_missing_h_register_is_partial_not_total_failure():
    """A register the controller rejects (exception 2) only drops its chunk."""
    async def go():
        values = dict(H_VALUES)
        del values[94]  # e.g. lock register unsupported on this unit
        async with MockGateway(dimplex=values) as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            _specs, plan = _h_plan()
            result = await mc.execute_read_plan(client, plan)
            await client.close()
            return result
    holding, _coils, failed = run(go())
    assert failed == 1
    assert 94 not in holding
    assert holding[27] == 0xFF9C and holding[14] == 2


def test_all_chunks_failing_raises_and_closes():
    async def go():
        async with MockGateway(dimplex={500: 1}) as gw:  # none of the H registers exist
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            _specs, plan = _h_plan()
            with pytest.raises(ConnectionError):
                await mc.execute_read_plan(client, plan)
            return client._client
    assert run(go()) is None  # connection closed for a clean reconnect


def test_timeout_then_recovery():
    """Silent peer (accepts TCP, never answers) -> timeout; later reconnect works."""
    async def go():
        port = _free_port()

        async def silent(reader, writer):
            with contextlib.suppress(Exception):
                await reader.read(1024)
                await asyncio.sleep(10)

        server = await asyncio.start_server(silent, "127.0.0.1", port)
        client = mc.DimplexModbusClient("127.0.0.1", port, UNIT_DIMPLEX, 1)
        _specs, plan = _h_plan()
        with pytest.raises(ConnectionError):
            await mc.execute_read_plan(client, plan[:1])
        server.close()
        await server.wait_closed()

        async with MockGateway() as gw:
            client2 = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            holding, _c, failed = await mc.execute_read_plan(client2, plan)
            await client2.close()
            return holding, failed

    holding, failed = run(go())
    assert failed == 0 and holding[31] == 321


def test_unreachable_host_fails_cleanly():
    async def go():
        client = mc.DimplexModbusClient("127.0.0.1", _free_port(), UNIT_DIMPLEX, 1)
        _specs, plan = _h_plan()
        with pytest.raises(ConnectionError):
            await mc.execute_read_plan(client, plan)
    run(go())


def test_probe_h_uses_register_27_and_skips_capability_probes():
    async def go():
        async with MockGateway() as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            hints = await mc.probe_device(
                client, reg.connection_probe_address("H"), reg.capability_probes("H")
            )
            return hints, gw.requests
    hints, requests = run(go())
    assert hints == {}
    assert requests == [(UNIT_DIMPLEX, 3)]  # exactly one FC03 read (register 27)


def test_probe_h_fails_when_register_27_missing():
    async def go():
        async with MockGateway(dimplex={1: 215}) as gw:  # looks like an L/M map
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            with pytest.raises(ConnectionError):
                await mc.probe_device(client, reg.connection_probe_address("H"), {})
    run(go())


def test_probe_lm_still_probes_capabilities():
    async def go():
        lm = {1: 215, 5170: 150}  # outdoor + electric meter present, 5168/114 absent
        async with MockGateway(dimplex=lm) as gw:
            client = mc.DimplexModbusClient("127.0.0.1", gw.port, UNIT_DIMPLEX, 2)
            hints = await mc.probe_device(
                client, reg.connection_probe_address("M"), reg.capability_probes("M")
            )
            return hints, gw.function_codes()
    hints, fcs = run(go())
    assert hints == {
        reg.CAP_ELECTRIC_METER: True, reg.CAP_HEAT_METER: False, reg.CAP_INVERTER_FREQ: False,
    }
    assert fcs == {3}


def test_write_encoding_offline_only():
    """Encoding of J/L/M write specs is validated without any device I/O."""
    by_key = {w.key: w for w in reg.WRITE_REGISTERS}
    off = by_key["set_hc1_curve_offset"]
    assert off.to_raw(-19) == 0 and off.to_raw(0) == 19 and off.to_raw(19) == 38
    assert off.to_raw(99) == 38 and off.from_raw(20) == 1  # clamped / decoded
    dhw = by_key["set_dhw_setpoint"]
    assert dhw.to_raw(48.4) == 48 and dhw.to_raw(200) == 85 and dhw.to_raw(-5) == 10
    # H never yields a write spec, so no H write can be encoded or sent.
    assert reg.active_write_registers(enabled_modules=frozenset(reg.ALL_MODULES), version="H") == []
