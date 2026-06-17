"""通用后台 ReportWorker — QThread 子类"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal


class ReportWorker(QThread):
    """通用后台 Worker — 在子线程执行耗时的 AI/渲染任务."""

    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, work_fn, args=None, kwargs=None):
        super().__init__()
        self._work_fn = work_fn
        self._args = args or ()
        self._kwargs = kwargs or {}

    def run(self):
        try:
            result = self._work_fn(*self._args, **self._kwargs)
            self.finished.emit(result)
        except Exception as e:
            import traceback
            self.error.emit(f'{e}\n{traceback.format_exc()}')

