"""Общие фикстуры и Allure-обвязка для тестов BookShelf API.

Каждый HTTP-запрос оформляется шагом Allure (`allure.step`) с вложениями
запроса и ответа — как в нормальном проде: в отчёте виден каждый шаг,
параметры, тело и статус.
"""

import json
import os
import time
import uuid

import allure
import pytest
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("BOOKSHELF_URL", "https://qahacking.up.railway.app")


# --------------------------------------------------------------------------- #
# Allure-хелперы                                                              #
# --------------------------------------------------------------------------- #
def _attach(name: str, content: str) -> None:
    allure.attach(content, name=name, attachment_type=allure.attachment_type.TEXT)


def _format_response(resp: requests.Response) -> str:
    try:
        body = json.dumps(resp.json(), ensure_ascii=False, indent=2)
    except ValueError:
        body = resp.text
    return f"HTTP {resp.status_code}\n\n{body[:3000]}"


def _raw_request(caller, method: str, url: str, **kwargs) -> requests.Response:
    """HTTP-запрос с мягким ретраем ТОЛЬКО на 429 (rate limit).

    Закладки не трогаем: 500 от unstable, ConnectionError, Timeout, упавший assert
    ретраю не подлежат — их порождает суть теста. Ретраится лишь rate limit (429),
    который к сути падения отношения не имеет: честим `Retry-After`, иначе
    экспоненциальный бэкофф. Так teapot-429 от частых прогонов не роняет сьют.
    """
    resp = caller.request(method, url, **kwargs)
    for attempt in range(4):
        if resp.status_code != 429:
            return resp
        # Retry-After может быть числом секунд или HTTP-датой; на дату — бэкофф.
        try:
            delay = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            delay = 2**attempt
        time.sleep(min(delay, 10))
        resp = caller.request(method, url, **kwargs)
    return resp


def api_request(caller, method: str, url: str, *, title: str | None = None, **kwargs):
    """Выполнить HTTP-запрос как шаг Allure с вложениями запроса и ответа.

    caller — requests.Session (фикстура api) или сам модуль requests.
    Запрос идёт через `_raw_request` — с ретраем на rate limit.
    """
    with allure.step(title or f"{method.upper()} {url}"):
        if kwargs.get("params"):
            _attach(
                "Параметры запроса", json.dumps(kwargs["params"], ensure_ascii=False)
            )
        if kwargs.get("json"):
            _attach(
                "Тело запроса", json.dumps(kwargs["json"], ensure_ascii=False, indent=2)
            )
        resp = _raw_request(caller, method, url, **kwargs)
        _attach("Ответ", _format_response(resp))
        return resp


def check(title: str):
    """Шаг-проверка Allure. Использование: `with check("..."):` вокруг assert'ов."""
    return allure.step(title)


# --------------------------------------------------------------------------- #
# Фикстуры авторизации                                                        #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def api():
    """Авторизованная HTTP-сессия для работы с каталогом."""
    session = requests.Session()
    with allure.step("Авторизация: получить Bearer-токен (POST /auth/token)"):
        resp = _raw_request(session, "POST", f"{BASE_URL}/auth/token", timeout=15)
        _attach("Ответ /auth/token", _format_response(resp))
        assert resp.status_code in (200, 201), (
            f"Не удалось получить токен: {resp.status_code} {resp.text[:200]}"
        )
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        assert token, f"в ответе /auth/token нет токена: {data}"
        session.headers["Authorization"] = f"Bearer {token}"
    return session


@pytest.fixture(scope="session")
def reader(api):
    """Токен и профиль читателя для reader-эндпоинтов.

    Написана по образцу api(): выпускаем отдельный токен,
    привязываем к нему нового читателя.
    """
    with allure.step("Выпустить токен читателя (POST /auth/token)"):
        resp = _raw_request(requests, "POST", f"{BASE_URL}/auth/token", timeout=15)
        _attach("Ответ /auth/token", _format_response(resp))
        assert resp.status_code == 200, (
            f"auth/token: ожидали 200, получили {resp.status_code}"
        )
        token = resp.json().get("token") or resp.json().get("access_token")

    with allure.step("Зарегистрировать читателя (POST /readers)"):
        suffix = uuid.uuid4().hex[:8]
        reg = _raw_request(
            requests,
            "POST",
            f"{BASE_URL}/readers",
            json={"username": f"aiqa_{suffix}", "email": f"aiqa_{suffix}@example.com"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        _attach("Ответ /readers", _format_response(reg))
        assert reg.status_code == 201, (
            f"readers: ожидали 201, получили {reg.status_code}"
        )

    return {"token": token, "reader_id": reg.json()["id"]}
