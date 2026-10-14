from pathlib import Path


def safe_child(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("path escapes root")
    return candidate
