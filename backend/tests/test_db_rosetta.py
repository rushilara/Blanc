"""Startup warning for an x86_64 Python under Rosetta, where loading sqlite-vec can crash."""

import logging
import os
from types import SimpleNamespace

import pytest

from app import db

ARM_KERNEL = "Darwin Kernel Version 24.6.0: root:xnu-11417.140.69~1/RELEASE_ARM64_T8112"
INTEL_KERNEL = "Darwin Kernel Version 24.6.0: root:xnu-11417.140.69~1/RELEASE_X86_64"


def fake_host(monkeypatch, platform_name: str, machine: str, kernel: str) -> None:
    monkeypatch.setattr(db.sys, "platform", platform_name)
    monkeypatch.setattr(db.platform, "machine", lambda: machine)
    real = os.uname()
    monkeypatch.setattr(
        db.os, "uname", lambda: SimpleNamespace(sysname=real.sysname, version=kernel)
    )


@pytest.mark.parametrize(
    ("platform_name", "machine", "kernel", "expected"),
    [
        ("darwin", "x86_64", ARM_KERNEL, True),  # Rosetta
        ("darwin", "arm64", ARM_KERNEL, False),  # native Apple Silicon
        ("darwin", "x86_64", INTEL_KERNEL, False),  # Intel Mac
        ("linux", "x86_64", "#1 SMP ARM64", False),
    ],
)
def test_running_under_rosetta(monkeypatch, platform_name, machine, kernel, expected) -> None:
    fake_host(monkeypatch, platform_name, machine, kernel)
    assert db.running_under_rosetta() is expected


def test_make_engine_warns_under_rosetta(monkeypatch, caplog) -> None:
    fake_host(monkeypatch, "darwin", "x86_64", ARM_KERNEL)
    with caplog.at_level(logging.WARNING, logger="app.db"):
        db.make_engine("sqlite:///:memory:").dispose()
    assert db.ROSETTA_WARNING in caplog.messages


def test_make_engine_silent_on_native_python(monkeypatch, caplog) -> None:
    fake_host(monkeypatch, "darwin", "arm64", ARM_KERNEL)
    with caplog.at_level(logging.WARNING, logger="app.db"):
        db.make_engine("sqlite:///:memory:").dispose()
    assert db.ROSETTA_WARNING not in caplog.messages
