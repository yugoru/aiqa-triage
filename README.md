# aiqa-triage — ИИ-триаж падений автотестов

Учебный инструмент к занятию 6 курса «ИИ в тестировании».

Автотесты отвечают на вопрос **«что упало»**. Этот инструмент добавляет ответ
**«почему упало и что с этим делать»**: прогоняет тесты против живого API
[BookShelf](https://qahacking.up.railway.app), берёт отчёт прогона и с помощью
модели ставит каждому падению диагноз, группирует падения по причинам и заводит
черновики багов.

> ⚠️ **Красные тесты здесь — это норма.** Это учебный полигон: часть тестов
> падает намеренно (баги продукта, ошибки в самих тестах, сломанное окружение),
> чтобы было что триажить. Задача инструмента — правильно объяснить каждое падение.

---

## Как устроено

Конвейер из шести стадий. Детерминированный код делает всё, что требует
**гарантий**; модель — только то, что требует **смысла**.

```
report → parse ── enrich ── triage ─────── group ──── export
                  (код+     (модель,        (одна     (verdicts.json,
                  контракт)  structured      причина —  triage.md,
                             output +        один       issues.json)
                             Pydantic)       инцидент)

  ├─ КОД (гарантии): parse, enrich, group, валидация схемы, заведение багов
  └─ МОДЕЛЬ (смысл): категория, первопричина, улики, рекомендация
```

- **parse** — читает падения из junit.xml **или** из allure-results.
- **enrich** — добавляет то, чего в отчёте нет: код упавшего теста, код фикстуры
  и **контракт** задетого эндпоинта из снапшота OpenAPI. Это решает качество вердикта.
- **triage** — модель ставит диагноз строго по схеме (`analyzer/schema.py`);
  формат гарантирует наша Pydantic-валидация, а не API.
- **group** — детерминированный фингерпринт: одинаковые падения (например, 4 теста
  из-за одной сломанной фикстуры) слипаются в один инцидент.
- **export** — `verdicts.json` (для машин), `triage.md` (сводка к стендапу),
  `issues.json` (черновики багов).

---

## Быстрый старт (≈5 минут)

```powershell
git clone https://github.com/yugoru/aiqa-triage
cd aiqa-triage

python -m venv .venv
.venv\Scripts\activate            # Windows (PowerShell)
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt

copy .env.example .env            # Windows;  на macOS/Linux: cp .env.example .env
# затем выберите модель в .env и впишите ключ
```

**Модель — любая OpenAI-совместимая.** По умолчанию DeepSeek, но в `.env.example`
готовы пресеты: **OpenAI, OpenRouter, Ollama, LM Studio** — раскомментируйте нужный.
Ключ у каждого **свой**, `.env` в репозиторий не коммитится (для локальных моделей
ключ — заглушка). Подробнее про локальный запуск — в разделе ниже.

---

## Как запускать

Основные команды напрямую через `python` (работают везде, `make` не нужен):

### 1. Прогнать тесты
```powershell
python -m pytest -v
```
Покажет каждый тест построчно. Красные — норма (учебный полигон).

### 2. JUnit-отчёт (вход для триажа)
```powershell
python -m pytest -q --junitxml=reports/report.xml
```

### 3. Allure-отчёт (визуальный, для людей)
```powershell
python -m pytest -q --alluredir=reports/allure-results
allure serve reports/allure-results
```
Нужен **Allure CLI** (`scoop install allure` / `npm i -g allure-commandline`, требует Java).
Каждый тест раскрывается в шаги с реальными запросом и ответом.

### 4. Триаж
```powershell
python analyzer/analyzer.py reports/report.xml        # из JUnit
python analyzer/analyzer.py reports/allure-results    # из Allure (жирный вход)
python analyzer/analyzer.py                            # дефолт: reports/report.xml
```
Результат — `output/triage.md` (markdown) и `output/triage.html` (красивый отчёт).

### 5. Открыть отчёт триажа
```powershell
start output/triage.html      # цветной HTML в браузере
notepad output/triage.md      # или markdown
```

### Флаги триажа
```
--dump-context   показать, что именно уходит в модель (пакет контекста)
--no-enrich      без обогащения кодом/контрактом (для сравнения качества)
--escalate       спорные переспросить умной моделью (LLM_MODEL_HARD)
--file-issues    завести черновики багов через POST /issues
--from-allure    принудительно взять reports/allure-results
```

> Есть и `Makefile` (Linux/macOS или Windows с установленным make):
> `make report | triage | issues | escalate | dump-context | report-allure | triage-allure`.

---

## Два входа: JUnit vs Allure

Анализатор принимает оба формата, тип определяется автоматически (`.xml` → JUnit,
каталог → Allure). В логе видно, какой вход выбран.

| | **JUnit** | **Allure** |
|---|---|---|
| Формат | один файл `report.xml` | каталог `allure-results/` |
| Как получить | `pytest --junitxml=…` | `pytest --alluredir=…` |
| Что видит модель | message, трейсбек, stdout | **+ шаги + реальные запрос/ответ на каждом шаге** |
| Тела запросов/ответов | нет | **да (из вложений)** |
| failure vs error | чётко (`<error>` = setup) | размыто (allure сам решает failed/broken) |
| Стоимость (токены) | дешевле | дороже (жирнее вход) |
| Когда брать | CI, переносимость, скорость | глубокий разбор, когда важны тела ответов |

Коротко: **junit — для скорости и CI, allure — для качества разбора.**

---

## Артефакты (`output/`)

| Файл | Что это |
|------|---------|
| `triage.html` | Красивый отчёт для браузера: цветные категории, улики, ⚠ у спорных. |
| `triage.md` | Та же сводка в markdown (для чата/стендапа). |
| `verdicts.json` | Массив вердиктов для машинной обработки. |
| `issues.json` | Черновики багов (только уверенные, ≥0.6, не спорные). |

Категории вердикта: **продукт**, **код_автотеста**, **инфраструктура**,
**инструментарий**, **flaky**.

Готовый пример реального прогона (можно открыть без ключа):
[`examples/triage.html`](examples/triage.html) / [`examples/triage.md`](examples/triage.md).

---

## Allure: предел эвристик репортера

Помимо привычного визуального отчёта, Allure показывает **свой предел**. В
`reports/categories.json` заданы стандартные правила (Product defects = `failed`,
Test defects = `broken`). На нашем прогоне они **ошибаются**: падение с
`AssertionError` Allure относит к «дефектам продукта», хотя это баг теста; падение
с `ConnectionError` (`broken`) — к «дефектам теста», хотя это инфраструктура.
ИИ-триаж на тех же данных разбирается верно — сравните с `output/triage.md`.

Чтобы увидеть категории, подложите файл перед просмотром:
```powershell
copy reports/categories.json reports/allure-results/
allure serve reports/allure-results
```

---

## Заведение багов (human-in-the-loop)

По умолчанию баги **не заводятся** — сначала человек смотрит `triage.md`.

```powershell
python analyzer/analyzer.py reports/report.xml --file-issues
start https://qahacking.up.railway.app/issues     # открыть трекер
```

Черновики уходят со статусом `draft` и меткой `ai-triage`. В реальном проекте
`POST /issues` заменяется на Jira/YouTrack — код тот же, меняется endpoint.

---

## Нет ключа DeepSeek? Уровень 0

Триаж можно проделать вручную, без кода и ключа:

1. Возьмите эталонный отчёт `reports/sample_report.xml` и промпт `analyzer/prompt.md`.
2. Скопируйте промпт и одно падение из отчёта в любой чат с LLM.
3. Получите структурированный вердикт и сравните с логикой инструмента.

---

## Локальная LLM (без облака, для тех, кто хочет)

Клиент провайдер-агностичен — работает с любым **OpenAI-совместимым** endpoint,
поэтому DeepSeek можно заменить на локальную модель. Управляют этим три
переменные в `.env`: `LLM_BASE_URL`, `LLM_API_KEY` (для локали — любая заглушка,
локальному серверу ключ не нужен) и `LLM_MODEL`.

### Вариант 1 — Ollama (проще всего)

1. Установить [Ollama](https://ollama.com) (Windows / macOS / Linux).
2. Скачать инструктивную модель:
   ```powershell
   ollama pull qwen2.5:7b
   ```
3. Ollama сам поднимает OpenAI-совместимый сервер на `http://localhost:11434/v1`.
4. Прописать в `.env`:
   ```
   LLM_BASE_URL=http://localhost:11434/v1
   LLM_API_KEY=ollama          # заглушка, не проверяется
   LLM_MODEL=qwen2.5:7b        # типовые падения
   LLM_MODEL_HARD=qwen2.5:14b  # спорные (--escalate)
   ```
5. Запускать как обычно: `python analyzer/analyzer.py reports/report.xml`.

### Вариант 2 — LM Studio

Загрузить модель, включить сервер (вкладка **Developer → Start Server**), затем:
```
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=lmstudio
LLM_MODEL=<имя загруженной модели>
```

### Вариант 3 — vLLM (сервер помощнее, для GPU)

```
LLM_BASE_URL=http://localhost:8000/v1
LLM_API_KEY=vllm
LLM_MODEL=<путь или имя модели>
```

### Важно про локальные модели

- Клиент всегда просит **JSON-режим** (`response_format={"type":"json_object"}`).
  Свежие Ollama и LM Studio его поддерживают; если endpoint не принимает — вызов
  вернёт 400. Проверьте версию сервера.
- Берите **инструктивную** модель (`…-instruct`), не базовую — она лучше держит схему.
- Мелкие модели (7B) чаще ошибаются в категории и обрезают/портят JSON. Наша
  Pydantic-валидация это ловит и просит модель переотправить ответ, но для качества
  лучше 14B+ или `--escalate` на модель побольше.
- Скорость зависит от железа; GPU сильно ускоряет.
- Бонус: локальная модель не отправляет трейсбеки и тела ответов в облако — удобно,
  если в логах бывают токены и персональные данные (см. слайд «Безопасность»).

---

## Где код триажа и что покрутить для экспериментов

Весь «мозг» инструмента — в папке [`analyzer/`](analyzer/). Самое интересное для
опытов:

| Файл | За что отвечает | Что попробовать поменять |
|------|-----------------|--------------------------|
| [`analyzer/prompt.md`](analyzer/prompt.md) | промпт триажа: роль, таксономия, правила, схема | убрать/добавить правило (например, про таймаут) и посмотреть, как поедет вердикт; переписать признаки категории |
| [`analyzer/schema.py`](analyzer/schema.py) | схема вердикта и список категорий | добавить поле (например, `severity`) или свою категорию в `Category` |
| [`analyzer/analyzer.py`](analyzer/analyzer.py) | конвейер | `build_context` — что кладём модели; `find_contracts`/`find_test_source`/`find_fixture_source` — обогащение; `fingerprint` — как группируем; `build_issues` — порог `confidence` для багов (сейчас 0.6) |
| [`analyzer/deepseek.py`](analyzer/deepseek.py) | клиент модели | `temperature`, `max_tokens`, логика ретраев/эскалации |
| [`.env`](.env.example) | модель и endpoint | сменить `LLM_MODEL`, подключить локальную LLM (см. выше) |
| [`tests/`](tests/), [`conftest.py`](conftest.py) | сами тесты | добавить свой тест-кейс с новой «закладкой» и посмотреть вердикт |

Быстрые эксперименты без правки кода — флагами: `--no-enrich` (убрать код/контракт
из контекста и увидеть, как просядет улика), `--dump-context` (посмотреть, что
именно уходит в модель), `--from-allure` (жирный вход), `--escalate` (спорные —
умной моделью).

Хороший первый опыт: открыть `analyzer/prompt.md`, ослабить правило про место
ошибки, прогнать `--dump-context` и триаж, сравнить, как изменилась категория и
склейка инцидентов.

---

## Заметки

- **Тесты ходят в общий инстанс.** Не ломайте чужой прогон: создаваемые сущности
  уникальны (uuid) и удаляются за собой, ассертов на общие счётчики каталога нет.
- **Модели.** Клиент провайдер-агностичен: работает с любым OpenAI-совместимым
  endpoint. По умолчанию DeepSeek (`deepseek-v4-flash` / `deepseek-v4-pro` для
  `--escalate`); пресеты OpenAI / OpenRouter / Ollama / LM Studio — в `.env.example`.
- **Ключ.** `.env` в `.gitignore`; ключ DeepSeek в репозиторий не попадает.
