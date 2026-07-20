"""Тесты личного кабинета читателя.

Все четыре теста зависят от фикстуры ``reader`` (см. conftest.py). Если фикстура
падает на setup, тесты дают ERROR — и это один инцидент, а не четыре разных.
"""

import allure
import requests

from conftest import BASE_URL, api_request, check


def _headers(reader):
    return {"Authorization": f"Bearer {reader['token']}"}


@allure.feature("Кабинет читателя")
@allure.story("Профиль")
@allure.severity(allure.severity_level.NORMAL)
def test_reader_profile(reader):
    resp = api_request(
        requests,
        "GET",
        f"{BASE_URL}/readers/{reader['reader_id']}",
        title="Запросить профиль читателя",
        headers=_headers(reader),
        timeout=15,
    )
    with check("Статус 200 и совпадение id"):
        assert resp.status_code == 200
        assert resp.json()["id"] == reader["reader_id"]


@allure.feature("Кабинет читателя")
@allure.story("Заказы")
@allure.severity(allure.severity_level.NORMAL)
def test_reader_orders_empty(reader):
    resp = api_request(
        requests,
        "GET",
        f"{BASE_URL}/readers/{reader['reader_id']}/orders",
        title="Запросить заказы читателя",
        headers=_headers(reader),
        timeout=15,
    )
    with check("Статус 200"):
        assert resp.status_code == 200


@allure.feature("Кабинет читателя")
@allure.story("Рецензии")
@allure.severity(allure.severity_level.NORMAL)
def test_reader_reviews_empty(reader):
    resp = api_request(
        requests,
        "GET",
        f"{BASE_URL}/readers/{reader['reader_id']}/reviews",
        title="Запросить рецензии читателя",
        headers=_headers(reader),
        timeout=15,
    )
    with check("Статус 200"):
        assert resp.status_code == 200


@allure.feature("Кабинет читателя")
@allure.story("Список желаний")
@allure.severity(allure.severity_level.NORMAL)
def test_reader_wishlist_empty(reader):
    resp = api_request(
        requests,
        "GET",
        f"{BASE_URL}/readers/{reader['reader_id']}/wishlist",
        title="Запросить список желаний читателя",
        headers=_headers(reader),
        timeout=15,
    )
    with check("Статус 200"):
        assert resp.status_code == 200
