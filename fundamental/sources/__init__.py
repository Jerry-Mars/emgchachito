"""Acquisition source workers.

The package separates transport-specific workers from the acquisition
controller. Existing GUI code can keep using AcquisitionController while new
sources are added here.
"""

from fundamental.sources.base import AcquisitionSource, SourceName, SourceWorker
from fundamental.sources.ble_w2 import BLEW2Source, BLEW2Worker, W2BLEConfig
from fundamental.sources.myo import MyoBLEConfig, MyoSource, MyoWorker
from fundamental.sources.serial_ads1299 import SerialADS1299Source, SerialWorker

__all__ = [
    "AcquisitionSource",
    "BLEW2Source",
    "BLEW2Worker",
    "MyoBLEConfig",
    "MyoSource",
    "MyoWorker",
    "SerialADS1299Source",
    "SerialWorker",
    "SourceName",
    "SourceWorker",
    "W2BLEConfig",
]
