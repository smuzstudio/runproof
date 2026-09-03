"""runproof — deterministic verification that a scheduled agent actually ran.

Answers four questions from an agent's own database, with no model in the
loop: did it run, did it do work, did the work land, and would anyone know
if it hadn't.
"""
from .model import Assertion, Dimension, Report, Result, Status  # noqa: F401

__version__ = "0.1.0"
