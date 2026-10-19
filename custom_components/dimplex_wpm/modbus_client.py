"""Async Modbus TCP client wrapper."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import inspect
import logging
from typing import Any

from pymodbus.client import AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException

from .const import REGISTER_OFFSET

LOGGER = logging.getLogger(__name__)


class DimplexModbusClient:
    """Provide async access to a Modbus TCP device."""

    def __init__(
        self,
        host: str,
        port: int,
        unit_id: int,
        timeout: int,
    ) -> None:
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._timeout = timeout
        self._client: AsyncModbusTcpClient | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the Modbus connection."""
        if self._client and self._client.connected:
            return

        self._client = AsyncModbusTcpClient(
            host=self._host,
            port=self._port,
            timeout=self._timeout,
        )
        if not await self._client.connect():
            raise ConnectionError("Unable to open Modbus connection")
        LOGGER.debug("Connected to Modbus host %s:%s", self._host, self._port)

    async def close(self) -> None:
        """Close the Modbus connection (pymodbus 3.x close() is synchronous)."""
        if self._client:
            try:
                self._client.close()
            except Exception as err:
                LOGGER.debug("Error closing Modbus connection: %s", err)
            LOGGER.debug("Closed Modbus connection")
        self._client = None

    async def _ensure_connected(self) -> None:
        """Ensure that the connection is open."""
        if self._client is None or not self._client.connected:
            await self.connect()

    async def read_holding_registers(
        self, address: int, count: int
    ) -> list[int] | None:
        """Read holding registers."""
        return await self._read("read_holding_registers", address, count)

    async def read_input_registers(
        self, address: int, count: int
    ) -> list[int] | None:
        """Read input registers."""
        return await self._read("read_input_registers", address, count)

    async def read_coils(self, address: int, count: int) -> list[bool] | None:
        """Read coils (digital outputs/inputs)."""
        async with self._lock:
            await self._ensure_connected()
            try:
                assert self._client is not None
                result = await self._client.read_coils(
                    address=address,
                    count=count,
                    **self._unit_kwargs(self._client.read_coils),
                )
            except ModbusException as err:
                LOGGER.error("Modbus coil read failed: %s", err)
                raise
            if result.isError():
                LOGGER.warning("Modbus coil read error at %s: %s", address, result)
                return None
            return list(result.bits)[:count]

    async def write_register(self, address: int, value: int) -> None:
        """Write a single holding register."""
        address += REGISTER_OFFSET
        async with self._lock:
            try:
                await self._ensure_connected()
                assert self._client is not None
                result = await self._client.write_register(
                    address, value, **self._unit_kwargs(self._client.write_register)
                )
            except ModbusException as err:
                LOGGER.error("Modbus write failed: %s", err)
                raise
            except (ConnectionError, OSError) as err:
                # connect() raises builtin ConnectionError; map to ModbusException
                # so entity write handlers surface a friendly HomeAssistantError.
                LOGGER.error("Modbus write connection failed: %s", err)
                raise ModbusException(f"Connection failed: {err}") from err
            if result.isError():
                raise ModbusException(f"Write error: {result}")
            LOGGER.debug("Wrote register %s=%s", address, value)

    async def _read(
        self, method: str, address: int, count: int
    ) -> list[int] | None:
        """Read registers using provided client method."""
        async with self._lock:
            await self._ensure_connected()
            try:
                assert self._client is not None
                func = getattr(self._client, method)
                result = await func(
                    address=address,
                    count=count,
                    **self._unit_kwargs(func),
                )
            except ModbusException as err:
                LOGGER.error("Modbus read failed: %s", err)
                raise
            if result.isError():
                LOGGER.warning("Modbus read error for %s at %s: %s", method, address, result)
                return None
            LOGGER.debug(
                "Read %s registers from %s starting at %s: %s",
                count,
                method,
                address,
                result.registers,
            )
            return result.registers

    def _unit_kwargs(self, func: Callable[..., Any]) -> dict[str, int]:
        """Return the correct unit-id argument for the pymodbus call.

        pymodbus renamed the argument over time: ``unit`` (<3.0), ``slave``
        (3.0-3.9) and ``device_id`` (3.10+). Without a match the request would
        silently go to the library default unit 1 — on a shared RTU gateway that
        is a *different device* — so an unknown signature is an error.
        """
        params = inspect.signature(func).parameters
        for name in ("device_id", "slave", "unit"):
            if name in params:
                return {name: self._unit_id}
        raise TypeError(
            f"Cannot pass Modbus unit id to {getattr(func, '__name__', func)}: "
            "unsupported pymodbus signature"
        )

    async def read_ranges(
        self,
        ranges: Iterable[tuple[int, int]],
        register_type: str,
    ) -> dict[int, int]:
        """Batch read multiple ranges and return register/value mapping."""
        values: dict[int, int] = {}
        for start, count in ranges:
            modbus_start = start + REGISTER_OFFSET
            if register_type == "holding":
                data = await self.read_holding_registers(modbus_start, count)
            else:
                data = await self.read_input_registers(modbus_start, count)
            if data is None:
                continue
            for offset, value in enumerate(data):
                values[start + offset] = value
        return values


async def execute_read_plan(
    client: DimplexModbusClient,
    plan: Iterable[tuple[str, int, int]],
) -> tuple[dict[int, int], dict[int, bool], int]:
    """Execute ``(obj, start, count)`` reads, tolerating per-chunk failures.

    Returns ``(holding, coils, failed_chunks)``. A chunk that answers with a
    Modbus exception (e.g. illegal address on an unsupported register) or that
    times out is skipped, so the remaining registers still update. If *every*
    chunk failed, the connection is closed (forcing a clean reconnect on the
    next poll) and ``ConnectionError`` is raised so the caller marks the update
    failed. Read-only: this never writes.
    """
    holding: dict[int, int] = {}
    coils: dict[int, bool] = {}
    chunks = list(plan)
    failed = 0
    for obj, start, count in chunks:
        try:
            if obj == "holding":
                data = await client.read_holding_registers(start, count)
                if data:
                    for offset, value in enumerate(data):
                        holding[start + offset] = value
                else:
                    failed += 1
            else:
                bits = await client.read_coils(start, count)
                if bits:
                    for offset, value in enumerate(bits):
                        coils[start + offset] = bool(value)
                else:
                    failed += 1
        except (TimeoutError, ModbusException, ConnectionError, OSError) as err:
            failed += 1
            LOGGER.debug("Read of %s %s+%s failed: %s", obj, start, count, err)
    if chunks and failed == len(chunks):
        await client.close()
        raise ConnectionError(f"All {failed} Modbus read requests failed")
    return holding, coils, failed


async def probe_device(
    client: DimplexModbusClient,
    probe_address: int,
    capability_probes: dict[str, int],
) -> dict[str, bool]:
    """Validate the connection and probe optional capabilities (read-only).

    Reads ``probe_address`` (a documented register for the selected software
    version); raises ``ConnectionError`` if it does not answer. Each entry of
    ``capability_probes`` (name -> register) is read once; any failure means
    "absent". Pass an empty dict to skip probing (H software).
    """
    hints: dict[str, bool] = {}
    try:
        await client.connect()
        if await client.read_holding_registers(probe_address, 1) is None:
            raise ConnectionError("No response from device")
        for name, reg in capability_probes.items():
            try:
                hints[name] = await client.read_holding_registers(reg, 1) is not None
            except Exception:  # noqa: BLE001 - a probe failure just means "absent"
                hints[name] = False
    finally:
        await client.close()
    return hints
