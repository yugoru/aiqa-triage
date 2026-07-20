# Триаж прогона

Красных тестов: **14**, причин после группировки: **10**

## продукт — причин: 3

### Сервис возвращает fahrenheit=180 вместо 212 по формуле F = C × 9/5 + 32, нарушая контракт.
- тесты (1): tests.test_practice_contracts::test_temperature_conversion
- уверенность: 1.00
- улики: Ответ сервиса: {'celsius': 100.0, 'fahrenheit': 180.0}. Ожидалось: fahrenheit=212 согласно контракту (F = C × 9/5 + 32).
- действие: Исправить реализацию эндпоинта /practice/temperature, чтобы корректно применялась формула перевода Цельсия в Фаренгейты: F = C × 9/5 + 32.

### Сервис возвращает 200 и отрицательный баланс при попытке перевести сумму, превышающую баланс, хотя контракт требует 422.
- тесты (1): tests.test_practice_contracts::test_transfer_over_balance_rejected
- уверенность: 1.00
- улики: Запрос: POST /practice/transfer с json={'from_balance': 100, 'amount': 5000}. Ответ: 200, тело {'new_balance': -4900.0}. Тест ожидал 422.
- действие: Исправить серверную логику: при amount > from_balance возвращать 422 и не выполнять перевод.

### API возвращает приватные поля, несмотря на контракт, требующий только id и name
- тесты (1): tests.test_practice_contracts::test_public_profile_no_private_fields
- уверенность: 0.95
- улики: Ответ содержит поля email, password_hash, role, которых не должно быть согласно контракту
- действие: Исправить API, чтобы при запросе публичного профиля (GET /practice/user/{user_id}) возвращались только id и name, скрывая приватные поля

## код_автотеста — причин: 4

### Тест ожидает поле 'name' в ответе GET /books, но API возвращает 'title'. Поле 'name' не предусмотрено контрактом — в OpenAPI обязательные поля POST: title, isbn, price; в ответе коллекции присутствует 'title', а не 'name'.
- тесты (1): tests.test_books::test_book_fields
- уверенность: 1.00
- улики: assert 'name' in book -> AssertionError: в карточке книги нет поля 'name'. Фактические поля: ['author_id', 'created_at', 'description', 'genre_id', 'id', 'isbn', 'price', 'published_year', 'stock', 'title', 'updated_at']. Контракт не содержит 'name'.
- действие: Заменить 'name' на 'title' в списке обязательных полей в тесте (строка for field in ('id', 'name', 'isbn', 'price')).

### Тест отправляет ISBN с дефисом (978-582304648) вместо 13 цифр без разделителей, а также price = -10, нарушая контракт (price >= 0). Сервер корректно возвращает 422.
- тесты (1): tests.test_books::test_create_book_minimal
- уверенность: 1.00
- улики: Ошибка валидации: "Value error, ISBN должен содержать только цифры (13 штук)" для isbn и "Input should be greater than or equal to 0" для price. В тесте payload содержит "isbn": f"978-{uuid.uuid4().int % 10**9:09d}" (с дефисом) и "price": -10.
- действие: Исправить isbn на 13 цифр без дефиса (например, f"978{uuid.uuid4().int % 10**9:09d}" или f"{uuid.uuid4().int % 10**13:013d}") и установить price >= 0 (например, 0 или положительное число).

### Тест использует нереалистично малый таймаут (0.01 с) для запроса GET /books, что приводит к ReadTimeout. Контракт не гарантирует время ответа, поэтому проблема в тесте. ⚠ **needs_human**
- тесты (2): tests.test_books::test_books_list_response_time, tests.test_methods::test_slow_endpoint_within_budget
- уверенность: 0.40
- улики: Сообщение об ошибке: Read timed out. (read timeout=0.01). Код теста: timeout=0.01 в вызове api_request.
- действие: Увеличить значение timeout в тесте до разумного (например, 5 или 10 секунд) или удалить явное указание таймаута, чтобы использовать дефолтный.

### Тест ожидает статус 201 при GET /books, но по контракту метод GET безопасный и должен возвращать 200 OK. 201 возвращается только при POST /books.
- тесты (1): tests.test_methods::test_books_list_status_created
- уверенность: 0.99
- улики: Трейсбек: assert 200 == 201; контракт: GET /books — безопасный, 200 OK.
- действие: Изменить ожидаемый статус в тесте на 200 или переписать тест для проверки POST /books с созданием новой книги.

## инфраструктура — причин: 1

### Не удаётся разрешить DNS-имя 'qahacking.railway.internal'. Хост либо не существует, либо не доступен из текущего сетевого окружения.
- тесты (1): tests.test_genres::test_genres_list_ok
- уверенность: 0.95
- улики: NameResolutionError: Failed to resolve 'qahacking.railway.internal' ([Errno 11001] getaddrinfo failed)
- действие: Проверить доступность хоста qahacking.railway.internal через DNS и сеть. Возможно, нужно заменить адрес на корректный для тестового окружения.

## инструментарий — причин: 1

### Фикстура `reader` в conftest.py ожидает статус 200 от POST /auth/token, но по контракту API возвращает 201.
- тесты (4): tests.test_reader_flow::test_reader_profile, tests.test_reader_flow::test_reader_orders_empty, tests.test_reader_flow::test_reader_reviews_empty, tests.test_reader_flow::test_reader_wishlist_empty
- уверенность: 1.00
- улики: AssertionError: auth/token: ожидали 200, получили 201. Контракт OpenAPI для POST /auth/token указывает 201 Created.
- действие: Обновить фикстуру `reader` в conftest.py: заменить `assert resp.status_code == 200` на `assert resp.status_code == 201`.

## flaky — причин: 1

### Эндпоинт /practice/unstable по контракту является флаки-эндпоинтом: с вероятностью ~30% возвращает 500 вместо 200. Текущий запрос попал в окно падения. ⚠ **needs_human**
- тесты (1): tests.test_service::test_service_stability
- уверенность: 0.90
- улики: OpenAPI контракт указывает: 'отвечает 200 {"status": "ok"} — но с вероятностью ~30% падает с 500. Полигон для флаки-тестов'. Тест получил 500, что соответствует задокументированной нестабильности.
- действие: Рассмотреть стабилизацию эндпоинта или модифицировать тест: игнорировать флаки-падения (например, повторить запрос) либо помечать тест как ожидаемо нестабильный (pytest.mark.flaky).
