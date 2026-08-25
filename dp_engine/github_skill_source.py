"""GitHub skill source inspection — pure network service.

This module provides READ-ONLY inspection of GitHub-hosted skill repositories.
It checks whether a SKILL.md exists at a given URL and validates its structure,
but does NOT download, install, or execute any code.

All network I/O is contained here.  The UI layer MUST call these functions from
a background thread (e.g., QThread worker) — never from the main/GUI thread.

Front Matter parsing delegates to dp_engine.skills.manifest_parser —
the SINGLE SOURCE OF TRUTH for YAML/Front Matter rules.

Usage:
    from dp_engine.github_skill_source import inspect_github_skill_source

    result = inspect_github_skill_source(
        owner="example", repo="ppt-master", path="skills/", branch="main",
    )
    if result.is_viable_skill_source:
        print("Valid skill source found")
"""

from __future__ import annotations

import logging
import re

from dp_engine.skills.errors import SkillManifestError
from dp_engine.skills.models import SkillSourceInspectionResult

logger = logging.getLogger(__name__)

# ── Timeouts ──

# Connect timeout: how long to wait for the TCP handshake.
CONNECT_TIMEOUT_SECONDS: float = 10.0
# Read timeout: how long to wait between bytes once connected.
READ_TIMEOUT_SECONDS: float = 20.0

# Both values have finite upper bounds.  Never use timeout=None.
_MAX_TIMEOUT: float = 60.0

_DEFAULT_REQUEST_TIMEOUT: tuple[float, float] = (
    min(CONNECT_TIMEOUT_SECONDS, _MAX_TIMEOUT),
    min(READ_TIMEOUT_SECONDS, _MAX_TIMEOUT),
)

# ── HTML detection heuristic ──

_HTML_DETECT_RE = re.compile(
    r"<(!doctype\s+|html\b|head\b|body\b|title\b|meta\b|script\b)",
    re.IGNORECASE,
)


def _looks_like_html(content: str) -> bool:
    """Quick heuristic: does this content look like an HTML page?

    GitHub may return an HTML error page with HTTP 200 for rate-limit
    or authentication walls.  We must not treat these as valid SKILL.md.
    """
    stripped = content.strip()
    if not stripped:
        return False
    # Check first 512 bytes for HTML signature
    return bool(_HTML_DETECT_RE.search(stripped[:512]))


# ── Pure URL construction ──


def build_skill_md_url(
    owner: str,
    repo: str,
    path: str = "skills/",
    branch: str = "main",
) -> str:
    """Build the raw.githubusercontent.com URL for a SKILL.md file.

    Pure function — no network I/O, no filesystem access.

    Args:
        owner: GitHub repository owner (username or organization).
        repo: GitHub repository name.
        path: Path within the repository to the skill directory.
        branch: Git branch name (default "main").

    Returns:
        Full URL to the raw SKILL.md file.
    """
    raw_url = (
        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    )
    return raw_url.rstrip("/") + "/SKILL.md"


# ── Pure response classification ──


