"""Untimed identity capture for a prepared product kernel venv; no installs."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
from provenance import file_sha256

MAX_INSTALLED_FILES = 50_000
MAX_INSTALLED_BYTES = 1024 ** 3
MAX_DECLARED_FILES = 10_000
MAX_DECLARED_BYTES = 256 * 1024 ** 2


def bundled_skills(skills_root: Path) -> list[tuple[str, Path]]:
    """The python skills packaged with the release, by the product's own rule:
    a skills/<dir>/pyproject.toml whose src/<dir with dashes as underscores>/
    __init__.py exists (pa-core skills/discovery.rs). The product installs them
    editable, so their sources stay in the release package."""
    skills = []
    for pyproject in sorted(skills_root.glob('*/pyproject.toml')):
        name = pyproject.parent.name.replace('-', '_')
        if (pyproject.parent / 'src' / name / '__init__.py').is_file():
            skills.append((name, pyproject.parent.resolve()))
    if not skills:
        raise ValueError('release package carries no bundled python skills')
    return skills


def _foreign_pth_files(root: Path, allowed: list[Path]) -> list[str]:
    """Absolute .pth entries outside the venv and every declared release root:
    the old harness shared-dependency escape hatch."""
    foreign = []
    for site in root.glob('lib*/python*/site-packages'):
        for path in site.glob('*.pth'):
            for line in filter(None, path.read_text(errors='replace').splitlines()):
                target = Path(line)
                if target.is_absolute() and not any(
                        target.resolve().is_relative_to(base) for base in allowed):
                    foreign.append(f'{path}: {line}')
                    break
    return foreign


def inventory(python: Path, skills_root: Path | None = None) -> dict:
    """Record and verify one prepared product kernel venv. The venv itself must
    hold the rlm runtime (a copied wheel install) and every installed package
    file; the product's editable skill installs may reference their release
    package sources, which are declared via `skills_root` and inventoried as
    candidate files. Anything resolving anywhere else — a shared harness
    site-packages, for example — fails here."""
    skills = bundled_skills(skills_root) if skills_root is not None else []
    code = '''
import importlib, importlib.metadata, json, sys
from pathlib import Path
paths = {}
for name in %r:
    paths[name] = str(Path(importlib.import_module(name).__file__).resolve())
print(json.dumps({'python': sys.version, 'prefix': sys.prefix, 'executable': sys.executable,
 'modules': paths, 'packages': sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions())}))
'''
    code = code % (['rlm', *(name for name, _ in skills)],)
    result = subprocess.run([str(python), '-c', code], capture_output=True, text=True,
                            check=True, timeout=60)
    if len(result.stdout) > 1024 ** 2:
        raise ValueError('oversized runtime inventory')
    data = json.loads(result.stdout)
    # Raw paths on both sides: resolving the executable would dereference a
    # uv venv's bin/python symlink back to the base interpreter, while
    # resolving only the prefix would break venvs under symlinked roots.
    if Path(data['prefix']) != Path(data['executable']).parent.parent:
        raise ValueError('runtime interpreter does not belong to its reported venv')
    root = Path(data['prefix']).resolve()
    allowed = [root, *(directory for _, directory in skills)]
    if not Path(data['modules']['rlm']).is_relative_to(root):
        raise ValueError('rlm runtime must be installed inside the venv')
    for name, module_path in data['modules'].items():
        if not any(Path(module_path).is_relative_to(base) for base in allowed):
            raise ValueError(f'probed module outside the venv and release package: {name}')
    files = [p for p in root.rglob('*') if p.is_file() and p.suffix != '.pyc']
    if len(files) > MAX_INSTALLED_FILES or sum(p.stat().st_size for p in files) > MAX_INSTALLED_BYTES:
        raise ValueError('oversized installed runtime')
    package_files = [p for p in files if 'site-packages' in p.parts]
    if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in package_files):
        raise ValueError('runtime package escapes its environment')
    foreign = _foreign_pth_files(root, allowed)
    if foreign:
        raise ValueError('runtime venv references undeclared files outside itself: '
                         + ', '.join(foreign))
    data['installed_files'] = {str(p.relative_to(root)): file_sha256(p) for p in sorted(package_files)}
    declared = {}
    for name, directory in skills:
        skill_files = [p for p in directory.rglob('*')
                       if p.is_file() and p.suffix != '.pyc']
        if len(skill_files) > MAX_DECLARED_FILES or sum(
                p.stat().st_size for p in skill_files) > MAX_DECLARED_BYTES:
            raise ValueError('oversized bundled skill source')
        declared.update((f'{name}/{p.relative_to(directory)}', file_sha256(p))
                        for p in sorted(skill_files))
    data['declared_skill_files'] = declared
    data['interpreter_sha256'] = file_sha256(Path(data['executable']).resolve())
    return data
