import requests
import subprocess
import sys
import time
import re
from typing import Optional
from PyQt6.QtCore import QThread, pyqtSignal, QTimer


class OllamaGenerateThread(QThread):
    """Ollama生成线程 - 支持流式输出和token批处理"""
    # 信号定义
    token_received = pyqtSignal(str)  # 收到一批token时发射（批处理后）
    finished = pyqtSignal(str)        # 生成完毕时发射（完整内容或错误信息）
    error = pyqtSignal(str)           # 出错时发射

    def __init__(self, base_url: str, model: str, prompt: str, keep_alive: str = "5m",
                 timeout: int = 30, stream_interval_ms: int = 80, batch_size: int = 4):
        super().__init__()
        self.base_url = base_url
        self.model = model
        self.prompt = prompt
        self.keep_alive = keep_alive  # 字符串格式：如 "5m", "10m", "0"=立即卸载
        self.timeout = timeout  # 动态超时
        self.stream_interval_ms = stream_interval_ms  # 批处理间隔(ms)
        self.batch_size = batch_size  # 每批token数量

    def run(self):
        """在子线程中执行Ollama API调用（流式+批处理）"""
        try:
            full_response = ""
            token_buffer = ""
            last_emit_time = time.time() * 1000  # 毫秒

            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": self.prompt,
                    "stream": True,
                    "keep_alive": self.keep_alive
                },
                timeout=self.timeout,
                stream=True
            )

            if response.status_code != 200:
                self.error.emit(f"请求失败，状态码: {response.status_code}")
                return

            # 流式读取响应
            for line in response.iter_lines():
                if line:
                    try:
                        data = line.decode('utf-8')
                        if data.startswith('data:'):
                            data = data[5:].strip()
                        if not data:
                            continue
                        import json
                        result = json.loads(data)
                        if "error" in result:
                            self.error.emit(f"Ollama错误: {result['error']}")
                            return
                        token = result.get("response", "")
                        if token:
                            # 清理控制字符和emoji
                            token = re.sub(r'[\U00010000-\U0010ffff]', '', token)
                            token = token.replace('\x00', '').replace('﻿', '')
                            full_response += token
                            token_buffer += token

                            # 批处理逻辑：达到批量大小或超过时间间隔则发射
                            current_time = time.time() * 1000
                            elapsed = current_time - last_emit_time
                            if len(token_buffer) >= self.batch_size or elapsed >= self.stream_interval_ms:
                                if token_buffer:
                                    self.token_received.emit(token_buffer)
                                    token_buffer = ""
                                    last_emit_time = current_time

                        if result.get("done", False):
                            break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue

            # 发射剩余buffer
            if token_buffer:
                self.token_received.emit(token_buffer)

            # 检查是否为空响应
            if not full_response or len(full_response.strip()) < 5:
                self.error.emit("模型未返回有效响应，请尝试其他模型")
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

        except requests.exceptions.Timeout:
            self.error.emit("请求超时，模型响应时间过长")
        except Exception as e:
            self.error.emit(f"调用失败: {str(e)}")


class OllamaStatusThread(QThread):
    """Ollama状态检测线程 - 后台静默检测"""
    status_result = pyqtSignal(bool)
    models_list = pyqtSignal(list)
    model_loaded = pyqtSignal(str, bool)  # 模型名, 是否已加载

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url

    def run(self):
        """后台检测Ollama服务状态"""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                result = response.json()
                models = [m['name'] for m in result.get('models', [])]
                self.status_result.emit(True)
                self.models_list.emit(models)

                # 检测当前指定模型是否已加载
                try:
                    # 检查模型是否在内存中
                    model_info = requests.get(f"{self.base_url}/api/show", timeout=3,
                                            json={"name": self._extract_model_name()})
                    if model_info.status_code == 200:
                        info = model_info.json()
                        # 根据keep_alive判断，如果非0则模型应该在内存中
                        self.model_loaded.emit(self._extract_model_name(), True)
                    else:
                        self.model_loaded.emit(self._extract_model_name(), False)
                except:
                    self.model_loaded.emit(self._extract_model_name() if hasattr(self, '_extract_model_name') else "", False)
            else:
                self.status_result.emit(False)
                self.models_list.emit([])
                self.model_loaded.emit("", False)
        except:
            self.status_result.emit(False)
            self.models_list.emit([])
            self.model_loaded.emit("", False)

    def _extract_model_name(self):
        """从base_url提取模型名（简化）"""
        # 这个需要在外部设置，实际使用时通过其他方式传递
        return ""


