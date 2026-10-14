# Jumis

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/docker-compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/aiogram-3.31-2CA5E0?style=flat-square&logo=telegram&logoColor=white" alt="aiogram">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
  <img src="https://img.shields.io/github/stars/shliamb/jumis?style=flat-square&color=yellow" alt="Stars">
  <img src="https://img.shields.io/github/last-commit/shliamb/jumis?style=flat-square" alt="Last commit">
</p>

> **Персональный ИИ-секретарь, который живёт в твоём Telegram.**
> Не бот. Не автопилот. Продолжение тебя — только быстрее.

---

## Что это такое

Представь, что у тебя есть секретарь.

Не тот, что сидит в приёмной и отвечает «шеф занят». А тот, который знает всех твоих клиентов по именам, помнит, что ты говорил вчера, знает, кому ты забыл ответить, и в курсе, что у твоей младшей дочери день рождения через три дня. Тот, кому ты просто говоришь голосом: *«Глянь, что там этот написал, и ответь как обычно»* — и через секунду ответ уже готов, с твоими смайликами, твоим тоном, твоими словами.

Jumis — это такой секретарь. Только он не человек, он живёт в Telegram, работает круглосуточно, стоит копейки и никогда не устаёт.

---

## Зачем это нужно

Потому что писать — долго. А говорить — быстро.

Потому что держать в голове сорок диалогов одновременно — невозможно. А Jumis держит.

Потому что иногда приходит клиент, пишет целую поэму, а тебе нужно просто понять: что он хочет, и что ему ответить. И ты говоришь: *«Сократи. Что он хочет?»* — и получаешь суть в двух строках.

Это не попытка сделать агента, который отвечает за тебя. Это попытка сделать инструмент, который **помогает тебе оставаться собой**, но в десять раз быстрее.

---

## Как это выглядит в жизни

Ты открываешь Telegram. Бот уже ждёт. Он не тычет в тебя кнопками меню — их просто нет. Он пишет сам:

> **Jumis:** Привет. Я тут. Могу то-то и то-то. Погнали.

Дальше — ты просто говоришь голосом. Или текстом. Он ловит каждое личное сообщение, которое тебе приходит, сохраняет, превращает в смысл, векторизует, кладёт в память. Когда ты отвечаешь — тоже сохраняет. Через месяц у него уже 8000 сообщений, и он помнит их все.

Он присылает тебе короткие красивые оповещения: *«Написали двое. Четыре сообщения. Два от этого, два от этого»*. Если человек написал впервые — звук. Если повторно — тихо, без раздражения. Ты ответил вручную, или просто поставил реакцию — он снимает с напоминания. Забыл ответить — он мягко напомнит: *«Ты вот этому не ответил. Может, ответим?»*

А ещё он умеет:
- помнить факты с категориями («адрес мастерской», «время работы») и подтягивать их в нужный момент
- ставить будильники и напоминалки через крон
- писать «как обычно» — твоим стилем, с твоими смайликами
- переключаться на умную модель, когда задача сложная, и возвращаться назад сам
- показывать процесс в реальном времени — как в GPT или Claude, только в Telegram

Никакой магии. Просто аккуратно собранный, экономный, управляемый секретарь.

---

## Философия проекта

**Jumis не решает за тебя. Она решает с тобой.**

Здесь нет цели сделать бездумного автопилота, который отвечает клиентам без твоего ведома. Цель — снять с тебя рутину: печатание, поиск контекста, запоминание мелочей. Экспертность, решения, голос — остаются твоими.

Все инструменты специально сделаны **узкими**. Чтобы не засорять диалог. Чтобы не жечь токены. Чтобы оставаться быстрым и дешёвым. DeepSeek кэширует большую часть, и месяц активной работы стоит буквально полтора доллара.

Это не «ещё один AI-стартап». Это личный инструмент, который однажды захотелось собрать — и он работает уже больше месяца, стабильно, без вылетов.

---

## Технический стек

Коротко, по делу — для тех, кому интересна начинка.

**Ядро**
- `aiogram 3.31` — Telegram-бот
- `telethon 1.44` — перехват и отправка личных сообщений от твоего имени
- `litellm 1.93` — единый слой над моделями (OpenAI, DeepSeek, Anthropic, Gemini и др.), автоподтягивание новых моделей и цен

**Данные**
- `PostgreSQL` + `pgvector` — хранение сообщений, пользователей, фактов, памяти
- `asyncpg`, `psycopg2` — асинхронная и синхронная работа с БД
- Минимальная векторизация — `sentence-transformers`

**Голос**
- `kairos-asr[gpu]` — быстрая и качественная транскрибация аудио в текст (китайская модель, миллисекунды)
- `soundfile`, `cffi` — работа со звуком
- `ffmpeg`, `libsndfile1` — системные зависимости

**Прочее**
- `python-dotenv` — окружение
- `aiohttp-socks` — SOCKS-прокси
- `lxml`, `numpy`, `markdown` — утилиты
- `croniter` — будильники и напоминалки
- `Docker Compose` — запуск одной командой
- `uv` — управление зависимостями

**Железо:** работает даже на скромной видеокарте (проверено на 3060, ~4 ГБ VRAM). Векторизация и транскрибация — локальные и бесплатные.

---

## Установка

> Проект в стадии активной доработки. Публичная инструкция по установке появится позже.
> Сейчас в репозитории — код, конфиги и заметки. Если хочешь собрать сам — загляни в `requirements.txt`, `docker-compose.yml` и `.env.example`.

