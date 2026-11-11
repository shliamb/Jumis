#!/usr/bin/env bash
# Jumis installer.
#
# Запуск: ./install.sh
#
# Скрипт идемпотентен: каждый шаг проверяет, не сделан ли он уже, и только
# добирает недостающее. Повторный запуск безопасен — он не перезапишет .env,
# не перенесёт сессию дважды и не пересоберёт образ без изменений.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ENV_FILE=".env"
LLM_FILE=".env.llm"
STATE_DIR="state"
SESSION_FILE="$STATE_DIR/session_jumis.session"
IMAGE="jumis-bot:local"
PG_CONTAINER="jumis_postgres"
BOT_CONTAINER="jumis_bot"
COMPOSE=(docker compose)

step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '    ok: %s\n' "$*"; }
warn() { printf '    !!: %s\n' "$*"; }
die()  { printf '\nОшибка: %s\n' "$*" >&2; exit 1; }

have() { command -v "$1" >/dev/null 2>&1; }

env_get() { # файл ключ
    [ -f "$1" ] || return 0
    sed -n "s/^$2=//p" "$1" | tail -n 1
}

env_set() { # файл ключ значение
    local file="$1" key="$2" value="$3" tmp
    touch "$file"
    tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" '
        BEGIN { done = 0 }
        $0 ~ "^" k "=" { print k "=" v; done = 1; next }
        { print }
        END { if (!done) print k "=" v }
    ' "$file" > "$tmp"
    mv "$tmp" "$file"
    chmod 600 "$file" 2>/dev/null || true
}

ask_env() { # файл ключ подпись [secret]
    local file="$1" key="$2" label="$3" secret="${4:-no}"
    local current value=""
    [ "$INTERACTIVE" -eq 1 ] || return 0
    current="$(env_get "$file" "$key")"

    if [ -n "$current" ]; then
        if [ "$secret" = "yes" ]; then
            printf '    %s (уже задан, Enter — оставить): ' "$label"
        else
            printf '    %s [%s] (Enter — оставить): ' "$label" "$current"
        fi
    else
        printf '    %s (Enter — пропустить): ' "$label"
    fi

    if [ "$secret" = "yes" ]; then
        read -rs value || true
        printf '\n'
    else
        read -r value || true
    fi

    if [ -n "$value" ]; then
        env_set "$file" "$key" "$value"
        ok "$key обновлён"
    fi
    return 0
}

# ---------------------------------------------------------------- 1. окружение
step "Проверка окружения"

have docker || die "не найден docker. Установи Docker Engine: https://docs.docker.com/engine/install/"
docker info >/dev/null 2>&1 || die "Docker не запущен или нет доступа к сокету (проверь: systemctl start docker, группа docker)"
ok "docker: $(docker --version | cut -d, -f1)"

"${COMPOSE[@]}" version >/dev/null 2>&1 || die "не найден docker compose (плагин v2)"
ok "compose: $("${COMPOSE[@]}" version --short 2>/dev/null || echo ok)"

if have nvidia-smi; then
    gpu="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1)"
    ok "GPU: ${gpu:-NVIDIA}"
else
    warn "nvidia-smi не найден: kairos-asr на старте инициализируется на cuda, без NVIDIA GPU бот не поднимется"
    warn "текстовые функции сами по себе GPU не требуют, но образ стартует STT сразу"
fi

# ------------------------------------------------------------------- 2. .env
step "Настройки (.env)"

# Вопросы задаём только в живом терминале: при запуске из скрипта/крона
# ввода нет, и скрипт просто идемпотентно доходит до запуска.
INTERACTIVE=1
[ -t 0 ] || INTERACTIVE=0
if [ "$INTERACTIVE" -eq 0 ]; then
    warn "ввод не интерактивный: вопросы пропущены, .env не меняю"
    warn "запусти ./install.sh в терминале, чтобы заполнить значения"
fi

if [ ! -f "$ENV_FILE" ]; then
    cp .env.example "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    ok ".env создан из .env.example"
else
    ok ".env уже есть — заполняю только пустые значения"
fi

# Служебные значения БД: генерируются один раз и дальше не меняются.
if [ -z "$(env_get "$ENV_FILE" USER_DB)" ]; then
    env_set "$ENV_FILE" USER_DB "jumis"
    ok "USER_DB=jumis"
fi
if [ -z "$(env_get "$ENV_FILE" POSTGRES_DB)" ]; then
    env_set "$ENV_FILE" POSTGRES_DB "jumis"
    ok "POSTGRES_DB=jumis"
fi
if [ -z "$(env_get "$ENV_FILE" PASSWORD_DB)" ]; then
    generated="$(openssl rand -hex 16 2>/dev/null \
        || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    env_set "$ENV_FILE" PASSWORD_DB "$generated"
    ok "PASSWORD_DB сгенерирован (лежит в .env, менять не нужно)"
fi

ask_env "$ENV_FILE" telegram_bot_token "Токен бота от @BotFather" yes
ask_env "$ENV_FILE" admin_id "Твой Telegram id (admin_id)"
ask_env "$ENV_FILE" api_id "api_id с my.telegram.org"
ask_env "$ENV_FILE" api_hash "api_hash с my.telegram.org" yes

missing=""
for key in telegram_bot_token admin_id api_id api_hash; do
    [ -n "$(env_get "$ENV_FILE" "$key")" ] || missing="$missing $key"
done
if [ -n "$missing" ]; then
    die "не заполнено:$missing. Впиши значения и запусти ./install.sh снова — он продолжит с этого места"
fi

# --------------------------------------------------------------- 3. .env.llm
step "Ключи LLM (.env.llm)"

