"""Тесты каталога книг."""

import uuid

import allure
import pytest

from conftest import BASE_URL, api_request, check


@allure.feature("Каталог книг")
@allure.story("Список книг")
@allure.severity(allure.severity_level.NORMAL)
def test_books_list_ok(api):
    resp = api_request(
        api, "GET", f"{BASE_URL}/books", title="Запросить список книг", timeout=15
    )
    with check("Статус 200 и структура пагинации"):
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["total"], int)


@allure.feature("Каталог книг")
@allure.story("Книга по идентификатору")
@allure.severity(allure.severity_level.NORMAL)
def test_book_by_id(api):
    items = api_request(
        api,
        "GET",
        f"{BASE_URL}/books",
        title="Взять первую книгу из списка",
        timeout=15,
    ).json()["items"]
    if not items:
        pytest.skip("каталог пуст")
    book_id = items[0]["id"]
    resp = api_request(
        api,
        "GET",
        f"{BASE_URL}/books/{book_id}",
        title=f"Запросить книгу id={book_id}",
        timeout=15,
    )
    with check("Статус 200 и совпадение id"):
        assert resp.status_code == 200
        assert resp.json()["id"] == book_id


@allure.feature("Каталог книг")
@allure.story("Поля карточки книги")
@allure.severity(allure.severity_level.NORMAL)
def test_book_fields(api):
    """У каждой книги есть базовые поля карточки."""
    items = api_request(
        api, "GET", f"{BASE_URL}/books", title="Запросить список книг", timeout=15
    ).json()["items"]
    if not items:
        pytest.skip("каталог пуст")
    book = items[0]
    with check("В карточке есть обязательные поля"):
        for field in ("id", "name", "isbn", "price"):
            assert field in book, f"в карточке книги нет поля '{field}': {sorted(book)}"


@allure.feature("Каталог книг")
@allure.story("Создание книги")
@allure.severity(allure.severity_level.CRITICAL)
def test_create_book_minimal(api):
    """Создание книги с минимальным набором полей."""
    payload = {
        "title": f"Автотест {uuid.uuid4().hex[:8]}",
        "isbn": f"978-{uuid.uuid4().int % 10**9:09d}",
        "price": -10,
        "stock": 1,
    }
    resp = api_request(
        api,
        "POST",
        f"{BASE_URL}/books",
        title="Создать книгу",
        json=payload,
        timeout=15,
    )
    with check("Статус 201 (книга создана)"):
        assert resp.status_code == 201, (
            f"ожидали 201, получили {resp.status_code}: {resp.text[:300]}"
        )
    api_request(
        api,
        "DELETE",
        f"{BASE_URL}/books/{resp.json()['id']}",
        title="Удалить созданную книгу",
        timeout=15,
    )


@allure.feature("Каталог книг")
@allure.story("Время ответа списка")
@allure.severity(allure.severity_level.MINOR)
def test_books_list_response_time(api):
    """Список книг отвечает быстро."""
    resp = api_request(
        api,
        "GET",
        f"{BASE_URL}/books",
        title="Запросить список книг (timeout=0.01)",
        timeout=0.01,
    )
    with check("Статус 200"):
        assert resp.status_code == 200
