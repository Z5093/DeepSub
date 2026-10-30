from PyQt5.QtCore import QObject, pyqtSignal


class SignalBus(QObject):
    pipeline_step_changed = pyqtSignal(str)
    pipeline_step_completed = pyqtSignal(str)
    pipeline_step_error = pyqtSignal(str, str)
    pipeline_started = pyqtSignal()
    pipeline_paused = pyqtSignal()
    pipeline_resumed = pyqtSignal()
    pipeline_stopped = pyqtSignal()
    pipeline_completed = pyqtSignal()

    download_started = pyqtSignal(str)
    download_progress = pyqtSignal(str, int)
    download_completed = pyqtSignal(str)
    download_failed = pyqtSignal(str, str)

    asr_started = pyqtSignal()
    asr_progress = pyqtSignal(int)
    asr_completed = pyqtSignal()
    asr_failed = pyqtSignal(str)

    translate_started = pyqtSignal()
    translate_progress = pyqtSignal(int, int)
    translate_completed = pyqtSignal()
    translate_failed = pyqtSignal(str)

    model_download_started = pyqtSignal(str)
    model_download_progress = pyqtSignal(str, int)
    model_download_completed = pyqtSignal(str)
    model_download_failed = pyqtSignal(str, str)

    config_changed = pyqtSignal(str)
    config_applied = pyqtSignal()

    log_message = pyqtSignal(str, str)

    nav_page_changed = pyqtSignal(str)

    audio_extract_started = pyqtSignal(str)
    audio_extract_completed = pyqtSignal(str)
    audio_extract_failed = pyqtSignal(str, str)

    subtitle_export_started = pyqtSignal(str)
    subtitle_export_completed = pyqtSignal(str)
    subtitle_export_failed = pyqtSignal(str, str)

    wizard_completed = pyqtSignal()

    split_started = pyqtSignal(str)
    split_completed = pyqtSignal(str)
    split_failed = pyqtSignal(str, str)

    align_started = pyqtSignal(str)
    align_completed = pyqtSignal(str)
    align_failed = pyqtSignal(str, str)

    gpu_detected = pyqtSignal(bool, dict)

    llamacpp_server_started = pyqtSignal()
    llamacpp_server_stopped = pyqtSignal()
    llamacpp_server_error = pyqtSignal(str)

    model_installed = pyqtSignal(str)
    model_removed = pyqtSignal(str)


signal_bus = SignalBus()
