"""Тесты справочника авторов."""

import allure
import pytest

from conftest import BASE_URL, api_request, check


@allure.feature("Справочник авторов")
@allure.story("Список авторов")
@allure.severity(allure.severity_level.NORMAL)
def test_authors_list_ok(api):
    resp = api_request(
        api, "GET", f"{BASE_URL}/authors", title="Запросить список авторов", timeout=15
    )
    with check("Статус 200 и наличие items"):
        assert resp.status_code == 200
        assert "items" in resp.json()


@allure.feature("Справочник авторов")
@allure.story("Поля карточки автора")
@allure.severity(allure.severity_level.NORMAL)
def test_author_fields(api):
    items = api_request(
        api, "GET", f"{BASE_URL}/authors", title="Запросить список авторов", timeout=15
    ).json()["items"]
    if not items:
        pytest.skip("справочник пуст")
    author = items[0]
    with check("В карточке есть id и name"):
        for field in ("id", "name"):
            assert field in author
