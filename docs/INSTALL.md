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

## 1. Установить пакет

### Вариант A: Из PyPI (рекомендуется)

```bash
# Через pip
pip install rlm-tools-bsl

# Через uv (как глобальный инструмент)
uv tool install rlm-tools-bsl
```

Обновление:
```bash
pip install --upgrade rlm-tools-bsl
# или
uv tool upgrade rlm-tools-bsl
```

**Windows — установка + служба одной командой** (PowerShell от имени администратора):

```powershell
irm https://raw.githubusercontent.com/Dach-Coin/rlm-tools-bsl/master/simple-install-from-pip.ps1 -OutFile simple-install-from-pip.ps1
PowerShell -ExecutionPolicy Bypass -File .\simple-install-from-pip.ps1
```

Скрипт установит пакет из PyPI, зарегистрирует Windows-службу, запустит сервер и проверит health. Повторный запуск обновит до последней версии.

**Linux — установка + systemd-служба одной командой:**

```bash
curl -LO https://raw.githubusercontent.com/Dach-Coin/rlm-tools-bsl/master/simple-install-from-pip.sh
chmod +x simple-install-from-pip.sh && ./simple-install-from-pip.sh
```

### Вариант B: Docker

Контейнер автоматически проверяет наличие новой версии в PyPI при каждом старте,
обновляется и пересчитывает индексы — разработчику достаточно публиковать пакет в PyPI.

