import json
import sys
import time
import urllib.request


def check_url(url, attempts=15, delay=4):
    # ждет успешного ответа приложения после выкладки
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status == 200 and json.load(response) == {"status": "ok"}:
                    return True
        except (OSError, ValueError):
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    return False


if __name__ == "__main__":
    if len(sys.argv) != 2 or not check_url(sys.argv[1]):
        raise SystemExit("Backend healthcheck failed")
    print("Backend healthcheck passed")
