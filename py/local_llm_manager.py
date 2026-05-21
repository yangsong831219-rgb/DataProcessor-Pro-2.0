"""
LocalLLMManager - 基于 llama-cpp-python 的本地大模型管理
支持多模型平滑切换、流式输出、GPU加速
"""
from llama_cpp import Llama
from llama_cpp.server import LlamaServer
import gc
import os


class LocalLLMManager:
    """
    单例模式的大模型管理器
    负责模型的加载、卸载、生成
    """

    def __init__(self):
        self.llm = None
        self.current_model_path = ""
        self.n_ctx = 2048
        self.n_gpu_layers = -1  # -1表示全部使用GPU

    def load_model(self, model_path: str, n_ctx: int = 2048, n_gpu_layers: int = -1) -> bool:
        """
        加载模型，如果已加载不同模型，则先释放旧模型

        Args:
            model_path: 模型文件路径(.gguf)
            n_ctx: 上下文长度，越小越省内存
            n_gpu_layers: GPU层数，-1表示全部使用GPU

        Returns:
            bool: 加载是否成功
        """
        # 如果请求的模型已经是当前加载的模型，直接返回
        if self.llm is not None and self.current_model_path == model_path:
            return True

        # 如果在切换新模型，必须彻底释放旧模型内存
        if self.llm is not None:
            print(f"正在卸载旧模型: {self.current_model_path}")
            del self.llm
            self.llm = None
            gc.collect()  # 强制Python回收内存

        if not os.path.exists(model_path):
            print(f"模型文件不存在: {model_path}")
            return False

        print(f"正在加载新模型: {model_path}")
        try:
            self.llm = Llama(
                model_path=model_path,
                n_gpu_layers=n_gpu_layers,
                n_ctx=n_ctx,
                verbose=False
            )
            self.current_model_path = model_path
            self.n_ctx = n_ctx
            self.n_gpu_layers = n_gpu_layers
            print(f"模型加载成功: {model_path}")
            return True
        except Exception as e:
            print(f"模型加载失败: {e}")
            self.llm = None
            self.current_model_path = ""
            return False

    def unload_model(self) -> bool:
        """主动卸载模型"""
        if self.llm is not None:
            print(f"正在卸载模型: {self.current_model_path}")
            del self.llm
            self.llm = None
            self.current_model_path = ""
            gc.collect()
            return True
        return False

    def is_model_loaded(self) -> bool:
        """检查模型是否已加载"""
        return self.llm is not None

    def get_current_model_path(self) -> str:
        """获取当前模型路径"""
        return self.current_model_path

    def generate_stream(self, prompt: str, max_tokens: int = 512, temp: float = 0.7):
        """
        生成流式回复

        Args:
            prompt: 输入提示词
            max_tokens: 最大生成token数
            temp: 温度参数

        Yields:
            str: 生成的文本片段
        """
        if not self.llm:
            yield "错误：模型未加载"
            return

        try:
            response = self.llm(
                prompt,
                max_tokens=max_tokens,
                temperature=temp,
                stream=True
            )

            for chunk in response:
                if "choices" in chunk and len(chunk["choices"]) > 0:
                    text_chunk = chunk["choices"][0].get("text", "")
                    if text_chunk:
                        yield text_chunk

        except Exception as e:
            yield f"生成错误: {str(e)}"

    def generate(self, prompt: str, max_tokens: int = 512, temp: float = 0.7) -> str:
        """
        非流式生成（简单场景用）

        Args:
            prompt: 输入提示词
            max_tokens: 最大生成token数
            temp: 温度参数

        Returns:
            str: 生成的完整文本
        """
        if not self.llm:
            return "错误：模型未加载"

        try:
            response = self.llm(
                prompt,
                max_tokens=max_tokens,
                temperature=temp,
                stream=False
            )
            if "choices" in response and len(response["choices"]) > 0:
                return response["choices"][0].get("text", "")
            return ""
        except Exception as e:
            return f"生成错误: {str(e)}"


# 全局单例
_global_llm_manager = None


def get_global_llm_manager() -> LocalLLMManager:
    """获取全局LLM管理器单例"""
    global _global_llm_manager
    if _global_llm_manager is None:
        _global_llm_manager = LocalLLMManager()
    return _global_llm_manager