#!/usr/bin/env python3
"""Compatibility entry point: defaults to the new single-member pilot."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name('mongo.py')),run_name='__main__')
