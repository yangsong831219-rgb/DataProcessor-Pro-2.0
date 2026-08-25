---
schema_version: 1
skill_id: data-formatter
name: Data Formatter
version: 1.0.0
description: Formats sensor data into structured reports
skill_type: instruction
artifact_types:
  - txt
  - csv
capabilities:
  - read_workspace
dependencies:
  - pandas
entrypoints:
  format: scripts/format.py
source_url: https://github.com/example/data-formatter
min_app_version: 2.0.0
---

# Data Formatter

This is a test skill for instruction-based data formatting.