def classify_skill_source_response(
    *,
    source_url: str,
    status_code: int | None,
    content: str,
    network_error: str | None = None,
    timed_out: bool = False,
    repository_name: str = "",
) -> SkillSourceInspectionResult:
    """Classify an HTTP response into a structured SkillSourceInspectionResult.

    Pure function — no network I/O, no filesystem access.
    Both synchronous (requests) and asynchronous (QNetworkAccessManager)
    callers pass raw response data here and receive a typed result.

    Args:
        source_url: The URL that was requested.
        status_code: HTTP status code (None if no HTTP response received).
        content: Response body as a string.
        network_error: Human-readable network error description, if any.
        timed_out: True if the request was aborted due to timeout.
        repository_name: Human-readable repo label (e.g., "owner/repo").

    Returns:
        SkillSourceInspectionResult with layered boolean fields.
        Never returns None.
    """
    # ── Network-layer failures ──
    if timed_out:
        return SkillSourceInspectionResult(
            source_reachable=False,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message="请求超时，请检查网络连接",
            error_code="TIMEOUT",
        )

    if network_error is not None:
        # Distinguish connection-refused from other network errors.
        # Check both the English keyword and known Chinese patterns.
        _nw = network_error.lower()
        if "connection" in _nw or "连接" in _nw or "refused" in _nw:
            return SkillSourceInspectionResult(
                source_reachable=False,
                skill_md_found=False,
                content_nonempty=False,
                front_matter_detected=False,
                metadata_parseable=False,
                source_url=source_url,
                repository_name=repository_name,
                message=network_error,
                error_code="CONNECTION_ERROR",
            )
        return SkillSourceInspectionResult(
            source_reachable=False,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message=network_error,
            error_code="NETWORK_ERROR",
        )

    # No HTTP response at all
    if status_code is None:
        return SkillSourceInspectionResult(
            source_reachable=False,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message="未收到 HTTP 响应",
            error_code="NO_RESPONSE",
        )

    # ── HTTP status classification ──
    # source_reachable=True from here on (we received an HTTP response)

    if status_code == 404:
        return SkillSourceInspectionResult(
            source_reachable=True,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message=f"SKILL.md 未找到 (HTTP 404)，请确认仓库路径",
            error_code="NOT_FOUND",
        )

    if status_code == 403:
        return SkillSourceInspectionResult(
            source_reachable=True,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message="访问被拒绝 (HTTP 403)，可能需要 GitHub Token",
            error_code="FORBIDDEN",
        )

    if status_code != 200:
        return SkillSourceInspectionResult(
            source_reachable=True,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message=f"GitHub API 返回意外状态码: {status_code}",
            error_code=f"HTTP_{status_code}",
        )

    # ── HTTP 200: content quality checks ──
    # skill_md_found=True from here on

    if not content or not content.strip():
        return SkillSourceInspectionResult(
            source_reachable=True,
            skill_md_found=True,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message="HTTP 200 但响应内容为空 — 这可能是一个空文件",
            error_code="EMPTY_RESPONSE",
        )

    # content_nonempty=True from here on

    # Check for HTML error pages (GitHub rate-limit wall, etc.)
    if _looks_like_html(content):
        return SkillSourceInspectionResult(
            source_reachable=True,
            skill_md_found=True,
            content_nonempty=True,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message="响应内容是 HTML 页面而非 SKILL.md（可能是 GitHub 限流或认证页面）",
            error_code="HTML_RESPONSE",
            warnings=(
                "Response body appears to be HTML, not Markdown. "
                "This may indicate a rate-limit or authentication wall.",
            ),
        )

    # ── Front Matter checks ──
    has_front_matter = content.strip().startswith("---")
    if not has_front_matter:
        return SkillSourceInspectionResult(
            source_reachable=True,
            skill_md_found=True,
            content_nonempty=True,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message=(
                f"SKILL.md 已找到 ({len(content)} 字节)，"
                f"但缺少 YAML Front Matter"
            ),
            error_code="NO_FRONT_MATTER",
        )

    # front_matter_detected=True from here on

    # Use SSOT Front Matter parser
    from dp_engine.skills.manifest_parser import parse_skill_front_matter

    try:
        parsed = parse_skill_front_matter(content, source_label=source_url)
    except SkillManifestError as e:
        return SkillSourceInspectionResult(
            source_reachable=True,
            skill_md_found=True,
            content_nonempty=True,
            front_matter_detected=True,
            metadata_parseable=False,
            source_url=source_url,
            repository_name=repository_name,
            message=f"SKILL.md Front Matter 无法解析: {e}",
            error_code="BAD_FRONT_MATTER",
        )

    # All checks passed
    metadata_keys = tuple(sorted(str(k) for k in parsed.metadata.keys()))
    all_warnings = parsed.warnings

    return SkillSourceInspectionResult(
        source_reachable=True,
        skill_md_found=True,
        content_nonempty=True,
        front_matter_detected=True,
        metadata_parseable=True,
        source_url=source_url,
        repository_name=repository_name,
        message=(
            f"SKILL.md 已找到 ({len(content)} 字节)，"
            f"含有效 YAML Front Matter，"
            f"包含 {len(metadata_keys)} 个字段："
            f"{', '.join(metadata_keys[:8])}"
            + ("..." if len(metadata_keys) > 8 else "")
        ),
        raw_metadata_keys=metadata_keys,
        warnings=all_warnings,
    )


