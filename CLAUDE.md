# DataProcessor Pro 2.0 — Claude Code 执行纪律（SSOT）

本文件是项目级 Claude Code 行为规范的唯一来源。AGENTS.md 仅做指针引用，不重复内容。

---

## 1. 领域代码纪律（优先级最高）

- **Pandas**: 严禁 float64 切片就地插对象/字符串；必须 `astype(object)` → `pd.concat` 拼接。禁止 `if df`/`if series` 布尔判断，用 `df is None` 或 `df.empty`。
- **PyQt6**: 跨标签页取数据禁用 `self.parent()`，必须用 `self.window()`。UI 标签含单位后缀，用 `re.sub(r'[\(（].*?[\)）]', '', name).strip()` 剥离后再做 DataFrame 键。
- **光纤 vs 通用数据**: `analysis_tab.py` 中必须严格 IF-ELSE 隔离。通用数据锁死"物理量"，绕过 `SensorSystem.calculate()`。
- **公式求值**: 必须走 `dp_engine.formula.calculate()`（asteval），禁止 `eval` 或字符串替换。
- **字典取值**: 键可能为 None 时必须用 `d.get(key) or default`，不能只用 `d.get(key, default)`。

## 2. AI 工具使用

- 查 Python 符号、定义和调用关系时优先使用可用的 `codegraph_*` 工具；文本、配置、安全模式及未索引文件使用 `rg`。工具不可用时记录原因并使用 `rg`/IDE 搜索回退。
- 改函数签名前必须 `codegraph_callers` 确认所有调用点。
- LLM 输出必须经 Pydantic schema 校验，严禁 `json.loads` 静默降级。
- AI 调用失败必须抛异常，严禁返回空字符串。

## 3. 批次与范围纪律

- 未经审核通过不得提前进入下一批。每批开始前明确目标文件、禁止修改项和验收命令。
- 不得顺手重构无关模块（报告、模板等）。用户已有未提交修改不得擅自回退。
- 完成后检查 Git diff，清理重复实现和不可达旧代码。

## 4. 测试纪律

- 禁止 `-k not ...`、skip、xfail、删除测试或弱化断言来绕过验收。
- 核心生命周期测试必须零 skipped、零 deselected。"未包含在命令中"就是未执行。
- 本批新增失败不得称为预存失败。预存认定需要：修改前基线 + 相同 node ID + 未触及代码证据齐全。
- 禁止模糊统计（"基本通过""约 400 passed"）。必须给出精确数字。
- 真实主窗口测试必须实例化 `DataProcessorWindow()`。
- 每个失败必须给出 node ID、根因、修改文件和复测结果。

## 5. 静态检查与审核证据

- Pyright 必须覆盖本批全部修改文件，修改行 warning 必须清零。
- 禁止用全局配置或文件级 ignore 隐藏自有代码问题。
- 审核包必须给出真实命令原文、精确统计和关键 diff。
- 声称零危险代码前必须执行对应搜索（rg 模式 + 文件名清单）。

## 6. Qt / Worker 生命周期

- 禁止覆盖 `QThread.started/finished/terminated`。
- 禁止 `QThread.terminate()`、无限 `wait()`、销毁运行中的 QThread。
- Worker 必须有生命周期长于 Widget/MainWindow 的明确所有者（如 app 级 TaskOwner）。
- 应用有活动文件事务时不得退出。超时只能提示等待，不得交 OS 强制清理。
- UI 线程禁止 ZIP 中央目录解析、CRC、解压、大文件哈希或大量复制。

## 7. 网络、文件与 Registry 安全

- GitHub 下载必须手动验证每一跳重定向（ManualRedirectPolicy）。
- Token 不得进入 URL、日志、异常或结果对象。
- ZIP 解压前检查：路径遍历、数量、大小、压缩比、symlink、加密、特殊文件。
- 禁止移动用户源目录/源 ZIP/项目文件。安装/替换/卸载/迁移前必须取 Registry 内存快照。
- Registry 保存失败必须同时恢复内存和文件系统。

## 8. 提交硬门槛

以下全部满足才能写"可提交审核"，缺任何一项只能写"待整改"：

1. 本批范围测试零失败
2. 核心生命周期测试零 skipped、零 deselected
3. 本批修改代码 Pyright 零 error、零 warning
4. Compileall 通过
5. Git diff 无无关修改和重复实现

---

## 常用命令

```bash
python main.py                           # 运行主程序
pyright <changed_files>                  # 类型检查（提交前零红线）
python -m pytest tests/ -q --tb=short    # 全量测试
python -m compileall <files>             # 语法编译检查
```
