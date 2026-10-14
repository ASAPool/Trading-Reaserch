"""Downloading, storing and validating historical price data (Phases 3-4).

Validation will check timestamps, duplicates, missing bars, corrupted
values, timezone consistency and unexpected gaps. Questionable data is
never silently repaired, and every change is logged.
"""