if [ ! -f "$LLM_FILE" ]; then
    cp .env.llm.example "$LLM_FILE"
    chmod 600 "$LLM_FILE"
    ok ".env.llm создан из .env.llm.example"
else
    ok ".env.llm уже есть — заполняю только пустые значения"
fi

ask_env "$LLM_FILE" DEEPSEEK_API_KEY  "DEEPSEEK_API_KEY"  yes
ask_env "$LLM_FILE" OPENAI_API_KEY    "OPENAI_API_KEY"    yes
ask_env "$LLM_FILE" GEMINI_API_KEY    "GEMINI_API_KEY"    yes
ask_env "$LLM_FILE" ANTHROPIC_API_KEY "ANTHROPIC_API_KEY" yes
ask_env "$LLM_FILE" XAI_API_KEY       "XAI_API_KEY"       yes

llm_keys=0
for key in DEEPSEEK_API_KEY OPENAI_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY XAI_API_KEY; do
    [ -n "$(env_get "$LLM_FILE" "$key")" ] && llm_keys=$((llm_keys + 1))
done
if [ "$llm_keys" -eq 0 ]; then
    warn "ни одного ключа LLM нет: бот запустится, но отвечать не сможет."
    warn "впиши хотя бы один ключ в .env.llm и перезапусти: docker compose restart bot"
else
    ok "ключей LLM: $llm_keys"
fi

# ------------------------------------------------------------ 4. файл сессии
step "Файл сессии Telethon"

mkdir -p "$STATE_DIR"

if [ ! -f "$SESSION_FILE" ]; then
    for legacy in "session_jumis.session" "jumis/session_jumis.session"; do
        if [ -f "$legacy" ]; then
            mv "$legacy" "$SESSION_FILE"
            [ -f "${legacy}-journal" ] && mv "${legacy}-journal" "${SESSION_FILE}-journal"
            ok "сессия перенесена: $legacy -> $SESSION_FILE"
            break
        fi
    done
fi

if [ -f "$SESSION_FILE" ]; then
    chmod 600 "$SESSION_FILE"
    ok "файл сессии на месте ($SESSION_FILE) — вход сохранится"
else
    ok "файла сессии пока нет: вход выполняется командой /login в боте"
fi

# ------------------------------------------------------------------ 5. образ
step "Образ бота"

need_build=1
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    created="$(docker image inspect -f '{{.Created}}' "$IMAGE")"
    created_epoch="$(date -d "$created" +%s)"
    # Данные времени выполнения (логи, бэкапы, json, downloads) меняются каждую
    # секунду — их в проверку актуальности не берём, только код и файлы сборки.
    newer="$(
        {
            find jumis \
                -path jumis/logs -prune -o \
                -path jumis/backups -prune -o \
                -path jumis/json -prune -o \
                -path jumis/downloads -prune -o \
                -path '*/__pycache__' -prune -o \
                -name 'SPEC.md' -prune -o \
                -type f -newermt "@$created_epoch" -print -quit 2>/dev/null || true
            find requirements.lock Dockerfile.jumis docker-compose.yml .dockerignore \
                -newermt "@$created_epoch" -print -quit 2>/dev/null || true
        } | head -n 1
    )"
    if [ -z "$newer" ]; then
        need_build=0
        ok "образ $IMAGE актуален"
    else
        ok "изменились файлы проекта ($newer) — пересобираю образ"
    fi
fi

if [ "$need_build" -eq 1 ]; then
    "${COMPOSE[@]}" build bot
fi

# ---------------------------------------------------------------- 6. запуск
step "Запуск контейнеров"

"${COMPOSE[@]}" up -d
ok "docker compose up -d выполнен"

# -------------------------------------------------------------- 7. ожидание
step "Ожидание готовности"

pg_health=""
for _ in $(seq 1 60); do
    pg_health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$PG_CONTAINER" 2>/dev/null || echo missing)"
    [ "$pg_health" = "healthy" ] && break
    sleep 1
done
if [ "$pg_health" = "healthy" ]; then
    ok "PostgreSQL готов"
else
    warn "PostgreSQL не перешёл в healthy за 60 с (текущее состояние: ${pg_health:-нет контейнера})"
fi

started=0
for _ in $(seq 1 60); do
    if "${COMPOSE[@]}" logs --tail=120 bot 2>/dev/null | grep -q "Jumis starting"; then
        started=1
        break
    fi
    sleep 1
done
if [ "$started" -eq 1 ]; then
    ok "бот стартовал"
else
    warn "в логах бота пока нет 'Jumis starting'"
fi

telethon_ok=0
for _ in $(seq 1 30); do
    if "${COMPOSE[@]}" logs --tail=200 bot 2>/dev/null | grep -q "Telethon connected"; then
        telethon_ok=1
        break
    fi
    sleep 1
done
if [ "$telethon_ok" -eq 1 ]; then
    ok "userbot подключён (сессия рабочая)"
else
    ok "userbot ещё не подключён: открой бота и отправь /login"
fi

# --------------------------------------------------------------- 8. итог
step "Готово"

"${COMPOSE[@]}" ps

cat <<'EOF'

Что дальше:
  1. Открой своего бота в Telegram и отправь /start — регистрация владельца.
  2. Отправь /login и подтверди вход (QR-ссылка или код) — это userbot.
  3. Таблицы создаются автоматически при старте.

Логи:      docker compose logs -f bot
Остановка: docker compose down        (не добавляй -v: удалит базу)
Обновление: ./install.sh              (пересоберёт только при изменениях)
EOF

if [ "$llm_keys" -eq 0 ]; then
    printf '\n    !! без ключей LLM бот не сможет отвечать — см. .env.llm\n'
fi
