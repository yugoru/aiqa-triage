"""Тесты справочника жанров."""

import os

import allure
import requests

from conftest import api_request, check

# сервис жанров исторически ходил через отдельный внутренний адрес
GENRES_URL = os.getenv("GENRES_API_URL", "https://qahacking.railway.internal")


@allure.feature("Справочник жанров")
@allure.story("Список жанров")
@allure.severity(allure.severity_level.NORMAL)
def test_genres_list_ok(api):
    token = api.headers.get("Authorization", "")
    resp = api_request(
        requests,
        "GET",
        f"{GENRES_URL}/genres",
        title="Запросить список жанров (внешний хост)",
        headers={"Authorization": token},
        timeout=10,
    )
    with check("Статус 200 и наличие items"):
        assert resp.status_code == 200
        assert "items" in resp.json()
