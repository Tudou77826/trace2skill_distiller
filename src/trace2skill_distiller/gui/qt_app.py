"""Native PySide6 desktop GUI for Trace2Skill.

Single executable, dual purpose:
  - launched with no arguments  -> opens the desktop window
  - launched with arguments     -> delegates to the Click CLI

All backend logic lives in :mod:`trace2skill_distiller.gui.services`; this module
only renders the UI and routes user actions to those helpers.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.config import load_config
from ..orchestrator.pipeline import DistillPipeline
from ..output.types import DistillReport
from . import services


# Decision-button copy, mirroring the former HTML ``memoryDecisionCopy``.
_DECISION_COPY = {
    "confirm": {
        "label": "保存到记忆文件",
        "effect": "写入 {target}，后续 AI 会按这条记忆工作。",
        "done": "已保存到 {target}；后续 AI 可以直接使用这条记忆。",
    },
    "review": {
        "label": "放入待确认",
        "effect": "留在待确认区，暂不写入 {target}。",
        "done": "已放入待确认；它暂时不会写入 {target}。",
    },
    "archive": {
        "label": "不保存",
        "effect": "从有效记忆里移除，不写入 {target}。",
        "done": "已暂不保留；这条内容不会写入 {target}。",
    },
}


# --------------------------------------------------------------------------- #
# Entry point / argv dispatch
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    """Dispatch: no args -> GUI window; args -> CLI."""
    argv = list(sys.argv[1:] if argv is None else argv)
    # Click reserves ``--help`` etc.; any argument means CLI mode.
    if argv:
        from ..cli.main import cli

        try:
            cli(args=argv, standalone_mode=False, prog_name="trace2skill-gui")
        except SystemExit as exc:  # Click raises SystemExit on --help / errors.
            return int(exc.code or 0)
        return 0
    return _run_window()


def _run_window() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Trace2Skill")
    window = MainWindow()
    window.show()
    return app.exec()


# --------------------------------------------------------------------------- #
# Background pipeline worker
# --------------------------------------------------------------------------- #
class DreamWorker(QThread):
    """Run the distillation pipeline off the UI thread."""

    finished_ok = Signal(object)   # DistillReport
    failed = Signal(str)           # user-facing error message

    def __init__(self, cfg, project: str, session_ids: list[str]):
        super().__init__()
        self._cfg = cfg
        self._project = project
        self._session_ids = session_ids

    def run(self) -> None:  # noqa: D401
        try:
            pipeline = DistillPipeline.from_config(self._cfg)
            report = pipeline.run(
                project=self._project,
                session_ids=self._session_ids,
                mode="full",
                preview=False,
                max_sessions=None,
                incremental=False,
            )
            self.finished_ok.emit(report)
        except BaseException as exc:  # noqa: BLE001
            self.failed.emit(services.friendly_error(exc))


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Trace2Skill 记忆整理")
        self.resize(1180, 760)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(10, 10, 10, 6)

        header = QHBoxLayout()
        title = QLabel("Trace2Skill 记忆整理")
        title.setFont(QFont(title.font().family(), 14, QFont.Bold))
        header.addWidget(title)
        header.addStretch()
        quit_btn = QPushButton("✕ 退出")
        quit_btn.setCursor(Qt.PointingHandCursor)
        quit_btn.setStyleSheet("color: #c0392b;")
        quit_btn.clicked.connect(self.close)
        header.addWidget(quit_btn)
        outer.addLayout(header)

        self._summary = QLabel("数据源：…  |  输出目录：…")
        self._summary.setStyleSheet("color: #6b7280;")
        outer.addWidget(self._summary)

        self.tabs = QTabWidget()
        self.extract = ExtractPanel(self)
        self.settings = SettingsPanel()
        self.tabs.addTab(self.extract, "记忆整理")
        self.tabs.addTab(self.settings, "设置")
        outer.addWidget(self.tabs, 1)

        self.setStatusBar(self.statusBar())
        self.setCentralWidget(central)

        # Bootstrap: load config + sessions.
        self._refresh_summary()
        QTimer.singleShot(0, self.extract.load_sessions)
        QTimer.singleShot(50, self.settings.load_into_form)

    def _refresh_summary(self) -> None:
        try:
            cfg = load_config()
            loc = services._source_location(cfg)
            self._summary.setText(
                f"数据源：{cfg.source.type}（{loc}）  |  输出目录：{cfg.output.skill_output_dir}"
            )
        except Exception:  # noqa: BLE001
            self._summary.setText("数据源：未配置  |  输出目录：未配置")


# --------------------------------------------------------------------------- #
# Extract panel (session selection + results/review)
# --------------------------------------------------------------------------- #
class ExtractPanel(QWidget):
    COL_CHECK, COL_TITLE, COL_PROJECT, COL_MSGS, COL_TOOLS, COL_DATE = range(6)

    def __init__(self, main_window: MainWindow):
        super().__init__()
        self._main = main_window
        self._cfg = None
        self._current_project = "general"
        self._memory_target = "agent-context.md"
        self._worker: DreamWorker | None = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._elapsed_start = 0.0

        splitter = QSplitter(Qt.Horizontal)

        # --- left: session selection ------------------------------------- #
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 6, 0)

        form = QHBoxLayout()
        self.project_input = QLineEdit()
        self.project_input.setPlaceholderText("项目筛选")
        form.addWidget(QLabel("项目："))
        form.addWidget(self.project_input, 2)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 10000)
        self.limit_spin.setValue(80)
        form.addWidget(QLabel("数量："))
        form.addWidget(self.limit_spin)
        load_btn = QPushButton("加载")
        load_btn.clicked.connect(self.load_sessions)
        form.addWidget(load_btn)
        left_layout.addLayout(form)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "会话", "项目", "消息", "工具", "日期"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_CHECK, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_TITLE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_PROJECT, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_MSGS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_TOOLS, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_DATE, QHeaderView.ResizeToContents)
        # Header "select all" checkbox.
        self._select_all = QCheckBox()
        self._select_all.stateChanged.connect(self._toggle_all)
        self.table.setCellWidget(-1, 0, None)  # placeholder
        left_layout.addWidget(self.table, 1)

        run_row = QHBoxLayout()
        self.dream_btn = QPushButton("提取所选会话")
        self.dream_btn.clicked.connect(self.run_dream)
        run_row.addWidget(self.dream_btn)
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.setFixedWidth(140)
        self.spinner.setVisible(False)
        run_row.addWidget(self.spinner)
        run_row.addStretch()
        left_layout.addLayout(run_row)

        splitter.addWidget(left)

        # --- right: results / review ------------------------------------- #
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)

        top_row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_memory)
        top_row.addWidget(refresh_btn)
        top_row.addStretch()
        right_layout.addLayout(top_row)

        self.status_label = QLabel("请选择会话，然后开始提取。")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("padding: 4px;")
        right_layout.addWidget(self.status_label)

        self.quality_label = QLabel("")
        self.quality_label.setFont(QFont(self.quality_label.font().family(), 12, QFont.Bold))
        right_layout.addWidget(self.quality_label)
        self.result_summary = QLabel("")
        self.result_summary.setWordWrap(True)
        self.result_summary.setStyleSheet("color: #4b5563;")
        right_layout.addWidget(self.result_summary)
        self.metrics_label = QLabel("")
        self.metrics_label.setStyleSheet("color: #6b7280;")
        right_layout.addWidget(self.metrics_label)

        # Lanes (scroll area holding the three groups).
        self.lanes_host = QWidget()
        lanes_v = QVBoxLayout(self.lanes_host)
        lanes_v.setContentsMargins(0, 0, 0, 0)
        self.lanes_layout = lanes_v
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.lanes_host)
        scroll.setFrameShape(QScrollArea.NoFrame)
        right_layout.addWidget(scroll, 1)

        actions_box = QGroupBox("建议操作")
        self.actions_label = QLabel("")
        self.actions_label.setWordWrap(True)
        actions_v = QVBoxLayout(actions_box)
        actions_v.addWidget(self.actions_label)
        right_layout.addWidget(actions_box)

        self.path_label = QLabel("")
        self.path_label.setStyleSheet("font-family: monospace; color: #6b7280;")
        self.path_label.setWordWrap(True)
        right_layout.addWidget(self.path_label)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 4)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

    # -- sessions -------------------------------------------------------- #
    def load_sessions(self) -> None:
        self.status_label.setText("正在加载会话…")
        QApplication.processEvents()
        try:
            self._cfg = load_config()
            project = self.project_input.text().strip() or None
            rows = services.session_rows(self._cfg, project=project, limit=self.limit_spin.value())
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"加载失败：{exc}")
            return
        self.table.setRowCount(0)
        for row in rows:
            self._add_session_row(row)
        eligible = sum(1 for r in rows if r.get("eligible"))
        self.status_label.setText(
            f"已加载 {len(rows)} 个会话，其中 {eligible} 个满足当前筛选。"
            "优先选择没有红字提示的会话。"
        )

    def _add_session_row(self, row: dict[str, Any]) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        chk = QCheckBox()
        self.table.setCellWidget(r, self.COL_CHECK, chk)
        title = row.get("title") or "（未命名）"
        if row.get("hint"):
            title = f"{title}    不会产生结果：{row['hint']}"
        items = [
            QTableWidgetItem(title),
            QTableWidgetItem(str(row.get("project") or "")),
            QTableWidgetItem(str(row.get("messages") or 0)),
            QTableWidgetItem(str(row.get("tools") or 0)),
            QTableWidgetItem(str(row.get("date") or "")),
        ]
        # Stash the session id on the title item so selection can recover it.
        items[0].setData(Qt.UserRole, row.get("id"))
        for c, it in enumerate(items, start=1):
            if c in (self.COL_MSGS, self.COL_TOOLS):
                it.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(r, c, it)
        if not row.get("eligible"):
            for c in range(1, 6):
                item = self.table.item(r, c)
                if item:
                    item.setForeground(Qt.gray)
            title_item = self.table.item(r, self.COL_TITLE)
            if title_item:
                title_item.setForeground(Qt.red)

    def _toggle_all(self, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, self.COL_CHECK)
            if isinstance(w, QCheckBox):
                w.setChecked(checked)

    # -- dream ----------------------------------------------------------- #
    def run_dream(self) -> None:
        ids = self._collect_checked_ids()
        if not ids:
            self.status_label.setText("请至少选择一个会话。")
            return
        if self._cfg is None:
            self._cfg = load_config()
        project = self.project_input.text().strip() or self._current_project
        self._current_project = project
        self.dream_btn.setEnabled(False)
        self.spinner.setVisible(True)
        self.status_label.setText(f"正在提取 {len(ids)} 个会话…")
        self._elapsed_start = time.time()
        self._elapsed_timer.start(1000)

        self._worker = DreamWorker(self._cfg, project, ids)
        self._worker.finished_ok.connect(self._on_dream_done)
        self._worker.failed.connect(self._on_dream_failed)
        self._worker.start()

    def _collect_checked_ids(self) -> list[str]:
        ids: list[str] = []
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, self.COL_CHECK)
            if isinstance(w, QCheckBox) and w.isChecked():
                # Id was stashed on the title item at load time.
                ids.append(self.table.item(r, self.COL_TITLE).data(Qt.UserRole) or "")
        return [i for i in ids if i]

    def _tick_elapsed(self) -> None:
        secs = int(time.time() - self._elapsed_start)
        self.status_label.setText(f"正在提取…（{secs}s）")

    def _on_dream_done(self, report: DistillReport) -> None:
        self._dream_cleanup()
        self._current_project = report.project or self._current_project
        status = services.run_status_payload(report)
        self.status_label.setText(status["message"])
        try:
            snap = services.memory_snapshot(load_config(), self._current_project)
        except Exception as exc:  # noqa: BLE001
            snap = {"groups": [], "quality": {}, "agent_context_path": "", "summary": ""}
        self._render_memory(snap, status["action"], status["status"])

    def _on_dream_failed(self, msg: str) -> None:
        self._dream_cleanup()
        self.status_label.setText(msg)

    def _dream_cleanup(self) -> None:
        self._elapsed_timer.stop()
        self.dream_btn.setEnabled(True)
        self.spinner.setVisible(False)

    # -- memory review --------------------------------------------------- #
    def refresh_memory(self) -> None:
        if self._cfg is None:
            self._cfg = load_config()
        self.status_label.setText("正在刷新记忆…")
        QApplication.processEvents()
        try:
            snap = services.memory_snapshot(self._cfg, self._current_project)
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"刷新失败：{exc}")
            return
        total = sum(len(g.get("items", [])) for g in snap.get("groups", []))
        self.status_label.setText(f"已刷新 {total} 条记忆。")
        self._render_memory(snap, None, None)

    def _render_memory(self, snap: dict[str, Any], run_action: str | None, run_status: str | None) -> None:
        # Target filename for the decision-button copy.
        ctx_path = snap.get("agent_context_path") or ""
        self._memory_target = ctx_path.replace("\\", "/").split("/")[-1] if ctx_path else "agent-context.md"
        self.path_label.setText(f"保存位置：{ctx_path}")

        quality = snap.get("quality", {}) or {}
        total = int(quality.get("total", 0) or 0)
        self.quality_label.setText(f"学到了 {total} 条可复用记忆")
        self.result_summary.setText(snap.get("summary") or "")
        q = quality
        self.metrics_label.setText(
            f"可直接使用：{q.get('agent_ready', 0)}    "
            f"需要确认：{q.get('review', 0)}    "
            f"开放问题：{q.get('open_questions', 0)}    "
            f"可信度：{q.get('score', 0)}/100"
        )

        # Suggested actions: run-level hint takes priority when non-ok.
        actions = quality.get("next_actions", []) or []
        if run_action and run_status and run_status != "ok":
            actions = [run_action]
        self.actions_label.setText("\n".join(f"• {a}" for a in actions) if actions else "暂无建议操作。")

        # Clear lanes host.
        while self.lanes_layout.count():
            child = self.lanes_layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()
        for group in snap.get("groups", []):
            self.lanes_layout.addWidget(self._build_lane(group))
        self.lanes_layout.addStretch()

    def _build_lane(self, group: dict[str, Any]) -> QWidget:
        box = QGroupBox(f"{group.get('title', '')}（{len(group.get('items', []))}）")
        v = QVBoxLayout(box)
        sub = QLabel(group.get("subtitle", ""))
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #6b7280; font-size: 11px;")
        v.addWidget(sub)
        for item in group.get("items", []):
            v.addWidget(self._build_card(item))
        if not group.get("items"):
            empty = QLabel("（暂无）")
            empty.setStyleSheet("color: #9ca3af;")
            v.addWidget(empty)
        return box

    def _build_card(self, item: dict[str, Any]) -> QWidget:
        card = QFrame_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(8, 6, 8, 6)

        head = QHBoxLayout()
        type_badge = QLabel(item.get("type", ""))
        type_badge.setStyleSheet(
            "background:#eef2ff;color:#3730a3;padding:1px 6px;border-radius:3px;font-size:11px;"
        )
        head.addWidget(type_badge)
        status_lbl = QLabel(item.get("status", ""))
        tone = item.get("tone")
        status_lbl.setStyleSheet(
            f"color:#fff;padding:1px 6px;border-radius:3px;font-size:11px;"
            f"background:{'#d97706' if tone == 'review' else '#059669'};"
        )
        head.addWidget(status_lbl)
        conf = QLabel(f"{item.get('scope', '')} · 可信度 {int((item.get('confidence') or 0) * 100)}%")
        conf.setStyleSheet("color:#6b7280;font-size:11px;")
        head.addWidget(conf)
        head.addStretch()
        lay.addLayout(head)

        action = QLabel(item.get("action", ""))
        action.setWordWrap(True)
        action.setFont(QFont(action.font().family(), 10, QFont.Bold))
        lay.addWidget(action)

        for label, key in (("用途", "why"), ("AI 使用", "ai_use"), ("适用场景", "condition")):
            val = item.get(key)
            if val:
                lbl = QLabel(f"{label}：{val}")
                lbl.setWordWrap(True)
                lbl.setStyleSheet("color:#4b5563;font-size:11px;")
                lay.addWidget(lbl)
        evidence = item.get("evidence") or []
        if evidence:
            ev = QLabel("依据：" + "；".join(evidence))
            ev.setWordWrap(True)
            ev.setStyleSheet("color:#6b7280;font-size:11px;")
            lay.addWidget(ev)

        btn_row = QHBoxLayout()
        tone_val = item.get("tone")
        # Hide the confirm button when the item is already confirmed & ready.
        can_confirm = tone_val == "review" or item.get("ai_use") != "已确认，可进入长期上下文"
        for action_key in ("confirm", "review", "archive"):
            if action_key == "confirm" and not can_confirm:
                continue
            copy = _DECISION_COPY[action_key]
            btn = QPushButton(copy["label"])
            btn.setToolTip(copy["effect"].format(target=self._memory_target))
            if action_key == "confirm":
                btn.setStyleSheet("background:#4f46e5;color:white;")
            elif action_key == "archive":
                btn.setStyleSheet("color:#b91c1c;")
            btn.clicked.connect(lambda _=False, k=action_key, it=item: self._decide(it, k))
            btn_row.addWidget(btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return card

    def _decide(self, item: dict[str, Any], action: str) -> None:
        copy = _DECISION_COPY[action]
        self.status_label.setText(f"正在处理：{item.get('action', '')[:30]}…")
        QApplication.processEvents()
        try:
            snap = services.update_memory_item(
                load_config(),
                {"project": self._current_project, "id": item.get("id"), "action": action},
            )
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"操作失败：{exc}")
            return
        self.status_label.setText(copy["done"].format(target=self._memory_target))
        self._render_memory(snap, None, None)


class QFrame_card(QWidget):
    """A bordered card container (named to read well at call sites)."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(
            "QFrame_card{border:1px solid #e5e7eb;border-radius:6px;background:#ffffff;}"
        )


