import os
import platform
import socket
from datetime import datetime, timezone
from pathlib import Path

import psutil


GIB = 1024 ** 3


def _bytes_to_gb(value: int) -> float:
    return round(value / GIB, 2)


def _get_cpu_temperature() -> dict:
    temperatures = psutil.sensors_temperatures(fahrenheit=False)

    if not temperatures:
        return {
            "available": False,
            "celsius": None,
            "fahrenheit": None,
        }

    preferred_groups = ("k10temp", "coretemp", "cpu_thermal")

    for group_name in preferred_groups:
        entries = temperatures.get(group_name, [])

        for entry in entries:
            if entry.current is not None:
                celsius = round(entry.current, 1)

                return {
                    "available": True,
                    "celsius": celsius,
                    "fahrenheit": round((celsius * 9 / 5) + 32, 1),
                    "sensor": group_name,
                    "label": entry.label or "CPU",
                }

    for group_name, entries in temperatures.items():
        for entry in entries:
            if entry.current is not None:
                celsius = round(entry.current, 1)

                return {
                    "available": True,
                    "celsius": celsius,
                    "fahrenheit": round((celsius * 9 / 5) + 32, 1),
                    "sensor": group_name,
                    "label": entry.label or "Unknown",
                }

    return {
        "available": False,
        "celsius": None,
        "fahrenheit": None,
    }


def _get_load_average() -> dict:
    if not hasattr(os, "getloadavg"):
        return {
            "available": False,
            "one_minute": None,
            "five_minutes": None,
            "fifteen_minutes": None,
        }

    one, five, fifteen = os.getloadavg()

    return {
        "available": True,
        "one_minute": round(one, 2),
        "five_minutes": round(five, 2),
        "fifteen_minutes": round(fifteen, 2),
    }


def get_system_health() -> dict:
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = psutil.disk_usage(Path("/"))
    boot_time = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    checked_at = datetime.now(timezone.utc)

    logical_cores = psutil.cpu_count(logical=True)
    physical_cores = psutil.cpu_count(logical=False)

    return {
        "status": "healthy",
        "system": {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "platform_release": platform.release(),
            "architecture": platform.machine(),
            "boot_time": boot_time.isoformat(),
            "uptime_seconds": int((checked_at - boot_time).total_seconds()),
        },
        "cpu": {
            "percent_used": psutil.cpu_percent(interval=0.5),
            "physical_cores": physical_cores,
            "logical_cores": logical_cores,
            "frequency_mhz": (
                round(psutil.cpu_freq().current, 1)
                if psutil.cpu_freq()
                else None
            ),
            "load_average": _get_load_average(),
            "temperature": _get_cpu_temperature(),
        },
        "memory": {
            "percent_used": memory.percent,
            "used_gb": _bytes_to_gb(memory.used),
            "available_gb": _bytes_to_gb(memory.available),
            "total_gb": _bytes_to_gb(memory.total),
        },
        "swap": {
            "percent_used": swap.percent,
            "used_gb": _bytes_to_gb(swap.used),
            "free_gb": _bytes_to_gb(swap.free),
            "total_gb": _bytes_to_gb(swap.total),
        },
        "disk": {
            "mount": "/",
            "percent_used": disk.percent,
            "used_gb": _bytes_to_gb(disk.used),
            "free_gb": _bytes_to_gb(disk.free),
            "total_gb": _bytes_to_gb(disk.total),
        },
        "checked_at": checked_at.isoformat(),
    }