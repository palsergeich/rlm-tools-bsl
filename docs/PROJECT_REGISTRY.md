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
| `add`    | `name`, `path`, `description` (опц.)        | `rlm_projects(action="add", name="Dev", path="/data/dev-config")`   |
| `remove` | `name`                                      | `rlm_projects(action="remove", name="Dev")`                         |
| `rename` | `name`, `new_name`                          | `rlm_projects(action="rename", name="Dev", new_name="Development")` |
| `update` | `name`, `path` (опц.), `description` (опц.) | `rlm_projects(action="update", name="Dev", description="Updated")`  |

## Использование в rlm_start

Параметр `project` -- альтернатива `path`. Достаточно указать один из них:

```
# По имени проекта (точное или подстрока)
rlm_start(project="My Config", query="find module SomeModule")

# По пути (как раньше, обратная совместимость)
rlm_start(path="/path/to/1c-sources", query="find module SomeModule")
```

### Поиск по имени

Поиск трёхуровневый:
1. **Точное совпадение** (без учёта регистра) -- сессия создаётся
2. **Подстрока** (без учёта регистра) -- сессия создаётся, если совпадение единственное
3. **Нечёткий поиск** (расстояние Левенштейна) -- сессия НЕ создаётся, возвращается подсказка "Did you mean '...'?"

При неоднозначном совпадении (несколько проектов подходят) сессия не создаётся -- возвращается список вариантов.

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