class OllamaClient:
    """Ollama本地模型客户端"""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "gemma4:e4b"):
        self.base_url = base_url
        self.model = model
        self.process = None
        self._model_loaded = False  # 模型是否已加载到内存

    def is_available(self) -> bool:
        """检查Ollama服务是否可用（同步，用于快速检测）"""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False

    def is_model_loaded(self, model: str = None) -> bool:
        """检查指定模型是否已加载到内存"""
        model = model or self.model
        try:
            resp = requests.post(
                f"{self.base_url}/api/show",
                json={"name": model},
                timeout=3
            )
            if resp.status_code == 200:
                info = resp.json()
                # info包含"loaded"字段表示模型状态
                return info.get("loaded", False) or info.get("size", 0) > 0
            return False
        except:
            return False

    def get_model_load_time(self, model: str = None) -> float:
        """估算模型加载时间（用于动态超时）"""
        model = model or self.model
        # 根据模型大小估算，qwen3.5:0.8b约800MB, qwen3.5:9b约5GB
        # 假设加载速度约100MB/s
        size_map = {
            "qwen3.5:0.8b": 800,
            "qwen3.5:1.8b": 1700,
            "qwen3.5:3b": 2700,
            "qwen3.5:9b": 5000,
            "gemma4:e4b": 2500,
        }
        size_mb = size_map.get(model, 2000)  # 默认2GB
        return size_mb / 100  # 秒

    def start_service(self) -> bool:
        """启动Ollama服务"""
        if self.is_available():
            return True

        try:
            if sys.platform == 'win32':
                self.process = subprocess.Popen(
                    ['ollama', 'serve'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                self.process = subprocess.Popen(
                    ['ollama', 'serve'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )

            for _ in range(10):
                time.sleep(1)
                if self.is_available():
                    return True

            return False
        except Exception as e:
            print(f"启动Ollama服务失败: {e}")
            return False

    def stop_service(self) -> bool:
        """停止Ollama服务"""
        if self.process:
            self.process.terminate()
            self.process = None
            return True
        return False

    def unload_model(self) -> bool:
        """主动卸载模型（发送keep_alive=0的空请求）"""
        try:
            resp = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": "",
                    "keep_alive": 0
                },
                timeout=5
            )
            self._model_loaded = False
            return resp.status_code == 200
        except:
            return False

    def generate(self, prompt: str, keep_alive: str = "5m", timeout: int = 30) -> str:
        """同步调用Ollama生成（非流式，简单场景用）"""
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "prompt": prompt, "stream": False, "keep_alive": keep_alive},
                timeout=timeout
            )
            if response.status_code == 200:
                result = response.json()
                if "error" in result:
                    print(f"Ollama返回错误: {result['error']}")
                    return ""
                resp = result.get("response", "")
                if not resp:
                    return ""
                resp = re.sub(r'[\U00010000-\U0010ffff]', '', resp)
                resp = resp.replace('\x00', '').replace('﻿', '')
                return resp[:1000] if len(resp) > 1000 else resp
            print(f"Ollama响应状态码: {response.status_code}")
            return ""
        except requests.exceptions.Timeout:
            print("Ollama请求超时")
            return ""
        except Exception as e:
            print(f"Ollama调用失败: {e}")
            return ""

    def generate_diagnosis(self, sensor_id: str, stats: dict, all_data: list = None,
                          keep_alive: str = "5m") -> str:
        """生成诊断摘要"""
        if all_data and len(all_data) > 0:
            sample_count = min(50, len(all_data))
            sample_data = all_data[:sample_count]
            sample_str = ', '.join([f'{v:.2f}' for v in sample_data])
            data_count = stats.get('count', len(all_data))

            # 按比例压缩数据
            if len(all_data) > 200:
                # 超过200个点，按1/4压缩
                step = max(1, len(all_data) // 50)
                compressed_data = all_data[::step][:50]
                sample_str = ', '.join([f'{v:.2f}' for v in compressed_data])

            prompt = f"""Analyze {sensor_id} physical measurement data:
- Data count: {data_count}
- Sample values (first {len(compressed_data) if 'compressed_data' in dir() else sample_count}): [{sample_str}]
- Stats: max={stats.get('max', 0):.2f}, min={stats.get('min', 0):.2f}, mean={stats.get('mean', 0):.2f}, std={stats.get('std', 0):.2f}
- Peak-to-peak: {stats.get('peak_to_peak', 0):.2f}, RMS: {stats.get('rms', 0):.2f}

Please analyze this time series data and identify:
1. Overall trend (stable, increasing, decreasing, fluctuating)
2. Any anomalies or outliers
3. Data quality assessment

If anything is unclear, ask me to clarify via dialog. Keep response concise under 200 words."""
        else:
            prompt = f"Health check: max={stats.get('max', 0):.1f}, min={stats.get('min', 0):.1f}, std={stats.get('std', 0):.1f}. Short verdict only."
        return self.generate(prompt, keep_alive=keep_alive, timeout=30)

    def generate_report_summary(self, data_info: dict, stats: dict) -> str:
        """生成报告摘要"""
        prompt = f"Data: {data_info.get('rows', 0)} rows x {data_info.get('cols', 0)} cols. Mean={stats.get('mean', 0):.1f}. 1-line summary."
        return self.generate(prompt, keep_alive="5m", timeout=30)