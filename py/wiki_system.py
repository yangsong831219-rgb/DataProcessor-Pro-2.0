"""WikiFileSystem - 本地 LLM Wiki 知识库工具集

纯 Python 实现的轻量级知识库文件系统。
提供基础的 Wiki 页面读写、索引维护、语义搜索和智能标签功能。
"""

import json
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional


class WikiFileSystem:
    """本地 Wiki 文件系统工具类 — 支持语义搜索与 LLM 自动打标"""

    # 默认嵌入模型 (首次使用时自动下载 ~80MB)
    EMBEDDING_MODEL = "all-MiniLM-L6-v2"

    def __init__(self, base_dir: Optional[str] = None):
        """
        初始化 Wiki 文件系统

        Args:
            base_dir: wiki_vault 根目录路径，默认为项目根目录下的 wiki_vault
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent / "wiki_vault"
        self.base_dir = Path(base_dir)
        self.pages_dir = self.base_dir / "pages"
        self.map_file = self.base_dir / "wiki_map.json"
        self.chroma_dir = self.base_dir / ".chroma"

        self._embedder = None          # sentence-transformers 模型 (懒加载)
        self._chroma_client = None     # ChromaDB 客户端 (懒加载)
        self._collection = None        # ChromaDB 集合引用
        self._embed_lock = threading.Lock()

        self._ensure_directories()

    def _ensure_directories(self):
        """确保目录结构存在"""
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / ".history").mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════
    # 索引持久化
    # ═══════════════════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════════════════
    # 向量嵌入引擎 (懒加载)
    # ═══════════════════════════════════════════════════════════════

    def _ensure_embedder(self):
        """懒加载 sentence-transformers 嵌入模型"""
        if self._embedder is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(self.EMBEDDING_MODEL)
            print(f"[Wiki] 嵌入模型已加载: {self.EMBEDDING_MODEL}")
        except ImportError:
            raise ImportError(
                "语义搜索需要 sentence-transformers，请执行: pip install sentence-transformers"
            )
        except Exception as e:
            print(f"[Wiki] 嵌入模型加载失败: {e}")
            raise

    def _ensure_chroma(self):
        """懒加载 ChromaDB 客户端和集合"""
        if self._collection is not None:
            return
        try:
            import chromadb
            from chromadb.config import Settings
            self._chroma_client = chromadb.PersistentClient(
                path=str(self.chroma_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._chroma_client.get_or_create_collection(
                name="wiki_pages",
                metadata={"hnsw:space": "cosine"},
            )
            print(f"[Wiki] ChromaDB 已连接: {self.chroma_dir}")
        except ImportError:
            raise ImportError(
                "语义搜索需要 chromadb，请执行: pip install chromadb"
            )
        except Exception as e:
            print(f"[Wiki] ChromaDB 连接失败: {e}")
            raise

    def _embed_content(self, text: str) -> list[float]:
        """计算文本的嵌入向量"""
        self._ensure_embedder()
        # 截断过长文本 (模型最大 512 tokens，约 2000 中文字)
        truncated = text[:8000] if len(text) > 8000 else text
        embedding = self._embedder.encode(truncated, show_progress_bar=False)
        return embedding.tolist()

    def _update_embedding(self, page_name: str, content: str):
        """更新一个页面的向量索引 (后台异步)"""
        def _job():
            try:
                with self._embed_lock:
                    self._ensure_embedder()
                    self._ensure_chroma()
                    vec = self._embed_content(content)
                    # upsert: 先删后加
                    ids = self._collection.get(ids=[page_name])
                    if ids and ids["ids"]:
                        self._collection.update(
                            ids=[page_name], embeddings=[vec],
                            documents=[content[:500]],
                        )
                    else:
                        self._collection.add(
                            ids=[page_name], embeddings=[vec],
                            documents=[content[:500]],
                        )
                    print(f"[Wiki] 向量已更新: {page_name}")
            except Exception as e:
                print(f"[Wiki] 向量更新失败 ({page_name}): {e}")

        t = threading.Thread(target=_job, daemon=True)
        t.start()

    def _delete_embedding(self, page_name: str):
        """删除一个页面的向量索引"""
        try:
            self._ensure_chroma()
            self._collection.delete(ids=[page_name])
        except Exception as e:
            print(f"[Wiki] 向量删除失败 ({page_name}): {e}")

    def rebuild_embeddings(self) -> int:
        """全量重建所有页面的向量索引。

        Returns:
            重建的页面数量。
        """
        from concurrent.futures import ThreadPoolExecutor

        self._ensure_embedder()
        self._ensure_chroma()

        # 清空旧向量
        try:
            self._chroma_client.delete_collection("wiki_pages")
        except Exception:
            pass
        self._collection = self._chroma_client.get_or_create_collection(
            name="wiki_pages", metadata={"hnsw:space": "cosine"},
        )

        data = self._load_map()
        index = data.get("index", {})
        if not index:
            return 0

        def _embed_one(item):
            name, info = item
            fp = self.base_dir / info["path"]
            if fp.exists():
                content = fp.read_text(encoding="utf-8")
                vec = self._embed_content(content)
                return name, vec, content[:500]
            return None

        items = list(index.items())
        embeddings, ids, docs = [], [], []
        with ThreadPoolExecutor(max_workers=2) as pool:
            for result in pool.map(_embed_one, items):
                if result:
                    name, vec, doc = result
                    ids.append(name)
                    embeddings.append(vec)
                    docs.append(doc)

        if ids:
            self._collection.add(ids=ids, embeddings=embeddings, documents=docs)

        print(f"[Wiki] 全量向量重建完成: {len(ids)} 页")
        return len(ids)

    # ═══════════════════════════════════════════════════════════════
    # 语义搜索
    # ═══════════════════════════════════════════════════════════════

    def semantic_search(self, query: str, top_k: int = 3) -> str:
        """语义向量搜索知识库页面。

        基于句子嵌入的余弦相似度检索，能匹配语义相近但关键词不同的内容。

        Args:
            query: 自然语言查询 (如 "温度补偿失败的原因")
            top_k: 返回最相关的结果数

        Returns:
            格式化的搜索结果 Markdown 字符串。
        """
        try:
            self._ensure_embedder()
            self._ensure_chroma()
        except ImportError as e:
            return f"Error: {e}"

        query_vec = self._embed_content(query)

        try:
            results = self._collection.query(
                query_embeddings=[query_vec],
                n_results=top_k,
                include=["documents", "distances"],
            )
        except Exception as e:
            # 集合可能为空
            if "non-existent" in str(e).lower() or "empty" in str(e).lower():
                return "# 语义搜索结果\n\n知识库向量索引为空，请先创建页面或执行 rebuild_embeddings()。"
            return f"Error: 语义搜索失败: {e}"

        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]

        if not ids:
            return f"# 语义搜索结果: '{query}'\n\n未找到相关页面。知识库可能尚未建立向量索引。"

        data = self._load_map()
        index = data.get("index", {})

        lines = [f"# 语义搜索结果: '{query}'\n"]
        for i, pid in enumerate(ids):
            similarity = max(0.0, 1.0 - distances[i]) if distances[i] is not None else 0.0
            info = index.get(pid, {})
            summary = info.get("summary", "")
            tags = ", ".join(info.get("tags", []))
            lines.append(f"## {i + 1}. {pid} (相关度: {similarity:.0%})")
            if tags:
                lines.append(f"标签: {tags}")
            if summary:
                lines.append(f"摘要: {summary}")
            lines.append("")

        return "\n".join(lines)

    # ═══════════════════════════════════════════════════════════════
    # LLM 自动打标
    # ═══════════════════════════════════════════════════════════════

    def auto_tag_page(self, page_name: str,
                      generate_fn: Callable[[str], str],
                      model_label: str = "LLM",
                      async_mode: bool = True) -> str:
        """使用 LLM 自动分析页面内容并生成标签和摘要。

        标签和摘要自动回写到 wiki_map.json。支持同步和异步两种模式。

        Args:
            page_name: 页面名称
            generate_fn: LLM 生成函数 (prompt) -> str
            model_label: 模型标识（日志用）
            async_mode: True=后台线程执行, False=同步等待

        Returns:
            操作结果描述。
        """
        # 读取页面内容
        try:
            content = self.read_wiki_page(page_name)
        except Exception:
            pass
        if not content or content.startswith("Error:"):
            return f"Error: 页面 '{page_name}' 不存在或无法读取"

        # 截断上下文（控制 token 消耗）
        snippet = content[:3000] if len(content) > 3000 else content

        prompt = (
            "你是一个知识库管理助手。请分析以下 Markdown 文章，完成两项任务：\n"
            "1. 生成 3-5 个精准的标签 (tags)，用于分类和检索\n"
            "2. 生成一段不超过 50 字的中文摘要 (summary)\n\n"
            "请严格按以下 JSON 格式输出，不要加任何其他文字：\n"
            '{"tags": ["标签1", "标签2", ...], "summary": "摘要内容"}\n\n'
            f"=== 文章内容 ===\n{snippet}\n\n"
            "请输出 JSON："
        )

        def _do_tag():
            try:
                print(f"[auto_tag] {model_label} 分析 '{page_name}' ...")
                raw = generate_fn(prompt)
                # 提取 JSON
                import re as _re
                match = _re.search(r'\{[^}]+\}', raw)
                if not match:
                    print(f"[auto_tag] LLM 返回格式不正确: {raw[:200]}")
                    return
                result = json.loads(match.group())
                tags = result.get("tags", [])[:5]
                summary = result.get("summary", "")[:100]

                # 回写 wiki_map.json
                data = self._load_map()
                if page_name in data.get("index", {}):
                    data["index"][page_name]["tags"] = tags
                    data["index"][page_name]["summary"] = summary
                    self._save_map(data)
                    print(f"[auto_tag] '{page_name}' 标签: {tags} 摘要: {summary}")
            except Exception as e:
                print(f"[auto_tag] 失败 ({page_name}): {e}")

        if async_mode:
            t = threading.Thread(target=_do_tag, daemon=True)
            t.start()
            return f"Auto-tag 已提交 (后台): {page_name}"
        else:
            _do_tag()
            return f"Auto-tag 完成: {page_name}"

    # ═══════════════════════════════════════════════════════════════
    # 基础 CRUD API
    # ═══════════════════════════════════════════════════════════════

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

        # 异步更新向量索引
        self._update_embedding(page_name, content)

        return f"Wiki page written: {page_name} -> {file_path}"

    def smart_merge_wiki_page(self, page_name: str, new_content: str,
                              generate_fn: Callable[[str], str],
                              model_label: str = "LLM") -> str:
        """使用 LLM 智能合并知识库页面。

        将新知识融入已有文章：去重、重组标题层级、保留技术细节。
        原始内容自动备份到 wiki_vault/.history/ 目录。

        Args:
            page_name: 页面名称
            new_content: 新增内容 (Markdown)
            generate_fn: LLM 生成函数，签名为 (prompt: str) -> str
            model_label: 模型标识（用于日志）

        Returns:
            操作结果描述字符串
        """
        data = self._load_map()
        index = data.get("index", {})

        # 定位目标文件
        if page_name in index:
            relative_path = index[page_name]["path"]
        else:
            relative_path = f"pages/{page_name}.md"
            index[page_name] = {"path": relative_path, "tags": [], "summary": ""}
            data["index"] = index

        file_path = self.base_dir / relative_path
        existing_content = ""
        if file_path.exists():
            existing_content = file_path.read_text(encoding="utf-8")

        # 无旧内容时直接写入
        if not existing_content.strip():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(new_content, encoding="utf-8")
            self._save_map(data)
            self._update_embedding(page_name, new_content)
            return f"Wiki page created (no merge needed): {page_name}"

        # ── LLM 智能合并 ──
        system_prompt = (
            "你是一个知识库整理专家。请将以下新知识融入旧 Markdown 文章中。\n"
            "要求：\n"
            "1. 去重：新内容与旧文章重复的部分只保留一处\n"
            "2. 重组标题层级：合理调整 # / ## / ### 结构，使文章逻辑清晰\n"
            "3. 保留所有技术细节：新旧内容中的具体数值、公式、代码块、引用都必须完整保留\n"
            "4. 输出格式为纯 Markdown，不要添加额外说明"
        )
        merge_prompt = (
            f"{system_prompt}\n\n"
            f"=== 旧文章 (当前版本) ===\n{existing_content}\n\n"
            f"=== 新知识 (待融入) ===\n{new_content}\n\n"
            f"请输出合并后的完整 Markdown 文章："
        )

        print(f"[smart_merge] 调用 {model_label} 合并页面 '{page_name}' ...")
        try:
            merged = generate_fn(merge_prompt)
        except Exception as e:
            print(f"[smart_merge] LLM 调用失败: {e}")
            separator = (
                f"\n\n---\n"
                f"*更新于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (追加模式)*\n"
            )
            merged = existing_content + separator + new_content

        if not merged or len(merged.strip()) < 10:
            print("[smart_merge] LLM 返回空内容，降级为追加模式")
            merged = existing_content + (
                f"\n\n---\n"
                f"*更新于: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (追加模式)*\n"
            ) + new_content

        # ── 备份原始内容到 .history/ ──
        history_dir = self.base_dir / ".history"
        history_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{page_name}_{ts}.md"
        backup_path = history_dir / backup_name
        backup_path.write_text(existing_content, encoding="utf-8")
        print(f"[smart_merge] 原始内容已备份至: {backup_path}")

        # ── 写入合并结果 ──
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(merged, encoding="utf-8")

        self._save_map(data)
        self._update_embedding(page_name, merged)

        return f"Wiki page smart-merged: {page_name} (backup: .history/{backup_name})"

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

        # 清理向量索引
        self._delete_embedding(page_name)

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
            self._delete_embedding(page_name)
            return f"File deleted: {page_name}"

        # 尝试模糊匹配（去掉扩展名）
        for name, info in list(index.items()):
            if name == page_name or info["path"] == f"pages/{page_name}" or info["path"] == f"pages/{page_name}.md":
                file_path = self.base_dir / info["path"]
                if file_path.exists():
                    file_path.unlink()
                del index[name]
                self._save_map(data)
                self._delete_embedding(name)
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

    def sync_missing_pages(self) -> int:
        """扫描 pages/ 目录，将未索引的 .md 文件自动注册到 wiki_map.json。

        Returns:
            新注册的页面数量。
        """
        data = self._load_map()
        index = data.setdefault("index", {})
        indexed_paths = {info["path"] for info in index.values()}

        count = 0
        for f in sorted(self.pages_dir.glob("*.md")):
            rel_path = f"pages/{f.name}"
            if rel_path not in indexed_paths:
                page_name = f.stem
                index[page_name] = {
                    "path": rel_path,
                    "tags": [],
                    "summary": "",
                }
                # 为新发现的页面建立向量索引
                content = f.read_text(encoding="utf-8")
                self._update_embedding(page_name, content)
                count += 1

        if count > 0:
            self._save_map(data)
        return count

    def list_pages(self) -> str:
        """
        列出所有知识库页面（自动同步未索引文件）

        Returns:
            页面列表的格式化字符串
        """
        self.sync_missing_pages()
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
        self.sync_missing_pages()
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
