"""Create local configuration once without exposing or rotating its signing key."""

import os
from pathlib import Path
import secrets


def initialize_env(project_root: Path) -> bool:
    target = project_root / ".env"
    template = (project_root / ".env.example").read_text()
    content = template.replace(
        "JWT_SECRET_KEY=replace-this-with-a-long-random-secret",
        f"JWT_SECRET_KEY={secrets.token_hex(32)}",
    )
    if content == template:
        raise ValueError("The environment template is missing the JWT secret placeholder.")
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w") as output:
        output.write(content)
    return True


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    if initialize_env(root):
        print("Created private .env with a random signing key. Restart API and MCP.")
    else:
        print(".env already exists; left unchanged to preserve configuration and tokens.")