def inspect_github_skill_source(
    owner: str,
    repo: str,
    path: str = "skills/",
    branch: str = "main",
    token: str | None = None,
    connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
    read_timeout: float = READ_TIMEOUT_SECONDS,
) -> SkillSourceInspectionResult:
    """Inspect a GitHub repository for a valid skill source.

    This fetches SKILL.md from the given GitHub raw URL and checks its
    structure layer by layer.  Each check populates a specific boolean
    field in the result.

    URL construction delegates to build_skill_md_url().
    Response classification delegates to classify_skill_source_response().
    Front Matter parsing uses the SSOT parser from
    dp_engine.skills.manifest_parser.parse_skill_front_matter().

    No code is downloaded, installed, or executed.

    Args:
        owner: GitHub repository owner (username or organization).
        repo: GitHub repository name.
        path: Path within the repository to the skill directory.
        branch: Git branch name (default "main").
        token: Optional GitHub personal access token for private repos
               or higher rate limits.
        connect_timeout: TCP connection timeout in seconds (default 10).
        read_timeout: Read timeout in seconds (default 20).

    Returns:
        SkillSourceInspectionResult with layered inspection state.
        Never returns None.  Callers MUST check individual boolean fields
        rather than assuming a single 'success' field.
    """
    # ── Build URL (pure) ──
    skill_md_url = build_skill_md_url(owner, repo, path, branch)
    repo_name = f"{owner}/{repo}"

    # ── Validate inputs (pre-network) ──
    if not owner.strip() or not repo.strip():
        return SkillSourceInspectionResult(
            source_reachable=False,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=skill_md_url,
            repository_name=repo_name,
            message="仓库所有者和仓库名称不能为空",
            error_code="INVALID_INPUT",
        )

    # ── Import guard (pre-network) ──
    try:
        import requests
    except ImportError:
        return SkillSourceInspectionResult(
            source_reachable=False,
            skill_md_found=False,
            content_nonempty=False,
            front_matter_detected=False,
            metadata_parseable=False,
            source_url=skill_md_url,
            repository_name=repo_name,
            message="requests 库未安装，无法进行网络检查",
            error_code="NO_REQUESTS",
        )

    # ── Network request ──
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"token {token}"

    timeout = (
        min(connect_timeout, _MAX_TIMEOUT),
        min(read_timeout, _MAX_TIMEOUT),
    )

    try:
        resp = requests.get(skill_md_url, headers=headers, timeout=timeout)
    except requests.exceptions.Timeout:
        return classify_skill_source_response(
            source_url=skill_md_url,
            status_code=None,
            content="",
            timed_out=True,
            repository_name=repo_name,
        )
    except requests.exceptions.ConnectionError as e:
        return classify_skill_source_response(
            source_url=skill_md_url,
            status_code=None,
            content="",
            network_error=f"网络连接失败: {e}",
            repository_name=repo_name,
        )
    except requests.exceptions.RequestException as e:
        return classify_skill_source_response(
            source_url=skill_md_url,
            status_code=None,
            content="",
            network_error=f"网络请求异常: {e}",
            repository_name=repo_name,
        )

    # ── Delegate all HTTP response classification to pure function ──
    return classify_skill_source_response(
        source_url=skill_md_url,
        status_code=resp.status_code,
        content=resp.text,
        repository_name=repo_name,
    )
