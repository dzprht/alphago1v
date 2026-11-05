import io
from unittest.mock import Mock

from deploy.check_url import check_url


def test_deploy_probe_retries_and_requires_backend_json(monkeypatch):
    # повторяет проверку после ошибки и не принимает чужую страницу
    wrong = io.BytesIO(b'{"status":"not ready"}')
    wrong.status = 200
    ready = io.BytesIO(b'{"status":"ok"}')
    ready.status = 200
    request = Mock(side_effect=[OSError("connection failed"), wrong, ready])
    delay = Mock()
    monkeypatch.setattr("deploy.check_url.urllib.request.urlopen", request)
    monkeypatch.setattr("deploy.check_url.time.sleep", delay)
    assert check_url("https://example.com/health/", attempts=3, delay=0)
    assert request.call_count == 3 and delay.call_count == 2


def test_deploy_probe_fails_after_last_attempt(monkeypatch):
    # завершает проверку если приложение так и не ответило
    request = Mock(side_effect=OSError("connection failed"))
    delay = Mock()
    monkeypatch.setattr("deploy.check_url.urllib.request.urlopen", request)
    monkeypatch.setattr("deploy.check_url.time.sleep", delay)
    assert not check_url("https://example.com/health/", attempts=2, delay=0)
    assert request.call_count == 2 and delay.call_count == 1
