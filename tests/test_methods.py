"""Покрытие HTTP-методов и жизненного цикла ресурсов (HEAD/OPTIONS/PUT/PATCH/DELETE).

Содержит две намеренные закладки:
- test_books_list_status_created — очевидная ошибка теста (ждёт 201 на GET списка);
- test_slow_endpoint_within_budget — спорный таймаут (инфраструктура ↔ код теста).
"""

import uuid

import allure

from conftest import BASE_URL, api_request, check


def _isbn() -> str:
    """Валидный ISBN: ровно 13 цифр."""
    return f"978{uuid.uuid4().int % 10**10:010d}"


# --------------------------------------------------------------------------- #
# Служебные HTTP-методы                                                       #
# --------------------------------------------------------------------------- #
@allure.feature("HTTP-методы")
@allure.story("HEAD — проверка доступности без тела")
@allure.severity(allure.severity_level.MINOR)
def test_head_books(api):
    resp = api_request(
        api, "HEAD", f"{BASE_URL}/books", title="HEAD /books", timeout=15
    )
    with check("Статус 200, тело пустое"):
        assert resp.status_code == 200
        assert resp.text == ""


@allure.feature("HTTP-методы")
@allure.story("OPTIONS — список доступных методов")
@allure.severity(allure.severity_level.MINOR)
def test_options_books(api):
    resp = api_request(
        api, "OPTIONS", f"{BASE_URL}/books", title="OPTIONS /books", timeout=15
    )
    with check("Статус 200 и заголовок Allow с методами"):
        assert resp.status_code == 200
        assert "GET" in resp.headers.get("Allow", "")


# --------------------------------------------------------------------------- #
# Жизненный цикл книги: POST → PATCH → GET → DELETE                            #
# --------------------------------------------------------------------------- #
@allure.feature("Каталог книг")
@allure.story("Жизненный цикл книги (POST/PATCH/DELETE)")
@allure.severity(allure.severity_level.CRITICAL)
def test_book_full_lifecycle(api):
    payload = {
        "title": f"Жизненный цикл {uuid.uuid4().hex[:8]}",
        "isbn": _isbn(),
        "price": 500,
        "stock": 3,
    }

    with allure.step("Создать книгу (POST)"):
        created = api_request(
            api,
            "POST",
            f"{BASE_URL}/books",
            title="POST /books",
            json=payload,
            timeout=15,
        )
        assert created.status_code == 201, created.text[:300]
        book_id = created.json()["id"]

    with allure.step("Обновить цену (PATCH)"):
        patched = api_request(
            api,
            "PATCH",
            f"{BASE_URL}/books/{book_id}",
            title="PATCH цену",
            json={"price": 750},
            timeout=15,
        )
        assert patched.status_code == 200
        assert float(patched.json()["price"]) == 750

    with allure.step("Проверить изменение (GET)"):
        got = api_request(
            api, "GET", f"{BASE_URL}/books/{book_id}", title="GET книгу", timeout=15
        )
        assert got.status_code == 200
        assert float(got.json()["price"]) == 750

    with allure.step("Удалить книгу (DELETE)"):
        deleted = api_request(
            api,
            "DELETE",
            f"{BASE_URL}/books/{book_id}",
            title="DELETE книгу",
            timeout=15,
        )
        assert deleted.status_code == 204


@allure.feature("Каталог книг")
@allure.story("Изменение остатка на складе (PATCH stock)")
@allure.severity(allure.severity_level.NORMAL)
def test_book_stock_increment(api):
    payload = {
        "title": f"Склад {uuid.uuid4().hex[:8]}",
        "isbn": _isbn(),
        "price": 300,
        "stock": 2,
    }
    with allure.step("Создать книгу"):
        created = api_request(
            api,
            "POST",
            f"{BASE_URL}/books",
            title="POST /books",
            json=payload,
            timeout=15,
        )
        assert created.status_code == 201
        book_id = created.json()["id"]
    with allure.step("Пополнить остаток на +5 (PATCH stock)"):
        stocked = api_request(
            api,
            "PATCH",
            f"{BASE_URL}/books/{book_id}/stock",
            title="PATCH stock +5",
            json={"quantity_delta": 5},
            timeout=15,
        )
        assert stocked.status_code == 200
        assert stocked.json()["stock"] == 7
    api_request(
        api, "DELETE", f"{BASE_URL}/books/{book_id}", title="Удалить книгу", timeout=15
    )


@allure.feature("Каталог книг")
@allure.story("Несуществующая книга")
@allure.severity(allure.severity_level.NORMAL)
def test_get_missing_book_404(api):
    resp = api_request(
        api,
        "GET",
        f"{BASE_URL}/books/999999999",
        title="GET несуществующую книгу",
        timeout=15,
    )
    with check("Статус 404"):
        assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Жизненный цикл автора                                                        #
# --------------------------------------------------------------------------- #
@allure.feature("Справочник авторов")
@allure.story("Жизненный цикл автора (POST/PATCH/DELETE)")
@allure.severity(allure.severity_level.NORMAL)
def test_author_lifecycle(api):
    with allure.step("Создать автора (POST)"):
        created = api_request(
            api,
            "POST",
            f"{BASE_URL}/authors",
            title="POST /authors",
            json={"name": f"Автор {uuid.uuid4().hex[:8]}"},
            timeout=15,
        )
        assert created.status_code == 201
        author_id = created.json()["id"]
    with allure.step("Обновить страну (PATCH)"):
        patched = api_request(
            api,
            "PATCH",
            f"{BASE_URL}/authors/{author_id}",
            title="PATCH автора",
            json={"country": "Франция"},
            timeout=15,
        )
        assert patched.status_code == 200
    with allure.step("Удалить автора (DELETE)"):
        deleted = api_request(
            api,
            "DELETE",
            f"{BASE_URL}/authors/{author_id}",
            title="DELETE автора",
            timeout=15,
        )
        assert deleted.status_code == 204


# --------------------------------------------------------------------------- #
# Закладка 1: очевидная ошибка теста (код_автотеста)                           #
# --------------------------------------------------------------------------- #
@allure.feature("Каталог книг")
@allure.story("Статус списка книг")
@allure.severity(allure.severity_level.NORMAL)
def test_books_list_status_created(api):
    """Список книг должен создаваться со статусом 201."""
    resp = api_request(api, "GET", f"{BASE_URL}/books", title="GET /books", timeout=15)
    with check("Статус 201 Created"):
        assert resp.status_code == 201


# --------------------------------------------------------------------------- #
# Закладка 2: спорный таймаут (needs_human)                                    #
# --------------------------------------------------------------------------- #
@allure.feature("Сервисные проверки")
@allure.story("Время ответа медленного эндпоинта")
@allure.severity(allure.severity_level.MINOR)
def test_slow_endpoint_within_budget(api):
    """Сервисный эндпоинт должен уложиться в 1 секунду."""
    resp = api_request(
        api,
        "GET",
        f"{BASE_URL}/practice/slow",
        title="GET /practice/slow (timeout=1)",
        timeout=1,
    )
    with check("Статус 200 в пределах бюджета"):
        assert resp.status_code == 200
