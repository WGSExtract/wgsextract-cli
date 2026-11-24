"""Collected only by explicit node ID in the differential-corpus test."""

import pytest


def test_case_passes() -> None:
    pass


def test_fix_is_skipped() -> None:
    pytest.skip("not run here")
