import cv2

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
            if load_media_model("screenshot", settings.ANALYZER_DIR) is None:
                raise ValueError("Не настроено распознавание скриншотов")
            cv2.QRCodeDetector()
        except Exception as error:
            raise CommandError("Комплект моделей не готов: " + str(error)) from error
        self.stdout.write(self.style.SUCCESS("Текст URL QR и скриншоты готовы к запуску"))