---

## Что дальше

- Выход в интернет через **компьютерное зрение** (скриншот → смысл), а не через HTML-мусор
- Парсер РС-рассылок и Telegram-каналов — только важное, без шума
- Многопоточные вызовы инструментов
- Доступ к большему числу функций
- Инструменты очистки истории

Этот репозиторий — **open source**. Пользуйся, форкай, улучшай.

---

<p align="center"><i>Jumis. Секретарь, который не устаёт.</i></p>



# Jumis

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/docker-compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">
  <img src="https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?style=flat-square&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/aiogram-3.31-2CA5E0?style=flat-square&logo=telegram&logoColor=white" alt="aiogram">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="License">
  <img src="https://img.shields.io/github/stars/shliamb/jumis?style=flat-square&color=yellow" alt="Stars">
  <img src="https://img.shields.io/github/last-commit/shliamb/jumis?style=flat-square" alt="Last commit">
</p>

> **A personal AI secretary that lives inside your Telegram.**
> Not a bot. Not an autopilot. An extension of you — just faster.

---

## What it is

Imagine you have a secretary.

Not the kind that sits at the front desk and says "the boss is busy." The kind that knows all your clients by name, remembers what you said yesterday, knows who you forgot to reply to, and is aware that your youngest daughter's birthday is in three days. The kind you just tell out loud: *"Check what that guy wrote and reply like usual"* — and a second later the reply is ready, with your emojis, your tone, your words.

Jumis is that secretary. Only he's not human, he lives in Telegram, works around the clock, costs pennies, and never gets tired.

---

## Why it exists

Because typing is slow. Talking is fast.

Because holding forty conversations in your head at once is impossible. Jumis holds them.

Because sometimes a client sends you a whole poem, and all you need to know is: what does he want, and what should you say back. So you say: *"Summarize it. What does he want?"* — and get the point in two lines.

This isn't an attempt to build an agent that replies for you. It's an attempt to build a tool that **helps you stay yourself**, just ten times faster.

---

## What it looks like in real life

You open Telegram. The bot is already waiting. No menu buttons — there are none by design. It speaks first:

> **Jumis:** Hey. I'm here. I can do this and that. Let's go.

From there — you just talk. Voice or text. It catches every personal message that comes to you, stores it, turns it into meaning, vectorizes it, and puts it into memory. When you reply — it stores that too. After a month, it holds 8,000 messages, and remembers them all.

It sends you short, clean notifications: *"Two people wrote. Four messages. Two from this one, two from that one."* First message from someone new — a sound. Repeat message — silent, no nagging. You replied manually, or just dropped a reaction — it clears the reminder. Forgot to reply — it gently nudges: *"You didn't reply to this one. Want to?"*

It can also:
- remember facts with categories ("workshop address," "working hours") and pull them up at the right moment
- set alarms and reminders via cron
- write "like usual" — your style, your emojis
- switch to a smarter model for hard tasks, and switch back on its own
- show its process in real time — like GPT or Claude, but inside Telegram

No magic. Just a carefully assembled, economical, controllable secretary.

---

## Philosophy

**Jumis doesn't decide for you. It decides with you.**

There's no goal here to build a mindless autopilot that replies to clients behind your back. The goal is to take away the routine: typing, searching context, remembering small things. Expertise, decisions, voice — they stay yours.

Every tool is deliberately **narrow**. To keep the dialogue clean. To not burn tokens. To stay fast and cheap. DeepSeek caches most of it, and a month of active use costs literally a dollar and a half.

This isn't "yet another AI startup." It's a personal tool that one day wanted to be built — and it's been running for over a month, stable, without crashes.

---

## Tech stack

Short and to the point — for those who care about the internals.

**Core**
- `aiogram 3.31` — Telegram bot
- `telethon 1.44` — intercepting and sending personal messages as you
- `litellm 1.93` — unified layer over models (OpenAI, DeepSeek, Anthropic, Gemini, and more), auto-pulls new models and prices

**Data**
- `PostgreSQL` + `pgvector` — messages, users, facts, memory
- `asyncpg`, `psycopg2` — async and sync DB access
- Minimal vectorization — `sentence-transformers`

**Voice**
- `kairos-asr[gpu]` — fast, high-quality audio-to-text transcription (Chinese model, milliseconds)
- `soundfile`, `cffi` — audio handling
- `ffmpeg`, `libsndfile1` — system dependencies

**Other**
- `python-dotenv` — environment
- `aiohttp-socks` — SOCKS proxy
- `lxml`, `numpy`, `markdown` — utilities
- `croniter` — alarms and reminders
- `Docker Compose` — one-command startup
- `uv` — dependency management

**Hardware:** runs on even a modest GPU (tested on a 3060, ~4 GB VRAM). Vectorization and transcription are local and free.

---

## Installation

> The project is under active development. Public install instructions will come later.
> For now, the repo holds the code, configs, and notes. If you want to build it yourself — check `requirements.txt`, `docker-compose.yml`, and `.env.example`.

---

## What's next

- Internet access via **computer vision** (screenshot → meaning), not HTML garbage
- Parser for government broadcasts and Telegram channels — only what matters, no noise
- Multi-threaded tool calls
- Access to more functions
- History cleanup tools

This repo is **open source**. Use it, fork it, improve it.

---

<p align="center"><i>Jumis. The secretary that never gets tired.</i></p>