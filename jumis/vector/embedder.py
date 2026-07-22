#! jumis/vector/embedder.py
import os
import asyncio
import torch
from sentence_transformers import SentenceTransformer
from typing import List, Union

# Путь к папке models в корне проекта Jumis
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models_cache", "embeddings")

# Модель по умолчанию (отличный выбор для русского языка)
DEFAULT_MODEL = "intfloat/multilingual-e5-base"


class TextEmbedder:
    """
    Асинхронный класс для генерации векторов (эмбеддингов).
    Вычисления вынесены в отдельный тред, чтобы не блокировать event loop.
    """
    def __init__(self, model_name: str = DEFAULT_MODEL):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model_name = model_name
        self.cache_dir = MODELS_DIR
        
        # Загружаем модель (если нет локально в models/ — скачает туда один раз)
        print(f"📦 Инициализация эмбеддера на [{self.device.upper()}]...")
        self.model = SentenceTransformer(
            model_name_or_path=self.model_name,
            device=self.device,
            cache_folder=self.cache_dir
        )
        print(f"✅ Модель векторизации готова к работе.")

    def _encode_sync(self, texts: Union[str, List[str]]) -> List[List[float]]:
        """ Внутренний синхронный метод генерации векторов на GPU """
        embeddings = self.model.encode(
            texts, 
            convert_to_numpy=True, 
            normalize_embeddings=True  # Нормализуем для удобного поиска по косинусу
        )
        return embeddings.tolist()

    async def get_embedding(self, text: str, is_query: bool = False) -> List[float]:
            """
            Получить вектор для одного текста (асинхронно).
            is_query=True — если это поисковый запрос пользователя.
            is_query=False — если это сохраняемый факт/сообщение в базу.
            """
            prefix = "query: " if is_query else "passage: "
            formatted_text = f"{prefix}{text}"
            
            # Передаем список [formatted_text], чтобы модель вернула полноценную матрицу
            embeddings = await asyncio.to_thread(self._encode_sync, [formatted_text])
            return embeddings[0]

    async def get_embeddings_batch(self, texts: List[str], is_query: bool = False) -> List[List[float]]:
        """ Пакетированная асинхронная векторизация списка текстов """
        if not texts:
            return []
            
        prefix = "query: " if is_query else "passage: "
        formatted_texts = [f"{prefix}{t}" for t in texts]
        
        return await asyncio.to_thread(self._encode_sync, formatted_texts)




# Использование

# from vector.embedder import TextEmbedder

# embedder = TextEmbedder()

# # 1. Записать факт в базу (создаем вектор)
# vector_to_save = await embedder.get_embedding("У клиента Alex не работает экран HP", is_query=False)

# # 2. Найти факт по вопросу пользователя
# query_vector = await embedder.get_embedding("Почему не горит дисплей?", is_query=True)










# import time
# import torch
# from sentence_transformers import SentenceTransformer

# # 1. Загружаем модель сразу на твоем GPU (cuda)
# device = "cuda" if torch.cuda.is_available() else "cpu"
# print(f"Используем устройство: {device}")

# # Модель весит ~1.1 GB, скачается автоматически при первом запуске
# model = SentenceTransformer("intfloat/multilingual-e5-base", device=device)

# # 2. База сохраненных сообщений/фактов клиентов
# database_chunks = [
#     "passage: Клиент Alex: не работает подсветка экрана на ноутбуке HP",
#     "passage: Клиент Иван: залил клавиатуру кофе на Lenovo ThinkPad",
#     "passage: Клиент Сергей: нужно восстановить данные с флешки 32 ГБ",
# ]

# # Векторизуем базу
# db_vectors = model.encode(database_chunks, convert_to_tensor=True)

# # 3. Новое входящее сообщение от клиента
# user_query = "query: Перестали гореть диоды на дисплее ноутбука ЭйчПи"

# # Замеряем скорость поиска
# start_time = time.time()

# # Превращаем запрос в вектор
# query_vector = model.encode(user_query, convert_to_tensor=True)

# # Считаем смысловое сходство (Косинусное сходство)
# from sentence_transformers import util
# search_results = util.cos_sim(query_vector, db_vectors)

# # Находим самый похожий индекс
# best_match_idx = torch.argmax(search_results).item()

# execution_time = (time.time() - start_time) * 1000

# print(f"\n⏱ Время поиска на GPU: {execution_time:.2f} мс")
# print(f"🎯 Самый релевантный контекст из базы:\n  -> {database_chunks[best_match_idx]}")