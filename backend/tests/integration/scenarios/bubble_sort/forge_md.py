"""Bubble sort forge.md content — minimal whitepaper for full pipeline test."""

FORGE_MD = """\
# Bubble Sort Library

## Overview

This document specifies a small library that provides a bubble sort
implementation for sorting lists of integers.

## Functional Requirements

The system shall provide a function `bubble_sort` that accepts a list of
integers and returns a new list containing the same integers sorted in
ascending order.  The original list shall not be modified.

The system shall handle the following edge cases:
- An empty list shall return an empty list.
- A list with a single element shall return a list containing that element.
- A list that is already sorted shall be returned sorted.

The system shall raise a TypeError if any element in the input list is not
an integer.
"""
