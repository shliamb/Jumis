import asyncio
import time
import tempfile
import os
import numpy as np
import soundfile as sf
from kairos_asr import KairosASR

class FastTranscriber:
    def __init__(self, device="cuda"):
        print("Loading Kairos ASR model (sync init)...")
        self.asr = KairosASR(device=device)
        self._warmup()
        print("Model ready.")

    def _warmup(self):
        # синхронный прогрев (блокирует только инициализацию, один раз)
        dummy_audio = np.zeros(8000, dtype=np.float32)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            sf.write(tmp.name, dummy_audio, 16000)
            _ = self.asr.transcribe(tmp.name)

    async def transcribe(self, audio_bytes: bytes, file_extension: str = ".ogg") -> str:
        start = time.perf_counter()
        loop = asyncio.get_running_loop()
        # запускаем синхронную часть в потоке
        result_text = await loop.run_in_executor(
            None,  # используем стандартный ThreadPoolExecutor
            self._transcribe_sync,
            audio_bytes,
            file_extension
        )
        print(f"\nTranscription took {time.perf_counter() - start:.2f}s")
        return result_text

    def _transcribe_sync(self, audio_bytes: bytes, file_extension: str) -> str:
        with tempfile.NamedTemporaryFile(suffix=file_extension, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            result = self.asr.transcribe(tmp_path)
            return result.full_text if hasattr(result, 'full_text') else str(result)
        finally:
            os.unlink(tmp_path)



# stt = FastTranscriber()  # глобально, один раз

# @dp.message(lambda m: m.voice)
# async def handle_voice(message: types.Message):
#     file = await message.voice.get_file()
#     audio_bytes = await bot.download_file(file.file_path)
#     text = await stt.transcribe(audio_bytes.read(), ".ogg")
#     await message.reply(f"Распознано: {text}")