"""WikiFileSystem - 本地 LLM Wiki 知识库工具集

纯 Python 实现的轻量级知识库文件系统，不引入任何重型向量库。
提供基础的 Wiki 页面读写和索引维护功能。
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional


class WikiFileSystem:
    """本地 Wiki 文件系统工具类"""

    def __init__(self, base_dir: Optional[str] = None):
        """
        初始化 Wiki 文件系统

        Args:
            base_dir: wiki_vault 根目录路径，默认为项目根目录下的 wiki_vault
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent.parent / "wiki_vault"
        self.base_dir = Path(base_dir)
        self.pages_dir = self.base_dir / "pages"
        self.map_file = self.base_dir / "wiki_map.json"

        self._ensure_directories()

    def _ensure_directories(self):
        """确保目录结构存在"""
        self.pages_dir.mkdir(parents=True, exist_ok=True)

    def _load_map(self) -> dict:
        """加载 wiki_map.json"""
        if not self.map_file.exists():
            return {"version": "1.0", "last_updated": None, "index": {}}
        with open(self.map_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save_map(self, data: dict):
        """保存 wiki_map.json"""
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        with open(self.map_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_wiki_map(self) -> str:
        """
        读取知识库索引表（wiki_map.json）

        Returns:
            wiki_map.json 的 JSON 字符串内容
        """
        data = self._load_map()
        return json.dumps(data, ensure_ascii=False, indent=2)

    def update_wiki_map(self, page_name: str, path: str, tags: list = None, summary: str = ""):
        """
        更新知识库索引表

        Args:
            page_name: 页面名称（作为索引键）
            path: 页面文件路径（相对于 wiki_vault）
            tags: 标签列表
            summary: 页面摘要描述
        """
        data = self._load_map()
        if "index" not in data:
            data["index"] = {}

        data["index"][page_name] = {
            "path": path,
            "tags": tags or [],
            "summary": summary
        }

        self._save_map(data)
        return f"Wiki map updated: {page_name}"

    def read_wiki_page(self, page_name: str) -> str:
        """
        读取指定知识库页面

        Args:
            page_name: 页面名称

        Returns:
            页面内容的 Markdown 字符串，如果不存在返回错误信息
        """
        data = self._load_map()

        if page_name in data.get("index", {}):
            relative_path = data["index"][page_name]["path"]
            file_path = self.base_dir / relative_path
        else:
            file_path = self.pages_dir / f"{page_name}.md"

        if not file_path.exists():
            return f"Error: Page '{page_name}' not found"

        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    def write_wiki_page(self, page_name: str, content: str, author: str = "DeepSeek-Agent"):
        """
        新建或更新知识库页面

        Args:
            page_name: 页面名称
            content: Markdown 格式的页面内容
            author: 作者/更新者名称

        Returns:
            操作结果的描述字符串
        """
        data = self._load_map()
        index = data.get("index", {})

        if page_name in index:
            relative_path = index[page_name]["path"]
        else:
            relative_path = f"pages/{page_name}.md"
            index[page_name] = {
                "path": relative_path,
                "tags": [],
                "summary": ""
            }
            data["index"] = index

        file_path = self.base_dir / relative_path

        existing_content = ""
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                existing_content = f.read()

        if existing_content and existing_content != content:
            separator = f"\n\n---\n*更新于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} by {author}*\n"
            if separator in existing_content:
                content = existing_content.split(separator)[0] + separator + content
            else:
                content = existing_content + separator + content

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        self._save_map(data)
        return f"Wiki page written: {page_name} -> {file_path}"

    def delete_wiki_page(self, page_name: str) -> str:
        """
        删除知识库页面

        Args:
            page_name: 页面名称

        Returns:
            操作结果的描述字符串
        """
        data = self._load_map()
        index = data.get("index", {})

        if page_name not in index:
            return f"Error: Page '{page_name}' not found in index"

        relative_path = index[page_name]["path"]
        file_path = self.base_dir / relative_path

        if file_path.exists():
            file_path.unlink()

        del index[page_name]
        self._save_map(data)
        return f"Wiki page deleted: {page_name}"

    def upload_file_to_wiki(self, source_path: str, target_name: str = None) -> str:
        """
        上传文件到知识库

        Args:
            source_path: 源文件完整路径
            target_name: 目标文件名（不包含扩展名），默认为源文件名

        Returns:
            操作结果的描述字符串
        """
        import shutil

        source = Path(source_path)
        if not source.exists():
            return f"Error: Source file not found: {source_path}"

        if target_name is None:
            target_name = source.stem
            target_ext = source.suffix
        else:
            target_ext = source.suffix
            target_name = target_name

        # 生成唯一文件名
        target_path = self.pages_dir / f"{target_name}{target_ext}"
        counter = 1
        while target_path.exists():
            target_path = self.pages_dir / f"{target_name}_{counter}{target_ext}"
            counter += 1

        # 复制文件
        shutil.copy2(source, target_path)

        # 更新索引
        relative_path = f"pages/{target_path.name}"
        data = self._load_map()
        if "index" not in data:
            data["index"] = {}

        data["index"][target_path.stem] = {
            "path": relative_path,
            "tags": [],
            "summary": f"上传文件: {source.name}"
        }

        self._save_map(data)
        return f"File uploaded: {source.name} -> {target_path.name}"

    def delete_file_from_wiki(self, page_name: str) -> str:
        """
        从知识库删除文件

        Args:
            page_name: 页面名称或文件名

        Returns:
            操作结果的描述字符串
        """
        data = self._load_map()
        index = data.get("index", {})

        # 尝试精确匹配
        if page_name in index:
            relative_path = index[page_name]["path"]
            file_path = self.base_dir / relative_path
            file_path.unlink()
            del index[page_name]
            self._save_map(data)
            return f"File deleted: {page_name}"

        # 尝试模糊匹配（去掉扩展名）
        for name, info in list(index.items()):
            if name == page_name or info["path"] == f"pages/{page_name}" or info["path"] == f"pages/{page_name}.md":
                file_path = self.base_dir / info["path"]
                if file_path.exists():
                    file_path.unlink()
                del index[name]
                self._save_map(data)
                return f"File deleted: {name}"

        return f"Error: File '{page_name}' not found in wiki"

    def read_wiki_file_binary(self, page_name: str) -> tuple:
        """
        读取知识库文件（二进制模式，用于非文本文件）

        Args:
            page_name: 页面名称

        Returns:
            (文件路径, 文件内容bytes) 元组
        """
        data = self._load_map()
        index = data.get("index", {})

        if page_name in index:
            relative_path = index[page_name]["path"]
        else:
            relative_path = f"pages/{page_name}"
            if not (self.base_dir / relative_path).exists():
                relative_path = f"pages/{page_name}.md"

        file_path = self.base_dir / relative_path
        if not file_path.exists():
            return (None, None)

        with open(file_path, "rb") as f:
            return (str(file_path), f.read())

    def list_pages(self) -> str:
        """
        列出所有知识库页面

        Returns:
            页面列表的格式化字符串
        """
        data = self._load_map()
        index = data.get("index", {})

        if not index:
            return "No pages in wiki"

        lines = ["# Wiki Pages Index", ""]
        for name, info in index.items():
            lines.append(f"## {name}")
            lines.append(f"- Path: {info['path']}")
            lines.append(f"- Tags: {', '.join(info.get('tags', [])) or 'None'}")
            lines.append(f"- Summary: {info.get('summary', 'No description')}")
            lines.append("")

        return "\n".join(lines)

    def search_pages(self, keyword: str) -> str:
        """
        在所有页面中搜索关键词（简单文本搜索）

        Args:
            keyword: 搜索关键词

        Returns:
            匹配的页面列表
        """
        data = self._load_map()
        index = data.get("index", {})
        matches = []

        for page_name, info in index.items():
            if keyword.lower() in page_name.lower():
                matches.append(f"- {page_name}: {info.get('summary', '')}")
                continue

            file_path = self.base_dir / info["path"]
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if keyword.lower() in content.lower():
                        matches.append(f"- {page_name}: {info.get('summary', '')}")

        if not matches:
            return f"No pages found matching '{keyword}'"

        return f"# Search Results for '{keyword}'\n\n" + "\n".join(matches)