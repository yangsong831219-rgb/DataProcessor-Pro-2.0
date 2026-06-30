from .template import FileTemplate, load_templates, save_template, parse_file

def handle_load_file(file_path: str, template_id: str) -> dict:
    """处理文件加载请求"""
    templates = load_templates()
    template = next((t for t in templates if t.id == template_id), None)
    if not template:
        raise ValueError(f"Template {template_id} not found")
    return parse_file(file_path, template)
