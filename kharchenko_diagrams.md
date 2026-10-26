**Объектные и классовые диаграммы «Альфа Защиты»**

**Статус:** проектирование, 25 октября 2026 года. Диаграммы соответствуют [семи сценариям](kharchenko_userscripts.md) и [спецификации](SPECIFICATION.md). Приложение и изображённые классы пока не реализованы. Все экземпляры и значения в объектных схемах условные.

**Как читать схемы**

Для каждого сценария приведены объектная схема и UML-диаграмма классов, как требует Word-гайд (§4–5). UML показывает только нужную сценарию часть сущностей. Сервисы обозначают проектные роли и операции, а не обязанность создавать отдельный класс для каждой функции.

`Check` хранит исходные сведения, снимок результата и версию анализа; для аудио и скриншота также сохраняется извлечённый текст. Исходные медиа обрабатываются временно. `CheckFeedback` хранит редактируемый непроверенный отзыв отдельно от прогноза. История транзакций и история проверок — разные данные.

**S01. Аккаунт и история с другого устройства — F01, F08**

**Объектная схема**

```mermaid
flowchart LR
    browser1["browser01: первый браузер"] -->|вход| auth["auth01: сервис аккаунтов"]
    browser2["browser02: второй браузер"] -->|вход| auth
    auth -->|определяет владельца| user["u01: User; username=demo"]
    user -->|владеет| check["c01: Check; сохранённый результат"]
    history["history01: сервис истории"] -->|читает свои проверки u01| check
    browser2 -->|открывает историю| history
```

**UML классов**

```mermaid
classDiagram
    class User {
        +int id
        +str username
    }
    class Check {
        +int id
        +str type
        +datetime created_at
        +dict result
        +str analyzer_version
    }
    class AccountService {
        +register()
        +login()
        +logout()
    }
    class CheckService {
        +list_own_checks(user)
        +get_own_check(user, id)
    }
    User "1" --> "0..*" Check : владеет
    AccountService ..> User : авторизация
    CheckService ..> Check : чтение с проверкой владельца
```

**S02. Текстовое сообщение — F02**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|передаёт текст и контекст| service["checks02: сервис проверок"]
    service -->|создаёт| check["c02: Check; type=text"]
    user -->|владеет| check
    check -->|хранит| result["result02: оценка, объяснения, ограничения"]
```

**UML классов**

```mermaid
classDiagram
    class User {
        +int id
    }
    class Check {
        +int id
        +dict input_data
        +dict result
        +str analyzer_version
    }
    class AnalysisService {
        +analyze_text(text, context)
    }
    class CheckService {
        +save_result(user, input_data, result)
    }
    User "1" --> "0..*" Check : владеет
    CheckService ..> AnalysisService : получает оценку
    CheckService ..> Check : сохраняет
```

**S03. Ссылка — F03**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|URL, сообщение, ожидаемый сайт, контекст| service["checks03: сервис проверок"]
    service -->|создаёт| check["c03: Check; type=url"]
    user -->|владеет| check
    check -->|хранит| result["result03: признаки URL и рекомендации"]
```

**UML классов**

```mermaid
classDiagram
    class User {
        +int id
    }
    class Check {
        +int id
        +dict input_data
        +dict result
        +str analyzer_version
    }
    class AnalysisService {
        +analyze_url(url, message, expected_site, context)
    }
    class CheckService {
        +save_result(user, input_data, result)
    }
    User "1" --> "0..*" Check : владеет
    CheckService ..> AnalysisService : получает оценку
    CheckService ..> Check : сохраняет
```

**S04. Аудиозапись — F04**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|загружает| audio["audio04: временный аудиофайл"]
    audio -->|передан для обработки| service["checks04: сервис проверок"]
    user -->|участники и контекст| service
    service -->|создаёт| check["c04: Check; type=audio"]
    user -->|владеет| check
    check -->|хранит| text["text04: извлечённый текст"]
    check -->|хранит| result["result04: оценка и ограничения распознавания"]
```

**UML классов**

```mermaid
classDiagram
    class User {
        +int id
    }
    class Check {
        +int id
        +dict input_data
        +str extracted_text
        +dict result
        +str analyzer_version
    }
    class AnalysisService {
        +analyze_audio(file, participants, context)
    }
    class CheckService {
        +save_result(user, input_data, result)
    }
    User "1" --> "0..*" Check : владеет
    CheckService ..> AnalysisService : передаёт временный файл
    CheckService ..> Check : сохраняет текст и результат
