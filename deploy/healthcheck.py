import json
import os
import urllib.request


def main():
    # проверяет локальный ответ приложения и базы
    host = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost").split(",")[0].strip()
    request = urllib.request.Request("http://127.0.0.1:8000/health/", headers={
        "Host": host, "X-Real-IP": "127.0.0.1", "X-Forwarded-Proto": "https",
    })
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status != 200 or json.load(response) != {"status": "ok"}:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
