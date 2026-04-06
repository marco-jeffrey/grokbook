"""Python environment discovery for kernel selection.

Discovers Python interpreters via multiple sources (uv, kernelspecs, cwd .venv,
$VIRTUAL_ENV, sys.executable), merges them, and probes each for ipykernel.

The heavy lifting is deliberately small — we lean on `uv python list` because
grokbook ships via `uvx`, so uv is guaranteed to be on the host.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

_PROBE_SCRIPT = (
    "import sys,json;"
    "d={'v':list(sys.version_info[:3]),'x':sys.executable,'p':sys.prefix};"
    "\ntry:\n import ipykernel\n d['ipk']=True\nexcept Exception:\n d['ipk']=False\n"
    "print(json.dumps(d))"
)

_PROBE_TIMEOUT = 3.0
_DISCOVERY_TIMEOUT = 8.0


def _find_uv() -> str | None:
    """Locate uv binary. Falls back to common install paths since PATH may
    be stripped in server subprocesses."""
    found = shutil.which("uv")
    if found:
        return found
    for candidate in [
        Path.home() / ".local" / "bin" / "uv",
        Path("/usr/local/bin/uv"),
        Path("/opt/homebrew/bin/uv"),
    ]:
        if candidate.exists():
            return str(candidate)
    return None


_UV_CMD = _find_uv()


@dataclass
class EnvInfo:
    name: str
    path: str
    version: tuple[int, int, int]
    source: str  # "cwd" | "uv" | "kernelspec" | "virtualenv" | "current"
    has_ipykernel: bool
    label_glyph: str  # "V" | "U" | "K" | "E" | "S"

    @property
    def version_str(self) -> str:
        return ".".join(str(x) for x in self.version)


_cache: list[EnvInfo] | None = None


async def _probe(python_path: str) -> tuple[tuple[int, int, int], bool] | None:
    """Run a small script to read version + ipykernel presence. Returns None on failure."""

    def _run():
        try:
            r = subprocess.run(
                [python_path, "-c", _PROBE_SCRIPT],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
            )
            if r.returncode != 0:
                return None
            d = json.loads(r.stdout)
            return tuple(d["v"]), bool(d["ipk"])
        except Exception:
            return None

    return await asyncio.to_thread(_run)


def _absolute(python_path: str) -> str:
    """Return absolute path WITHOUT resolving symlinks.

    Venvs use a symlinked python that, when followed, loses the venv's
    site-packages. We need to keep the symlink so ipykernel probes see
    the venv's actual installed packages.
    """
    p = Path(python_path)
    if p.is_absolute():
        return str(p)
    return str(Path.cwd() / p)


async def probe_env(python_path: str) -> EnvInfo | None:
    """Probe a single python path, return EnvInfo or None if not a valid interpreter."""
    resolved = _absolute(python_path) if Path(python_path).exists() else python_path
    probe = await _probe(resolved)
    if probe is None:
        return None
    version, has_ipk = probe
    name, source, glyph = _classify(resolved)
    return EnvInfo(
        name=name,
        path=resolved,
        version=version,  # type: ignore[arg-type]
        source=source,
        has_ipykernel=has_ipk,
        label_glyph=glyph,
    )


def _classify(path: str) -> tuple[str, str, str]:
    """Guess display name, source tag, and glyph from a python path."""
    p = Path(path)
    parts = p.parts
    if "uv" in parts and "python" in parts:
        # ~/.local/share/uv/python/cpython-3.14-macos-aarch64-none/bin/python3.14
        for part in parts:
            if part.startswith("cpython-") or part.startswith("pypy-"):
                return (f"uv: {part}", "uv", "U")
        return ("uv python", "uv", "U")
    # .venv / venv dirs — look for pyvenv.cfg
    for anc in [p.parent.parent, p.parent.parent.parent]:
        if (anc / "pyvenv.cfg").exists():
            # name = parent dir name relative to its own parent
            return (f"{anc.name} ({anc.parent.name})", "cwd", "V")
    if path == sys.executable:
        return ("grokbook (self)", "current", "S")
    return (p.name, "kernelspec", "K")


async def _from_uv() -> list[EnvInfo]:
    """List uv-managed and uv-discoverable Pythons."""
    if _UV_CMD is None:
        return []

    def _run():
        try:
            r = subprocess.run(
                [_UV_CMD, "python", "list", "--only-installed", "--output-format", "json"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode != 0:
                return []
            return json.loads(r.stdout)
        except Exception:
            return []

    entries = await asyncio.to_thread(_run)
    envs: list[EnvInfo] = []
    for e in entries:
        path = e.get("path")
        if not path or not Path(path).exists():
            continue
        vp = e.get("version_parts", {})
        version = (vp.get("major", 0), vp.get("minor", 0), vp.get("patch", 0))
        key = e.get("key", "python")
        envs.append(EnvInfo(
            name=f"uv: {key}",
            path=_absolute(path),
            version=version,
            source="uv",
            has_ipykernel=False,  # filled in by probe pass
            label_glyph="U",
        ))
    return envs


async def _from_kernelspecs() -> list[EnvInfo]:
    """Read jupyter kernelspecs and return EnvInfos for python kernels."""
    try:
        from jupyter_client.kernelspec import KernelSpecManager
    except ImportError:
        return []

    def _run():
        try:
            ksm = KernelSpecManager()
            out = []
            for name, dirpath in ksm.find_kernel_specs().items():
                spec = ksm.get_kernel_spec(name)
                if spec.language != "python":
                    continue
                if not spec.argv:
                    continue
                py = spec.argv[0]
                if not Path(py).exists():
                    continue
                out.append((name, spec.display_name, py))
            return out
        except Exception:
            return []

    raw = await asyncio.to_thread(_run)
    envs: list[EnvInfo] = []
    for name, display, py in raw:
        envs.append(EnvInfo(
            name=f"kernelspec: {display}",
            path=_absolute(py),
            version=(0, 0, 0),
            source="kernelspec",
            has_ipykernel=True,  # kernelspecs imply ipykernel present
            label_glyph="K",
        ))
    return envs


def _from_virtualenv() -> list[EnvInfo]:
    """$VIRTUAL_ENV, $CONDA_PREFIX, cwd .venv/venv, and cwd .pixi/envs."""
    found: list[EnvInfo] = []
    seen: set[str] = set()

    def _add(py_path: Path, name: str, source: str = "virtualenv", glyph: str = "V"):
        if not py_path.exists():
            return
        resolved = _absolute(str(py_path))
        if resolved in seen:
            return
        seen.add(resolved)
        found.append(EnvInfo(
            name=name,
            path=resolved,
            version=(0, 0, 0),
            source=source,
            has_ipykernel=False,
            label_glyph=glyph,
        ))

    # Activated venvs
    venv_env = os.environ.get("VIRTUAL_ENV")
    if venv_env:
        _add(Path(venv_env) / "bin" / "python", f"$VIRTUAL_ENV ({Path(venv_env).name})")

    # Activated conda/pixi environments
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        _add(Path(conda_prefix) / "bin" / "python", f"$CONDA_PREFIX ({Path(conda_prefix).name})", "conda", "P")

    cwd = Path.cwd()

    # Standard venvs
    _add(cwd / ".venv" / "bin" / "python", f".venv ({cwd.name})")
    _add(cwd / "venv" / "bin" / "python", f"venv ({cwd.name})")

    # Pixi environments (cwd/.pixi/envs/<name>/bin/python)
    pixi_envs = cwd / ".pixi" / "envs"
    if pixi_envs.is_dir():
        for env_dir in sorted(pixi_envs.iterdir()):
            py = env_dir / "bin" / "python"
            if py.exists():
                _add(py, f"pixi: {env_dir.name} ({cwd.name})", "pixi", "P")

    return found


async def discover_envs(cwd: Path | None = None) -> list[EnvInfo]:
    """Merge all sources, dedupe by resolved path, re-probe each for ipykernel/version."""
    if cwd is not None:
        os.chdir(str(cwd))

    # Gather from all sources
    uv_task = asyncio.create_task(_from_uv())
    ks_task = asyncio.create_task(_from_kernelspecs())
    custom_task = asyncio.create_task(_from_custom())
    venv_list = _from_virtualenv()

    try:
        uv_list, ks_list, custom_list = await asyncio.wait_for(
            asyncio.gather(uv_task, ks_task, custom_task),
            timeout=_DISCOVERY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        uv_list = uv_task.result() if uv_task.done() else []
        ks_list = ks_task.result() if ks_task.done() else []
        custom_list = custom_task.result() if custom_task.done() else []

    # Add current grokbook python as a last-resort entry
    self_env = EnvInfo(
        name="grokbook (self)",
        path=_absolute(sys.executable),
        version=tuple(sys.version_info[:3]),  # type: ignore[arg-type]
        source="current",
        has_ipykernel=True,  # grokbook's env has ipykernel (it's a dep)
        label_glyph="S",
    )

    # Merge with priority: custom > virtualenv > kernelspec > uv > current
    # (so e.g. cwd .venv "wins" if also discovered by uv)
    merged: dict[str, EnvInfo] = {}
    for env in [self_env] + uv_list:
        merged[env.path] = env
    for env in ks_list:
        merged[env.path] = env
    for env in venv_list:
        merged[env.path] = env
    for env in custom_list:
        merged[env.path] = env

    # Probe each to fill version + ipykernel accurately
    async def _fill(env: EnvInfo) -> EnvInfo:
        probe = await _probe(env.path)
        if probe is None:
            return env
        version, has_ipk = probe
        return EnvInfo(
            name=env.name,
            path=env.path,
            version=version,  # type: ignore[arg-type]
            source=env.source,
            has_ipykernel=has_ipk,
            label_glyph=env.label_glyph,
        )

    filled = await asyncio.gather(*(_fill(e) for e in merged.values()))
    # Sort: has_ipykernel desc, then source priority, then name
    source_prio = {"custom": 0, "virtualenv": 0, "cwd": 0, "pixi": 0, "conda": 0, "kernelspec": 1, "uv": 2, "current": 3}
    filled.sort(key=lambda e: (not e.has_ipykernel, source_prio.get(e.source, 9), e.name))
    return list(filled)


def _custom_envs_path() -> Path:
    """File that stores user-added Python paths."""
    return Path.home() / ".grokbook" / "custom_envs.json"


def _load_custom_paths() -> list[str]:
    p = _custom_envs_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return [str(x) for x in data if isinstance(x, str)]
    except Exception:
        return []


def _save_custom_paths(paths: list[str]) -> None:
    p = _custom_envs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(paths, indent=2))


async def _from_custom() -> list[EnvInfo]:
    """Envs from ~/.grokbook/custom_envs.json."""
    paths = _load_custom_paths()
    envs: list[EnvInfo] = []
    for path in paths:
        if not Path(path).exists():
            continue
        envs.append(EnvInfo(
            name=Path(path).name,
            path=_absolute(path),
            version=(0, 0, 0),
            source="custom",
            has_ipykernel=False,
            label_glyph="C",
        ))
    return envs


async def add_custom_env(path: str) -> EnvInfo | None:
    """Probe a user-typed path; persist + return EnvInfo on success."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return None
    env = await probe_env(str(p))
    if env is None:
        return None
    # Make it look like a custom entry with a reasonable display name
    env = EnvInfo(
        name=f"{p.parent.parent.name}/{p.name}" if p.parent.name == "bin" else p.name,
        path=env.path,
        version=env.version,
        source="custom",
        has_ipykernel=env.has_ipykernel,
        label_glyph="C",
    )
    paths = _load_custom_paths()
    if env.path not in paths:
        paths.append(env.path)
        _save_custom_paths(paths)
    await refresh()
    return env