> **Windows + WSL2 + Docker Desktop — НЕ РЕКОМЕНДУЕТСЯ**
>
> Docker Desktop на Windows запускает контейнеры в WSL2 VM. Файлы хоста (исходники 1С)
> прокидываются через Virtiofs/9P протокол — **каждая операция чтения файла проходит через
> границу виртуальной машины**. На практике это даёт **замедление I/O в 5-10 раз**:
>
> - **Построение индексов:** конфигурация, которая индексируется за ~6 мин на хосте,
>   в контейнере может строиться **50+ минут**
> - **Работа хелперов:** `grep`, `glob`, `read_file` и другие операции с исходниками
>   также будут значительно медленнее — это влияет на **каждый** запрос к серверу
>
> Это задокументированное архитектурное ограничение Docker Desktop/WSL2, а не rlm-tools-bsl:
> - [Microsoft: Comparing WSL Versions](https://learn.microsoft.com/en-us/windows/wsl/compare-versions) — WSL2 проигрывает в «performance across OS file systems»
> - [Microsoft: Working across file systems](https://learn.microsoft.com/en-us/windows/wsl/filesystems) — «We recommend against working across operating systems with your files»
> - [Docker: WSL 2 Best Practices](https://docs.docker.com/desktop/features/wsl/best-practices/) — «Performance is much higher when files are bind-mounted from the Linux filesystem»
> - [microsoft/WSL#4197](https://github.com/microsoft/WSL/issues/4197) — замеры: ~11x замедление I/O через 9P протокол
>
> Для Windows рекомендуется **Вариант A** (установка пакета на хост, см. выше) или
> **установка службой** через `simple-install-from-pip.ps1` (сценарий W2 в [QUICKSTART.md](QUICKSTART.md)).
>
> **Docker-вариант оптимален для Linux** (нативный Docker, без прослойки VM) — там
> bind mount работает на скорости файловой системы.
>
> **Если всё же используете Docker на Windows** — построение индекса (`rlm_index(action="build")`)
> обязательно. Без индекса каждый запрос хелпера идёт через файловую систему напрямую,
> и замедление WSL2 (8-10x) будет ощущаться на **каждом** вызове `rlm_execute`.
> С индексом большинство операций выполняются из SQLite за миллисекунды — замедление I/O
> влияет только на начальное построение индекса, а не на повседневную работу.

> **Важно:** Docker-образ использует модель **PyPI-first** — при сборке и обновлении пакет устанавливается из PyPI, а не из локальных исходников. Для работы новых фич в контейнере нужна опубликованная версия в PyPI.
>
> **Для разработчиков:** если вы хотите запустить контейнер из локальных исходников (свои доработки, тестирование до публикации в PyPI), соберите wheel перед сборкой образа:
> ```bash
> uv build                        # создаст dist/*.whl из текущих исходников
> docker compose up -d --build    # Dockerfile подхватит wheel автоматически
> ```
> Без `dist/*.whl` образ установит пакет из PyPI как обычно.

**1. Подготовка:**

```bash
cp docker-compose.example.yml docker-compose.yml
# Отредактируйте REPOS_ROOT и другие переменные
```

**2. Запуск:**

```bash
docker compose up -d
```

**3. Проверка:**

```bash
docker compose logs -f rlm
curl http://localhost:9000/health
```

**4. Настройте трансляцию путей** (рекомендуется):

Добавьте в `docker-compose.yml` переменную `RLM_PATH_MAP`, чтобы использовать привычные хостовые пути:
```yaml
environment:
  - RLM_PATH_MAP=D:/Repos/1c:/repos    # Windows
  # - RLM_PATH_MAP=/home/user/repos:/repos  # Linux
```

С `RLM_PATH_MAP` сервер автоматически транслирует хостовые пути в контейнерные.
Без неё — нужно указывать контейнерные пути (`/repos/...`) вручную.

**5. Регистрация проектов:**

Зарегистрируйте проекты через MCP-хелпер из AI-клиента:

```
rlm_projects(action="add", name="ERP", path="D:/Repos/1c/erp/src/cf")
```

> При наличии `RLM_PATH_MAP` путь `D:/Repos/1c/erp/src/cf` автоматически
> транслируется в `/repos/erp/src/cf` внутри контейнера.

**6. Построение индекса:**

Индекс ускоряет работу хелперов на больших конфигурациях. Строится через MCP-тул из AI-клиента:

```
rlm_index(action="build", project="ERP")
```

Или через CLI:
```bash
docker compose exec rlm rlm-bsl-index index build /repos/erp/src/cf
```

Полный набор действий MCP-тула `rlm_index`: `build`, `update`, `info`, `drop`.

При следующих стартах/рестартах контейнера entrypoint автоматически обновляет
существующие индексы зарегистрированных проектов (последовательно, один за другим).
Если индекс не строился — проект пропускается.
MCP-сервер запускается **после** завершения обновления всех индексов.
На больших конфигурациях это может занять несколько минут — контейнер будет unhealthy до запуска сервера. Прогресс виден в логах (`docker logs rlm`).

**Хранение данных:**

| Что | Путь в контейнере | Том |
|-----|-------------------|-----|
| Реестр проектов, логи | `~/.config/rlm-tools-bsl/` | `rlm-config` (включён) |
| SQLite-индексы | `~/.cache/rlm-tools-bsl/` | `rlm-index-cache` (включён) |
| Исходники 1С | `/repos/` | `REPOS_ROOT` (read-only) |

Оба тома включены по умолчанию — реестр проектов и индексы переживают `docker compose down && up`.

**7. Обновление и откат:**

Автоматически при перезапуске:
```bash
docker compose restart rlm
```

Если PyPI недоступен — контейнер стартует с текущей версией.

**Фиксация/откат версии** — если новая версия сломалась:
```yaml
environment:
  - RLM_VERSION=1.6.2   # зафиксировать конкретную версию
```
```bash
docker compose restart rlm
```
Убрать `RLM_VERSION` (или `=latest`) → вернуться к авто-обновлению.

**8. Конфиг AI-клиента:**

```json
{
  "mcpServers": {
    "rlm-tools-bsl": {
      "type": "http",
      "url": "http://HOST:9000/mcp"
    }
  }
}
```

Все переменные окружения: [ENV_REFERENCE.md](ENV_REFERENCE.md)

### Вариант C: Из исходников (для разработки)

```bash
git clone https://github.com/Dach-Coin/rlm-tools-bsl.git
cd rlm-tools-bsl
uv tool install . --force
```

Команда `rlm-tools-bsl` станет доступна глобально. `uv tool install` создаёт изолированное окружение и ставит пакет из текущего каталога — версия подхватывается из `pyproject.toml` автоматически.

> **Если появилось предупреждение** `... is not on your PATH` — выполните:
> ```bash
> uv tool update-shell
> ```
> Затем **перезапустите терминал** (или откройте новый). Команда `uv tool update-shell` один раз добавляет каталог `~/.local/bin` в системный PATH — повторно запускать не нужно.

## 3. (Опционально) Настройка llm_query

См. раздел «Настройка llm_query» в [README.md](../README.md#настройка-llm_query-опционально).

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

**Рекомендуемый способ** (от администратора):

```bash
git pull
PowerShell -ExecutionPolicy Bypass -File .\reinstall-service.ps1
```

Скрипт автоматически:
1. Остановит и удалит службу
2. Очистит stale-артефакты предыдущих установок (dangling dist-info, user site-packages, каталог dist/)
3. Очистит кэш uv и пересоберет пакет (`uv tool install`)
4. Обновит глобальный Python, используемый службой (`uv pip install`)
5. Установит и запустит службу
6. Проверит health и выведет версию

**Без службы** (только CLI):

```bash
git pull
uv cache clean rlm-tools-bsl
uv tool install ".[service]" --force --reinstall
```

Проверьте версию: `rlm-tools-bsl --version`, `rlm-bsl-index --version`

> **Важно:** Не используйте `pip install -e .` для обновления — это может установить пакет в неправильное окружение Python и сломать службу. Всегда используйте `uv tool install` или `reinstall-service.ps1`.

## 5. (Опционально) Построить SQLite-индекс

Индекс ускоряет работу хелперов на больших конфигурациях (ERP, УТ и др.): `extract_procedures`, `find_callers_context`, `find_roles`, `find_register_movements` и другие работают мгновенно из SQLite вместо парсинга файлов.

```bash
rlm-bsl-index index build <path-to-1c-sources>
```

Подробности: [INDEXING.md](INDEXING.md)

## 6. Проверить работоспособность

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
uv run pytest tests/ -q
```
