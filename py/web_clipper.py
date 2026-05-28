"""网络信息剪藏 (Web Clipper) — 底层工具模块

支持的抓取目标：微信公众号、学术期刊、Bilibili、YouTube
核心能力：图文抓取 → 图片本地化 → 多媒体下载 → 知识库入库

依赖（按需安装）：pip install requests beautifulsoup4 lxml trafilatura markdownify yt-dlp
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urljoin, urlparse


def _ensure(name: str, pkg: str = ""):
    """运行时懒加载检查，缺少依赖时给出明确提示"""
    try:
        return __import__(name)
    except ImportError:
        pkg_name = pkg or name
        raise ImportError(
            f"缺少依赖包 '{pkg_name}'，请执行: pip install {pkg_name}"
        ) from None


class WebClipper:
    """通用网页剪藏器 — 抓取、图片本地化、视频下载、知识库入库"""

    # 浏览器 User-Agent 池 — 用于绕过防盗链检测
    _USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    ]

    _REQUEST_TIMEOUT = 30
    _MAX_IMAGE_SIZE_MB = 20

    # ── 项目路径 ──

    def __init__(self, wiki_base_dir: Optional[str] = None) -> None:
        if wiki_base_dir is None:
            wiki_base_dir = str(Path(__file__).resolve().parent.parent / "wiki_vault")
        self._wiki_dir = Path(wiki_base_dir)
        self._pages_dir = self._wiki_dir / "pages"
        self._assets_dir = self._wiki_dir / "assets"
        self._videos_dir = self._wiki_dir / "videos"
        for d in (self._pages_dir, self._assets_dir, self._videos_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════
    # 公共 API
    # ═══════════════════════════════════════════════════════════════

    def fetch_article(self, url: str, cookies: str = "") -> Tuple[str, str]:
        """抓取网页图文内容，返回 (title, markdown_body)。

        微信公众号：预处理 HTML 将 data-src → src，再转为 Markdown。
        知乎：需传入浏览器 Cookie 以绕过反爬验证。
        通用网页：trafilatura 提取正文 → markdownify 转换。

        Args:
            url: 目标网页 URL
            cookies: 可选的 Cookie 字符串（用于需要登录的网站如知乎）
        """
        html, title = self._download_html(url, cookies=cookies)
        md_body = self._html_to_markdown(html, url)
        return title, md_body

    def download_and_replace_images(
        self, md_content: str, base_dir: Optional[str] = None,
        cookies: str = "", referer: str = "",
    ) -> str:
        """下载 Markdown 中引用的远程图片到本地 assets 目录，替换为相对路径。

        返回替换后的 Markdown 文本。
        """
        if base_dir is None:
            base_dir = str(self._wiki_dir)
        dest = str(self._assets_dir)

        pattern = r"!\[([^\]]*)\]\((https?://[^\s)]+)\)"
        matches = list(re.finditer(pattern, md_content))
        if not matches:
            return md_content

        os.makedirs(dest, exist_ok=True)
        result = md_content

        for m in reversed(matches):
            alt_text = m.group(1)
            img_url = m.group(2)
            local_path = self._download_single_image(img_url, dest, cookies=cookies, referer=referer)
            if local_path is None:
                continue
            rel = os.path.relpath(local_path, base_dir).replace("\\", "/")
            replacement = f"![{alt_text}]({rel})"
            result = result[: m.start()] + replacement + result[m.end() :]

        return result

    def download_video(self, url: str, output_dir: Optional[str] = None) -> Optional[str]:
        """使用 yt-dlp 下载视频，限制最高 1080p。

        返回下载后的文件路径，失败返回 None。
        """
        try:
            import yt_dlp  # type: ignore
        except ImportError:
            raise ImportError(
                "yt-dlp 未安装，请执行: pip install yt-dlp"
            ) from None

        if output_dir is None:
            output_dir = str(self._videos_dir)
        os.makedirs(output_dir, exist_ok=True)

        is_bilibili = "bilibili.com" in url
        is_youtube = "youtube.com" in url or "youtu.be" in url

        if is_bilibili or is_youtube:
            fmt = (
                "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
                "/bestvideo+bestaudio/best"
            )
        else:
            fmt = "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"

        ydl_opts = {
            "format": fmt,
            "outtmpl": str(Path(output_dir) / "%(title).100s_%(id)s.%(ext)s"),
            "merge_output_format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "http_headers": {
                "User-Agent": random.choice(self._USER_AGENTS),
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    return None
                filename = ydl.prepare_filename(info)
                if not os.path.exists(filename):
                    base, _ = os.path.splitext(filename)
                    for ext in (".mp4", ".mkv", ".webm", ".flv"):
                        candidate = base + ext
                        if os.path.exists(candidate):
                            return candidate
                return filename
        except Exception:
            return None

    def save_to_wiki(self, url: str, title: str, md_content: str,
                     generate_fn=None, model_label: str = "LLM") -> str:
        """将 Markdown 内容清洗后注入 Front-matter，保存到知识库并更新索引。

        如果提供了 generate_fn，保存后会自动调用 LLM 分析生成标签和摘要。

        返回保存的 .md 文件路径。
        """
        safe_title = self._safe_filename(title)
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        front_matter = (
            f"---\n"
            f"title: {title}\n"
            f"source_url: {url}\n"
            f"date: {date_str}\n"
            f"---\n\n"
        )
        # 保存前清洗内容
        cleaned = self._clean_article_content(md_content)
        full_content = front_matter + cleaned

        file_path = self._pages_dir / f"{safe_title}.md"
        file_path.write_text(full_content, encoding="utf-8")

        self._update_wiki_index(title, f"pages/{safe_title}.md")

        # 自动打标 (异步)
        if generate_fn:
            from py.wiki_system import WikiFileSystem
            wiki = WikiFileSystem(str(self._wiki_dir))
            wiki.auto_tag_page(title, generate_fn, model_label, async_mode=True)

        return str(file_path)

    def _update_wiki_index(self, page_name: str, relative_path: str) -> None:
        """向 wiki_map.json 索引中注册新页面"""
        import json
        map_file = self._wiki_dir / "wiki_map.json"
        if map_file.exists():
            with open(map_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"version": "1.0", "last_updated": None, "index": {}}

        data.setdefault("index", {})[page_name] = {
            "path": relative_path,
            "tags": ["web_clip"],
            "summary": f"剪藏自: {page_name}",
        }
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")

        with open(map_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _clean_article_content(self, md_content: str) -> str:
        """清洗文章内容，去除微信等平台的界面垃圾信息。

        处理策略：
        1. 定位并截断尾部垃圾（从微信页脚标记开始）
        2. 逐行清理残留的交互式垃圾链接和空图片
        3. 清理文章开头的重复元信息
        """
        lines = md_content.split("\n")
        if len(lines) < 3:
            return md_content

        # ── 第一遍：找到尾部垃圾截断点 ──
        wechat_footer_markers = [
            "预览时标签不可点",
            "微信扫一扫",
            "继续滑动看下一个",
            "轻触阅读原文",
            "向上滑动看下一个",
            "微信扫一扫可打开此内容",
            "，轻点两下取消赞",
            "，轻点两下取消在看",
        ]
        cut_index = len(lines)
        for i, line in enumerate(lines):
            stripped = line.strip()
            for marker in wechat_footer_markers:
                if marker in stripped:
                    cut_index = min(cut_index, i)
                    break

        lines = lines[:cut_index]

        # ── 第二遍：标记需要删除的行 ──
        garbage_exact = {
            "原创",
            "在小说阅读器读本章",
            "去阅读",
            "在小说阅读器中沉浸阅读",
            "×",
            "分析",
            "视频",
            "小程序",
            "赞",
            "在看",
            "分享",
            "留言",
            "收藏",
            "听过",
            "：",
            "，",
            "。",
        }
        # 只包含这些字符的行（Unicode 标点 + 空白 + &nbsp;）
        import re as _re
        _garbage_chars_pattern = _re.compile(r'^[\s：，。 　·•·​]*$')

        cleaned = []
        prev_empty = False
        for i, line in enumerate(lines):
            stripped = line.strip()

            # 空行去重（最多连续一个空行）
            if not stripped:
                if not prev_empty:
                    cleaned.append("")
                    prev_empty = True
                continue
            prev_empty = False

            # 精确匹配垃圾行
            if stripped in garbage_exact:
                continue

            # 只有垃圾字符的行
            if _garbage_chars_pattern.match(stripped):
                continue

            # 空图片引用 ![]()
            if stripped == "![]()":
                continue

            # javascript: 伪链接
            if stripped.startswith("[") and ("javascript:void(0)" in stripped or "javascript:;" in stripped):
                continue

            # 跳转二维码占位
            if stripped.startswith("![跳转二维码]"):
                continue

            # 开头的重复作者名行（紧跟 h1 标题后的单行纯文本）
            # 比如 "量子智元" 或 "罗湳Roland" 作为独立行重复出现
            if i <= 3 and not any(c in stripped for c in "![]()#*->`|"):
                # 检查是否是文章标题下面紧跟着的独立纯文本行（通常是作者名/公众号名）
                if i + 1 < len(lines) and not lines[i + 1].strip():
                    continue

            # ⭐点赞转发三连行
            if "⭐" in stripped and ("点赞" in stripped or "转发" in stripped):
                continue

            # "阅读原文" javascript 链接
            if stripped == "[阅读原文](javascript:;)":
                continue

            cleaned.append(line)

        # 去掉末尾多余空行
        while cleaned and not cleaned[-1]:
            cleaned.pop()

        return "\n".join(cleaned)

    def clip(self, url: str, download_images: bool = True, cookies: str = "") -> Tuple[str, str]:
        """一站式剪藏：抓取 → 图片本地化 → 内容清洗 → 入库。

        返回 (title, saved_file_path)。
        """
        title, md_body = self.fetch_article(url, cookies=cookies)
        if download_images:
            md_body = self.download_and_replace_images(md_body)
        md_body = self._clean_article_content(md_body)
        saved_path = self.save_to_wiki(url, title, md_body)
        return title, saved_path

    # ═══════════════════════════════════════════════════════════════
    # 内部实现
    # ═══════════════════════════════════════════════════════════════

    def _download_html(self, url: str, cookies: str = "") -> Tuple[str, str]:
        """下载网页 HTML，返回 (html, title)。"""
        requests = _ensure("requests")
        BeautifulSoup = _ensure("bs4", "beautifulsoup4").BeautifulSoup

        is_wechat = "mp.weixin.qq.com" in url
        is_zhihu = "zhihu.com" in url

        headers = {
            "User-Agent": random.choice(self._USER_AGENTS),
        }

        # ── 微信专用头 ──
        if is_wechat:
            headers["Accept"] = "text/html,application/xhtml+xml"
            headers["Accept-Language"] = "zh-CN,zh;q=0.9"

        # ── 知乎专用头 + Cookie 认证 ──
        if is_zhihu:
            headers.update({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate",
                "Referer": "https://www.zhihu.com/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            })
            if cookies:
                headers["Cookie"] = cookies.strip()
            else:
                # 没有 Cookie 时添加一个基础 cookie 尝试访问公开内容
                headers["Cookie"] = ""

        resp = requests.get(
            url,
            headers=headers,
            timeout=self._REQUEST_TIMEOUT,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # ── Brotli 解压防御：requests 默认不解压 br ──
        if resp.headers.get("Content-Encoding", "").lower() == "br":
            try:
                import brotli
                resp._content = brotli.decompress(resp.content)
            except ImportError:
                try:
                    import brotlicffi
                    resp._content = brotlicffi.decompress(resp.content)
                except ImportError:
                    raise ImportError(
                        "服务器返回了 Brotli 压缩内容，需安装 brotli 库: pip install brotli"
                    ) from None

        # 编码检测：优先 Content-Type charset → HTML meta → apparent → utf-8
        encoding = None
        content_type = resp.headers.get("Content-Type", "")
        _enc_match = re.search(r'charset\s*=\s*([^\s;]+)', content_type, re.IGNORECASE)
        if _enc_match:
            encoding = _enc_match.group(1).strip('"\'')
        if not encoding:
            # 从 HTML 头部 meta 标签中查找 charset
            _meta_match = re.search(
                br'<meta[^>]+charset\s*=\s*["\']?([^"\'>;\s]+)',
                resp.content[:2048], re.IGNORECASE,
            )
            if _meta_match:
                encoding = _meta_match.group(1).decode("ascii")
        if not encoding:
            encoding = resp.apparent_encoding
        if not encoding:
            encoding = "utf-8"
        resp.encoding = encoding

        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        # 提取标题
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        if not title:
            title = urlparse(url).path.strip("/").split("/")[-1] or "未命名"

        # 清理干扰元素
        for tag_name in ("script", "style", "nav", "footer", "iframe"):
            for t in soup.find_all(tag_name):
                t.decompose()

        # ── 懒加载修复：data-src / data-original → src（微信、知乎等通用）──
        for img in soup.find_all("img"):
            data_src = img.get("data-src") or img.get("data-original")
            if data_src:
                img["src"] = data_src

        # 知乎 <noscript> 备用图：提取后替换（仅在 <img> 无有效 src 时使用）
        for noscript in soup.find_all("noscript"):
            ns_soup = BeautifulSoup(noscript.decode_contents(), "lxml")
            ns_img = ns_soup.find("img")
            if ns_img and ns_img.get("src"):
                # 如果父级已有有效图片就不重复添加
                parent = noscript.parent
                has_img = parent and parent.find("img")
                if not has_img:
                    noscript.replace_with(ns_img)
                else:
                    noscript.decompose()
            else:
                noscript.decompose()

        # 移除残留的空图（无效 src：javascript、data:占位图、空值）
        for img in soup.find_all("img"):
            src = (img.get("src") or "").strip()
            if not src or src.startswith("data:image/svg") or src.startswith("javascript:"):
                img.decompose()

        return str(soup), title

    def _html_to_markdown(self, html: str, base_url: str = "") -> str:
        """HTML → Markdown，优先 trafilatura 正文提取，回退 markdownify 全量转换。"""
        try:
            import trafilatura  # type: ignore

            extracted = trafilatura.extract(
                html,
                output_format="markdown",
                with_metadata=True,
                favor_precision=True,
                include_images=True,
                include_formatting=True,
                include_links=True,
                url=base_url,
            )
            if extracted and len(extracted.strip()) > 50:
                return extracted.strip()
        except Exception:
            pass

        # 回退：markdownify 转换 BeautifulSoup 预处理后的 HTML
        try:
            from markdownify import markdownify as md  # type: ignore
            return md(html, heading_style="ATX", strip=["script", "style", "nav", "footer"])
        except ImportError:
            pass

        # 最终回退：BeautifulSoup 纯文本提取
        BeautifulSoup = _ensure("bs4", "beautifulsoup4").BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        return soup.get_text("\n", strip=True)

    @staticmethod
    def _detect_image_ext(data: bytes) -> str:
        """根据文件头魔数检测图片实际格式，返回扩展名（含点）。"""
        if data[:3] == b'\xff\xd8\xff':
            return '.jpg'
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return '.png'
        if data[:4] == b'RIFF' and len(data) >= 12 and data[8:12] == b'WEBP':
            return '.webp'
        if data[:6] in (b'GIF87a', b'GIF89a'):
            return '.gif'
        if data[:2] in (b'BM',):
            return '.bmp'
        if data[:4] == b'<svg' or data[:5] == b'<?xml':
            return '.svg'
        if data[:4] == b'\x00\x00\x00\x1c' and b'ftypavif' in data[:32]:
            return '.avif'
        return '.jpg'

    def _download_single_image(
        self, img_url: str, dest_dir: str,
        cookies: str = "", referer: str = "",
    ) -> Optional[str]:
        """下载单张图片，返回本地路径。按实际格式检测扩展名，跳过重复下载。"""
        url_hash = hashlib.md5(img_url.encode()).hexdigest()[:12]

        # 先检查是否已有任意扩展名的已下载文件
        for existing in Path(dest_dir).glob(f"img_{url_hash}.*"):
            if existing.stat().st_size > 0:
                return str(existing)

        requests = _ensure("requests")

        headers = {
            "User-Agent": random.choice(self._USER_AGENTS),
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        if cookies:
            headers["Cookie"] = cookies
        if referer:
            headers["Referer"] = referer
        elif any(d in img_url for d in ("zhimg.com", "zhihu.com")):
            headers["Referer"] = "https://www.zhihu.com/"

        try:
            resp = requests.get(
                img_url,
                headers=headers,
                timeout=self._REQUEST_TIMEOUT,
                stream=True,
            )
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            content_length = resp.headers.get("Content-Length")
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > self._MAX_IMAGE_SIZE_MB:
                    return None

            if "image" not in content_type and content_type:
                return None

            # 收集所有数据块
            chunks = []
            total_size = 0
            for chunk in resp.iter_content(chunk_size=8192):
                chunks.append(chunk)
                total_size += len(chunk)
                if total_size > self._MAX_IMAGE_SIZE_MB * 1024 * 1024:
                    return None

            data = b''.join(chunks)
            ext = self._detect_image_ext(data)
            filename = f"img_{url_hash}{ext}"
            filepath = os.path.join(dest_dir, filename)

            with open(filepath, "wb") as f:
                f.write(data)

            time.sleep(random.uniform(0.3, 0.8))
            return filepath
        except Exception:
            return None

    @staticmethod
    def _safe_filename(title: str) -> str:
        """清理文件名中的非法字符"""
        safe = re.sub(r'[\\/:*?"<>|]', "_", title)
        safe = re.sub(r"\s+", " ", safe).strip()
        return safe[:120] if len(safe) > 120 else safe