def complete_path(prefix: str, limit: int = 12) -> list[str]:
    """Return up to `limit` filesystem paths that start with `prefix`.

    Ranks 'bin' directories and files starting with 'python' first so they
    float to the top. Safe: only lists directories, no recursion, no
    following symlinks out of the home/root.
    """
    prefix = prefix.strip()
    if not prefix:
        return []
    expanded = Path(prefix).expanduser()
    if prefix.endswith("/") or expanded.is_dir():
        # List contents of this directory
        dir_path = expanded if expanded.is_dir() else expanded.parent
        basename_filter = ""
    else:
        # List contents of parent, filter by basename prefix
        dir_path = expanded.parent
        basename_filter = expanded.name
    try:
        if not dir_path.is_dir():
            return []
        entries = []
        for p in dir_path.iterdir():
            if basename_filter and not p.name.startswith(basename_filter):
                continue
            if p.name.startswith(".") and not basename_filter.startswith("."):
                continue
            # Show trailing slash for dirs so user knows to keep typing
            suffix = "/" if p.is_dir() else ""
            # Use the user's original prefix style (keep ~ if they used it)
            if prefix.startswith("~") and str(p).startswith(str(Path.home())):
                display = "~" + str(p)[len(str(Path.home())):] + suffix
            else:
                display = str(p) + suffix
            # Rank: python-named files > bin dirs > other dirs > other files
            if not p.is_dir() and p.name.startswith("python"):
                rank = 0
            elif p.is_dir() and p.name == "bin":
                rank = 1
            elif p.is_dir():
                rank = 2
            else:
                rank = 3
            entries.append((rank, p.name, display))
        entries.sort()
        return [d for _, _, d in entries[:limit]]
    except (PermissionError, OSError):
        return []


