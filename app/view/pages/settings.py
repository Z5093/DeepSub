from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget,
)

from app.common.constants import DEFAULT_API_TIMEOUT_MS, SUPPORTED_BROWSERS
from app.common.signal_bus import signal_bus
from app.common.theme import UITheme as T
from app.components.buttons import Button
from app.components.panels import Card
from app.components.widgets import (
    CheckBox, Field, Form, Label, NumberField, Select, TextField,
)


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageRoot")
        self._setup_ui()
        self._init_config()

    # ------------------------------------------------------------------ UI
    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 12)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        h1 = QLabel("设置")
        h1.setObjectName("H2")
        sub = QLabel("修改后点击「保存设置」生效，正在运行的任务不受影响")
        sub.setStyleSheet(
            f"color: {T.TEXT_SECONDARY}; font-size: {T.SIZE_SUB}px; background: transparent;"
        )
        titles.addWidget(h1)
        titles.addWidget(sub)
        header.addLayout(titles)
        header.addStretch(1)

        guide_btn = Button("新手引导", variant="ghost")
        guide_btn.setToolTip("重新检查运行环境、补下模型、确认文件位置")
        guide_btn.clicked.connect(self._open_guide)
        header.addWidget(guide_btn, 0, Qt.AlignVCenter)

        reset_btn = Button("恢复默认")
        reset_btn.clicked.connect(self._on_reset)
        header.addWidget(reset_btn, 0, Qt.AlignVCenter)

        apply_btn = Button("保存设置", variant="primary")
        apply_btn.clicked.connect(self._on_apply)
        header.addWidget(apply_btn, 0, Qt.AlignVCenter)
        root.addLayout(header)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._make_download_tab(), "下载")
        self._tabs.addTab(self._make_translate_tab(), "翻译")
        self._tabs.addTab(self._make_output_tab(), "输出")
        self._tabs.addTab(self._make_interface_tab(), "界面")
        self._tabs.addTab(self._make_performance_tab(), "性能")
        root.addWidget(self._tabs, 1)

    def _wrap(self, form: Form) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(form)
        return widget

    def _make_download_tab(self) -> QWidget:
        form = Form()

        path_card = Card("下载路径", "留空则使用程序目录下的 downloads/")
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self._download_path_input = TextField("默认：项目根目录 / downloads")
        path_row.addWidget(self._download_path_input, 1)
        self._browse_download_btn = Button("浏览…")
        self._browse_download_btn.clicked.connect(self._on_browse_download_path)
        path_row.addWidget(self._browse_download_btn)
        path_card.add_layout(path_row)
        form.add_widget(path_card)

        browser_card = Card("浏览器 Cookie", "读取本机浏览器登录态，用于下载会员或年龄限制内容")
        self._browser_combo = Select()
        for browser_id, browser_label in SUPPORTED_BROWSERS:
            self._browser_combo.addItem(browser_label, browser_id)
        browser_card.add_widget(Field("默认浏览器", self._browser_combo))
        form.add_widget(browser_card)

        video_card = Card("视频处理")
        self._merge_check = CheckBox("自动合并音视频流（需要 ffmpeg）")
        self._merge_check.setChecked(True)
        video_card.add_widget(self._merge_check)
        form.add_widget(video_card)

        return self._wrap(form)

    def _make_translate_tab(self) -> QWidget:
        form = Form()

        engine_card = Card("翻译引擎")
        self._enable_translation_check = CheckBox("启用翻译")
        self._enable_translation_check.setChecked(True)
        self._enable_translation_check.stateChanged.connect(self._on_enable_translation_changed)
        engine_card.add_widget(self._enable_translation_check)

        self._service_combo = Select()
        self._service_combo.addItems(["远程 API（OpenAI 兼容）", "本地 llama.cpp"])
        self._service_combo.currentIndexChanged.connect(self._on_service_changed)
        engine_card.add_widget(Field("引擎", self._service_combo))
        form.add_widget(engine_card)

        self._api_section = Card("远程 API")
        self._api_url_input = TextField("https://api.openai.com/v1", mono=True)
        self._api_section.add_widget(Field("接口地址", self._api_url_input))
        self._api_key_input = TextField("sk-…", mono=True)
        self._api_key_input.setEchoMode(TextField.Password)
        self._api_section.add_widget(Field("密钥", self._api_key_input))
        self._translate_model_input = TextField("gpt-4o-mini", mono=True)
        self._api_section.add_widget(Field("模型", self._translate_model_input))
        self._api_timeout_spin = NumberField()
        self._api_timeout_spin.setRange(1, 180)
        self._api_timeout_spin.setValue(DEFAULT_API_TIMEOUT_MS // 60000)
        self._api_timeout_spin.setSingleStep(1)
        self._api_timeout_spin.setSuffix(" 分钟")
        self._api_section.add_widget(Field("请求超时", self._api_timeout_spin))
        form.add_widget(self._api_section)

        self._llamacpp_hint_section = Card("本地推理")
        self._llamacpp_hint_label = Label(
            "服务器地址、模型与超时在左侧「翻译引擎」页面配置。", "muted")
        self._llamacpp_hint_label.setWordWrap(True)
        self._llamacpp_hint_section.add_widget(self._llamacpp_hint_label)
        form.add_widget(self._llamacpp_hint_section)

        ctx_card = Card("上下文", "滑动窗口让模型看到前后文，串行翻译时才生效")
        self._concurrency_spin = NumberField()
        self._concurrency_spin.setRange(1, 10)
        self._concurrency_spin.setValue(1)
        ctx_card.add_widget(Field("并发度", self._concurrency_spin, hint="大于 1 时关闭上下文窗口"))

        self._context_window_spin = NumberField()
        self._context_window_spin.setRange(1, 10)
        self._context_window_spin.setValue(3)
        ctx_card.add_widget(Field("窗口条数", self._context_window_spin))

        self._context_size_spin = NumberField()
        self._context_size_spin.setRange(0, 10)
        self._context_size_spin.setValue(1)
        ctx_card.add_widget(Field("上下文条数", self._context_size_spin))
        form.add_widget(ctx_card)

        style_card = Card("风格与模板")
        self._video_style_combo = Select()
        self._video_style_combo.addItems([
            "自动检测", "电影", "纪录片", "动漫", "综艺", "讲座", "电视剧",
            "ASMR 助眠", "视频博客", "游戏解说", "新闻播报", "知识科普", "不区分风格",
        ])
        style_card.add_widget(Field("视频风格", self._video_style_combo))

        self._prompt_combo = Select()
        self._prompt_combo.addItems(["自动（跟随视频风格）", "默认模板", "自定义"])
        style_card.add_widget(Field("提示词", self._prompt_combo))

        self._glossary_combo = Select()
        self._glossary_combo.addItems(["无", "通用术语表", "自定义"])
        style_card.add_widget(Field("术语表", self._glossary_combo))
        form.add_widget(style_card)

        return self._wrap(form)

    def _make_output_tab(self) -> QWidget:
        form = Form()

        format_card = Card("输出格式", "可多选；三种格式会同时生成原文字幕、译文字幕与双语字幕")
        format_row = QHBoxLayout()
        format_row.setSpacing(20)
        self._srt_check = CheckBox("SRT")
        self._srt_check.setChecked(True)
        self._ass_check = CheckBox("ASS")
        self._vtt_check = CheckBox("VTT")
        for cb in (self._srt_check, self._ass_check, self._vtt_check):
            format_row.addWidget(cb)
        format_row.addStretch(1)
        format_card.add_layout(format_row)
        form.add_widget(format_card)

        style_card = Card("字幕样式")
        self._style_combo = Select()
        self._style_combo.addItems(["默认样式", "电影风格", "动画风格", "自定义"])
        style_card.add_widget(Field("样式模板", self._style_combo))
        form.add_widget(style_card)

        bilingual_card = Card("双语字幕")
        self._bilingual_layout_combo = Select()
        self._bilingual_layout_combo.addItems(["上下堆叠", "左右并排"])
        bilingual_card.add_widget(Field("布局", self._bilingual_layout_combo))
        form.add_widget(bilingual_card)

        naming_card = Card("文件命名")
        self._output_subdir_input = TextField("DeepSub")
        naming_card.add_widget(Field("输出子目录", self._output_subdir_input))
        form.add_widget(naming_card)

        return self._wrap(form)

    def _make_interface_tab(self) -> QWidget:
        form = Form()

        lang_card = Card("语言")
        self._lang_combo = Select()
        self._lang_combo.addItems(["中文 (zh-CN)", "English (en-US)"])
        lang_card.add_widget(Field("界面语言", self._lang_combo))
        form.add_widget(lang_card)

        font_card = Card("字体")
        self._font_size_spin = NumberField()
        self._font_size_spin.setRange(9, 24)
        self._font_size_spin.setValue(13)
        font_card.add_widget(Field("字号", self._font_size_spin, hint="重启后生效"))
        form.add_widget(font_card)

        return self._wrap(form)

    def _make_performance_tab(self) -> QWidget:
        form = Form()

        perf_card = Card("资源")
        self._thread_count_spin = NumberField()
        self._thread_count_spin.setRange(1, 32)
        self._thread_count_spin.setValue(4)
        perf_card.add_widget(Field("线程数", self._thread_count_spin))

        self._cache_size_spin = NumberField()
        self._cache_size_spin.setRange(100, 5000)
        self._cache_size_spin.setValue(500)
        perf_card.add_widget(Field("缓存上限", self._cache_size_spin))
        form.add_widget(perf_card)

        debug_card = Card("诊断")
        self._debug_check = CheckBox("输出调试日志（记录每个步骤的耗时与中间状态）")
        debug_card.add_widget(self._debug_check)
        form.add_widget(debug_card)

        proxy_card = Card("代理")
        self._proxy_combo = Select()
        self._proxy_combo.addItems(["系统代理", "HTTP 代理", "SOCKS5 代理", "不使用代理"])
        proxy_card.add_widget(Field("类型", self._proxy_combo))
        self._proxy_addr_input = TextField("127.0.0.1:7890", mono=True)
        proxy_card.add_widget(Field("地址", self._proxy_addr_input))
        form.add_widget(proxy_card)

        return self._wrap(form)

    # -------------------------------------------------------------- 行为
    def _on_enable_translation_changed(self, state):
        enabled = state in (Qt.Checked, True, 2)
        self._service_combo.setEnabled(enabled)
        self._api_section.setEnabled(enabled)
        self._llamacpp_hint_section.setEnabled(enabled)
        self._concurrency_spin.setEnabled(enabled)
        self._context_window_spin.setEnabled(enabled)
        self._context_size_spin.setEnabled(enabled)
        self._video_style_combo.setEnabled(enabled)
        self._prompt_combo.setEnabled(enabled)
        self._glossary_combo.setEnabled(enabled)

    def _on_service_changed(self, index: int):
        is_llamacpp = index == 1
        self._api_section.setVisible(not is_llamacpp)
        self._llamacpp_hint_section.setVisible(is_llamacpp)

    @staticmethod
    def _ms_to_minutes(value, default_ms: int) -> int:
        """配置里超时以毫秒存储，界面按分钟显示。"""
        try:
            ms = float(value)
        except (TypeError, ValueError):
            ms = float(default_ms)
        if ms <= 0:
            ms = float(default_ms)
        return max(1, int(round(ms / 60000)))

    def _on_browse_download_path(self):
        from app.core.utils.file_utils import get_downloads_root

        current = get_downloads_root()
        new_path = QFileDialog.getExistingDirectory(self, "选择下载根目录", current)
        if new_path:
            self._download_path_input.setText(new_path)

    def _on_apply(self):
        from app.common.config import config_manager

        config_manager.set("general", "download_path", self._download_path_input.text().strip())
        browser_data = self._browser_combo.currentData() or "auto"
        config_manager.set("general", "browser_cookie", browser_data)
        config_manager.set("general", "merge_video_audio", self._merge_check.isChecked())

        service_map = {0: "openai_compat", 1: "llamacpp"}
        config_manager.set("translate", "enable_translation", self._enable_translation_check.isChecked())
        config_manager.set("translate", "service",
                           service_map.get(self._service_combo.currentIndex(), "openai_compat"))

        config_manager.set("translate", "api_base_url", self._api_url_input.text())
        config_manager.set("translate", "api_key", self._api_key_input.text())
        config_manager.set("translate", "model", self._translate_model_input.text())
        config_manager.set("translate", "timeout", self._api_timeout_spin.value() * 60000)
        config_manager.set("translate", "concurrency", self._concurrency_spin.value())
        config_manager.set("translate", "context_window_size", self._context_window_spin.value())
        config_manager.set("translate", "context_size", self._context_size_spin.value())

        style_map = {0: "", 1: "movie", 2: "documentary", 3: "anime", 4: "variety", 5: "lecture",
                     6: "drama", 7: "asmr", 8: "vlog", 9: "gaming", 10: "news", 11: "education",
                     12: "default"}
        config_manager.set("translate", "video_style",
                           style_map.get(self._video_style_combo.currentIndex(), ""))
        config_manager.set("translate", "auto_detect_style", self._video_style_combo.currentIndex() == 0)

        prompt_map = {0: "auto", 1: "default", 2: "custom"}
        config_manager.set("translate", "prompt_template",
                           prompt_map.get(self._prompt_combo.currentIndex(), "auto"))

        glossary_map = {0: "", 1: "general", 2: "custom"}
        config_manager.set("translate", "glossary",
                           glossary_map.get(self._glossary_combo.currentIndex(), ""))

        formats = []
        if self._srt_check.isChecked():
            formats.append("srt")
        if self._ass_check.isChecked():
            formats.append("ass")
        if self._vtt_check.isChecked():
            formats.append("vtt")
        config_manager.set("general", "output_formats", formats)

        out_style_map = {0: "default", 1: "movie", 2: "anime", 3: "custom"}
        config_manager.set("output", "subtitle_style",
                           out_style_map.get(self._style_combo.currentIndex(), "default"))

        layout_map = {0: "stacked", 1: "side_by_side"}
        config_manager.set("output", "bilingual_layout",
                           layout_map.get(self._bilingual_layout_combo.currentIndex(), "stacked"))

        config_manager.set("general", "output_subdir", self._output_subdir_input.text())

        lang_map = {0: "zh-CN", 1: "en-US"}
        config_manager.set("interface", "language",
                           lang_map.get(self._lang_combo.currentIndex(), "zh-CN"))
        config_manager.set("interface", "font_size", self._font_size_spin.value())

        config_manager.set("performance", "thread_count", self._thread_count_spin.value())
        config_manager.set("performance", "cache_size_mb", self._cache_size_spin.value())
        config_manager.set("performance", "debug_mode", self._debug_check.isChecked())

        proxy_map = {0: "system", 1: "http", 2: "socks5", 3: "none"}
        config_manager.set("general", "proxy_type",
                           proxy_map.get(self._proxy_combo.currentIndex(), "system"))

        addr = self._proxy_addr_input.text().strip()
        if ":" in addr:
            host, port_str = addr.rsplit(":", 1)
            config_manager.set("general", "proxy_host", host)
            try:
                config_manager.set("general", "proxy_port", int(port_str))
            except ValueError:
                config_manager.set("general", "proxy_port", 0)
        else:
            config_manager.set("general", "proxy_host", addr)
            config_manager.set("general", "proxy_port", 0)

        config_manager.apply()
        signal_bus.config_applied.emit()

    def _init_config(self):
        from app.common.config import config_manager

        download_path = config_manager.get("general", "download_path", "")
        self._download_path_input.setText(download_path)

        browser_cookie = config_manager.get("general", "browser_cookie", "auto")
        idx = self._browser_combo.findData(browser_cookie)
        if idx >= 0:
            self._browser_combo.setCurrentIndex(idx)

        self._merge_check.setChecked(config_manager.get("general", "merge_video_audio", True))

        enable_translation = config_manager.get("translate", "enable_translation", True)
        self._enable_translation_check.setChecked(enable_translation)
        self._on_enable_translation_changed(enable_translation)

        service = config_manager.get("translate", "service", "openai_compat")
        service_idx = 1 if service == "llamacpp" else 0
        self._service_combo.setCurrentIndex(service_idx)
        self._on_service_changed(service_idx)

        self._api_url_input.setText(config_manager.get("translate", "api_base_url", ""))
        self._api_key_input.setText(config_manager.get("translate", "api_key", ""))
        self._translate_model_input.setText(config_manager.get("translate", "model", ""))
        self._api_timeout_spin.setValue(self._ms_to_minutes(
            config_manager.get("translate", "timeout", DEFAULT_API_TIMEOUT_MS),
            DEFAULT_API_TIMEOUT_MS,
        ))
        self._concurrency_spin.setValue(config_manager.get("translate", "concurrency", 1))
        self._context_window_spin.setValue(config_manager.get("translate", "context_window_size", 3))
        self._context_size_spin.setValue(config_manager.get("translate", "context_size", 1))

        video_style = config_manager.get("translate", "video_style", "")
        auto_detect = config_manager.get("translate", "auto_detect_style", True)
        if auto_detect:
            self._video_style_combo.setCurrentIndex(0)
        else:
            style_map = {"": 0, "movie": 1, "documentary": 2, "anime": 3, "variety": 4, "lecture": 5,
                         "drama": 6, "asmr": 7, "vlog": 8, "gaming": 9, "news": 10,
                         "education": 11, "default": 12}
            self._video_style_combo.setCurrentIndex(style_map.get(video_style, 0))

        prompt = config_manager.get("translate", "prompt_template", "auto")
        prompt_map = {"auto": 0, "default": 1, "custom": 2}
        self._prompt_combo.setCurrentIndex(prompt_map.get(prompt, 0))

        glossary = config_manager.get("translate", "glossary", "")
        glossary_map = {"": 0, "general": 1, "custom": 2}
        self._glossary_combo.setCurrentIndex(glossary_map.get(glossary, 0))

        formats = config_manager.get("general", "output_formats", ["srt"])
        self._srt_check.setChecked("srt" in formats)
        self._ass_check.setChecked("ass" in formats)
        self._vtt_check.setChecked("vtt" in formats)

        style = config_manager.get("output", "subtitle_style", "default")
        style_map = {"default": 0, "movie": 1, "anime": 2, "custom": 3}
        self._style_combo.setCurrentIndex(style_map.get(style, 0))

        layout = config_manager.get("output", "bilingual_layout", "stacked")
        layout_map = {"stacked": 0, "side_by_side": 1}
        self._bilingual_layout_combo.setCurrentIndex(layout_map.get(layout, 0))

        self._output_subdir_input.setText(config_manager.get("general", "output_subdir", "DeepSub"))

        lang = config_manager.get("interface", "language", "zh-CN")
        lang_map = {"zh-CN": 0, "en-US": 1}
        self._lang_combo.setCurrentIndex(lang_map.get(lang, 0))

        self._font_size_spin.setValue(config_manager.get("interface", "font_size", 13))

        self._thread_count_spin.setValue(config_manager.get("performance", "thread_count", 4))
        self._cache_size_spin.setValue(config_manager.get("performance", "cache_size_mb", 500))
        self._debug_check.setChecked(config_manager.get("performance", "debug_mode", False))

        proxy_type = config_manager.get("general", "proxy_type", "system")
        proxy_map = {"system": 0, "http": 1, "socks5": 2, "none": 3}
        self._proxy_combo.setCurrentIndex(proxy_map.get(proxy_type, 0))

        proxy_host = config_manager.get("general", "proxy_host", "")
        proxy_port = config_manager.get("general", "proxy_port", 0)
        if proxy_host and proxy_port:
            self._proxy_addr_input.setText(f"{proxy_host}:{proxy_port}")
        else:
            self._proxy_addr_input.setText("127.0.0.1:7890")

    def _open_guide(self):
        from app.view.pages.wizard import GuideDialog

        dialog = GuideDialog(self.window())
        dialog.exec_()
        self._init_config()  # 引导里可能改过配置，回来后重新读一遍

    def _on_reset(self):
        from app.common.config import DEFAULT_CONFIG
        d = DEFAULT_CONFIG

        self._download_path_input.setText(d["general"]["download_path"])
        browser_idx = self._browser_combo.findData(d["general"].get("browser_cookie", "auto"))
        if browser_idx >= 0:
            self._browser_combo.setCurrentIndex(browser_idx)
        self._merge_check.setChecked(d["general"].get("merge_video_audio", True))

        self._service_combo.setCurrentIndex(0)
        self._enable_translation_check.setChecked(d["translate"].get("enable_translation", True))
        self._on_enable_translation_changed(d["translate"].get("enable_translation", True))
        self._on_service_changed(0)

        self._api_url_input.setText(d["translate"]["api_base_url"])
        self._api_key_input.setText(d["translate"]["api_key"])
        self._translate_model_input.setText(d["translate"]["model"])
        self._api_timeout_spin.setValue(
            max(1, d["translate"].get("timeout", DEFAULT_API_TIMEOUT_MS) // 60000))
        self._concurrency_spin.setValue(d["translate"]["concurrency"])
        self._context_window_spin.setValue(d["translate"]["context_window_size"])
        self._context_size_spin.setValue(d["translate"].get("context_size", 1))
        self._video_style_combo.setCurrentIndex(0)
        self._prompt_combo.setCurrentIndex(0)
        self._glossary_combo.setCurrentIndex(0)

        formats = d["general"]["output_formats"]
        self._srt_check.setChecked("srt" in formats)
        self._ass_check.setChecked("ass" in formats)
        self._vtt_check.setChecked("vtt" in formats)
        self._style_combo.setCurrentIndex(0)
        self._bilingual_layout_combo.setCurrentIndex(0)
        self._output_subdir_input.setText(d["general"]["output_subdir"])

        self._lang_combo.setCurrentIndex(0)
        self._font_size_spin.setValue(d["interface"]["font_size"])

        self._thread_count_spin.setValue(d["performance"]["thread_count"])
        self._cache_size_spin.setValue(d["performance"]["cache_size_mb"])
        self._debug_check.setChecked(d["performance"]["debug_mode"])
        self._proxy_combo.setCurrentIndex(0)
        self._proxy_addr_input.setText("127.0.0.1:7890")
