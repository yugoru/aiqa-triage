"""Сервисные проверки."""

import allure
import pytest

from conftest import BASE_URL, api_request, check


@allure.feature("Сервисные проверки")
@allure.story("Чайник (RFC 2324)")
@allure.severity(allure.severity_level.MINOR)
def test_teapot_is_teapot(api):
    """Документированное поведение: /teapot всегда отвечает 418 (RFC 2324)."""
    resp = api_request(
        api, "GET", f"{BASE_URL}/teapot", title="Запросить /teapot", timeout=15
    )
    with check("Статус 418 (I'm a teapot)"):
        assert resp.status_code == 418


@allure.feature("Сервисные проверки")
@allure.story("Стабильность сервиса")
@allure.severity(allure.severity_level.CRITICAL)
def test_service_stability(api):
    """Проверка стабильности сервисного эндпоинта."""
    resp = api_request(
        api,
        "GET",
        f"{BASE_URL}/practice/unstable",
        title="Запросить /practice/unstable",
        timeout=15,
    )
    if resp.status_code == 404:
        pytest.skip(
            "эндпоинт /practice/unstable ещё не задеплоен (см. челленджер qachallenger)"
        )
    with check("Статус 200 (сервис стабилен)"):
        assert resp.status_code == 200, (
            f"сервис нестабилен: {resp.status_code} {resp.text[:200]}"
        )
