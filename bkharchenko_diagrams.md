# Диаграммы

Харченко Богдан Никитич, 11и2. Сначала объекты, затем классы; в конце — этапы работы.

**S01. Аккаунт — F01**

```mermaid
flowchart LR
    form["form: RegistrationForm"] --> user["user1: User"]
```

```mermaid
classDiagram
    class RegistrationForm {
        save()
    }
    class User {
        username
        check_password()
    }
    RegistrationForm --> User : создает
```

**S02. Текст — F02**

```mermaid
flowchart LR
    form["form: TextCheckForm"] --> analyzer["analyzer: Analyzer"]
    analyzer -->|результат| check["check1: Check"]
```

```mermaid
classDiagram
    class CheckForm {
        get_input_data()
    }
    class TextCheckForm {
        text
    }
    CheckForm <|-- TextCheckForm
```

**S03. Ссылка — F03**

```mermaid
flowchart LR
    form["form: URLCheckForm"] --> analyzer["analyzer: Analyzer"]
    analyzer -->|результат| check["check1: Check"]
```

```mermaid
classDiagram
    class CheckForm {
        get_input_data()
    }
    class URLCheckForm {
        url
        expected_site
    }
    CheckForm <|-- URLCheckForm
```

**S04. QR-код — F04**

```mermaid
flowchart LR
    form["form: QRCheckForm"] -->|ссылка из QR| analyzer["analyzer: Analyzer"]
    analyzer -->|результат| check["check1: Check"]
```

```mermaid
classDiagram
    class ScreenshotCheckForm {
        image
    }
    class QRCheckForm {
        clean_image()
        get_input_data()
    }
    ScreenshotCheckForm <|-- QRCheckForm
```

**S05. Скриншот — F05**

```mermaid
flowchart LR
    form["form: ScreenshotCheckForm"] --> ocr["ocr: ScreenshotModel"]
    ocr -->|переписка| analyzer["analyzer: Analyzer"]
    analyzer -->|результат| check["check1: Check"]
```

```mermaid
classDiagram
    class CheckForm {
        get_input_data()
    }
    class ScreenshotCheckForm {
        image
        clean_image()
    }
    CheckForm <|-- ScreenshotCheckForm
```

**S06. История операций — F07**

```mermaid
flowchart LR
    history["history1: TransactionImport"] --> operation["operation1: Transaction"]
```

```mermaid
classDiagram
    class User {
        username
    }
    class TransactionImport {
        row_count
    }
    class Transaction {
        occurred_at
        amount_kopecks
        recipient
    }
    User "1" --> "0..1" TransactionImport
    TransactionImport "1" --> "0..*" Transaction
```

**S07. Перевод — F06**

```mermaid
flowchart LR
    form["form: TransferCheckForm"] --> analyzer["analyzer: Analyzer"]
    operation["operation1: Transaction"] --> analyzer
    analyzer --> check["check1: Check"]
```

```mermaid
classDiagram
    class TransferCheckForm {
        amount
        recipient
        get_input_data()
    }
    class Analyzer {
        analyze()
    }
    class Transaction {
        amount_kopecks
        recipient
    }
    TransferCheckForm --> Analyzer : данные анкеты
    Transaction --> Analyzer : история
```

**S08. История проверок — F08**

```mermaid
flowchart LR
    user["user1: User"] --> check["check1: Check"]
    check --> result["страница результата"]
```

```mermaid
classDiagram
    class User {
        username
    }
    class Check {
        kind
        result
        created_at
    }
    User "1" --> "0..*" Check
```

**S09. Отзыв и согласие — F09**

```mermaid
flowchart LR
    check["check1: Check"] --> feedback["feedback1: CheckFeedback"]
    check --> consent["consent1: TrainingConsent"]
```

```mermaid
classDiagram
    class Check {
        result
    }
    class CheckFeedback {
        outcome
        comment
    }
    class TrainingConsent {
        granted
    }
    Check "1" --> "0..1" CheckFeedback
    Check "1" --> "0..1" TrainingConsent
```

## Этапы работы

Работа в текущем репозитории. Даты разработки взяты из истории Git; отдельный второй показ и отправка материалов в 2359 обозначены как план на 5 ноября.

```mermaid
gantt
    title Разработка и материалы для сдачи
    dateFormat YYYY-MM-DD
    axisFormat %d.%m
    section Разработка
    Аккаунты и хранение данных :done, backend, 2026-10-26, 2026-11-01
    Анализ и модели :done, models, 2026-11-01, 2026-11-06
    Интерфейс :done, frontend, 2026-11-04, 2026-11-06
    Подготовка запуска и VPS :done, server, 2026-11-01, 2026-11-06
    Тестирование :done, tests, 2026-10-26, 2026-11-06
    section Материалы Лицея
    Исходная заявка и сценарии :done, initial, 2026-10-26, 1d
    Итоговая заявка и сценарии :done, final, 2026-11-05, 1d
    Файл первого показа кода :done, first, 2026-11-05, 1d
    Видео второго показа — план :active, second, 2026-11-05, 1d
    Итоговое видео :done, video, 2026-11-05, 1d
    Отчет :done, report, 2026-11-05, 1d
    Сдача в GitHub и 2359 — план :milestone, deadline, 2026-11-05, 0d
```