# --------------------------------------------------------------------------- #
# Settings panel
# --------------------------------------------------------------------------- #
class SettingsPanel(QWidget):
    def __init__(self):
        super().__init__()
        self._last_config: dict[str, Any] = {}
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setLabelAlignment(Qt.AlignRight)
        form.setSpacing(8)

        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("请输入 API Key")
        form.addRow("API Key（写入 .env）", self.api_key)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("如 https://api.openai.com/v1")
        form.addRow("Base URL（写入 .env）", self.base_url)

        self.fast_model = QLineEdit()
        form.addRow("快速模型（预处理）", self.fast_model)
        self.strong_model = QLineEdit()
        form.addRow("强力模型（蒸馏）", self.strong_model)
        self.fast_concurrency = QSpinBox()
        self.fast_concurrency.setRange(1, 64)
        form.addRow("快速模型并发", self.fast_concurrency)
        self.strong_concurrency = QSpinBox()
        self.strong_concurrency.setRange(1, 64)
        form.addRow("强力模型并发", self.strong_concurrency)

        self.source_type = QComboBox()
        for opt in services._SOURCE_OPTIONS:
            self.source_type.addItem(opt)
        form.addRow("数据源类型", self.source_type)
        self.source_location = QLineEdit()
        form.addRow("数据源路径", self.source_location)

        self.output_dir = QLineEdit()
        form.addRow("技能输出目录", self.output_dir)
        self.agent_ctx = QLineEdit()
        self.agent_ctx.setPlaceholderText("留空=默认 agent-context.md；填路径则追加到该文件")
        form.addRow("Agent 上下文文件", self.agent_ctx)
        self.user_profile = QLineEdit()
        self.user_profile.setPlaceholderText("留空=默认 user-profile.md；填路径则追加到该文件")
        form.addRow("用户偏好文件", self.user_profile)
        self.repo_facts = QLineEdit()
        self.repo_facts.setPlaceholderText("留空=默认 repo-facts.md；填路径则追加到该文件")
        form.addRow("仓库事实文件", self.repo_facts)

        self.min_messages = QSpinBox()
        self.min_messages.setRange(0, 100000)
        form.addRow("最小消息数", self.min_messages)
        self.min_tools = QSpinBox()
        self.min_tools.setRange(0, 100000)
        form.addRow("最小工具数", self.min_tools)

        self.proxy = QLineEdit()
        form.addRow("代理地址", self.proxy)
        self.proxy_bypass = QLineEdit()
        form.addRow("代理绕过", self.proxy_bypass)
        self.verify_ssl = QCheckBox("验证 SSL")
        form.addRow("", self.verify_ssl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_widget)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("")
        self.hint.setStyleSheet("color:#6b7280;")
        outer.addWidget(self.hint)
        outer.addWidget(scroll)

        bottom = QHBoxLayout()
        bottom.addStretch()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#6b7280;")
        bottom.addWidget(self.status)
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("background:#4f46e5;color:white;padding:4px 18px;")
        save_btn.clicked.connect(self.save)
        bottom.addWidget(save_btn)
        outer.addLayout(bottom)

    def load_into_form(self) -> None:
        try:
            data = services.config_view(load_config())
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"读取配置失败：{exc}")
            return
        self._last_config = data
        self._fill(data)

    def _fill(self, data: dict[str, Any]) -> None:
        fast = data.get("fast", {}) or {}
        strong = data.get("strong", {}) or {}
        src = data.get("source", {}) or {}
        out = data.get("output", {}) or {}
        flt = data.get("filter", {}) or {}
        self.api_key.clear()
        self.api_key.setPlaceholderText(
            "已设置（留空不修改）" if fast.get("api_key_set") else "请输入 API Key"
        )
        self.base_url.setText(fast.get("base_url") or "")
        self.fast_model.setText(fast.get("model") or "")
        self.strong_model.setText(strong.get("model") or "")
        self.fast_concurrency.setValue(int(fast.get("max_concurrency") or 1))
        self.strong_concurrency.setValue(int(strong.get("max_concurrency") or 1))
        idx = self.source_type.findText(src.get("type") or "opencode")
        self.source_type.setCurrentIndex(idx if idx >= 0 else 0)
        self.source_location.setText(src.get("location") or "")
        self.output_dir.setText(out.get("skill_output_dir") or "")
        self.agent_ctx.setText(out.get("agent_context_path") or "")
        self.user_profile.setText(out.get("user_profile_path") or "")
        self.repo_facts.setText(out.get("repo_facts_path") or "")
        self.min_messages.setValue(int(flt.get("min_messages") or 0))
        self.min_tools.setValue(int(flt.get("min_tools") or 0))
        self.proxy.setText(fast.get("proxy") or "")
        self.proxy_bypass.setText(fast.get("proxy_bypass") or "")
        self.verify_ssl.setChecked(bool(fast.get("verify_ssl")))
        if data.get("configured"):
            self.hint.setText(f"配置文件：{data.get('config_path')}（API Key / Base URL 存于 .env）")
        else:
            self.hint.setText("尚未初始化配置，请在下方填写后保存。")

    def _collect(self) -> dict[str, Any]:
        return {
            "api_key": self.api_key.text().strip(),
            "base_url": self.base_url.text().strip(),
            "fast_model": self.fast_model.text().strip(),
            "strong_model": self.strong_model.text().strip(),
            "fast_max_concurrency": self.fast_concurrency.value(),
            "strong_max_concurrency": self.strong_concurrency.value(),
            "source_type": self.source_type.currentText(),
            "source_location": self.source_location.text().strip(),
            "output_skill_output_dir": self.output_dir.text().strip(),
            "output_agent_context_path": self.agent_ctx.text().strip(),
            "output_user_profile_path": self.user_profile.text().strip(),
            "output_repo_facts_path": self.repo_facts.text().strip(),
            "filter_min_messages": self.min_messages.value(),
            "filter_min_tools": self.min_tools.value(),
            "fast_proxy": self.proxy.text().strip(),
            "fast_proxy_bypass": self.proxy_bypass.text().strip(),
            "fast_verify_ssl": self.verify_ssl.isChecked(),
        }

    def save(self) -> None:
        self.status.setText("正在保存…")
        QApplication.processEvents()
        try:
            data = services.save_config(self._collect())
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"保存失败：{exc}")
            return
        self._last_config = data
        self._fill(data)
        self.status.setText("设置已保存。")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
