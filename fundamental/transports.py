"""Small reusable byte transports for serial ports and BLE GATT devices."""

from __future__ import annotations

import asyncio
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable

try:
    import serial as _serial
except ImportError:  # pragma: no cover - depends on local runtime
    _serial = None

try:
    from bleak import BleakClient as _BleakClient
    from bleak import BleakScanner as _BleakScanner
    from bleak.exc import BleakDeviceNotFoundError as _BleakDeviceNotFoundError
except ImportError:  # pragma: no cover - depends on local runtime
    _BleakClient = None
    _BleakScanner = None
    _BleakDeviceNotFoundError = None


ByteCallback = Callable[[bytearray], None]
LogCallback = Callable[[str], None]
_BLE_CONNECT_LOCK = threading.Lock()


@dataclass(frozen=True)
class SerialByteConfig:
    port: str
    baud_rate: int
    timeout_s: float

    def normalized(self) -> "SerialByteConfig":
        return SerialByteConfig(
            port=self.port.strip(),
            baud_rate=max(1, int(self.baud_rate)),
            timeout_s=max(0.001, float(self.timeout_s)),
        )


class SerialByteTransport:
    """8N1 serial byte transport with no device-protocol knowledge."""

    def __init__(self, config: SerialByteConfig, *, backend: Any = None) -> None:
        self.config = config.normalized()
        self.backend = _serial if backend is None else backend
        self.handle = None

    @property
    def is_open(self) -> bool:
        return bool(self.handle is not None and getattr(self.handle, "is_open", True))

    def open(self) -> None:
        if self.backend is None:
            raise RuntimeError("pyserial is not installed; serial acquisition is unavailable.")
        if not self.config.port:
            raise ValueError("Serial port cannot be empty.")
        self.handle = self.backend.Serial(
            self.config.port,
            self.config.baud_rate,
            bytesize=self.backend.EIGHTBITS,
            parity=self.backend.PARITY_NONE,
            stopbits=self.backend.STOPBITS_ONE,
            timeout=self.config.timeout_s,
        )

    def reset_input_buffer(self) -> None:
        if self.handle is not None:
            self.handle.reset_input_buffer()

    def read(self, size: int = 512) -> bytes:
        if self.handle is None:
            raise ConnectionError("Serial transport is not open.")
        return bytes(self.handle.read(max(1, int(size))))

    def write(self, data: bytes | bytearray) -> None:
        if self.handle is None:
            raise ConnectionError("Serial transport is not open.")
        self.handle.write(bytes(data))
        self.handle.flush()

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            handle.close()


@dataclass(frozen=True)
class BleGattConfig:
    address: str = ""
    name_filter: str = ""
    scan_timeout_s: float = 10.0
    windows_address_type: str | None = None

    def normalized(self) -> "BleGattConfig":
        address_type = self.windows_address_type
        if address_type not in (None, "public", "random"):
            raise ValueError("windows_address_type must be None, 'public', or 'random'.")
        return BleGattConfig(
            address=self.address.strip(),
            name_filter=self.name_filter.strip(),
            scan_timeout_s=max(0.1, float(self.scan_timeout_s)),
            windows_address_type=address_type,
        )


