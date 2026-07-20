"""Тесты документированных контрактов раздела /practice.

Ожидаемое поведение каждого эндпоинта описано в его контракте
в Swagger: https://qahacking.up.railway.app/docs
"""

import allure

from conftest import BASE_URL, api_request, check


@allure.feature("Контракты /practice")
@allure.story("Конвертация температуры")
@allure.severity(allure.severity_level.CRITICAL)
def test_temperature_conversion(api):
    """Контракт: F = C x 9/5 + 32, значит 100 C -> 212 F."""
    resp = api_request(
        api,
        "GET",
        f"{BASE_URL}/practice/temperature",
        title="Перевести 100 °C в °F",
        params={"celsius": 100},
        timeout=15,
    )
    with check("Статус 200"):
        assert resp.status_code == 200
    with check("В ответе есть 212 °F (по контракту)"):
        body = resp.json()
        assert any(value == 212 for value in body.values()), (
            f"212 F не найдено в ответе: {body}"
        )


@allure.feature("Контракты /practice")
@allure.story("Перевод средств")
@allure.severity(allure.severity_level.CRITICAL)
def test_transfer_over_balance_rejected(api):
    """Контракт: amount > from_balance -> 422 (баланс не должен уходить в минус)."""
    resp = api_request(
        api,
        "POST",
        f"{BASE_URL}/practice/transfer",
        title="Перевести сумму сверх баланса",
        json={"from_balance": 100, "amount": 5000},
        timeout=15,
    )
    with check("Перевод сверх баланса отклонён (422)"):
        assert resp.status_code == 422, (
            f"перевод сверх баланса должен отклоняться: "
            f"получили {resp.status_code}, тело {resp.text[:200]}"
        )


@allure.feature("Контракты /practice")
@allure.story("Публичный профиль без приватных полей")
@allure.severity(allure.severity_level.CRITICAL)
def test_public_profile_no_private_fields(api):
    """Контракт: публичный профиль отдает только id и name."""
    resp = api_request(
        api,
        "GET",
        f"{BASE_URL}/practice/user/1",
        title="Запросить публичный профиль",
        timeout=15,
    )
    with check("Статус 200"):
        assert resp.status_code == 200
    with check("В профиле нет приватных полей"):
        extra = set(resp.json()) - {"id", "name"}
        assert not extra, f"в публичном профиле лишние поля: {sorted(extra)}"
