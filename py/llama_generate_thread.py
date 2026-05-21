"""
Llama生成线程 - 基于 llama-cpp-python 的PyQt线程实现
支持流式输出、token批处理
"""
from PyQt6.QtCore import QThread, pyqtSignal
import time


def _check_llama_cpp():
    """检查llama_cpp是否可用"""
    try:
        from llama_cpp import Llama
        return True
    except ImportError:
        return False


LLAMA_CPP_AVAILABLE = _check_llama_cpp()


class LlamaGenerateThread(QThread):
    """
    基于 llama-cpp-python 的生成线程
    与 OllamaGenerateThread 接口兼容，便于切换
    """
    token_received = pyqtSignal(str)  # 收到一批token时发射
    finished = pyqtSignal(str)        # 生成完毕时发射（完整内容）
    error = pyqtSignal(str)           # 出错时发射

    def __init__(self, model_path: str, prompt: str, n_ctx: int = 2048,
                 max_tokens: int = 512, temp: float = 0.7,
                 stream_interval_ms: int = 80, batch_size: int = 4):
        """
        初始化

        Args:
            model_path: 模型文件路径(.gguf)
            prompt: 输入提示词
            n_ctx: 上下文长度
            max_tokens: 最大生成token数
            temp: 温度参数
            stream_interval_ms: 批处理间隔(ms)
            batch_size: 每批token数量
        """
        super().__init__()
        self.model_path = model_path
        self.prompt = prompt
        self.n_ctx = n_ctx
        self.max_tokens = max_tokens
        self.temp = temp
        self.stream_interval_ms = stream_interval_ms
        self.batch_size = batch_size

    def run(self):
        """在子线程中执行模型推理"""
        if not LLAMA_CPP_AVAILABLE:
            self.error.emit("llama-cpp-python未安装，请运行: pip install llama-cpp-python")
            return

        try:
            from py.local_llm_manager import get_global_llm_manager
            llm_manager = get_global_llm_manager()

            # 1. 确保模型已加载
            success = llm_manager.load_model(self.model_path, self.n_ctx)
            if not success:
                self.error.emit("模型加载失败，请检查模型文件路径")
                return

            # 2. 格式化prompt（支持Qwen等模型的ChatML格式）
            formatted_prompt = self._format_prompt(self.prompt)

            # 3. 流式生成，批处理发送
            buffer = ""
            last_emit_time = time.time() * 1000
            full_response = ""

            for token in llm_manager.generate_stream(formatted_prompt, self.max_tokens, self.temp):
                full_response += token
                buffer += token

                current_time = time.time() * 1000
                elapsed = current_time - last_emit_time

                # 批处理逻辑：达到批量大小或超过时间间隔则发射
                if len(buffer) >= self.batch_size or elapsed >= self.stream_interval_ms:
                    if buffer:
                        self.token_received.emit(buffer)
                        buffer = ""
                        last_emit_time = current_time

            # 发射剩余buffer
            if buffer:
                self.token_received.emit(buffer)

            # 检查是否为空响应
            if not full_response or len(full_response.strip()) < 5:
                self.error.emit("模型未返回有效响应")
                return

            # 清理重复内容
            if len(full_response) > 500:
                lines = full_response.split('\n')
                if len(lines) > 10:
                    seen = set()
                    clean_lines = []
                    repeat_count = 0
                    for line in lines:
                        stripped = line.strip()
                        if stripped in seen:
                            repeat_count += 1
                        else:
                            seen.add(stripped)
                            clean_lines.append(line)
                            repeat_count = 0
                        if repeat_count > 5:
                            break
                    if repeat_count > 5:
                        full_response = '\n'.join(clean_lines[:20]) + "\n[内容已截断避免重复]"

            # 截断过长响应
            if len(full_response) > 1000:
                full_response = full_response[:1000] + "\n[内容已截断]"

            self.finished.emit(full_response)

        except Exception as e:
            self.error.emit(f"生成失败: {str(e)}")

    def _format_prompt(self, prompt: str) -> str:
        """
        格式化prompt为对应模型的格式
        目前支持Qwen和通用格式
        """
        # 尝试检测模型类型并格式化
        model_path_lower = self.model_path.lower()

        if "qwen" in model_path_lower:
            # Qwen2.5 / Qwen2 ChatML格式
            return f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
        elif "gemma" in model_path_lower:
            # Gemma格式 (Instruction格式)
            return f"<start_of_turn>user\n{prompt}<end_of_turn>\n<start_of_turn>model\n"
        else:
            # 通用格式
            return f"User: {prompt}\n\nAssistant:"

    def _clean_response(self, text: str) -> str:
        """清理响应中的控制字符"""
        import re
        text = re.sub(r'[\U00010000-\U0010ffff]', '', text)
        text = text.replace('\x00', '').replace('﻿', '')
        return text