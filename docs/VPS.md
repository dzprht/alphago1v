# Запуск на AdminVPS

Целевая машина: VPS Standart, 8 vCPU, 12 ГБ RAM, Ubuntu 24.04 LTS. Домен и VPS еще оформляются; публичное размещение здесь не подтверждено.

## Система и код

```bash
sudo apt update
sudo apt install -y git nginx postgresql-common ffmpeg python3-venv certbot python3-certbot-nginx
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
sudo apt update
sudo apt install -y postgresql-17
sudo python3 -m venv /opt/uv
sudo /opt/uv/bin/pip install uv==0.11.7
sudo useradd --system --create-home alphago1v
sudo git clone https://github.com/dzprht/alphago1v.git /opt/alphago1v
sudo chown -R alphago1v:alphago1v /opt/alphago1v
cd /opt/alphago1v
sudo -u alphago1v -H /opt/uv/bin/uv sync --locked --python 3.11 --group ml --group media --no-dev
sudo -u postgres createuser --pwprompt alphago1v
sudo -u postgres createdb --owner=alphago1v alphago1v
```

Репозиторий PGDG подключается по [официальной инструкции PostgreSQL](https://www.postgresql.org/download/linux/ubuntu/): так устанавливается именно 17-я версия, как локально и в CI.

Создать `/opt/alphago1v/.env` по `deploy/env.example`: новый секрет, пароль созданной БД, реальный домен. Пока сертификат не установлен, DJANGO_HTTPS=0. Владелец файла alphago1v, права 600. Скопировать тот же файл в `/etc/alphago1v.env` с владельцем root и правами 600: systemd прочитает его до запуска Gunicorn.

Веса RuBERT/CNN передать отдельно в `var/ml/selected-models`. Whisper, pyannote и OCR подготовить командами из [WHISPER](WHISPER.md), [DIARIZATION](DIARIZATION.md), [OCR](OCR.md). Pyannote требует принятия условий и локального входа Hugging Face. В `var/ml/selected-models/media.json` использовать [пример](../examples/media.json) с фактическими версиями. Все каталоги проекта и весов должны читаться пользователем alphago1v.

На рабочем компьютере полный комплект уже собран в `var/release/models.tar.gz` без кешей и исследовательских данных. Его можно перенести и распаковать в `/opt/alphago1v/var/ml`, затем передать владельцу alphago1v. Повторная авторизация Hugging Face на VPS для такого переноса не нужна; модель работает локально.

```bash
sudo -u alphago1v .venv/bin/python manage.py migrate --noinput
sudo -u alphago1v .venv/bin/python manage.py collectstatic --noinput
sudo -u alphago1v .venv/bin/python manage.py check_models
sudo cp deploy/alphago1v.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now alphago1v
```

`check_models` проверяет все модели, включая диаризацию; неполный комплект завершает команду ошибкой. Это проверка загрузки, а не качества. Первый запрос после запуска тоже загружает нужные модели в процесс; после старта пройти все пять форм до демонстрации. Один worker обрабатывает запросы последовательно, не размножая веса в памяти. На VPS нужно измерить задержку и память.

## Домен и HTTPS

Направить DNS A-запись домена на IP сервера. В `deploy/nginx.conf` заменить example.com своим доменом, затем:

```bash
sudo cp deploy/nginx.conf /etc/nginx/sites-available/alphago1v
sudo ln -s /etc/nginx/sites-available/alphago1v /etc/nginx/sites-enabled/alphago1v
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d example.com
```

В последней команде также нужен реальный домен. После проверки сертификата поставить DJANGO_HTTPS=1 в обоих env-файлах и выполнить `sudo systemctl restart alphago1v`. Gunicorn слушает только 127.0.0.1:8000, публичный вход идет через Nginx. Nginx заменяет X-Real-IP адресом клиента; Django доверяет ему только от 127.0.0.1. Поэтому лимит входа учитывает посетителей раздельно. Лимит тела 8 МиБ, ожидание прокси 200 секунд; у Gunicorn 180 секунд.

Проверить HTTPS без циклических редиректов, вход с другого устройства, пять форм, импорт, отзывы и работу после `systemctl restart`. HSTS включать после проверки HTTPS. Открытыми наружу нужны SSH, HTTP и HTTPS; PostgreSQL и Gunicorn остаются локальными.

## Резервные копии

`sudo /opt/alphago1v/deploy/backup.sh` создает закрытый pg_dump в `/var/backups/alphago1v`. По умолчанию БД называется alphago1v; другой каталог и БД передаются двумя аргументами. Ошибочный неполный дамп удаляется. Для ежедневного запуска добавить команду в root cron, например в 03:30. Срок хранения копий и пользовательских данных утвердить перед публичным доступом; скрипт сам старые копии не удаляет.

Пробное восстановление выполнять в новой БД:

```bash
sudo -u postgres createdb alphago1v_restore_test
sudo cat /var/backups/alphago1v/ИМЯ_КОПИИ | sudo -u postgres pg_restore --exit-on-error --no-owner --dbname=alphago1v_restore_test
sudo -u postgres psql -d alphago1v_restore_test -c 'SELECT count(*) FROM django_migrations;'
sudo -u postgres dropdb alphago1v_restore_test
```

Дамп и восстановление в отдельную БД проверены локально на PostgreSQL 17. Это не проверка будущего VPS. Копии нельзя публиковать в Git или хранить вместе с доступной снаружи статикой.
