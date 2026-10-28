"""Errores propios del servicio."""


class ConfigError(ValueError):
    """Configuración inválida o incompleta."""


class TtsError(RuntimeError):
    """La síntesis de voz falló."""


class AudioError(RuntimeError):
    """ffmpeg falló o produjo audio inválido."""


class TransportError(RuntimeError):
    """No se pudo entregar el audio a AzuraCast.

    ``sent_audio`` indica si ya se había enviado al menos un fragmento antes
    del fallo. En ese caso no se reintenta por otro transporte para no repetir
    la alerta desde el principio.
    """

    def __init__(self, message: str, *, sent_audio: bool = False) -> None:
        super().__init__(message)
        self.sent_audio = sent_audio
