"""GitHub 动态技能加载器 (GitHub Skill Loader)

基于 requests 和 importlib 的轻量级动态加载器。
支持从 GitHub 下载 .py 技能脚本并动态注入到 Agent 工具箱。
"""

import os
import requests
import importlib.util
import inspect
import hashlib
from typing import Optional, List, Callable
from pathlib import Path
from datetime import datetime

from langchain_core.tools import BaseTool


class GitHubSkillManager:
    """GitHub 动态技能管理器"""

    def __init__(self, plugin_dir: str = "./plugins"):
        self.plugin_dir = Path(plugin_dir)
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.plugin_dir / ".cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.loaded_tools: List[BaseTool] = []
        self.proxy_url: Optional[str] = None

    def set_proxy(self, proxy_url: str):
        """设置代理服务器"""
        self.proxy_url = proxy_url
        print(f"🔧 代理已设置: {proxy_url}")

    def _get_raw_url(self, repo_owner: str, repo_name: str, file_path: str, branch: str = "main") -> str:
        """构建 GitHub Raw URL"""
        if self.proxy_url:
            base_url = self.proxy_url.rstrip('/')
            return f"{base_url}/https://raw.githubusercontent.com/{repo_owner}/{repo_name}/{branch}/{file_path}"
        return f"https://raw.githubusercontent.com/{repo_owner}/{repo_name}/{branch}/{file_path}"

    def _log_security_warning(self, repo_owner: str, repo_name: str, file_path: str):
        """输出安全警告"""
        print("=" * 60)
        print("⚠️  安全警告：你正在从第三方 GitHub 仓库加载代码！")
        print(f"📦 仓库：{repo_owner}/{repo_name}")
        print(f"📄 文件：{file_path}")
        print("🔒 请确保你信任该仓库的作者和代码内容！")
        print("=" * 60)

    def _compute_file_hash(self, content: str) -> str:
        """计算文件内容哈希"""
        return hashlib.sha256(content.encode()).hexdigest()[:12]

    def fetch_skill_from_github(
        self,
        repo_owner: str,
        repo_name: str,
        file_path: str,
        branch: str = "main",
        github_token: Optional[str] = None,
        use_cache: bool = True
    ) -> str:
        """
        从 GitHub 拉取单个技能脚本并缓存到本地

        Args:
            repo_owner: 仓库所有者
            repo_name: 仓库名称
            file_path: 文件路径
            branch: 分支名，默认 main
            github_token: GitHub Token（访问私有库或突破限流）
            use_cache: 是否使用本地缓存

        Returns:
            本地缓存文件路径
        """
        self._log_security_warning(repo_owner, repo_name, file_path)

        raw_url = self._get_raw_url(repo_owner, repo_name, file_path, branch)
        print(f"🔄 正在从 GitHub 拉取技能: {raw_url} ...")

        headers = {}
        if github_token:
            headers["Authorization"] = f"token {github_token}"

        proxies = {}
        if self.proxy_url:
            proxies = {"https": self.proxy_url, "http": self.proxy_url}

        try:
            response = requests.get(raw_url, headers=headers, proxies=proxies, timeout=30)
        except requests.exceptions.ProxyError as e:
            raise Exception(f"❌ 代理连接失败: {e}")
        except requests.exceptions.Timeout:
            raise Exception("❌ 请求超时，请检查网络连接或代理设置")

        if response.status_code == 200:
            content = response.text
            local_filename = f"{repo_owner}_{repo_name}_{Path(file_path).name}"
            local_path = self.plugin_dir / local_filename

            if use_cache:
                cache_info_path = self.cache_dir / f"{self._compute_file_hash(content)}.json"
                if cache_info_path.exists():
                    import json
                    with open(cache_info_path, 'r', encoding='utf-8') as f:
                        cache_info = json.load(f)
                    print(f"📦 检测到缓存文件: {cache_info.get('downloaded_at', 'unknown')}")

            with open(local_path, 'w', encoding='utf-8') as f:
                f.write(content)

            print(f"✅ 技能下载成功，已缓存至: {local_path}")
            return str(local_path)
        elif response.status_code == 404:
            raise Exception(f"❌ 文件不存在: {file_path} 在 {repo_owner}/{repo_name}/{branch}")
        elif response.status_code == 403:
            if not github_token:
                raise Exception("❌ 访问被拒绝，可能需要 GitHub Token 来突破限流")
            raise Exception("❌ Token 权限不足或已过期")
        else:
            raise Exception(f"❌ 无法拉取 GitHub 代码，状态码: {response.status_code}")

    def fetch_multiple_skills(
        self,
        skills: List[dict],
        github_token: Optional[str] = None
    ) -> List[str]:
        """
        批量拉取多个技能脚本

        Args:
            skills: List[dict]，每个 dict 包含 repo_owner, repo_name, file_path, branch
            github_token: GitHub Token

        Returns:
            本地文件路径列表
        """
        local_paths = []
        for skill in skills:
            try:
                path = self.fetch_skill_from_github(
                    repo_owner=skill["repo_owner"],
                    repo_name=skill["repo_name"],
                    file_path=skill["file_path"],
                    branch=skill.get("branch", "main"),
                    github_token=github_token,
                    use_cache=True
                )
                local_paths.append(path)
            except Exception as e:
                print(f"⚠️ 拉取失败 {skill['file_path']}: {e}")
        return local_paths

    def load_tools_from_file(self, local_path: str) -> List[BaseTool]:
        """
        动态加载并提取文件中的 @tool 装饰的函数

        Args:
            local_path: 本地 .py 文件路径

        Returns:
            提取到的工具列表
        """
        if not os.path.exists(local_path):
            raise Exception(f"❌ 文件不存在: {local_path}")

        module_name = Path(local_path).stem

        print(f"⚡ 正在动态加载模块: {module_name}")

        spec = importlib.util.spec_from_file_location(module_name, local_path)
        if spec is None or spec.loader is None:
            raise Exception(f"❌ 无法创建模块规范: {module_name}")

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        extracted_tools = []
        for name, obj in inspect.getmembers(module):
            if isinstance(obj, BaseTool):
                extracted_tools.append(obj)
                print(f"⚡ 成功挂载动态技能: {obj.name}")
            elif hasattr(obj, 'name') and hasattr(obj, 'description') and callable(getattr(obj, 'invoke', None)):
                extracted_tools.append(obj)
                print(f"⚡ 成功挂载动态技能: {name}")

        self.loaded_tools.extend(extracted_tools)
        print(f"📊 共已加载 {len(extracted_tools)} 个工具，当前总计 {len(self.loaded_tools)} 个")
        return extracted_tools

    def get_loaded_tools(self) -> List[BaseTool]:
        """获取所有已加载的工具"""
        return self.loaded_tools

    def clear_tools(self):
        """清除已加载的工具"""
        self.loaded_tools.clear()
        print("🗑️ 已清除所有动态加载的工具")

    def list_cached_skills(self) -> List[dict]:
        """列出本地缓存的技能"""
        cached = []
        for file_path in self.plugin_dir.glob("*.py"):
            stat = file_path.stat()
            cached.append({
                "filename": file_path.name,
                "path": str(file_path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            })
        return cached

    def delete_cached_skill(self, filename: str) -> bool:
        """删除本地缓存的技能"""
        file_path = self.plugin_dir / filename
        if file_path.exists():
            file_path.unlink()
            print(f"🗑️ 已删除缓存技能: {filename}")
            return True
        return False


# ============ 快捷工厂函数 ============

def quick_load_skill(
    repo_owner: str,
    repo_name: str,
    file_path: str,
    branch: str = "main",
    github_token: Optional[str] = None
) -> tuple:
    """
    快速加载单个技能（下载+提取工具一步完成）

    Returns:
        (工具列表, 本地文件路径)
    """
    manager = GitHubSkillManager()
    local_path = manager.fetch_skill_from_github(
        repo_owner, repo_name, file_path, branch, github_token
    )
    tools = manager.load_tools_from_file(local_path)
    return tools, local_path


if __name__ == "__main__":
    print("=== GitHub Skill Loader 测试 ===")
    manager = GitHubSkillManager()

    print("\n📦 本地缓存技能列表:")
    for skill in manager.list_cached_skills():
        print(f"  - {skill['filename']} ({skill['size']} bytes, 修改于 {skill['modified']})")

    print("\n⚠️ 测试需要配置 DEEPSEEK_API_KEY 环境变量")
    print("Usage:")
    print("  tools, path = quick_load_skill('owner', 'repo', 'path/to/skill.py')")