async def remove_custom_env(path: str) -> None:
    """Remove a path from custom_envs.json."""
    paths = _load_custom_paths()
    paths = [p for p in paths if p != path]
    _save_custom_paths(paths)
    await refresh()


async def refresh() -> list[EnvInfo]:
    """Re-scan and update cache."""
    global _cache
    _cache = await discover_envs()
    return _cache


def get_all() -> list[EnvInfo]:
    """Return cached envs. Call refresh() first."""
    return _cache or []


def find_by_path(path: str) -> EnvInfo | None:
    if not _cache:
        return None
    resolved = _absolute(path) if Path(path).exists() else path
    for env in _cache:
        if env.path == resolved or env.path == path:
            return env
    return None


async def pick_default(cwd: Path | None = None) -> EnvInfo | None:
    """Pick the default env for grokbook startup.

    Priority: $VIRTUAL_ENV > $CONDA_PREFIX > cwd/.venv > cwd/venv >
    cwd/.pixi/envs/default > first discovered env > sys.executable.
    """
    cwd = cwd or Path.cwd()

    # 1. $VIRTUAL_ENV (activated venv)
    venv_env = os.environ.get("VIRTUAL_ENV")
    if venv_env:
        py = Path(venv_env) / "bin" / "python"
        if py.exists():
            env = await probe_env(str(py))
            if env:
                return env

    # 2. $CONDA_PREFIX (activated conda/pixi env)
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        py = Path(conda_prefix) / "bin" / "python"
        if py.exists():
            env = await probe_env(str(py))
            if env:
                return env

    # 3. cwd venvs
    for candidate in [cwd / ".venv" / "bin" / "python", cwd / "venv" / "bin" / "python"]:
        if candidate.exists():
            env = await probe_env(str(candidate))
            if env:
                return env

    # 4. cwd pixi default env
    pixi_default = cwd / ".pixi" / "envs" / "default" / "bin" / "python"
    if pixi_default.exists():
        env = await probe_env(str(pixi_default))
        if env:
            return env

    # 3. Fall back to a discovered env — prefer uv's default
    envs = await discover_envs(cwd)
    for env in envs:
        if env.has_ipykernel and env.source != "current":
            return env

    # 4. sys.executable
    return await probe_env(sys.executable)


async def install_ipykernel(python_path: str) -> AsyncIterator[tuple[str, str]]:
    """Install ipykernel into the given env via `uv pip install --python X ipykernel`.

    Yields (kind, line) tuples as output arrives. kind is "out" for stdout
    lines or "done" for the terminal entry (line = "ok" on success, or an
    error summary on failure).
    """
    if _UV_CMD is not None:
        cmd = [_UV_CMD, "pip", "install", "--python", python_path, "ipykernel"]
    else:
        cmd = [python_path, "-m", "pip", "install", "ipykernel"]

    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        yield ("out", line.decode("utf-8", errors="replace").rstrip())
    await proc.wait()
    if proc.returncode == 0:
        yield ("done", "ok")
    else:
        yield ("done", f"install failed (exit code {proc.returncode})")
