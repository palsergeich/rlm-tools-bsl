# Установка и настройка rlm-tools-bsl

## 0. Установить Python и uv

rlm-tools-bsl требует **Python 3.10+** и менеджер пакетов **uv**.

**Python:**

Скачайте и установите с [python.org](https://www.python.org/downloads/). При установке на Windows обязательно отметьте галочку **«Add Python to PATH»**.

Проверьте:
```bash
python --version
# Python 3.12.x (или 3.10+)
```

**uv** (быстрый менеджер пакетов Python):

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Linux/macOS
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Проверьте:
```bash
uv --version
```

> **Альтернатива:** если предпочитаете pip, установите его вместе с Python (он идёт в комплекте). Далее в инструкции используется uv, но вместо `uv tool install .` можно использовать `pip install .`

## 1. Клонировать репозиторий

```bash
git clone https://github.com/<your-repo>/rlm-tools-bsl.git
cd rlm-tools-bsl
```

## 2. Установить глобально

```bash
uv tool install . --force
```

Команда `rlm-tools-bsl` станет доступна глобально. `uv tool install` создаёт изолированное окружение и ставит пакет из текущего каталога — версия подхватывается из `pyproject.toml` автоматически.

<details>
<summary>Вариант через pip</summary>

```bash
pip install .
```

</details>

## 3. (Опционально) Настройка llm_query

В песочнице есть хелпер `llm_query(prompt, context)` — он вызывает «маленькую» LLM прямо из `rlm_execute`, не возвращаясь в основной контекст. Это полезно, когда агент нашёл много данных и хочет классифицировать или суммировать их на стороне сервера.

Поддерживаются два варианта подключения LLM-провайдера (достаточно одного):

### Вариант A: OpenAI-совместимый endpoint (OpenRouter, LiteLLM, Ollama, vLLM)

```bash
# Windows
set RLM_LLM_BASE_URL=http://localhost:11434/v1
set RLM_LLM_API_KEY=
set RLM_LLM_MODEL=qwen2.5:7b

# Linux/macOS
export RLM_LLM_BASE_URL=http://localhost:11434/v1
export RLM_LLM_API_KEY=
export RLM_LLM_MODEL=qwen2.5:7b
```

- `RLM_LLM_BASE_URL` — базовый URL endpoint'а (обязателен для этого варианта)
- `RLM_LLM_MODEL` — имя модели (обязателен)
- `RLM_LLM_API_KEY` — API-ключ (может быть пустым, например для Ollama)

Требует пакет `openai` (входит в основные зависимости, ставится автоматически).

### Вариант B: Anthropic API

```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-api03-...

# Linux/macOS
export ANTHROPIC_API_KEY=sk-ant-api03-...
```

Ключ получается на [console.anthropic.com](https://console.anthropic.com) → API Keys. По умолчанию используется модель Claude Haiku; переопределяется через `RLM_SUB_MODEL`.

> Если заданы и `RLM_LLM_BASE_URL`, и `ANTHROPIC_API_KEY` — приоритет у OpenAI-совместимого endpoint'а.

**Как передать переменные окружения:**

- **stdio** — через секцию `env` в конфиге MCP (см. раздел 4)
- **StreamableHTTP** — через файл `.env` рядом с рабочим каталогом, откуда запускается сервер. Сервер вызывает `load_dotenv(override=True)` при старте

Пример `.env`:
```
RLM_LLM_BASE_URL=https://api.kilo.ai/api/gateway
RLM_LLM_API_KEY=your-api-key
RLM_LLM_MODEL=minimax/minimax-m2.5:free
```

**Без настройки LLM всё остальное работает нормально** — `find_module`, `grep`, `read_file`, `parse_object_xml` и все прочие хелперы не требуют API-ключа. Просто `llm_query()` будет недоступен.
Базовая функциональность rlm-tools-bsl не пострадает, просто для объяснения того как работает тот или иной механизм (в процессе анализа исходников) - основная модель-анализатор получит в отдельных сложных случаях неранжированный ответ и потратит больше токенов на поиск сути.

## 4. Настроить MCP

**Claude Code (глобально):**
```bash
claude mcp add rlm-tools-bsl -- rlm-tools-bsl
```

**Или (как для CC, так и для других AI-клиентов ) в `.claude.json` / `mcp.json`:**
```json
{
  "mcpServers": {
    "rlm-tools-bsl": {
      "command": "rlm-tools-bsl"
    }
  }
}
```

**С llm_query (передача ключей через env):**
```json
{
  "mcpServers": {
    "rlm-tools-bsl": {
      "command": "rlm-tools-bsl",
      "env": {
        "RLM_LLM_BASE_URL": "https://api.kilo.ai/api/gateway",
        "RLM_LLM_API_KEY": "your-api-key",
        "RLM_LLM_MODEL": "minimax/minimax-m2.5:free"
      }
    }
  }
}
```

**Для разработки (запуск из исходников без сборки пакета):**
```json
{
  "mcpServers": {
    "rlm-tools-bsl": {
      "command": "uv",
      "args": ["run", "rlm-tools-bsl"]
    }
  }
}
```

**Вариант запуска собранного пакета rlm-tools-bsl на StreamableHTTP (альтернатива stdio — стабильнее для некоторых клиентов):**

Некоторые клиенты (например, Kilo Code / Roo Code) могут некорректно работать с stdio-транспортом — переподключают сервер при ошибках в рантайме. StreamableHTTP решает эту проблему.

1. Запустите собранный ранее пакет как сервер отдельным процессом (предварительно настройте `.env` - при необходимости использовать стороннюю мини-llm для ранжирования ответов внутри `llm_query()`):
```bash
rlm-tools-bsl --transport streamable-http

# Или с кастомными портом/хостом
rlm-tools-bsl --transport streamable-http --host 0.0.0.0 --port 3000
```

2. Укажите URL в конфиге клиента:
```json
{
  "mcpServers": {
    "rlm-tools-bsl": {
      "type": "http",
      "url": "http://127.0.0.1:9000/mcp"
    }
  }
}
```

> **Важно:** для многих AI-клиентов обязателен `"type": "http"`, иначе сервер не будет обнаружен.

Дополнительные параметры: `--host 0.0.0.0` (слушать все интерфейсы), `--port 3000` (другой порт).
Или через переменные окружения: `RLM_TRANSPORT`, `RLM_HOST`, `RLM_PORT`.

> **Результат тестирования StreamableHTTP:** транспорт работает стабильно — множество вызовов `rlm_execute` подряд (сканирование 23 000+ BSL-файлов, ~350 сек) без единого обрыва. Это именно тот сценарий, где stdio мог бы дать сбой при долгой сессии.

## 5. Проверить работоспособность

Откройте проект с исходниками 1С в Claude Code и спросите:
```
Используй rlm-tools-bsl: найди все модули справочника "Номенклатура" и покажи экспортные функции
Покажи кто вызывает найденные экспортные функции
```

## Разработка

```bash
git clone https://github.com/<your-repo>/rlm-tools-bsl.git
cd rlm-tools-bsl
uv sync --dev
uv run python -m pytest tests/ -q
```
