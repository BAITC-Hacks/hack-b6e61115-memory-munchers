# Memory Munchers

Репозиторий команды Memory Munchers для хакатона.

Приложение — каталог товаров с AI-помощником и корзиной:

- **Бэкенд** — ASP.NET Core (.NET 10) в `MemoryMunchers/MemoryMunchers`, база данных PostgreSQL, AI-агент через OpenAI Responses API.
- **Фронтенд** — статические HTML/JS-страницы без зависимостей в папке `frontend`.

## Локальный запуск

### 1. Установите необходимые программы

- [.NET 10 SDK](https://dotnet.microsoft.com/download/dotnet/10.0) — проверьте командой `dotnet --version`.
- [PostgreSQL](https://www.postgresql.org/download/) (версия 14 или новее).
- [Python 3](https://www.python.org/downloads/) — только для раздачи статических файлов фронтенда (можно использовать любой другой статический сервер).

### 2. Установите и настройте PostgreSQL

**Windows:**

1. Скачайте установщик с [postgresql.org/download/windows](https://www.postgresql.org/download/windows/) и запустите его.
2. Оставьте порт по умолчанию `5432`.
3. Задайте пароль для пользователя `postgres` — он понадобится для строки подключения.
4. Дождитесь окончания установки (Stack Builder можно пропустить).

**Или через Docker:**

```powershell
docker run --name memory-munchers-db -e POSTGRES_PASSWORD=123 -p 5432:5432 -d postgres:17
```

Создавать базу данных вручную не нужно: при запуске API сам создаст базу `memory_munchers` и применит миграции. Пользователю БД нужны права на установку расширения `pg_trgm` (у пользователя `postgres` они есть).

### 3. Подключите приложение к PostgreSQL

Откройте `MemoryMunchers/MemoryMunchers/appsettings.json` и укажите свои данные в строке подключения:

```json
"ConnectionStrings": {
  "MemoryMunchers": "Host=localhost;Port=5432;Database=memory_munchers;Username=postgres;Password=ВАШ_ПАРОЛЬ"
}
```

Измените `Password` (и при необходимости `Host`, `Port`, `Username`) на значения, заданные при установке PostgreSQL.

Вместо правки файла можно задать переменную окружения:

```powershell
$env:ConnectionStrings__MemoryMunchers = "Host=localhost;Port=5432;Database=memory_munchers;Username=postgres;Password=ВАШ_ПАРОЛЬ"
```

### 4. Добавьте API-ключ OpenAI

Ключ нужен для работы AI-помощника (каталог и корзина работают и без него).

1. Создайте ключ на [platform.openai.com/api-keys](https://platform.openai.com/api-keys).
2. В файле `MemoryMunchers/MemoryMunchers/appsettings.json` найдите секцию `OpenAI` и вставьте ключ в поле `ApiKey`, остальные настройки не трогайте:

```json
"OpenAI": {
  "ApiKey": "sk-ваш-ключ",
  ...
}
```

Либо задайте переменную окружения (она имеет приоритет над `appsettings.json`):

```powershell
$env:OpenAI__ApiKey = "sk-ваш-ключ"
```

> ⚠️ Не коммитьте реальный ключ в репозиторий и не добавляйте его во фронтенд.

Если ключ не задан, запросы к агенту вернут HTTP 503 с кодом `provider_not_configured`.

### 5. Запустите API

Из корня репозитория:

```powershell
dotnet run --project MemoryMunchers/MemoryMunchers --launch-profile http
```

API будет доступен по адресу `http://localhost:5187`. При старте автоматически применяются миграции базы данных.

### 6. Запустите фронтенд

В другом терминале, из корня репозитория:

```powershell
python -m http.server 5500 --directory frontend
```

### 7. Откройте приложение

Откройте в браузере `http://localhost:5500`.

При первом открытии каталог автоматически импортирует товары из `MemoryMunchers/MemoryMunchers/nursultan_ekt_catalog.csv` (около 12 853 товаров) в базу — это может занять некоторое время.

Страницы:

| Адрес | Что открывается |
| --- | --- |
| `http://localhost:5500/` | Каталог товаров с чатом **Помощник** |
| `http://localhost:5500/basket.html` | Корзина |
| `http://localhost:5500/agents.html` | Чат с агентами |

Фронтенд по умолчанию обращается к API по адресу `http://localhost:5187`. Изменить адрес можно в блоке **API connection** на странице.

## Частые проблемы

- **Ошибка подключения к базе** — проверьте, что PostgreSQL запущен, и что пароль/порт в строке подключения верные.
- **Ошибка при установке `pg_trgm`** — используйте пользователя с правами суперпользователя или попросите администратора выполнить `CREATE EXTENSION pg_trgm;` в базе `memory_munchers`.
- **Помощник не отвечает (HTTP 503)** — не задан `OpenAI:ApiKey`. Добавьте ключ и перезапустите API.
- **HTTP 500** — смотрите логи API в терминале, где он запущен.

## Ручное применение миграций (необязательно)

Миграции применяются при старте API, но их можно применить и вручную из корня репозитория:

```powershell
dotnet tool restore
dotnet ef database update --project MemoryMunchers/MemoryMunchers
```
