from dataclasses import dataclass, asdict
from typing import Optional
import json
from pathlib import Path

@dataclass
class ColumnDef:
    name: str
    comment: str = ""
    data_type: str = "numeric"  # time, wavelength, strain, temperature, displacement, custom

@dataclass
class FileTemplate:
    id: str
    name: str  # e.g., "光明型光纤光栅", "东华型"
    file_format: str  # txt, csv, xlsx
    delimiter: str = ","
    skip_rows: int = 0
    data_start_row: int = 1
    columns: list[ColumnDef] = None

    def __post_init__(self):
        if self.columns is None:
            self.columns = []

def _templates_path() -> Path:
    """Return the path to the templates.json file in user home directory."""
    return Path.home() / ".dataprocessor" / "templates.json"

def load_templates() -> list[FileTemplate]:
    """从磁盘加载所有模板"""
    path = _templates_path()
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    templates = []
    for item in data:
        columns = []
        for col in item.get("columns", []):
            columns.append(ColumnDef(
                name=col.get("name", ""),
                comment=col.get("comment", ""),
                data_type=col.get("data_type", "numeric"),
            ))
        templates.append(FileTemplate(
            id=item.get("id", ""),
            name=item.get("name", ""),
            file_format=item.get("file_format", "txt"),
            delimiter=item.get("delimiter", ","),
            skip_rows=item.get("skip_rows", 0),
            data_start_row=item.get("data_start_row", 1),
            columns=columns,
        ))
    return templates

def save_template(template: FileTemplate) -> None:
    """保存模板到磁盘"""
    templates = load_templates()

    # Add or update template (by id)
    updated = False
    for i, t in enumerate(templates):
        if t.id == template.id:
            templates[i] = template
            updated = True
            break
    if not updated:
        templates.append(template)

    # Ensure directory exists
    path = _templates_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize
    data = []
    for t in templates:
        data.append({
            "id": t.id,
            "name": t.name,
            "file_format": t.file_format,
            "delimiter": t.delimiter,
            "skip_rows": t.skip_rows,
            "data_start_row": t.data_start_row,
            "columns": [asdict(col) for col in t.columns],
        })

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def parse_file(file_path: str, template: FileTemplate) -> dict:
    """根据模板解析数据文件"""
    import pandas as pd

    if template.file_format == "csv":
        df = pd.read_csv(file_path, delimiter=template.delimiter,
                        skiprows=template.skip_rows, header=None)
    elif template.file_format == "txt":
        df = pd.read_csv(file_path, delimiter=template.delimiter,
                        skiprows=template.skip_rows, header=None, encoding='gbk')
    elif template.file_format in ("xlsx", "xls"):
        df = pd.read_excel(file_path, header=None)

    # 应用模板列定义
    if template.columns:
        df.columns = [col.name for col in template.columns]

    return {"data": df.to_dict(), "columns": template.columns}
