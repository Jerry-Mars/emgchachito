"""Acquisition source workers.

The package separates transport-specific workers from the acquisition
controller. Existing GUI code can keep using AcquisitionController while new
sources are added here.
"""

from fundamental.sources.base import (
    AcquisitionSource,
    CaptureClock,
    SourceName,
    SourceWorker,
    SourceWorkerGroup,
    WorkerControl,
)
from fundamental.sources.ble_w2 import (
    BLEW2Source,
    BLEW2Worker,
    SerialW2Worker,
    W2BLEConfig,
    W2Config,
    W2DeviceConfig,
    W2SerialDeviceConfig,
    W2WorkerGroup,
)
from fundamental.sources.bwt901 import (
    BWT901BLEConfig,
    BWT901BLEWorker,
    BWT901DeviceConfig,
    BWT901Source,
)
from fundamental.sources.myo import MyoBLEConfig, MyoSource, MyoWorker
from fundamental.sources.serial_ads1299 import SerialADS1299Source, SerialWorker

__all__ = [
    "AcquisitionSource",
    "BWT901BLEConfig",
    "BWT901BLEWorker",
    "BWT901DeviceConfig",
    "BWT901Source",
    "BLEW2Source",
    "BLEW2Worker",
    "CaptureClock",
    "MyoBLEConfig",
    "MyoSource",
    "MyoWorker",
    "SerialADS1299Source",
    "SerialWorker",
    "SerialW2Worker",
    "SourceName",
    "SourceWorker",
    "SourceWorkerGroup",
    "W2BLEConfig",
    "W2Config",
    "W2DeviceConfig",
    "W2SerialDeviceConfig",
    "W2WorkerGroup",
    "WorkerControl",
]
