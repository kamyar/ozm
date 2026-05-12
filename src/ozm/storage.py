"""Shared storage helpers for user-owned ozm state."""

from __future__ import annotations

import errno
import os
import secrets

import yaml

_IGNORED_FSYNC_ERRNOS = {
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
    errno.EOPNOTSUPP,
}


def refuse_symlink(path: str, label: str) -> None:
    if os.path.islink(path):
        raise RuntimeError(f"refusing to use symlinked {label}: {path}")


def _open_directory_no_follow(directory: str, label: str) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(directory, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise RuntimeError(f"refusing to use symlinked {label}: {directory}") from exc
        raise


def _open_directory_no_follow_at(parent_fd: int, name: str, label: str) -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise RuntimeError(f"refusing to use symlinked {label}: {name}") from exc
        raise


def _relative_directory_parts(parent_directory: str, directory: str) -> list[str]:
    parent = os.path.abspath(parent_directory)
    child = os.path.abspath(directory)
    try:
        if os.path.commonpath([parent, child]) != parent:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"{directory} is not inside {parent_directory}") from exc

    rel = os.path.relpath(child, parent)
    if rel == ".":
        return []
    parts = rel.split(os.sep)
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"invalid storage directory: {directory}")
    return parts


def _ensure_path_is_in_directory(path: str, directory: str) -> str:
    basename = os.path.basename(path)
    if not basename or basename in {".", ".."}:
        raise ValueError(f"invalid storage path: {path}")
    if os.path.abspath(os.path.dirname(path)) != os.path.abspath(directory):
        raise ValueError(f"{path} is not inside {directory}")
    return basename


def _open_storage_directory(
    directory: str,
    directory_label: str,
    *,
    create: bool,
    parent_directory: str | None = None,
    parent_label: str | None = None,
) -> int:
    if parent_directory is None:
        if create:
            os.makedirs(directory, exist_ok=True)
        return _open_directory_no_follow(directory, directory_label)

    if create:
        os.makedirs(parent_directory, exist_ok=True)
    parent_fd = _open_directory_no_follow(parent_directory, parent_label or directory_label)
    current_fd = parent_fd
    try:
        parts = _relative_directory_parts(parent_directory, directory)
        for index, part in enumerate(parts):
            label = directory_label if index == len(parts) - 1 else parent_label or directory_label
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
            child_fd = _open_directory_no_follow_at(current_fd, part, label)
            os.close(current_fd)
            current_fd = child_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _fsync_directory(dir_fd: int) -> None:
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        if exc.errno not in _IGNORED_FSYNC_ERRNOS:
            raise


def _create_temp_file(dir_fd: int, basename: str) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    for _ in range(100):
        tmp_name = f".{basename}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        try:
            return tmp_name, os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            continue
    raise FileExistsError(f"could not create unique temporary file for {basename}")


def save_bytes_atomic_no_follow(
    path: str,
    content: bytes,
    *,
    directory: str,
    directory_label: str,
    parent_directory: str | None = None,
    parent_label: str | None = None,
) -> None:
    """Atomically save bytes without following storage symlinks."""

    basename = _ensure_path_is_in_directory(path, directory)
    dir_fd = _open_storage_directory(
        directory,
        directory_label,
        create=True,
        parent_directory=parent_directory,
        parent_label=parent_label,
    )
    tmp_name = None
    fd = None
    try:
        tmp_name, fd = _create_temp_file(dir_fd, basename)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, basename, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        tmp_name = None
        _fsync_directory(dir_fd)
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            try:
                if tmp_name is not None:
                    os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass
            finally:
                os.close(dir_fd)


def save_yaml_atomic_no_follow(
    path: str,
    data: dict,
    *,
    directory: str,
    directory_label: str,
    parent_directory: str | None = None,
    parent_label: str | None = None,
    default_flow_style: bool = False,
    sort_keys: bool = False,
) -> None:
    """Atomically save YAML without following storage symlinks."""

    content = yaml.dump(data, default_flow_style=default_flow_style, sort_keys=sort_keys)
    save_bytes_atomic_no_follow(
        path,
        content.encode(),
        directory=directory,
        directory_label=directory_label,
        parent_directory=parent_directory,
        parent_label=parent_label,
    )


def load_yaml_no_follow(
    path: str,
    *,
    directory: str,
    directory_label: str,
    file_label: str,
    parent_directory: str | None = None,
    parent_label: str | None = None,
) -> dict:
    """Load YAML without following storage symlinks."""

    basename = _ensure_path_is_in_directory(path, directory)
    try:
        dir_fd = _open_storage_directory(
            directory,
            directory_label,
            create=False,
            parent_directory=parent_directory,
            parent_label=parent_label,
        )
    except FileNotFoundError:
        return {}

    fd = None
    try:
        flags = os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(basename, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            return {}
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise RuntimeError(f"refusing to use symlinked {file_label}: {path}") from exc
            raise

        with os.fdopen(fd) as f:
            fd = None
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            os.close(dir_fd)
