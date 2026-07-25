#!/usr/bin/env python3
"""Log power-consumption statistics from a Joulescope into InfluxDB.

Subscribes to the Joulescope's ~2 Hz statistics stream and writes one point per
frame (current/voltage/power averages plus accumulated charge/energy) to an
InfluxDB v2 bucket. Reconnects automatically if the device is unplugged.

Configuration is read from environment variables (see /etc/joulescope-logger/influx.env):
    INFLUX_URL     e.g. http://localhost:8086
    INFLUX_ORG     InfluxDB organization
    INFLUX_BUCKET  target bucket
    INFLUX_TOKEN   write token
    MEASUREMENT    optional measurement name (default: "power")
"""
import logging
import os
import queue
import signal
import sys
import time

import joulescope
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("power_logger")
# The joulescope driver logs very verbosely at INFO; quiet it down.
logging.getLogger("joulescope").setLevel(logging.WARNING)
logging.getLogger("pyjoulescope_driver").setLevel(logging.WARNING)

INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086")
INFLUX_ORG = os.environ["INFLUX_ORG"]
INFLUX_BUCKET = os.environ["INFLUX_BUCKET"]
INFLUX_TOKEN = os.environ["INFLUX_TOKEN"]
MEASUREMENT = os.environ.get("MEASUREMENT", "power")
# If no statistics frame arrives within this many seconds while a device is
# open, assume the stream is wedged (e.g. after a USB hotplug the driver
# re-enumerates but does not resume our callback) and exit so systemd restarts
# us cleanly. Frames normally arrive at ~2 Hz.
#
# NOTE on target power: the JS110 retains its current-range setting (the path
# that powers the device-under-test) as long as it stays USB-powered, even
# while no software is connected. close()/reopen does NOT open the path, so a
# service restart does not power-cycle the target; only unplugging the USB
# cable (which removes power from the JS110 itself) does.
STATS_TIMEOUT = float(os.environ.get("STATS_TIMEOUT", "20"))

_running = True


def _stop(signum, _frame):
    global _running
    log.info("received signal %s, shutting down", signum)
    _running = False


def _get(stats, *path):
    """Safely walk the nested statistics dict, returning None if absent."""
    node = stats
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def build_point(stats, serial, model):
    """Turn one Joulescope statistics frame into an InfluxDB Point."""
    p = (
        Point(MEASUREMENT)
        .tag("device", serial)
        .tag("model", model)
        .time(time.time_ns(), WritePrecision.NS)
    )
    field_map = {
        "current": ("signals", "current", "avg", "value"),
        "voltage": ("signals", "voltage", "avg", "value"),
        "power": ("signals", "power", "avg", "value"),
        "current_min": ("signals", "current", "min", "value"),
        "current_max": ("signals", "current", "max", "value"),
        "power_min": ("signals", "power", "min", "value"),
        "power_max": ("signals", "power", "max", "value"),
        "charge_c": ("accumulators", "charge", "value"),
        "energy_j": ("accumulators", "energy", "value"),
    }
    n = 0
    for field, path in field_map.items():
        val = _get(stats, *path)
        if val is not None:
            p.field(field, float(val))
            n += 1
    return p if n else None


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    log.info("writing to %s org=%s bucket=%s measurement=%s",
             INFLUX_URL, INFLUX_ORG, INFLUX_BUCKET, MEASUREMENT)

    q = queue.Queue(maxsize=1000)

    while _running:
        try:
            device = joulescope.scan_require_one(config="auto")
        except Exception as e:
            log.warning("no Joulescope found (%s); retrying in 5s", e)
            time.sleep(5)
            continue

        serial = getattr(device, "serial_number", "unknown")
        model = getattr(device, "model", "unknown")
        log.info("connected to %s (%s)", serial, model)

        def stats_cb(stats):
            try:
                q.put_nowait(stats)
            except queue.Full:
                pass  # drop a frame rather than block the driver thread

        device.statistics_callback = stats_cb
        try:
            with device:
                last_frame = time.monotonic()
                while _running:
                    try:
                        stats = q.get(timeout=2.0)
                    except queue.Empty:
                        if time.monotonic() - last_frame > STATS_TIMEOUT:
                            log.error(
                                "no statistics for %.0fs (device wedged?); "
                                "exiting for clean restart", STATS_TIMEOUT)
                            write_api.close()
                            client.close()
                            return 1
                        continue
                    last_frame = time.monotonic()
                    point = build_point(stats, serial, model)
                    if point is None:
                        continue
                    try:
                        write_api.write(bucket=INFLUX_BUCKET, record=point)
                    except Exception as e:
                        log.error("influx write failed: %s", e)
                        time.sleep(1)
        except Exception as e:
            log.warning("device error (%s); will rescan in 3s", e)
            time.sleep(3)
        finally:
            device.statistics_callback = None

    write_api.close()
    client.close()
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(run())
