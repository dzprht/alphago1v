import os
import sys

from django.core.management import execute_from_command_line


def main():
    # запускает команды django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings") # если путь уже задан с наружи, то он сохраняется
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
