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

> **Корпоративный прокси / ошибка TLS** (`invalid peer certificate: UnknownIssuer`):
> корпоративный файрвол подменяет TLS-сертификат, и uv ему не доверяет. Добавьте флаг `--native-tls`, чтобы uv использовал системное хранилище сертификатов Windows:
> ```bash
> uv tool install . --force --native-tls
> ```
> Чтобы не указывать флаг каждый раз, задайте переменную окружения:
> ```powershell
> # PowerShell (постоянно для текущего пользователя)
> [Environment]::SetEnvironmentVariable("UV_NATIVE_TLS", "true", "User")
> ```
> Или добавьте в `pyproject.toml` проекта:
> ```toml
> [tool.uv]
> native-tls = true
> ```

## 1. Клонировать репозиторий

```bash
git clone https://github.com/Dach-Coin/rlm-tools-bsl.git
cd rlm-tools-bsl
```

## 2. Установить глобально

```bash
uv tool install . --force
```

Команда `rlm-tools-bsl` станет доступна глобально. `uv tool install` создаёт изолированное окружение и ставит пакет из текущего каталога — версия подхватывается из `pyproject.toml` автоматически.

> **Если появилось предупреждение** `... is not on your PATH` — выполните:
> ```bash
> uv tool update-shell
> ```
> Затем **перезапустите терминал** (или откройте новый). Команда `uv tool update-shell` один раз добавляет каталог `~/.local/bin` в системный PATH — повторно запускать не нужно.

<details>
<summary>Вариант через pip</summary>

```bash
pip install .
```

</details>

## 3. (Опционально) Настройка llm_query

См. раздел «Настройка llm_query» в [README.md](README.md#настройка-llm_query-опционально).

## 4. Настроить MCP

> **Рекомендация:** используйте **StreamableHTTP** (HTTP-транспорт) вместо stdio. Протокол stdio нестабилен при длительных сессиях — клиенты (Cursor, Kilo Code, Roo Code) могут терять соединение, переподключать сервер или обрывать сессию при таймауте одного вызова. HTTP-транспорт работает как отдельный процесс и не зависит от жизненного цикла клиента.

### Вариант A: StreamableHTTP (рекомендуется)

**1. Запустите сервер** (вручную или как службу — см. ниже):
```bash
rlm-tools-bsl --transport streamable-http

# Или с кастомными портом/хостом
rlm-tools-bsl --transport streamable-http --host 0.0.0.0 --port 3000
```

> **Примечание:** `.env` загружается из текущего рабочего каталога (cwd). Запускайте команду из папки, где лежит `.env`, или задайте переменные окружения (`RLM_LLM_BASE_URL`, `RLM_LLM_API_KEY`, `RLM_LLM_MODEL`) системно.

Дополнительные параметры: `--host 0.0.0.0` (слушать все интерфейсы), `--port 3000` (другой порт).
Или через переменные окружения: `RLM_TRANSPORT`, `RLM_HOST`, `RLM_PORT`.

**2. Укажите URL в конфиге клиента** (`.claude.json` / `mcp.json`):
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

> **Важно:** для большинства AI-клиентов обязателен `"type": "http"`, иначе сервер не будет обнаружен.

**Для Claude Code** можно также добавить командой:
```bash
claude mcp add --transport http rlm-tools-bsl http://127.0.0.1:9000/mcp
```

> **Результат тестирования StreamableHTTP:** транспорт работает стабильно — множество вызовов `rlm_execute` подряд (сканирование 23 000+ BSL-файлов, ~350 сек) без единого обрыва. Это именно тот сценарий, где stdio даёт сбои при долгих сессиях.

### Вариант B: stdio (fallback)

> **Внимание:** stdio подвержен обрывам сессий при долгих операциях. Используйте только если HTTP-транспорт недоступен (например, клиент не поддерживает HTTP MCP).

**Claude Code (глобально):**
```bash
claude mcp add rlm-tools-bsl -- rlm-tools-bsl
```

**Или в `.claude.json` / `mcp.json`:**
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

### Запуск как системная служба

Чтобы HTTP-сервер запускался автоматически при входе в систему (Windows) или при старте машины (Linux с `loginctl enable-linger`):

**1. Установить с поддержкой службы (только для Windows — нужен pywin32):**
```bash
uv tool install ".[service]" --force
```

**2. Зарегистрировать службу (один раз):**
```bash
# Без .env (если переменные окружения уже заданы системно):
rlm-tools-bsl service install

# С явным .env файлом:
rlm-tools-bsl service install --env /path/to/.env

# Нестандартный порт:
rlm-tools-bsl service install --host 127.0.0.1 --port 9000 --env /path/to/.env
```

> **Windows:** команду нужно запускать от имени администратора (cmd / PowerShell → «Запуск от имени администратора»).
> **Linux:** для автозапуска без входа выполните `loginctl enable-linger $USER`.

**3. Управление службой:**
```bash
rlm-tools-bsl service start
rlm-tools-bsl service stop
rlm-tools-bsl service status
rlm-tools-bsl service uninstall
```

Конфиг службы сохраняется в `~/.config/rlm-tools-bsl/service.json`. Если `.env` не указан — сервис стартует без него (все параметры берутся из переменных окружения, заданных системно).

### Обновление до новой версии

При обновлении из git необходимо очистить кэш сборки uv, иначе будет установлена старая версия из кэша:

```bash
git pull
uv cache clean rlm-tools-bsl
uv tool install ".[service]" --force --reinstall
```

Если служба уже была установлена — переустановите её (от администратора):
```bash
git pull
PowerShell -ExecutionPolicy Bypass -File .\reinstall-service.ps1
```

Или вручную:
```bash
rlm-tools-bsl service uninstall
uv cache clean rlm-tools-bsl
PowerShell -ExecutionPolicy Bypass -File .\simple-install.ps1
```

Проверьте версию: `rlm-tools-bsl --version`

## 5. Проверить работоспособность

Откройте проект с исходниками 1С в Claude Code и спросите:
```
Используй rlm-tools-bsl: найди все модули справочника "Номенклатура" и покажи экспортные функции
Покажи кто вызывает найденные экспортные функции
```

## Разработка

```bash
git clone https://github.com/Dach-Coin/rlm-tools-bsl.git
cd rlm-tools-bsl
uv sync --dev
uv run python -m pytest tests/ -q
```
