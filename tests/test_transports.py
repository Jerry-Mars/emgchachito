from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fundamental.transports import BleGattConfig, BleGattTransport


class BleGattTransportTests(unittest.TestCase):
    def test_connects_while_callback_scanner_is_active_and_reuses_device(self) -> None:
        device = SimpleNamespace(name="WT901", address="CF:B6:E0:FC:2F:98")

        class Scanner:
            instance = None

            def __init__(self, callback) -> None:
                self.callback = callback
                self.active = False
                type(self).instance = self

            async def start(self) -> None:
                self.active = True
                self.callback(device, SimpleNamespace(local_name="WT901"))

            async def stop(self) -> None:
                self.active = False

        class Client:
            created_with = None

            def __init__(self, selected_device, **_kwargs) -> None:
                type(self).created_with = selected_device
                self.is_connected = False

            async def connect(self) -> None:
                assert Scanner.instance is not None
                if not Scanner.instance.active:
                    raise AssertionError("scanner stopped before BLE connect")
                self.is_connected = True

            async def disconnect(self) -> None:
                self.is_connected = False

        transport = BleGattTransport(
            BleGattConfig(address=device.address),
            client_factory=Client,
            scanner=Scanner,
            not_found_error=RuntimeError,
        )
        asyncio.run(transport.connect())

        self.assertIs(Client.created_with, device)
        self.assertTrue(transport.is_connected)
        self.assertFalse(Scanner.instance.active)
        asyncio.run(transport.disconnect())
        self.assertFalse(transport.is_connected)


if __name__ == "__main__":
    unittest.main()
