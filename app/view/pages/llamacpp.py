import json
import os

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from app.common.config import config_manager
from app.common.constants import DEFAULT_LLAMACPP_TIMEOUT_MS
from app.common.signal_bus import signal_bus
from app.common.theme import UITheme as T
from app.components.buttons import Button
from app.components.panels import Card, StatTile
from app.components.widgets import DecimalField, Field, Form, Label, NumberField, TextField
from app.core.llamacpp.server import LlamaCppServer


class _ServerStartThread(QThread):
    started = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self._config = config

    def run(self):
        try:
            server = LlamaCppServer(self._config)
            if server.start():
                self.started.emit()
            else:
                self.failed.emit(server.last_error or "服务器启动失败，请检查配置")
        except Exception as e:
            self.failed.emit(str(e))


class LlamaCppPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self._server = None
        self._start_thread = None
        self._loading = False  # 初始化期间回填控件值时不回写配置文件
        self._setup_ui()
        self._connect_signals()
        self._init_config()
        self._update_status()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 12)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        h1 = QLabel("翻译引擎")
        h1.setObjectName("H2")
        sub = QLabel("用本地 llama.cpp 服务器离线翻译；只影响「引擎」为 llama.cpp 的任务")
        sub.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        titles.addWidget(h1)
        titles.addWidget(sub)
        header.addLayout(titles)
        header.addStretch(1)

        self._start_btn = Button("启动服务", variant="primary")
        header.addWidget(self._start_btn, 0, Qt.AlignVCenter)
        self._stop_btn = Button("停止", variant="danger")
        self._stop_btn.setEnabled(False)
        header.addWidget(self._stop_btn, 0, Qt.AlignVCenter)
        root.addLayout(header)

        stats = QHBoxLayout()
        stats.setSpacing(12)
        self._status_card = StatTile("服务状态", "已停止")
        self._model_card = StatTile("模型", "—")
        self._address_card = StatTile("监听地址", "—")
        for tile in (self._status_card, self._model_card, self._address_card):
            stats.addWidget(tile)
        root.addLayout(stats)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._make_program_tab(), "程序与模型")
        self._tabs.addTab(self._make_launch_tab(), "启动参数")
        self._tabs.addTab(self._make_request_tab(), "请求参数")
        root.addWidget(self._tabs, 1)

    def _make_program_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(0)
        form = Form()

        paths = Card("文件位置")
        bin_row = QHBoxLayout()
        bin_row.setSpacing(8)
        self._bin_dir_input = TextField("llama.cpp 所在目录")
        bin_row.addWidget(self._bin_dir_input, 1)
        browse_bin = Button("浏览")
        browse_bin.clicked.connect(self._browse_bin_dir)
        bin_row.addWidget(browse_bin)
        paths.add_widget(Label("程序目录", "body"))
        paths.add_layout(bin_row)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        self._model_path_input = TextField("模型文件路径 (.gguf)", mono=True)
        model_row.addWidget(self._model_path_input, 1)
        browse_model = Button("浏览")
        browse_model.clicked.connect(self._browse_model_path)
        model_row.addWidget(browse_model)
        paths.add_widget(Label("模型文件", "body"))
        paths.add_layout(model_row)
        form.add_widget(paths)

        network = Card("网络")
        self._host_input = TextField("127.0.0.1", mono=True)
        network.add_widget(Field("主机地址", self._host_input))
        self._port_spin = NumberField()
        self._port_spin.setRange(1, 65535)
        self._port_spin.setValue(8080)
        network.add_widget(Field("端口", self._port_spin))
        form.add_widget(network)

        extra = Card("额外启动参数", "原样透传给 llama-server，多个参数用空格分隔")
        self._extra_args_input = TextField("例如 --threads 8 --no-mmap", mono=True)
        extra.add_widget(self._extra_args_input)
        form.add_widget(extra)

        layout.addWidget(form)
        return widget

    def _make_launch_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(0)
        form = Form()

        ctx_card = Card("上下文与显存")
        self._ctx_size_spin = NumberField()
        self._ctx_size_spin.setRange(256, 1048576)
        self._ctx_size_spin.setValue(32768)
        self._ctx_size_spin.setSingleStep(1024)
        ctx_card.add_widget(Field("上下文大小", self._ctx_size_spin,
                                  hint="ctx_size，越大越能容纳长字幕批量翻译"))

        self._n_gpu_layers_spin = NumberField()
        self._n_gpu_layers_spin.setRange(-1, 999)
        self._n_gpu_layers_spin.setValue(99)
        ctx_card.add_widget(Field("GPU 层数", self._n_gpu_layers_spin,
                                  hint="n_gpu_layers，纯 CPU 环境填 0"))
        form.add_widget(ctx_card)

        timeout_card = Card("超时")
        self._timeout_spin = NumberField()
        self._timeout_spin.setRange(1, 180)
        self._timeout_spin.setValue(DEFAULT_LLAMACPP_TIMEOUT_MS // 60000)
        self._timeout_spin.setSingleStep(1)
        self._timeout_spin.setSuffix(" 分钟")
        timeout_card.add_widget(Field("单次请求超时", self._timeout_spin,
                                      hint="纯 CPU 推理较慢，建议保留较长时间"))
        form.add_widget(timeout_card)

        layout.addWidget(form)
        return widget

    def _make_request_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(0)
        form = Form()

        sampling = Card("采样")
        self._temperature_spin = DecimalField()
        self._temperature_spin.setRange(0.0, 2.0)
        self._temperature_spin.setValue(0.8)
        self._temperature_spin.setSingleStep(0.05)
        self._temperature_spin.setDecimals(2)
        sampling.add_widget(Field("Temperature", self._temperature_spin))

        self._top_k_spin = NumberField()
        self._top_k_spin.setRange(1, 1000)
        self._top_k_spin.setValue(20)
        sampling.add_widget(Field("Top-K", self._top_k_spin))

        self._top_p_spin = DecimalField()
        self._top_p_spin.setRange(0.0, 1.0)
        self._top_p_spin.setValue(0.95)
        self._top_p_spin.setSingleStep(0.05)
        self._top_p_spin.setDecimals(2)
        sampling.add_widget(Field("Top-P", self._top_p_spin))

        self._min_p_spin = DecimalField()
        self._min_p_spin.setRange(0.0, 1.0)
        self._min_p_spin.setValue(0.0)
        self._min_p_spin.setSingleStep(0.05)
        self._min_p_spin.setDecimals(2)
        sampling.add_widget(Field("Min-P", self._min_p_spin))

        self._n_predict_spin = NumberField()
        self._n_predict_spin.setRange(-1, 1048576)
        self._n_predict_spin.setValue(-1)
        sampling.add_widget(Field("最大输出", self._n_predict_spin, hint="-1 表示不限制"))
        form.add_widget(sampling)

        penalty = Card("惩罚")
        self._presence_penalty_spin = DecimalField()
        self._presence_penalty_spin.setRange(-2.0, 2.0)
        self._presence_penalty_spin.setValue(1.5)
        self._presence_penalty_spin.setSingleStep(0.1)
        self._presence_penalty_spin.setDecimals(2)
        self._presence_penalty_spin.setToolTip("0 表示不发送该参数，避免逼模型换词影响拟声词与重复台词")
        penalty.add_widget(Field("Presence Penalty", self._presence_penalty_spin,
                                 hint="0 · 不发送该参数（推荐）"))

        self._repeat_penalty_spin = DecimalField()
        self._repeat_penalty_spin.setRange(0.0, 2.0)
        self._repeat_penalty_spin.setValue(1.0)
        self._repeat_penalty_spin.setSingleStep(0.05)
        self._repeat_penalty_spin.setDecimals(2)
        self._repeat_penalty_spin.setToolTip("1.00 为中性值，不发送该参数；大于 1.00 才启用重复惩罚")
        penalty.add_widget(Field("Repeat Penalty", self._repeat_penalty_spin,
                                 hint="1.00 · 不发送该参数"))
        form.add_widget(penalty)

        template = Card("Chat Template")
        self._chat_template_kwargs_input = TextField('{"enable_thinking":false}', mono=True)
        template.add_widget(Field("Kwargs", self._chat_template_kwargs_input,
                                  hint="JSON，传给 chat_template_kwargs"))
        form.add_widget(template)

        layout.addWidget(form)
        return widget

    # -------------------------------------------------------------- 行为
    def _connect_signals(self):
        self._start_btn.clicked.connect(self._on_start)
        self._stop_btn.clicked.connect(self._on_stop)

        self._bin_dir_input.textChanged.connect(lambda v: self._persist("bin_dir", v))
        self._model_path_input.textChanged.connect(lambda v: self._persist("model_path", v))
        self._host_input.textChanged.connect(lambda v: self._persist("host", v))
        self._port_spin.valueChanged.connect(lambda v: self._persist("port", v))
        self._extra_args_input.textChanged.connect(lambda v: self._persist("extra_launch_args", v))

        self._ctx_size_spin.valueChanged.connect(lambda v: self._persist("ctx_size", v))
        self._n_gpu_layers_spin.valueChanged.connect(lambda v: self._persist("n_gpu_layers", v))
        self._timeout_spin.valueChanged.connect(lambda v: self._persist("timeout", v * 60000))

        self._temperature_spin.valueChanged.connect(lambda v: self._persist("temperature", v))
        self._top_k_spin.valueChanged.connect(lambda v: self._persist("top_k", v))
        self._top_p_spin.valueChanged.connect(lambda v: self._persist("top_p", v))
        self._min_p_spin.valueChanged.connect(lambda v: self._persist("min_p", v))
        self._n_predict_spin.valueChanged.connect(lambda v: self._persist("n_predict", v))
        self._presence_penalty_spin.valueChanged.connect(
            lambda v: self._persist("presence_penalty", v if v != 0.0 else None))
        self._repeat_penalty_spin.valueChanged.connect(
            lambda v: self._persist("repeat_penalty", v if v > 0 and abs(v - 1.0) > 1e-9 else None))
        self._chat_template_kwargs_input.textChanged.connect(self._on_chat_template_kwargs_changed)

        signal_bus.llamacpp_server_started.connect(self._on_server_started)
        signal_bus.llamacpp_server_stopped.connect(self._on_server_stopped)
        signal_bus.llamacpp_server_error.connect(self._on_server_error)

    def _persist(self, key: str, value):
        if self._loading:
            return
        config_manager.set("llamacpp", key, value)
        config_manager.save()

    @staticmethod
    def _num(cfg: dict, key: str, default):
        """读取数值型配置。

        配置里的 None 表示"该项不传/关闭"（例如 presence_penalty=null），
        不能直接 float(None)，否则界面构造时抛 TypeError。
        """
        value = cfg.get(key)
        if value is None or isinstance(value, bool):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _on_chat_template_kwargs_changed(self, text: str):
        if self._loading:
            return
        try:
            parsed = json.loads(text)
            config_manager.set("llamacpp", "chat_template_kwargs", parsed)
            config_manager.save()
        except (json.JSONDecodeError, TypeError):
            pass

    def _init_config(self):
        cfg = config_manager.get_section("llamacpp")
        self._loading = True
        try:
            self._bin_dir_input.setText(str(cfg.get("bin_dir") or ""))
            self._model_path_input.setText(str(cfg.get("model_path") or ""))
            self._host_input.setText(str(cfg.get("host") or "127.0.0.1"))
            self._port_spin.setValue(int(self._num(cfg, "port", 8080)))
            self._extra_args_input.setText(str(cfg.get("extra_launch_args") or ""))

            self._ctx_size_spin.setValue(int(self._num(cfg, "ctx_size", 32768)))
            self._n_gpu_layers_spin.setValue(int(self._num(cfg, "n_gpu_layers", 99)))

            timeout_ms = self._num(cfg, "timeout", DEFAULT_LLAMACPP_TIMEOUT_MS)
            if timeout_ms <= 0:
                timeout_ms = float(DEFAULT_LLAMACPP_TIMEOUT_MS)
            self._timeout_spin.setValue(max(1, int(round(timeout_ms / 60000))))

            self._temperature_spin.setValue(self._num(cfg, "temperature", 0.8))
            self._top_k_spin.setValue(int(self._num(cfg, "top_k", 20)))
            self._top_p_spin.setValue(self._num(cfg, "top_p", 0.95))
            self._min_p_spin.setValue(self._num(cfg, "min_p", 0.0))
            self._n_predict_spin.setValue(int(self._num(cfg, "n_predict", -1)))

            # presence_penalty 为 None 表示不发送该参数；界面用 0 表达"关闭"
            self._presence_penalty_spin.setValue(self._num(cfg, "presence_penalty", 0.0))
            # repeat_penalty 为 None 表示不发送该参数；界面用 1.0（中性值）表达"关闭"
            self._repeat_penalty_spin.setValue(self._num(cfg, "repeat_penalty", 1.0))

            chat_kwargs = cfg.get("chat_template_kwargs", {"enable_thinking": False})
            if isinstance(chat_kwargs, dict):
                self._chat_template_kwargs_input.setText(json.dumps(chat_kwargs, ensure_ascii=False))
            elif isinstance(chat_kwargs, str):
                self._chat_template_kwargs_input.setText(chat_kwargs)
        finally:
            self._loading = False

    def _get_current_config(self) -> dict:
        return config_manager.get_section("llamacpp")

    def _update_status(self):
        cfg = self._get_current_config()
        server = LlamaCppServer(cfg)
        running = server.is_running

        if running:
            self._status_card.set_value("运行中")
            self._status_card.set_status("success")
            self._start_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
            self._address_card.set_value(f"{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 8080)}")
        else:
            self._status_card.set_value("已停止")
            self._status_card.set_status("dim")
            self._start_btn.setEnabled(True)
            self._stop_btn.setEnabled(False)
            self._address_card.set_value("—")

        model_path = cfg.get("model_path", "")
        self._model_card.set_value(os.path.basename(model_path) if model_path else "—")

    def _browse_bin_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择 llama.cpp 目录")
        if path:
            self._bin_dir_input.setText(path)

    def _browse_model_path(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型文件", "", "模型文件 (*.gguf);;所有文件 (*)")
        if path:
            self._model_path_input.setText(path)

    def _on_start(self):
        cfg = self._get_current_config()
        if not cfg.get("bin_dir"):
            signal_bus.llamacpp_server_error.emit("请先配置 llama.cpp 目录")
            return
        if not cfg.get("model_path"):
            signal_bus.llamacpp_server_error.emit("请先配置模型文件路径")
            return

        self._start_btn.setEnabled(False)
        self._status_card.set_value("启动中…")
        self._status_card.set_status("warning")

        self._start_thread = _ServerStartThread(cfg)
        self._start_thread.started.connect(self._on_server_started)
        self._start_thread.failed.connect(self._on_server_error)
        self._start_thread.start()

    def _on_stop(self):
        if self._server is not None:
            self._server.stop()
            self._server = None
        else:
            server = LlamaCppServer(self._get_current_config())
            server.stop()
        signal_bus.llamacpp_server_stopped.emit()

    def _on_server_started(self):
        cfg = self._get_current_config()
        self._server = LlamaCppServer(cfg)
        self._status_card.set_value("运行中")
        self._status_card.set_status("success")
        self._start_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._address_card.set_value(f"{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 8080)}")

    def _on_server_stopped(self):
        self._server = None
        self._status_card.set_value("已停止")
        self._status_card.set_status("dim")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._address_card.set_value("—")

    def _on_server_error(self, msg: str):
        self._status_card.set_value("错误")
        self._status_card.set_status("error")
        self._start_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)
        for line in str(msg).splitlines():
            if line.strip():
                signal_bus.log_message.emit(line.strip(), "error")
