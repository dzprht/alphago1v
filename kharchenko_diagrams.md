**Диаграммы**

Для каждого сценария сначала объекты, затем классы. Check хранится в БД только после входа; у гостя это объект в памяти.

**S01. Аккаунт и история**

```mermaid
flowchart LR
    browser["другой браузер"] -->|вход| user["user1: User"]
    user --> check["check1: Check"]
```

```mermaid
classDiagram
    class User {
        username
        check_password()
    }
    class Check {
        result
    }
    User "1" --> "0..*" Check
```

**S02. Текст**

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

**S03. Ссылка**

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

**S04. QR-код**

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

**S05. Скриншот**

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

**S06. История операций и перевод**

```mermaid
flowchart LR
    history["history1: TransactionImport"] --> operation["operation1: Transaction"]
    operation --> analyzer["analyzer: Analyzer"]
    form["form: TransferCheckForm"] --> analyzer
    analyzer -->|результат| check["check1: Check"]
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

**S07. Отзыв и согласие**

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
