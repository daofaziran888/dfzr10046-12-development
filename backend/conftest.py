"""Pytest 隔离：每个用例前恢复内存种子并清空分段代办状态。

放在 backend 根目录而不是 tests/ 内，使评测用例无论位于 tests/ 还是 backend 根
都能获得一致的内存隔离。
"""
from __future__ import annotations

import pytest

from app.services.segment_delegation import segment_service
from app.store import store


@pytest.fixture(autouse=True)
def reset_in_memory_workspace():
    store.reset_seed_data()
    segment_service.reset()
    yield
