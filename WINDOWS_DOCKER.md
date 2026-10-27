# Запуск на Windows с Docker Desktop

Для работы учеников с других компьютеров используйте [полную инструкцию развёртывания в локальной сети](START_HERE_WINDOWS.md). Ниже описан прежний вариант проверки только на одном компьютере.

Эта конфигурация запускает PostgreSQL, Redis, Django/Gunicorn и Celery на одном Windows-компьютере через Linux-контейнеры. Python на Windows устанавливать не нужно. Домен не нужен. Веб-интерфейс опубликован только на `127.0.0.1:8010`.

1. Запустите Docker Desktop и дождитесь готовности движка. Используйте Linux containers. В PowerShell проверьте:

```powershell
docker info --format '{{.OSType}}'
```

Ожидаемый результат — `linux`.

2. Распакуйте архив проекта. Откройте PowerShell в папке `meshy-classroom`, содержащей `compose.windows.yaml`.

3. Соберите и запустите:

```powershell
docker compose -f compose.windows.yaml up -d --build --wait --wait-timeout 600
```

Первый запуск требует интернета для загрузки образов и Python-пакетов. Без `.env` включается деморежим; реальный Meshy не вызывается.

4. Создайте демонстрационные кабинеты:

```powershell
docker compose -f compose.windows.yaml exec web python manage.py seed_demo
```

5. Откройте http://localhost:8010 и выберите «Я преподаватель» либо «Я ученик». У демонстрационного ученика уже есть заявка на проверке; одобрите или верните её из кабинета преподавателя, прежде чем создавать следующую.

6. Если нужны полноценные пользовательские аккаунты и админка:

```powershell
docker compose -f compose.windows.yaml exec web python manage.py createsuperuser
```

Админка: http://localhost:8010/admin/ .

## Управление

```powershell
# Состояние сервисов
docker compose -f compose.windows.yaml ps

# Журнал при ошибке
docker compose -f compose.windows.yaml logs --tail=100 web worker beat

# Остановить, сохранив данные
docker compose -f compose.windows.yaml stop

# Продолжить работу
docker compose -f compose.windows.yaml up -d

# Пересобрать после изменения исходников
docker compose -f compose.windows.yaml up -d --build --wait --wait-timeout 600
```

База и файлы сохраняются в именованных Docker volumes проекта `forma-local`. Этот проект отделён от серверной конфигурации `compose.yaml`. Архив исходников не переносит существующую базу и файлы с другого компьютера.

## Реальная генерация на этом же компьютере

Создайте обычные аккаунты через админку, затем создайте в каталоге проекта файл `.env`:

```dotenv
MESHY_MODE=live
MESHY_API_KEY=ваш_API_ключ
```

Повторите команду `up -d --build --wait --wait-timeout 600`, чтобы контейнеры получили новое окружение. Демо-кнопки входа станут недоступны; входите по логину и паролю. Команду `seed_demo` в режиме live не запускайте. Старые демонстрационные готовые модели останутся учебными примерами.

`compose.windows.yaml` предназначен для локальной проверки и использует DEBUG. Для доступа учеников с других устройств нужна отдельная конфигурация сети, обычная авторизация и production-настройки; для публичного домена уже подготовлен `compose.yaml`. Не меняйте привязку `127.0.0.1` на все интерфейсы для публикации этого деморежима.

Конфигурация проверена командой `docker compose config`. Запуск контейнеров на конкретном Windows-компьютере должен быть проверен после переноса: на машине разработки Docker-служба не запущена.
