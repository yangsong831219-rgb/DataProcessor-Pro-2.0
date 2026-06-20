"""app 启动级冒烟 — AiDiagnosisWidget 导入 + _find_main 在类内。"""


class TestAiDiagBoot:

    def test_import_no_error(self):
        """AiDiagnosisWidget 和 AIClientStreamThread 都能导入。"""
        from ui.ai_diagnosis import AiDiagnosisWidget, AIClientStreamThread
        assert AiDiagnosisWidget is not None
        assert AIClientStreamThread is not None

    def test_find_main_is_method(self):
        """_find_main 是 AiDiagnosisWidget 的方法。"""
        from ui.ai_diagnosis import AiDiagnosisWidget
        import inspect
        assert hasattr(AiDiagnosisWidget, '_find_main')
        assert inspect.isfunction(AiDiagnosisWidget._find_main)

    def test_build_source_group_is_method(self):
        """_build_source_group 是类方法。"""
        from ui.ai_diagnosis import AiDiagnosisWidget
        assert hasattr(AiDiagnosisWidget, '_build_source_group')
        assert callable(AiDiagnosisWidget._build_source_group)

    def test_refresh_source_checkboxes_is_method(self):
        """_refresh_source_checkboxes 可调用。"""
        from ui.ai_diagnosis import AiDiagnosisWidget
        assert hasattr(AiDiagnosisWidget, '_refresh_source_checkboxes')
        assert callable(AiDiagnosisWidget._refresh_source_checkboxes)

    def test_show_event_is_method(self):
        """showEvent 可重写。"""
        from ui.ai_diagnosis import AiDiagnosisWidget
        assert hasattr(AiDiagnosisWidget, 'showEvent')
