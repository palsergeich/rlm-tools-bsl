# Реестр проектов (Project Registry)

Реестр проектов позволяет работать по человекочитаемым именам вместо абсолютных путей. Особенно удобно при подключении к удалённому серверу по MCP (streamable-http), когда пути на сервере неизвестны или неудобны.

## Быстрый старт

### 1. Посмотреть зарегистрированные проекты

```
rlm_projects(action="list")
```

### 2. Зарегистрировать проект

```
rlm_projects(action="add", name="My Config", path="/path/to/1c-sources", description="Production config", password="...")
```

Пароль обязателен. Без него сервер вернёт `approval_required` — AI-модель должна запросить пароль у пользователя.

### 3. Открыть сессию по имени

```
rlm_start(project="My Config", query="find all exported procedures")
```

## Управление реестром (rlm_projects)

| Действие | Параметры                                   | Пример                                                              |
| -------- | ------------------------------------------- | ------------------------------------------------------------------- |
| `list`   | --                                          | `rlm_projects(action="list")`                                       |
| `add`    | `name`, `path`, `password`, `description` (опц.) | `rlm_projects(action="add", name="Dev", path="/data/dev-config", password="...")` |
| `remove` | `name`, `password`                          | `rlm_projects(action="remove", name="Dev", password="...")`          |
| `rename` | `name`, `new_name`, `password`              | `rlm_projects(action="rename", name="Dev", new_name="Development", password="...")` |
| `update` | `name`, `password`, `path` (опц.), `description` (опц.), `clear_password` (опц.) | `rlm_projects(action="update", name="Dev", description="New desc", password="...")` |

**Параметр `password`:**

| Действие | Семантика `password` |
|----------|---------------------|
| `add` | Устанавливает начальный пароль (обязателен) |
| `remove`, `rename`, `update` | Текущий пароль для подтверждения операции |
| `update` на проекте без пароля | Устанавливает пароль (bootstrap) |

Смена пароля — в два шага: `clear_password` с текущим паролем, затем `update(password="новый")`.

## Использование в rlm_start и rlm_index

Параметр `project` -- альтернатива `path` в `rlm_start` и `rlm_index`. Достаточно указать один из них:

```
# По имени проекта (точное или подстрока)
rlm_start(project="My Config", query="find module SomeModule")

# По пути (как раньше, обратная совместимость)
rlm_start(path="/path/to/1c-sources", query="find module SomeModule")

# Индексирование по имени проекта
rlm_index(action="build", project="My Config")  # → {"started": true} (фон)
rlm_index(action="info", project="My Config")   # → build_status: "building"|"done"
```

### Поиск по имени

Поиск трёхуровневый:
1. **Точное совпадение** (без учёта регистра) -- сессия создаётся
2. **Подстрока** (без учёта регистра) -- сессия создаётся, если совпадение единственное
3. **Нечёткий поиск** (расстояние Левенштейна) -- сессия НЕ создаётся, возвращается подсказка "Did you mean '...'?"

При неоднозначном совпадении (несколько проектов подходят) сессия не создаётся -- возвращается список вариантов.

### Пароль проекта

Пароль обязателен при регистрации проекта (`add`) и для всех мутирующих операций (`remove`, `update`, `rename`) через MCP:

```
rlm_projects(action="add", name="ERP", path="D:\\Bases\\ERP", password="МойПароль")
```

Пароль хранится как SHA-256 hash + salt в `projects.json`.

**Мутирующие операции** (remove/update/rename) требуют параметр `password` с текущим паролем:

```
rlm_projects(action="remove", name="ERP", password="МойПароль")
rlm_projects(action="update", name="ERP", description="Новое описание", password="МойПароль")
rlm_projects(action="rename", name="ERP", new_name="ERP 2.5", password="МойПароль")
```

Без `password` сервер возвращает `approval_required` — AI-модель должна запросить пароль у пользователя.

**Управление паролем:**
- Сменить (два шага):
  1. `rlm_projects(action="update", name="ERP", password="СтарыйПароль", clear_password=true)`
  2. `rlm_projects(action="update", name="ERP", password="НовыйПароль")`
- Удалить: `rlm_projects(action="update", name="ERP", password="МойПароль", clear_password=true)`

После удаления пароля все MCP-мутации заблокированы до установки нового.

**Legacy-проекты** (зарегистрированные без пароля): для установки пароля вызовите `rlm_projects(action="update", name="...", password="...")`. Остальные мутации заблокированы до установки пароля.

**Флаг `has_password`**: CRUD-ответы `rlm_projects` (list, add, remove, rename, update) содержат поле `has_password: true/false` для каждого проекта. Это позволяет визуально контролировать, какие проекты защищены.

**Зачем нужен пароль проекта?** Слабые AI-модели могут самостоятельно добавлять, удалять или модифицировать проекты без ведома пользователя. Пароль создаёт точку взаимодействия, где модель запрашивает подтверждение у человека. CLI-интерфейс `rlm-bsl-index` не требует пароля.

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
