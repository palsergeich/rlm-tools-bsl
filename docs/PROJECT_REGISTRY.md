# Реестр проектов (Project Registry)

Реестр проектов позволяет работать по человекочитаемым именам вместо абсолютных путей. Особенно удобно при подключении к удалённому серверу по MCP (streamable-http), когда пути на сервере неизвестны или неудобны.

## Быстрый старт

### 1. Посмотреть зарегистрированные проекты

```
rlm_projects(action="list")
```

### 2. Зарегистрировать проект

```
rlm_projects(action="add", name="My Config", path="/path/to/1c-sources", description="Production config")
```

### 3. Открыть сессию по имени

```
rlm_start(project="My Config", query="find all exported procedures")
```

## Управление реестром (rlm_projects)

| Действие | Параметры                                   | Пример                                                              |
| -------- | ------------------------------------------- | ------------------------------------------------------------------- |
| `list`   | --                                          | `rlm_projects(action="list")`                                       |
| `add`    | `name`, `path`, `description` (опц.), `password` (опц.) | `rlm_projects(action="add", name="Dev", path="/data/dev-config", password="...")` |
| `remove` | `name`                                      | `rlm_projects(action="remove", name="Dev")`                         |
| `rename` | `name`, `new_name`                          | `rlm_projects(action="rename", name="Dev", new_name="Development")` |
| `update` | `name`, `path` (опц.), `description` (опц.), `password` (опц.), `clear_password` (опц.) | `rlm_projects(action="update", name="Dev", password="new")` |

## Использование в rlm_start и rlm_index

Параметр `project` -- альтернатива `path` в `rlm_start` и `rlm_index`. Достаточно указать один из них:

```
# По имени проекта (точное или подстрока)
rlm_start(project="My Config", query="find module SomeModule")

# По пути (как раньше, обратная совместимость)
rlm_start(path="/path/to/1c-sources", query="find module SomeModule")

# Индексирование по имени проекта
rlm_index(action="build", project="My Config")
rlm_index(action="info", project="My Config")
```

### Поиск по имени

Поиск трёхуровневый:
1. **Точное совпадение** (без учёта регистра) -- сессия создаётся
2. **Подстрока** (без учёта регистра) -- сессия создаётся, если совпадение единственное
3. **Нечёткий поиск** (расстояние Левенштейна) -- сессия НЕ создаётся, возвращается подсказка "Did you mean '...'?"

При неоднозначном совпадении (несколько проектов подходят) сессия не создаётся -- возвращается список вариантов.

### Пароль проекта для управления индексами

При регистрации проекта можно (и рекомендуется) задать пароль:

```
rlm_projects(action="add", name="ERP", path="D:\\Bases\\ERP", password="МойПароль")
```

Пароль хранится как SHA-256 hash + salt в `projects.json`. Без пароля управление индексами (build/update/drop) через MCP заблокировано.

Управление паролем:
- Установить/сменить: `rlm_projects(action="update", name="ERP", password="МойПароль")`
- Удалить (заблокировать MCP-индексацию): `rlm_projects(action="update", name="ERP", clear_password=true)`

**Зачем нужен пароль проекта?** Слабые AI-модели при обнаружении отсутствующего индекса самостоятельно запускают построение без согласия пользователя. Построение занимает 5-10 минут и блокирует I/O сервера. Пароль гарантирует, что только человек принимает решение об управлении индексами — модель не знает пароль и не может обойти проверку. CLI-интерфейс `rlm-bsl-index` не требует пароля.

### Подсказка о регистрации

Если `path` передан напрямую и не зарегистрирован в реестре, ответ `rlm_start` включит `project_hint` с предложением добавить его.

## Примеры промптов для агента

Естественная речь, которую агент корректно обработает:

- "Проанализируй модуль ОбщегоНазначения в My Config"
- "Найди все экспортные процедуры в проекте DevERP"
- "Покажи зарегистрированные проекты"
- "Добавь в реестр проект TestBuh, путь /data/test-config, описание 'Тестовая бухгалтерия'"
- "Переименуй проект TestBuh в TestingBuh"
- "Удали проект Test UNF из реестра"
- "Добавь проект ERP с паролем для управления индексами"
- "Смени пароль проекта ERP"

## Где хранится реестр

Файл `projects.json` располагается рядом с активным `service.json`:

- Если задан `RLM_CONFIG_FILE` -- в том же каталоге
- Иначе -- `~/.config/rlm-tools-bsl/projects.json`

Формат файла:

```json
{
  "projects": [
    {
      "name": "My Config",
      "path": "/path/to/1c-sources",
      "description": "Production config"
    }
  ]
}
```

При каждом изменении создаётся резервная копия `projects.bak`.