```

**S05. Скриншот переписки — F05**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|загружает| image["image05: временное изображение"]
    image -->|передано для обработки| service["checks05: сервис проверок"]
    user -->|свои реплики и контекст| service
    service -->|создаёт| check["c05: Check; type=screenshot"]
    user -->|владеет| check
    check -->|хранит| text["text05: извлечённая переписка"]
    check -->|хранит| result["result05: оценка и ограничения чтения"]
```

**UML классов**

```mermaid
classDiagram
    class User {
        +int id
    }
    class Check {
        +int id
        +dict input_data
        +str extracted_text
        +dict result
        +str analyzer_version
    }
    class AnalysisService {
        +analyze_screenshot(file, own_messages, context)
    }
    class CheckService {
        +save_result(user, input_data, result)
    }
    User "1" --> "0..*" Check : владеет
    CheckService ..> AnalysisService : передаёт временный файл
    CheckService ..> Check : сохраняет текст и результат
```

**S06. CSV и предполагаемый перевод — F06, F07**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|владеет| import["i06: TransactionImport; текущая загрузка"]
    import -->|содержит| transaction["t06: Transaction; amount_minor=150000; RUB"]
    user -->|сумма, назначение, контекст| service["checks06: сервис проверок"]
    transaction -->|контекст при наличии истории| service
    service -->|создаёт| check["c06: Check; type=transfer"]
    user -->|владеет| check
    check -->|хранит| result["result06: оценка и снимок использованных сведений"]
```

Без загруженной истории экземпляры `i06` и `t06` отсутствуют; проверка выполняется по введённым сведениям с указанием ограничений. Ошибка нового импорта не меняет прежнюю историю.

**UML классов**

```mermaid
classDiagram
    class User {
        +int id
    }
    class TransactionImport {
        +int id
        +datetime created_at
        +int row_count
    }
    class Transaction {
        +int id
        +datetime occurred_at
        +int amount_minor
        +str currency
        +str direction
        +str counterparty_id
    }
    class Check {
        +int id
        +dict input_data
        +dict result
        +str analyzer_version
    }
    class TransactionService {
        +validate_csv(file)
        +replace_history(user, rows)
        +get_history(user)
    }
    class AnalysisService {
        +analyze_transfer(input_data, history)
    }
    class CheckService {
        +run_transfer_check(user, input_data)
    }
    User "1" --> "0..*" TransactionImport : владеет
    TransactionImport "1" --> "0..*" Transaction : содержит
    User "1" --> "0..*" Check : владеет
    TransactionService ..> TransactionImport : заменяет текущую историю
    TransactionService ..> Transaction : проверяет и сохраняет
    CheckService ..> TransactionService : получает доступную историю
    CheckService ..> AnalysisService : передаёт сведения и историю
    CheckService ..> Check : сохраняет результат
```

Сумма хранится в целых минимальных денежных единицах, для рублей — копейках. Назначение и обстоятельства предполагаемого перевода относятся к `Check.input_data`, а не добавляются в CSV только ради этой схемы. Сохранённый результат не зависит от последующей замены истории.

**S07. Обратная связь по проверке — F08, F09**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|владеет| check["c02: Check; исходный прогноз сохранён"]
    check -->|имеет| feedback["f07: CheckFeedback; outcome=unknown"]
    user -->|может исправить| feedback
    feedback -->|содержит| comment["comment07: необязательный комментарий"]
    user -->|отдельное добровольное решение| permission["permission07: разрешение для c02; выключено"]
    permission -->|относится только к| check
```

**UML классов**

```mermaid
classDiagram
    class User {
        +int id
    }
    class Check {
        +int id
        +dict result
        +str analyzer_version
    }
    class CheckFeedback {
        +int id
        +str outcome
        +str comment
        +datetime updated_at
    }
    class FeedbackService {
        +save_or_update(user, check, outcome, comment)
        +set_training_permission(user, check, allowed)
    }
    User "1" --> "0..*" Check : владеет
    Check "1" --> "0..1" CheckFeedback : имеет отзыв
    FeedbackService ..> Check : проверяет владельца
    FeedbackService ..> CheckFeedback : сохраняет и исправляет
```

`outcome` принимает `fraud`, `ordinary` или `unknown`: мошенничество, обычная ситуация или неизвестно. Отзыв остаётся непроверенным и не меняет исходный прогноз. Разрешение на использование данных конкретной проверки для подготовки будущего обучения по умолчанию выключено и учитывается отдельно от отзыва; способ его хранения определяется спецификацией. Наличие разрешения не означает проверенную разметку или выполненное обучение. Операции обучения в этот сценарий не входят.
