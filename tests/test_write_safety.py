"""Wire-level write safety of DimplexModbusClient (fault-injecting raw device).

Regression for DAI-592 D1 (pymodbus retransmitted an unanswered FC06 up to 3
more times) and D2 (a failed poll could close the connection under an
in-flight write).
"""

from __future__ import annotations

import asyncio
import logging

from conftest import load
from fake_modbus import FakeModbusDevice
from pymodbus.exceptions import ModbusException
import pytest

logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

mc = load("modbus_client")

UNIT = 2
REGS = {27: 118, 149: 48, 129: 19, 134: 1}


def run(coro):
    return asyncio.run(coro)


def test_single_write_lost_response_exactly_one_frame_then_recovers():
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        dev.drop_fc06_response = True
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 1)
        with pytest.raises(ModbusException):
            await client.write_register_single(149, 50)
        frames_after_write = list(dev.frames)
        closed = client._client is None
        # timeout recovery: next request reconnects and reads the true value
        value = await client.read_holding_registers(149, 1)
        await client.close()
        await dev.stop()
        return frames_after_write, closed, value
    frames, closed, value = run(go())
    assert frames == [(UNIT, 6, 149)]  # exactly one FC06 on the wire, no retransmission
    assert closed  # connection dropped so a late response cannot be mismatched
    assert value == [50]  # write applied on the device; outcome visible on re-read


def test_single_write_success_restores_library_retries():
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 1)
        await client.connect()
        before = client._client.ctx.retries
        await client.write_register_single(129, 16)
        after = client._client.ctx.retries
        await client.close()
        await dev.stop()
        return before, after, dev.frames, dev.registers[129]
    before, after, frames, reg = run(go())
    assert before == after == 3  # read retransmission behaviour unchanged
    assert frames == [(UNIT, 6, 129)] and reg == 16


def test_single_write_refuses_when_retransmission_cannot_be_disabled():
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 1)
        await client.connect()
        client._client.ctx.retries = None  # simulate an unknown pymodbus internals layout
        with pytest.raises(ModbusException, match="retransmission"):
            await client.write_register_single(149, 50)
        await client.close()
        await dev.stop()
        return dev.frames
    assert run(go()) == []  # refused before any bus traffic


def test_legacy_write_register_unchanged_for_jlm():
    """J/L/M keep the upstream write path (library retransmission included)."""
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        dev.drop_fc06_response = True
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 1)
        with pytest.raises(ModbusException):
            await client.write_register(149, 50)
        await client.close()
        await dev.stop()
        return dev.fc(6)
    assert len(run(go())) == 4  # 1 + 3 pymodbus retries, exactly as before


def test_close_waits_for_inflight_write():
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        dev.fc06_delay = 0.4
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 2)
        await client.connect()
        write = asyncio.create_task(client.write_register_single(149, 55))
        await asyncio.sleep(0.1)  # FC06 is on the wire, response pending
        await client.close()  # must wait for the write, not cut it off
        await write  # raises if the write was interrupted
        await dev.stop()
        return dev.frames, dev.registers[149], client._client
    frames, reg, cl = run(go())
    assert frames == [(UNIT, 6, 149)] and reg == 55 and cl is None


def test_failed_poll_does_not_interrupt_inflight_write():
    """Real scenario: every poll chunk fails -> execute_read_plan closes the link."""
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 2)
        await client.connect()
        dev.fc06_delay = 0.4
        dev.fail_reads = True
        write = asyncio.create_task(client.write_register_single(134, 0))
        await asyncio.sleep(0.1)
        poll = asyncio.create_task(mc.execute_read_plan(client, [("holding", 27, 1)]))
        await write  # completes despite the failing poll
        with pytest.raises(ConnectionError):
            await poll
        await dev.stop()
        return dev.fc(6), dev.registers[134]
    fc6, reg = run(go())
    assert fc6 == [(UNIT, 6, 134)] and reg == 0


def test_verified_sequence_is_atomic_against_polls_and_close():
    """Pre-read, FC06 and read-back run under one lock hold: no interleaving."""
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        dev.fc06_delay = 0.3
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 2)
        seq = asyncio.create_task(client.write_single_verified(149, 57))
        await asyncio.sleep(0.05)
        poll = asyncio.create_task(client.read_holding_registers(27, 1))
        closer = asyncio.create_task(client.close())
        result = await seq
        await poll
        await closer
        await dev.stop()
        return result, dev.frames
    result, frames = run(go())
    assert result == (48, True, 57)
    assert frames[:3] == [(UNIT, 3, 149), (UNIT, 6, 149), (UNIT, 3, 149)]
    assert frames[3:] == [(UNIT, 3, 27)]


def test_verified_unchanged_value_sends_no_write():
    async def go():
        dev = await FakeModbusDevice(REGS, UNIT).start()
        client = mc.DimplexModbusClient("127.0.0.1", dev.port, UNIT, 1)
        result = await client.write_single_verified(149, 48)
        await client.close()
        await dev.stop()
        return result, dev.frames
    result, frames = run(go())
    assert result == (48, False, None) and frames == [(UNIT, 3, 149)]
