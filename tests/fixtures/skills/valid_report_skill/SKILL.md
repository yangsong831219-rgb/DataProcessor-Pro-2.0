---
schema_version: 1
skill_id: ppt-master
name: PPT Master
version: 1.2.0
description: Professional PowerPoint generation workflow
skill_type: report_backend
artifact_types:
  - pptx
capabilities:
  - read_skill_files
  - read_runtime_workspace
  - write_runtime_workspace
dependencies:
  - python-pptx
entrypoints:
  generate: workflows/generate.py:generate
source_url: https://github.com/example/ppt-master
min_app_version: 2.0.0
report_backend_contract: 1
template_modes:
  - none
  - normalized
---

# PPT Master

Professional PowerPoint generation.
