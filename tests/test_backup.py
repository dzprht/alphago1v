import os
from pathlib import Path
import subprocess


def test_backup_names_are_unique_and_failure_keeps_previous_copies(tmp_path):
    # сохраняет разные копии и удаляет только неудачный новый файл
    commands = tmp_path / "bin"
    commands.mkdir()
    scripts = {"date": "#!/bin/sh\nprintf '%s\\n' fixed\n",
               "sudo": '#!/bin/sh\nprintf database\nexit "${BACKUP_TEST_ERROR:-0}"\n'}
    for name, text in scripts.items():
        path = commands / name
        path.write_text(text)
        path.chmod(0o755)
    environment = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"]}
    directory = tmp_path / "backups"
    script = Path(__file__).resolve().parents[1] / "deploy/backup.sh"
    first = subprocess.run(["sh", str(script), str(directory)], env=environment,
                           check=True, capture_output=True, text=True).stdout.strip()
    second = subprocess.run(["sh", str(script), str(directory)], env=environment,
                            check=True, capture_output=True, text=True).stdout.strip()
    assert first != second
    assert Path(first).read_text() == Path(second).read_text() == "database"
    failed = subprocess.run(["sh", str(script), str(directory)],
                            env={**environment, "BACKUP_TEST_ERROR": "1"}, capture_output=True)
    assert failed.returncode == 1
    assert {str(path) for path in directory.iterdir()} == {first, second}
