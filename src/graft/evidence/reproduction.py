from __future__ import annotations

import shlex
import shutil
import tempfile
from pathlib import Path


INLINE_PAYLOAD_FLAGS = frozenset({"-c", "-e", "-E", "--eval", "-p", "--print"})


def canonical_reproduction_argv(
    command: tuple[str, ...],
    *,
    frozen_files: frozenset[str],
    run_root: Path | None = None,
) -> tuple[str, ...] | None:
    """Return a replayable argv bound only to the frozen candidate.

    Verifier workspaces are disposable.  This function rejects commands that
    depend on files outside the frozen checkpoint and removes a simple shell
    transport so the stored reproduction does not depend on the verifier's
    login-shell representation.  Absolute references to files inside the
    disposable copy are rewritten relative to the producer repository.
    """

    if not command:
        return None
    if len(command) == 1 and any(character.isspace() for character in command[0]):
        try:
            parsed = tuple(shlex.split(command[0]))
        except ValueError:
            return None
        return canonical_reproduction_argv(
            parsed,
            frozen_files=frozen_files,
            run_root=run_root,
        )

    root = run_root.resolve() if run_root is not None else None
    executable = Path(command[0]).name
    if executable in {"bash", "sh", "zsh"}:
        for flag in ("-c", "-lc"):
            if flag not in command:
                continue
            index = command.index(flag)
            if index != 1 or index + 2 != len(command):
                return None
            inner = simple_shell_argv(command[index + 1])
            if inner is None or Path(inner[0]).name in {"bash", "sh", "zsh"}:
                return None
            assignment_name, separator, _ = inner[0].partition("=")
            if separator and assignment_name.isidentifier():
                return None
            return canonical_reproduction_argv(
                inner,
                frozen_files=frozen_files,
                run_root=run_root,
            )
        return None

    canonical = list(command)
    canonical_executable = _canonical_executable(command[0], root, frozen_files)
    if canonical_executable is None:
        return None
    canonical[0] = canonical_executable

    inline_payload_indexes: set[int] = set()
    for index, part in enumerate(command[:-1]):
        if part in INLINE_PAYLOAD_FLAGS:
            inline_payload_indexes.add(index + 1)

    for index, raw in enumerate(command[1:], start=1):
        if index in inline_payload_indexes:
            payload = str(raw)
            if root is not None:
                payload = payload.replace(str(root), ".")
            if _references_ephemeral_path(payload):
                return None
            canonical[index] = payload
            continue
        token = str(raw).strip()
        if not token or token.startswith("-") or "\n" in token:
            continue
        path_text, separator, selector = token.partition("::")
        raw_path = Path(path_text)
        was_absolute = raw_path.is_absolute()
        candidate = raw_path if was_absolute or root is None else root / raw_path
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        looks_like_file = (
            (root is not None and resolved.is_file())
            or was_absolute
            or "/" in path_text
            or "\\" in path_text
            or bool(Path(path_text).suffix)
        )
        if not looks_like_file:
            continue
        if root is None:
            if was_absolute:
                return None
            relative = raw_path.as_posix()
        else:
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                return None
        if relative not in frozen_files:
            return None
        canonical[index] = relative + (f"::{selector}" if separator else "")
    return tuple(canonical)


def portable_reproduction_argv(
    command: tuple[str, ...],
    *,
    frozen_files: frozenset[str],
    run_root: Path | None = None,
) -> bool:
    """Whether a reported command survives beyond a verifier copy."""

    return (
        canonical_reproduction_argv(
            command,
            frozen_files=frozen_files,
            run_root=run_root,
        )
        is not None
    )


def simple_shell_argv(payload: str) -> tuple[str, ...] | None:
    """Parse one shell command while rejecting control-flow syntax."""

    try:
        lexer = shlex.shlex(
            payload,
            posix=True,
            punctuation_chars="();<>|&",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        parts = tuple(lexer)
    except ValueError:
        return None
    if not parts:
        return None
    shell_control = frozenset("();<>|&")
    if any(token and set(token) <= shell_control for token in parts):
        return None
    if any("`" in token or "$(" in token for token in parts):
        return None
    return parts


def _canonical_executable(
    raw: str,
    root: Path | None,
    frozen_files: frozenset[str],
) -> str | None:
    path = Path(raw)
    if path.is_absolute():
        try:
            resolved = path.resolve()
        except OSError:
            return None
        if root is not None:
            try:
                relative = resolved.relative_to(root).as_posix()
            except ValueError:
                relative = ""
            if relative:
                return _relative_executable(relative) if relative in frozen_files else None
        installed = shutil.which(path.name)
        if installed is None:
            return None
        try:
            if Path(installed).resolve() != resolved:
                return None
        except OSError:
            return None
        return raw
    if "/" not in raw and "\\" not in raw:
        return raw if shutil.which(raw) is not None else None
    if root is None:
        return raw if path.as_posix() in frozen_files else None
    try:
        relative = (root / path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    return _relative_executable(relative) if relative in frozen_files else None


def _relative_executable(relative: str) -> str:
    return relative if "/" in relative else f"./{relative}"


def _references_ephemeral_path(payload: str) -> bool:
    """Conservatively reject temp-directory dependencies hidden in inline code."""

    normalized = payload.replace("\\", "/")
    prefixes = {"/tmp/", "/var/tmp/", "/private/tmp/"}
    try:
        temporary_root = Path(tempfile.gettempdir()).resolve().as_posix().rstrip("/")
    except OSError:
        temporary_root = ""
    if temporary_root:
        prefixes.add(temporary_root + "/")
    return any(prefix in normalized for prefix in prefixes)
