from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from analysis.runtime import load_analyzer, load_media_model


class Command(BaseCommand):
    help = "Проверить полный локальный комплект моделей до запуска сайта"

    def handle(self, *args, **options):
        # загружает все модели и проверяет готовность пяти режимов
        try:
            analyzer = load_analyzer(settings.ANALYZER_DIR)
            if analyzer is None:
                raise ValueError("Не указан ANALYZER_DIR")
            for kind in ("audio", "screenshot"):
                model = load_media_model(kind, settings.ANALYZER_DIR)
                if model is None:
                    raise ValueError("Не настроено распознавание " + kind)
                if kind == "audio" and model.diarizer is None:
                    raise ValueError("Не подключено разделение голосов")
        except Exception as error:
            raise CommandError("Комплект моделей не готов: " + str(error)) from error
        self.stdout.write(self.style.SUCCESS("Текст URL аудио и скриншоты готовы к запуску"))