class BleGattTransport:
    """BLE connection/notification transport independent from device protocols."""

    def __init__(
        self,
        config: BleGattConfig,
        *,
        log: LogCallback | None = None,
        client_factory: Any = None,
        scanner: Any = None,
        not_found_error: Any = None,
    ) -> None:
        self.config = config.normalized()
        self.log = log or (lambda _message: None)
        self.client_factory = _BleakClient if client_factory is None else client_factory
        self.scanner = _BleakScanner if scanner is None else scanner
        self.not_found_error = (
            _BleakDeviceNotFoundError if not_found_error is None else not_found_error
        )
        self.client = None
        self.device = None
        self.disconnected_event = threading.Event()
        self.resolved_address = ""
        self.resolved_name = ""
        self._notify_uuids: set[str] = set()

    @property
    def is_connected(self) -> bool:
        return bool(self.client is not None and self.client.is_connected)

    async def connect(self) -> None:
        if self.is_connected:
            return
        if self.client_factory is None or self.scanner is None:
            raise RuntimeError("bleak is not installed; BLE acquisition is unavailable.")
        if not self.config.address and not self.config.name_filter:
            raise ValueError("BLE address and name filter cannot both be empty.")

        # Every BLE transport runs in its own worker thread/event loop, so a
        # normal lock safely serializes Windows scan/connect operations.
        _BLE_CONNECT_LOCK.acquire()
        try:
            await self._scan_and_connect()
        finally:
            _BLE_CONNECT_LOCK.release()

    async def _scan_and_connect(self) -> None:
        """Follow the verified BWT demo: connect while the scanner is still active."""

        requested_address = self.config.address.casefold() if self.config.address else None
        folded_filter = self.config.name_filter.casefold()

        def matches(device, advertisement) -> bool:
            if requested_address:
                return (device.address or "").casefold() == requested_address
            advertised_name = advertisement.local_name or device.name or ""
            return folded_filter in advertised_name.casefold()

        loop = asyncio.get_running_loop()
        found_device = loop.create_future()

        def device_found(device, advertisement) -> None:
            if not found_device.done() and matches(device, advertisement):
                found_device.set_result(device)

        scanner_instance = None
        try:
            scanner_instance = self.scanner(device_found)
        except TypeError:
            # Small fake scanners and older test doubles may only expose the
            # class-level find helpers. Production bleak uses the callback path.
            self.device = await self._resolve_device()
            if self.device is None:
                self._raise_scan_timeout()
            await self._connect_device()
            return

        try:
            await scanner_instance.start()
            try:
                self.device = await asyncio.wait_for(
                    found_device,
                    timeout=self.config.scan_timeout_s,
                )
            except asyncio.TimeoutError as exc:
                target = self.config.address or f"name containing {self.config.name_filter!r}"
                raise TimeoutError(
                    f"No BLE device matching {target} was found within "
                    f"{self.config.scan_timeout_s:g} seconds."
                ) from exc
            await self._connect_device()
        finally:
            await scanner_instance.stop()

    def _raise_scan_timeout(self) -> None:
        target = self.config.address or f"name containing {self.config.name_filter!r}"
        raise TimeoutError(
            f"No BLE device matching {target} was found within "
            f"{self.config.scan_timeout_s:g} seconds."
        )

    async def _connect_device(self) -> None:
        self.resolved_address = str(self.device.address)
        self.resolved_name = str(self.device.name or self.config.name_filter or "-")
        address_types = [self.config.windows_address_type]
        if sys.platform == "win32" and self.config.windows_address_type is None:
            address_types = [None, "random", "public"]

        last_not_found: Exception | None = None
        for address_type in address_types:
            options: dict[str, Any] = {"disconnected_callback": self._on_disconnected}
            if sys.platform == "win32" and address_type is not None:
                options["winrt"] = {"address_type": address_type}
            client = self.client_factory(self.device, **options)
            try:
                await client.connect()
                self.client = client
                self.disconnected_event.clear()
                return
            except Exception as exc:
                if self.not_found_error is None or not isinstance(exc, self.not_found_error):
                    raise
                last_not_found = exc
        if last_not_found is not None:
            raise last_not_found
        raise ConnectionError(f"Could not connect to BLE device {self.resolved_address}.")

    async def _resolve_device(self):
        if self.config.address:
            self.log(f"Scanning for configured BLE address {self.config.address}...")
            return await self.scanner.find_device_by_address(
                self.config.address,
                timeout=self.config.scan_timeout_s,
            )

        folded_filter = self.config.name_filter.casefold()
        self.log(f"Scanning for BLE device name containing {self.config.name_filter!r}...")
        return await self.scanner.find_device_by_filter(
            lambda device, advertisement: (
                folded_filter in (device.name or "").casefold()
                or folded_filter in (advertisement.local_name or "").casefold()
            ),
            timeout=self.config.scan_timeout_s,
        )

    async def start_notify(self, uuid: str, callback: ByteCallback) -> None:
        if self.client is None:
            raise ConnectionError("BLE transport is not connected.")
        await self.client.start_notify(uuid, callback)
        self._notify_uuids.add(uuid)

    async def stop_notify(self, uuid: str) -> None:
        if self.client is None or uuid not in self._notify_uuids:
            return
        try:
            await self.client.stop_notify(uuid)
        finally:
            self._notify_uuids.discard(uuid)

    async def write(self, uuid: str, data: bytes | bytearray, *, response: bool = False) -> None:
        if self.client is None:
            raise ConnectionError("BLE transport is not connected.")
        if response:
            await self.client.write_gatt_char(uuid, bytes(data), response=True)
        else:
            await self.client.write_gatt_char(uuid, bytes(data))

    async def disconnect(self) -> None:
        client, self.client = self.client, None
        if client is None:
            return
        first_error: Exception | None = None
        for uuid in tuple(self._notify_uuids):
            try:
                if client.is_connected:
                    await client.stop_notify(uuid)
            except Exception as exc:  # pragma: no cover - disconnect dependent
                first_error = first_error or exc
            finally:
                self._notify_uuids.discard(uuid)
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception as exc:  # pragma: no cover - disconnect dependent
            first_error = first_error or exc
        if first_error is not None:
            raise first_error

    def _on_disconnected(self, _client) -> None:
        self.disconnected_event.set()


__all__ = [
    "BleGattConfig",
    "BleGattTransport",
    "SerialByteConfig",
    "SerialByteTransport",
]
