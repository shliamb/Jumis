import asyncio
import time
from typing import Optional
import tempfile
import os
import numpy as np
import soundfile as sf
from kairos_asr import KairosASR
from logs.set_logger import set_logger

logger = set_logger(name="stt")




class FastTranscriber:

    def __init__(self, device: str = "cuda"):
        print("Loading Kairos ASR model (sync init)...")
        self.asr = KairosASR(device=device)
        self._warmup()
        self._lock: Optional[asyncio.Lock] = None  # Очередь доступа к GPU
        print("Model ready.")

    def _warmup(self):
        """Синхронный прогрев видеокарты при старте."""
        dummy_audio = np.zeros(8000, dtype=np.float32)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            sf.write(tmp.name, dummy_audio, 16000)
            _ = self.asr.transcribe(tmp.name)

    async def _get_lock(self) -> asyncio.Lock:
        """Ленивая инициализация Lock в правильном Event Loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def transcribe(self, audio_bytes: bytes, file_extension: str = ".ogg") -> str:
        """
        Низкоуровневый метод: принимает сырые байты аудио и транскрибирует их.
        Автоматически ставит задачи в очередь, если GPU занят.
        """
        lock = await self._get_lock()

        # Если модель уже транскрибирует другое аудио, новые запросы ждут тут
        async with lock:
            start = time.perf_counter()
            loop = asyncio.get_running_loop()

            result_text = await loop.run_in_executor(
                None, self._transcribe_sync, audio_bytes, file_extension
            )

            logger.info(f"[STT] Транскрибация заняла {time.perf_counter() - start:.2f} сек.")
            return result_text

    def _transcribe_sync(self, audio_bytes: bytes, file_extension: str) -> str:
        """Синхронная вырезка (выполняется в отдельном потоке)."""
        with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            result = self.asr.transcribe(tmp_path)
            return result.full_text if hasattr(result, "full_text") else str(result)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)



    # ------------------------------------------------------------------------
    # Хелперы для работы с Telegram (Aiogram / Bot API)
    # ------------------------------------------------------------------------

    @staticmethod
    async def download_telegram_file(
        bot, file_id: str, retries: int = 3, delay: float = 1.5
    ) -> Optional[bytes]:
        """
        Универсальный метод скачивания файла из Telegram с повторными попытками.
        Возвращает байты файла или None при неудаче.
        """
        file_info = None

        # 1. Получаем путь к файлу
        for attempt in range(1, retries + 1):
            try:
                file_info = await bot.get_file(file_id)
                break
            except Exception as e:
                logger.warning(f"[STT] get_file error (попытка {attempt}/{retries}): {e}")
                if attempt == retries:
                    return None
                await asyncio.sleep(delay)

        # 2. Скачиваем байты
        for attempt in range(1, retries + 1):
            try:
                stream = await bot.download_file(file_info.file_path)
                return stream.read()
            except Exception as e:
                logger.warning(f"[STT] download_file error (попытка {attempt}/{retries}): {e}")
                if attempt == retries:
                    return None
                await asyncio.sleep(delay)

        return None

    async def transcribe_telegram_voice(self, bot, file_id: str) -> Optional[str]:
        """
        Высокоуровневый метод «все-в-одном» для хэндлеров Telegram.
        Скачивает голосовое сообщение и отдаёт готовый текст.
        """
        audio_bytes = await self.download_telegram_file(bot, file_id)
        if not audio_bytes:
            return None

        return await self.transcribe(audio_bytes, file_extension=".ogg")



# stt = FastTranscriber()  # глобально, один раз
# await stt.transcribe(bytes)
#  Если из хэндлера Aiogram await stt.transcribe_telegram_voice(bot, file_id)