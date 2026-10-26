**Объектные и классовые диаграммы «Альфа Защиты»**

**Статус:** проектирование с гостевым режимом, 26 октября 2026 года. Диаграммы соответствуют [семи сценариям](kharchenko_userscripts.md) и [спецификации](SPECIFICATION.md). Основа Django, логика аккаунтов, хранилище и серверные формы проверок подготовлены; анализ и HTTP-обработчики пока не реализованы. Все экземпляры и значения в объектных схемах условные.

**Как читать схемы**

Для каждого сценария приведены объектная схема и UML-диаграмма классов, как требует Word-гайд (§4–5). UML показывает только нужную сценарию часть сущностей. Сервисы обозначают проектные роли и операции, а не обязанность создавать отдельный класс для каждой функции.

`Check` содержит исходные сведения, снимок результата и версию анализа; для аудио и скриншота также извлечённый текст. Для вошедшего пользователя эти сведения сохраняются в БД, для гостя доступны только на текущем экране и могут потеряться после закрытия или перезагрузки страницы. Исходные медиа обрабатываются временно. `CheckFeedback` хранит редактируемый непроверенный отзыв к сохранённой проверке отдельно от прогноза. История транзакций и история проверок — разные данные; CSV, история и отзывы доступны только после входа.

**Гостевой объект и сохранённая запись**

`create_check(user, kind, input_data)` возвращает гостю несохранённый объект `Check`: `user_id=None`, `pk=None`, `created_at` — текущее время. Общая валидация сохраняется; для гостя `full_clean` исключает только поле `user`. В БД `Check.user` остаётся обязательным (`NOT NULL`). Связь `User → Check` на всех диаграммах относится только к сохранённым записям. `Guest` обозначает посетителя без входа, а не новую таблицу или аккаунт. Создание подготавливает проверку; отдельная `finish_check` однократно фиксирует результат или ошибку, сохраняя в БД только итог владельца. Сам анализ пока не подключён.

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
    guest["g02: Guest"] -->|передаёт текст и контекст| service
    service -->|для вошедшего сохраняет| check["c02: Check; type=text; сохранён"]
    service -->|для гостя без записи в БД| guestcheck["gc02: Check; user_id=None; pk=None"]
    guestcheck -->|показывает| screen["screen02: только текущий экран"]
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
        +create_check(user, kind, input_data)
    }
    class Guest
    User "1" --> "0..*" Check : владеет сохранёнными
    Guest ..> CheckService : без входа
    CheckService ..> AnalysisService : получает оценку
    CheckService ..> Check : сохраняет только для вошедшего
```

**S03. Ссылка — F03**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|URL, сообщение, ожидаемый сайт, контекст| service["checks03: сервис проверок"]
    guest["g03: Guest"] -->|URL и необязательный контекст| service
    service -->|для вошедшего сохраняет| check["c03: Check; type=url; сохранён"]
    service -->|для гостя без записи в БД| guestcheck["gc03: Check; user_id=None; pk=None"]
    guestcheck -->|показывает| screen["screen03: только текущий экран"]
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
        +create_check(user, kind, input_data)
    }
    class Guest
    User "1" --> "0..*" Check : владеет сохранёнными
    Guest ..> CheckService : без входа
    CheckService ..> AnalysisService : получает оценку
    CheckService ..> Check : сохраняет только для вошедшего
```

**S04. Аудиозапись — F04**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|загружает| audio["audio04: временный аудиофайл"]
    guest["g04: Guest"] -->|загружает| audio
    audio -->|передан для обработки| service["checks04: сервис проверок"]
    user -->|участники и контекст| service
    guest -->|участники и контекст| service
    service -->|для вошедшего сохраняет| check["c04: Check; type=audio; сохранён"]
    service -->|для гостя без записи в БД| guestcheck["gc04: Check; user_id=None; pk=None; извлечённый текст"]
    guestcheck -->|показывает| screen["screen04: только текущий экран"]
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
        +create_check(user, kind, input_data)
    }
    class Guest
    User "1" --> "0..*" Check : владеет сохранёнными
    Guest ..> CheckService : без входа
    CheckService ..> AnalysisService : передаёт временный файл
    CheckService ..> Check : сохраняет только для вошедшего
```

**S05. Скриншот переписки — F05**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|загружает| image["image05: временное изображение"]
    guest["g05: Guest"] -->|загружает| image
    image -->|передано для обработки| service["checks05: сервис проверок"]
    user -->|свои реплики и контекст| service
    guest -->|свои реплики и контекст| service
    service -->|для вошедшего сохраняет| check["c05: Check; type=screenshot; сохранён"]
    service -->|для гостя без записи в БД| guestcheck["gc05: Check; user_id=None; pk=None; извлечённый текст"]
    guestcheck -->|показывает| screen["screen05: только текущий экран"]
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
        +create_check(user, kind, input_data)
    }
    class Guest
    User "1" --> "0..*" Check : владеет сохранёнными
    Guest ..> CheckService : без входа
    CheckService ..> AnalysisService : передаёт временный файл
    CheckService ..> Check : сохраняет только для вошедшего
```

**S06. CSV и предполагаемый перевод — F06, F07**

**Объектная схема**

```mermaid
flowchart LR
    user["u01: User"] -->|владеет| import["i06: TransactionImport; текущая загрузка"]
    import -->|содержит| transaction["t06: Transaction; amount_minor=150000; RUB"]
    user -->|сумма, назначение, контекст| service["checks06: сервис проверок"]
    guest["g06: Guest"] -->|только введённые сведения| service
    transaction -->|контекст только для вошедшего владельца| service
    service -->|для вошедшего сохраняет| check["c06: Check; type=transfer; сохранён"]
    service -->|для гостя без истории и записи в БД| guestcheck["gc06: Check; user_id=None; pk=None"]
    guestcheck -->|показывает| screen["screen06: только текущий экран"]
    user -->|владеет| check
    check -->|хранит| result["result06: оценка и снимок использованных сведений"]
```

Для гостя экземпляры `i06` и `t06` недоступны; проверка выполняется только по введённым сведениям с указанием ограничений. Вошедший пользователь также может выполнить проверку без истории. Ошибка нового импорта не меняет прежнюю историю.

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
        +create_check(user, kind, input_data)
    }
    class Guest
    User "1" --> "0..*" TransactionImport : владеет
    TransactionImport "1" --> "0..*" Transaction : содержит
    User "1" --> "0..*" Check : владеет сохранёнными
    Guest ..> CheckService : без CSV и истории
    TransactionService ..> TransactionImport : заменяет текущую историю
    TransactionService ..> Transaction : проверяет и сохраняет
    CheckService ..> TransactionService : только для вошедшего владельца
    CheckService ..> AnalysisService : передаёт сведения и историю
    CheckService ..> Check : сохраняет только для вошедшего
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

Сценарий доступен только после входа и только для своей сохранённой проверки. У гостевого несохранённого `Check` отзыва нет; доступ к чужой сохранённой проверке закрыт.

`outcome` принимает `fraud`, `ordinary` или `unknown`: мошенничество, обычная ситуация или неизвестно. Отзыв остаётся непроверенным и не меняет исходный прогноз. Разрешение на использование данных конкретной проверки для подготовки будущего обучения по умолчанию выключено и учитывается отдельно от отзыва; способ его хранения определяется спецификацией. Наличие разрешения не означает проверенную разметку или выполненное обучение. Операции обучения в этот сценарий не входят.
