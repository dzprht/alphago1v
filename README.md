# Альфа Защита

Сайт для проверки сообщений, ссылок, QR-кодов, скриншотов и переводов. Показывает подозрительные признаки и объясняет, на что обратить внимание.

[Открыть сайт](https://91.197.98.216/) · [Заявка](kharchenko_application.md) · [Сценарии](kharchenko_userscripts.md) · [Диаграммы](kharchenko_diagrams.md)

Проверки работают без регистрации. Аккаунт нужен для истории, загрузки операций и отзывов. Отсутствие предупреждения не гарантирует безопасность.

## Запуск

Нужны Python 3.11, uv и Docker. Команды выполняются из папки проекта.

```sh
cp .env.example .env
uv sync --locked --group ml --group media
docker compose up -d db
```

В `.env` задай свой `DJANGO_SECRET_KEY` и пароль БД в `POSTGRES_PASSWORD`. Для `ANALYZER_DIR` укажи `var/ml/selected-models`.

Из [выпуска с моделями](https://github.com/dzprht/alphago1v/releases/tag/text-negation-20261105) скачай `models.tar.gz` и положи в корень проекта. Архив содержит RuBERT, URL-CNN и EasyOCR. Веса через git не скачиваются.

```sh
mkdir -p var/ml
tar -xzf models.tar.gz -C var/ml
```

Дождись готовности PostgreSQL: в `docker compose logs db` должна появиться строка `ready to accept connections`. Затем:

```sh
uv run --no-sync python manage.py migrate
uv run --no-sync python manage.py collectstatic --noinput
uv run --no-sync python manage.py check_models
uv run --no-sync python manage.py runserver
```

Открой http://127.0.0.1:8000/. После замены весов перезапусти сервер.

## Проверка

Обычные тесты и полный проход с настоящими моделями:

```sh
uv run --no-sync pytest -q --ignore-glob='tests/test_benchmark*'
NATIVE_MODELS_DIR=var/ml/selected-models uv run --no-sync pytest -q tests/test_native_site.py
```

Вручную можно пройти [семь сценариев](kharchenko_userscripts.md). Картинки для показа лежат в `examples/demo`.

## История операций

После входа можно загрузить CSV или ввести операции вручную. По умолчанию строки добавляются, включая дубли. Галочка «Заменить всю историю» удаляет прежние операции и записывает новые. Ошибка ввода оставляет историю как была.

CSV: UTF-8, разделитель `;`, до 2 МиБ и 10 000 операций. [Готовый пример](examples/transactions.csv):

```csv
occurred_at;amount;direction;recipient
2026-10-20T14:30:00+03:00;1500.50;outgoing;shop_1
```

Дата нужна со временем и часовым поясом. Сумма положительная, в рублях, максимум два знака после запятой. Направление — `outgoing` или `incoming`. Получателя можно не заполнять; для сравнения используй одинаковое обозначение одного человека или организации.

Текст принимается до 10 000 символов, ссылка — до 2048. Для QR и скриншотов нужны PNG/JPEG до 5 МиБ и 4 млн пикселей. В ручном вводе можно добавить до 100 операций за раз.

## Как устроено

`accounts` — вход и регистрация, `checks` — проверки и отзывы, `transactions` — история операций, `analysis` — модели и правила. Страницы лежат в `templates`, стили и скрипты — в `static`.

RuBERT ищет семь признаков в тексте. CNN проверяет адрес ссылки без открытия сайта. OpenCV читает QR, EasyOCR — переписку на скриншоте. Для перевода учитываются анкета и статистика загруженной истории. Предупреждение собирается по правилам; положительные ответы анкеты не отменяются моделью.

Данные, обучение и результаты сравнения сохранены в том же [выпуске](https://github.com/dzprht/alphago1v/releases/tag/text-negation-20261105). Проверка качества проводилась на синтетике.

## Сервер

На VPS с Ubuntu 24.04 приложение и PostgreSQL работают через `compose.prod.yaml` и `compose.frozen.yaml`, снаружи — Nginx и HTTPS. `/ping/` проверяет процесс, `/health/` — еще и БД.

Рабочая папка — `/opt/alphago1v`. За копии БД отвечает `alphadefense-backup.timer`, за сертификат — `alphadefense-cert-renew.timer`. Копии лежат в `var/backups`. Готовый комплект для переноса — в `var/release`. Автовыкладка через Actions выключена: текущий сервер использует сохраненный образ.
