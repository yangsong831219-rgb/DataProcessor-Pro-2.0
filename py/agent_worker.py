"""DeepSeek Agent Worker - PyQt6 后台线程包装类

符合 PyQt6 异步规范的 QThread 后台线程包装类，
确保大模型的思考状态、日志及图表重绘信号能够流畅地与主界面交互。
"""

from PyQt6.QtCore import QThread, pyqtSignal
from typing import Optional
import threading


class DeepSeekAgentWorker(QThread):
    """
    DeepSeek Agent 后台工作线程

    Signals:
        text_stream_signal: 流式向前端文本框吐出 DeepSeek 的思考日志
        data_mutation_signal: 工具箱生成新数据文件时触发，通知主窗口重绘图表
        report_ready_signal: 最终专家诊断完成后，投递富文本报告
        error_signal: 发生错误时触发
        finished_signal: 线程执行完成时触发
    """

    text_stream_signal = pyqtSignal(str)
    data_mutation_signal = pyqtSignal(str)
    report_ready_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._user_input: str = ""
        self._csv_path: Optional[str] = None
        self._is_running: bool = False
        self._lock = threading.Lock()

    def set_task(self, user_input: str, csv_path: Optional[str] = None):
        """
        设置任务参数

        Args:
            user_input: 用户输入的指令
            csv_path: 当前处理的 CSV 文件路径
        """
        with self._lock:
            self._user_input = user_input
            self._csv_path = csv_path

    def run(self):
        """在线程中执行 Agent"""
        with self._lock:
            if self._is_running:
                return
            self._is_running = True

        try:
            self._execute_agent()
        except Exception as e:
            self.error_signal.emit(f"Agent 执行错误: {str(e)}")
        finally:
            self._is_running = False
            self.finished_signal.emit()

    def _execute_agent(self):
        """执行 Agent 逻辑"""
        from py.agent_graph import run_agent, TOOLS
        from py.wiki_system import WikiFileSystem
        from langchain_core.messages import HumanMessage
        from langgraph.graph import StateGraph, END
        from langgraph.prebuilt import ToolNode
        from langchain_openai import ChatOpenAI
        import os

        user_input = self._user_input
        csv_path = self._csv_path

        self.text_stream_signal.emit(f"[Agent] 收到任务: {user_input[:50]}...")

        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        api_base = os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com")

        if not api_key:
            self.text_stream_signal.emit("[Agent] 离线模式: 无 API Key 配置")
            self.report_ready_signal.emit("当前处于离线模式，无法连接 DeepSeek API。请检查环境变量 DEEPSEEK_API_KEY 配置。")
            return

        llm = ChatOpenAI(
            model="deepseek-chat",
            api_key=api_key,
            base_url=api_base,
            streaming=True,
        )

        from typing import TypedDict, Annotated, List
        from langchain_core.messages import BaseMessage

        class AgentState(TypedDict):
            messages: Annotated[List[BaseMessage], lambda x, y: x + y]
            current_csv_path: str
            execution_logs: List[str]

        def should_continue(state: AgentState) -> str:
            last_message = state["messages"][-1]
            if hasattr(last_message, "tool_calls") and last_message.tool_calls:
                return "call_tools"
            return "end"

        def tool_execution_node(state: AgentState) -> AgentState:
            """工具执行节点"""
            last_message = state["messages"][-1]
            if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
                return state

            execution_logs = list(state.get("execution_logs", []))
            for call in last_message.tool_calls:
                tool_name = call.get("name", "")
                tool_args = call.get("args", {})

                self.text_stream_signal.emit(f"[工具调用] {tool_name}({tool_args.get('file_path', '')})")

                try:
                    for tool in TOOLS:
                        if tool.name == tool_name:
                            result = tool.invoke(tool_args)
                            self.text_stream_signal.emit(f"[结果] {result[:100]}...")

                            if "_filtered.csv" in str(result) or "_formula.csv" in str(result):
                                file_match = [w for w in str(result).split() if w.endswith(".csv")]
                                if file_match:
                                    self.data_mutation_signal.emit(file_match[-1])

                            execution_logs.append(f"{tool_name}: {result}")
                            break
                except Exception as e:
                    error_msg = f"工具执行错误 {tool_name}: {str(e)}"
                    self.text_stream_signal.emit(f"[错误] {error_msg}")
                    execution_logs.append(error_msg)

            new_messages = state["messages"] + [
                HumanMessage(content=f"工具执行完成，结果: {execution_logs[-1] if execution_logs else '无'}")
            ]
            return {
                "messages": new_messages,
                "execution_logs": execution_logs,
                "current_csv_path": state.get("current_csv_path", ""),
            }

        workflow = StateGraph(AgentState)
        workflow.add_node("llm_brain", lambda state: state)
        workflow.add_node("action_tools", tool_execution_node)
        workflow.set_entry_point("llm_brain")

        workflow.add_conditional_edges(
            "llm_brain",
            should_continue,
            {"call_tools": "action_tools", "end": END}
        )
        workflow.add_edge("action_tools", "llm_brain")

        graph = workflow.compile()

        self.text_stream_signal.emit("[Agent] 开始执行 ReAct 循环...")

        from typing import List
        initial_state = AgentState(
            messages=[HumanMessage(content=user_input)],
            current_csv_path=csv_path or "",
            execution_logs=[]
        )

        result = graph.invoke(initial_state)
        final_response = result["messages"][-1].content

        self.text_stream_signal.emit(f"[Agent] 执行完成")
        self.report_ready_signal.emit(final_response)

    def stop(self):
        """停止 Agent 执行"""
        with self._lock:
            self._is_running = False
        self.terminate()
        self.text_stream_signal.emit("[Agent] 已停止")


class WikiSyncWorker(QThread):
    """
    Wiki 知识库同步工作线程
    用于在后台执行知识库的读写操作，不阻塞主界面
    """

    completed_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._operation: str = ""
        self._page_name: str = ""
        self._content: str = ""

    def set_write_task(self, page_name: str, content: str):
        """设置写入任务"""
        self._operation = "write"
        self._page_name = page_name
        self._content = content

    def set_read_task(self, page_name: str):
        """设置读取任务"""
        self._operation = "read"
        self._page_name = page_name
        self._content = ""

    def run(self):
        """执行 Wiki 操作"""
        try:
            from py.wiki_system import WikiFileSystem
            wiki = WikiFileSystem()

            if self._operation == "write":
                result = wiki.write_wiki_page(self._page_name, self._content)
                self.completed_signal.emit(result)
            elif self._operation == "read":
                result = wiki.read_wiki_page(self._page_name)
                self.completed_signal.emit(result)
            else:
                self.error_signal.emit(f"未知操作: {self._operation}")
        except Exception as e:
            self.error_signal.emit(f"Wiki 操作错误: {str(e)}")