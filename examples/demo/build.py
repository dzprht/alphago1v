import argparse
import platform
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


FOLDER = Path(__file__).resolve().parent
DIALOGUES = {
    "warning": [
        ("left", ["Это служба безопасности банка.", "Срочно переведите деньги", "на безопасный счет.", "Никому об этом не говорите."]),
        ("right", ["Зачем переводить деньги?"]),
        ("left", ["Так мы защитим ваши средства.", "Назовите код из СМС."]),
    ],
    "ordinary": [
        ("left", ["Привет! Завтра идем", "в библиотеку после уроков?"]),
        ("right", ["Да, встретимся у входа."]),
        ("left", ["Хорошо, возьму учебник."]),
    ],
}
VOICE_MESSAGES = {
    "warning": "Здравствуйте, это служба безопасности банка. Чтобы защитить деньги, срочно переведите их на безопасный счет. Никому не говорите о нашем разговоре. Назовите код из СМС.",
    "ordinary": "Привет! Завтра встречаемся в библиотеке после уроков. Я возьму учебник. До встречи!",
}


def draw_chat(name, font_path):
    # рисует учебный пример переписки без личных данных
    image = Image.new("RGB", (720, 600), "#e8edf2")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), 25)
    y = 42
    for side, lines in DIALOGUES[name]:
        width = 500 if side == "left" else 440
        left = 28 if side == "left" else 720 - width - 28
        height = len(lines) * 38 + 32
        draw.rounded_rectangle((left, y, left + width, y + height), radius=18,
                               fill="#ffffff" if side == "left" else "#c7e9cf")
        for index, line in enumerate(lines):
            draw.text((left + 18, y + 15 + index * 38), line, font=font, fill="#111111")
        y += height + 42
    image.save(FOLDER / f"{name}.png", optimize=True)
    text = "\n\n".join(("Собеседник" if side == "left" else "Я") + ": " + " ".join(lines)
                       for side, lines in DIALOGUES[name])
    (FOLDER / f"{name}-chat.txt").write_text(text + "\n", encoding="utf-8")


def make_audio(name):
    # озвучивает учебную просьбу установленным голосом macOS
    source = FOLDER / f"{name}-voice.txt"
    source.write_text(VOICE_MESSAGES[name] + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory() as directory:
        aiff = Path(directory) / "voice.aiff"
        subprocess.run(["say", "-v", "Milena", "-r", "145", "-f", str(source), "-o", str(aiff)], check=True)
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1",
                        str(aiff), str(FOLDER / f"{name}.wav")], check=True)


def main():
    # создает примеры для ручной демонстрации сайта
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", type=Path, default=Path("/System/Library/Fonts/Supplemental/Arial.ttf"))
    parser.add_argument("--audio", action="store_true")
    args = parser.parse_args()
    for name in DIALOGUES:
        draw_chat(name, args.font)
        if args.audio:
            if platform.system() != "Darwin":
                parser.error("Для генерации голоса нужны macOS say и afconvert")
            make_audio(name)


if __name__ == "__main__":
    main()
