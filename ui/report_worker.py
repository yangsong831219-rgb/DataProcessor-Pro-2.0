"""通用后台 ReportWorker — QThread 子类，支持进度反馈与安全取消"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class ReportWorker(QThread):
    """通用后台 Worker — 在子线程执行耗时的 AI/渲染任务.

    Signals:
        finished(obj): 任务完成，obj 为 work_fn 返回值
        error(str): 任务失败，附带错误消息（含 collected_warnings）
        progress(dict): 进度更新，{'stage': 'section'|'render', 'current': int, 'total': int, 'heading': str}
    """

    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(dict)

    def __init__(self, work_fn, args=None, kwargs=None):
        super().__init__()
        self._work_fn = work_fn
        self._args = args or ()
        self._kwargs = kwargs or {}
        self._cancelled = False

    def cancel(self) -> None:
        """设置取消标志 — worker 应在节间检查并优雅停止."""
        self._cancelled = True

    def run(self):
        import traceback
        # 签名感知注入：只传 work_fn 声明的 kwarg，不声明的不传
        import inspect as _inspect
        kwargs = dict(self._kwargs)
        try:
            sig = _inspect.signature(self._work_fn)
            allowed = set(sig.parameters.keys())
        except Exception:
            allowed = set()
        for k in list(kwargs.keys()):
            if k not in allowed:
                del kwargs[k]
        # 注入 worker 引用（若 work_fn 声明了对应参数名）
        for name in ('worker', '_worker'):
            if name in allowed:
                kwargs[name] = self
        try:
            result = self._work_fn(*self._args, **kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(f'{e}\n{traceback.format_exc()}')
