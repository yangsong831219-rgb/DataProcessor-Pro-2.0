"""
OnlineLlamaGenerateThread - 基于 OpenAI 兼容接口的在线大模型推理线程
支持 DeepSeek、阿里通义千问、硅基流动等所有 OpenAI 兼容 API
"""
import re
import time
from PyQt6.QtCore import QThread, pyqtSignal
import requests
from openai import OpenAI


def format_api_error(error: Exception) -> str:
    """将 API 异常格式化为用户友好的中文消息"""
    msg = str(error)
    # 429 限流错误
    if '429' in msg or 'rate_limit' in msg:
        reset_match = re.search(r'resets at (.+?)(?:\s*\(\d+\))?$', msg)
        if reset_match:
            reset_time = reset_match.group(1)
            return f"API 调用额度已用完，将于 {reset_time} 重置，请稍后再试或切换到其他模型"
        return "API 调用额度已用完，请稍后再试或切换到其他模型（阿里百炼/硅基流动）"
    # 401 认证错误
    if '401' in msg or 'unauthorized' in msg.lower() or 'invalid api key' in msg.lower():
        return "API Key 无效，请检查并重新输入"
    # 超时
    if 'timeout' in msg.lower():
        return "连接超时，请检查网络或 API 地址是否正确"
    # 其他错误，保留原始信息
    return f"请求失败: {msg}"


class OnlineLlamaGenerateThread(QThread):
    """
    基于 OpenAI 兼容接口的流式推理线程
    完美兼容 PyQt6 信号槽机制
    """
    token_received = pyqtSignal(str)  # 实时 token 发射
    finished = pyqtSignal(str)         # 完成时发射完整响应
    error = pyqtSignal(str)            # 错误时发射错误信息
    latency_tested = pyqtSignal(int)   # 延迟测试结果（毫秒 TTFB）

    def __init__(self, prompt: str, api_key: str, base_url: str,
                 model_name: str, system_prompt: str = None,
                 temperature: float = 0.7, max_tokens: int = 2048,
                 latency_mode: bool = False):
        """
        初始化

        Args:
            prompt: 用户输入的提示词
            api_key: API 密钥
            base_url: API 基础地址（如 https://api.deepseek.com/v1）
            model_name: 模型名称（如 deepseek-chat、qwen-turbo 等）
            system_prompt: 系统提示词（可选）
            temperature: 温度参数
            max_tokens: 最大生成长度
            latency_mode: 若为 True，仅测首字延迟（TTFB），收到第一块立即熔断
        """
        super().__init__()
        self.prompt = prompt
        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.system_prompt = system_prompt or "你是一个专业的设备与系统 AI 诊断专家，请根据数据给出精准分析。"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.latency_mode = latency_mode

    def run(self):
        """在子线程中执行在线大模型推理"""
        if self.latency_mode:
            self._run_latency_test()
        else:
            self._run_diagnosis()

    def _run_latency_test(self):
        """纯网络心跳延迟探针 — 抛弃推理接口，直接 HTTP GET 测量 TCP/TLS 往返。

        向 base_url 的 roots 路径发送轻量 GET（不触发 GPU 推理），
        精确测量 DNS + TCP + TLS + HTTP 首字节的纯网络耗时。
        """
        t0 = time.time()
        try:
            response = requests.get(
                self.base_url.rstrip('/v1') + '/v1/models',
                timeout=5,
            )
            elapsed = int((time.time() - t0) * 1000)
            self.latency_tested.emit(elapsed)
            response.close()
        except requests.exceptions.Timeout:
            self.latency_tested.emit(-1)
        except Exception:
            self.latency_tested.emit(-1)

    def _run_diagnosis(self):
        """正常诊断模式 — 全量流式推理"""
        try:
            # 1. 初始化客户端（自带连接池）
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )

            # 2. 构建消息
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self.prompt}
            ]

            # 3. 发起流式请求
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                stream=True,
                temperature=self.temperature,
                max_tokens=self.max_tokens
            )

            # 4. 解析数据流
            full_response = ""
            buffer = ""

            for chunk in response:
                # 提取增量内容
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    token = delta.content
                    full_response += token
                    buffer += token

                    # 积累到 3 个字或遇到标点再发射，减少 PyQt 信号开销
                    if len(buffer) >= 3 or any(p in buffer for p in ['\n', '。', '！', '？', '，', '.', '!', '?']):
                        self.token_received.emit(buffer)
                        buffer = ""

            # 清空残余缓冲区
            if buffer:
                self.token_received.emit(buffer)

            # 5. 发射完成信号
            if full_response and len(full_response.strip()) > 0:
                self.finished.emit(full_response)
            else:
                self.error.emit("模型未返回有效响应")

        except Exception as e:
            self.error.emit(f"AI 诊断请求失败: {format_api_error(e)}")