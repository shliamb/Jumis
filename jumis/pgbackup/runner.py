# jumis/pgbackup/runner.py
"""
Логический бэкап базы в файл: pg_dump + ротация копий.

Почему через `docker exec`, а не локальный pg_dump:
  база живёт в контейнере pgvector/pg17, а на хосте pg_dump версии 14 — он
  отказывается дампить сервер новее себя. Внутри контейнера лежит ровно та
  версия, что и сервер, поэтому `docker exec jumis_postgres pg_dump ...`
  всегда совпадает по версии и не требует пароля (unix-сокет, trust).

Формат `-Fc` (custom): сжатый, цельный файл, восстанавливается `pg_restore`.
Проверка результата не на веру: файл пишется во временное имя, проверяется
размер и сигнатура "PGDMP", и только потом переименовывается в боевое.
Старые копии удаляются ТОЛЬКО после успешного нового дампа.
"""
import asyncio
import time
from datetime import datetime
from pathlib import Path

from config import DB_NAME, PATH_BACKUPS, USER_DB
from logs.set_logger import logger
from utils.common import get_now_datetime


CONTAINER = "jumis_postgres"
KEEP_COPIES = 3
DUMP_TIMEOUT = 30 * 60          # 30 минут: с запасом, дамп занимает секунды
MIN_VALID_SIZE = 1024           # меньше килобайта — точно не живой дамп
MAGIC = b"PGDMP"                # сигнатура custom-формата pg_dump
PREFIX = "jumis_"
SUFFIX = ".dump"


class BackupError(RuntimeError):
    """Дамп не удался: причина в тексте исключения."""


def human_size(size: int) -> str:
    """Размер файла по-человечески: 34.7 MB."""
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


class DBRunner:
    """Создаёт дампы и следит, чтобы копий было не больше `keep`."""

    def __init__(
            self,
            directory: str | Path = PATH_BACKUPS,
            keep: int = KEEP_COPIES,
            container: str = CONTAINER,
            db_name: str | None = DB_NAME,
            db_user: str | None = USER_DB,
            timeout: int = DUMP_TIMEOUT,
    ):
        self.directory = Path(directory)
        self.keep = max(1, int(keep))
        self.container = container
        self.db_name = db_name
        self.db_user = db_user
        self.timeout = timeout
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ файлы
    def ensure_dir(self) -> Path:
        """Папка бэкапов создаётся по требованию (её нет в git)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory

    def list_files(self, limit: int | None = None) -> list[dict]:
        """Копии от новой к старой. Имена — ISO-время, поэтому сортировка строк = хронология."""
        if not self.directory.exists():
            return []
        items = []
        for path in sorted(self.directory.glob(f"{PREFIX}*{SUFFIX}"), reverse=True):
            stat = path.stat()
            items.append({
                "name": path.name,
                "path": str(path),
                "size": stat.st_size,
                "size_human": human_size(stat.st_size),
                "created_at": datetime.fromtimestamp(stat.st_mtime),
            })
        return items[:limit] if limit else items

    def _target_name(self) -> str:
        return f"{PREFIX}{get_now_datetime():%Y-%m-%d_%H%M%S}{SUFFIX}"

    # ------------------------------------------------------------------- дамп
    def _command(self) -> list[str]:
        """pg_dump внутри контейнера: та же версия, что у сервера, без пароля."""
        return [
            "docker", "exec", self.container,
            "pg_dump",
            "-U", str(self.db_user),
            "-d", str(self.db_name),
            "-Fc",
        ]

    def _verify(self, path: Path) -> None:
        """Дамп — это файл правильного формата и непустой."""
        size = path.stat().st_size if path.exists() else 0
        if size < MIN_VALID_SIZE:
            raise BackupError(f"файл слишком мал: {size} байт (pg_dump ничего не отдал)")
        with open(path, "rb") as fh:
            head = fh.read(len(MAGIC))
        if head != MAGIC:
            raise BackupError(f"это не custom-формат pg_dump (сигнатура {head!r})")

    async def _dump(self, path: Path) -> None:
        """Запускает pg_dump, потоково пишет stdout в файл, ждёт окончания."""
        cmd = self._command()
        logger.info("[Backup] Запуск: %s -> %s", " ".join(cmd), path.name)

        try:
            with open(path, "wb") as out:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=out,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    # stderr забирает communicate(): из wait() его не достать
                    _, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    raise BackupError(f"pg_dump не ответил за {self.timeout} с")
        except FileNotFoundError:
            raise BackupError("не найден docker — бэкап невозможен (см. SPEC.md)")
        except BackupError:
            raise
        except Exception as e:
            raise BackupError(f"не удалось запустить pg_dump: {e}")

        if proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()[:500]
            raise BackupError(f"pg_dump завершился с кодом {proc.returncode}: {detail or 'без вывода'}")

        self._verify(path)

    async def create(self) -> dict:
        """
        Один дамп. Возвращает отчёт-словарь:
        {ok, name, path, size, size_human, duration, deleted, keep, error}
        Никогда не бросает исключение наружу — крон не должен умирать.
        """
        if self._lock.locked():
            logger.warning("[Backup] Дамп уже идёт — второй запуск пропущен.")
            return {"ok": False, "error": "already_running"}

        async with self._lock:
            started = time.monotonic()
            try:
                directory = self.ensure_dir()
            except Exception as e:
                logger.error("[Backup] Не удалось создать папку %s: %s", self.directory, e, exc_info=True)
                return {"ok": False, "error": f"папка недоступна: {e}"}

            final = directory / self._target_name()
            part = final.with_name(final.name + ".part")

            try:
                await self._dump(part)
                # Атомарная подмена: в папке не бывает полуфайлов с боевым именем.
                part.replace(final)
            except Exception as e:
                part.unlink(missing_ok=True)
                logger.error("[Backup] Дамп не удался: %s", e)
                return {"ok": False, "error": str(e)}

            deleted: list[str] = []
            try:
                deleted = self._rotate()
            except Exception as e:
                logger.error("[Backup] Ошибка ротации копий: %s", e, exc_info=True)

            size = final.stat().st_size
            duration = round(time.monotonic() - started, 1)
            logger.info(
                "[Backup] Готово: %s (%s) за %s с, удалено старых: %d",
                final.name, human_size(size), duration, len(deleted),
            )
            return {
                "ok": True,
                "name": final.name,
                "path": str(final),
                "size": size,
                "size_human": human_size(size),
                "duration": duration,
                "deleted": deleted,
                "keep": self.keep,
            }

    def _rotate(self) -> list[str]:
        """Оставляет `keep` последних копий, остальные удаляет. Частичные файлы — тоже."""
        deleted: list[str] = []

        for part in self.directory.glob(f"{PREFIX}*{SUFFIX}.part"):
            part.unlink(missing_ok=True)
            deleted.append(part.name)

        for old in self.list_files()[self.keep:]:
            try:
                Path(old["path"]).unlink()
                deleted.append(old["name"])
                logger.info("[Backup] Удалена старая копия: %s (%s)", old["name"], old["size_human"])
            except FileNotFoundError:
                continue
            except Exception as e:
                logger.error("[Backup] Не удалось удалить %s: %s", old["name"], e)

        return deleted

    def total_size(self) -> int:
        return sum(item["size"] for item in self.list_files())
