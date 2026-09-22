import ast
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from app.access import hash_owner_password, owner_password_hash_is_valid, verify_owner_password


ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "fixture-owner-password"
ENCODED = "pbkdf2_sha256$600000$AAECAwQFBgcICQoLDA0ODw$8BCTw1ZJEq4RR2CELCoTXsevLgXUbiZc8vPWDoLJaLo"


def _installer_code():
    body = (ROOT / "setup.sh").read_text().split("hash_owner_password() {", 1)[1].split("\n}\n", 1)[0]
    return body.split("python3 -c '\n", 1)[1].rsplit("\n'", 1)[0]


def _run_installer_hash(password):
    script = (
        "import io, secrets, sys\n"
        f"sys.argv = ['-c', {str(ROOT / 'backend')!r}]\n"
        "secrets.token_bytes = lambda length: bytes(range(length))\n"
        f"sys.stdin = io.StringIO({password!r})\n"
        f"exec({_installer_code()!r})\n"
    )
    return subprocess.run([sys.executable, "-I", "-S", "-c", script],
                          capture_output=True, text=True, check=False)


def test_installer_and_access_hash_match_fixed_salt_without_site_packages():
    result = _run_installer_hash(PASSWORD)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ENCODED
    assert hash_owner_password(PASSWORD, salt=bytes(range(16))) == ENCODED
    assert owner_password_hash_is_valid(ENCODED)
    assert verify_owner_password(PASSWORD, ENCODED)
    assert not verify_owner_password("wrong-password", ENCODED)


@pytest.mark.parametrize(("password", "access_message"), [
    ("short", "Owner password must contain at least 12 characters"),
    (PASSWORD + "\x00", "Owner password contains unsupported control characters"),
    (PASSWORD + "\r", "Owner password contains unsupported control characters"),
    (PASSWORD + "\n", "Owner password contains unsupported control characters"),
])
def test_installer_and_access_keep_distinct_password_errors(password, access_message):
    with pytest.raises(ValueError) as error:
        hash_owner_password(password, salt=bytes(range(16)))
    assert str(error.value) == access_message
    result = _run_installer_hash(password)
    assert result.returncode != 0
    assert not result.stdout
    assert result.stderr.strip() == "Owner password must contain at least 12 characters"


def test_password_verifier_keeps_rejecting_malformed_hashes():
    for encoded in ("", ENCODED.replace("600000", "1"), ENCODED.replace("pbkdf2_sha256", "sha256"), "pbkdf2_sha256$600000$AA$AA"):
        assert not owner_password_hash_is_valid(encoded)
        assert not verify_owner_password(PASSWORD, encoded)


def test_password_module_keeps_python36_syntax():
    source = (ROOT / "backend/app/owner_password.py").read_text()
    ast.parse(source, feature_version=(3, 6))


def test_installer_import_ignores_an_incompatible_package_in_working_directory(tmp_path):
    # On Python 3.6 the repository-root app bridge cannot be parsed. The
    # installer must select backend/app before the current working directory.
    shadow = tmp_path / "app"
    shadow.mkdir()
    (shadow / "__init__.py").write_text("raise AssertionError('wrong app package loaded')\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    interpreter = bin_dir / "python3"
    interpreter.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -S \"$@\"\n")
    interpreter.chmod(0o755)
    body = (ROOT / "setup.sh").read_text().split("hash_owner_password() {", 1)[1].split("\n}\n", 1)[0]
    environment = os.environ.copy()
    environment["PATH"] = str(bin_dir) + os.pathsep + environment["PATH"]
    result = subprocess.run(
        ["bash", "-c", 'ROOT_DIR="$1"\nhash_owner_password() {' + body + '\n}\nhash_owner_password "$2"',
         "installer-hash-test", str(ROOT), PASSWORD],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert verify_owner_password(PASSWORD, result.stdout.strip())
