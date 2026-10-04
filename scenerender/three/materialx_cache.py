"""MaterialX source dependencies and complete, content-addressed bake artifacts."""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile


def _stamp(path):
    try:
        st = os.stat(path)
        return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns
    except FileNotFoundError:
        return None


@lru_cache(maxsize=512)
def _digest(path, stamp):
    if stamp is None:
        return None
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(paths):
    """Hash changed files only; deletion, creation and restored mtimes are observed."""
    return tuple((str(p), _digest(str(p), _stamp(p))) for p in sorted(set(map(str, paths))))


@dataclass
class Entry:
    dependencies: tuple
    signature: tuple
    spec: object

    def current(self):
        try:
            return fingerprint(self.dependencies) == self.signature
        except OSError:
            return False


def read_document(path, dependencies, observed=None):
    """Track actual XInclude reads, including nested files hidden by source URIs."""
    import MaterialX as mx
    from .materialx_inputs import library
    observed = {} if observed is None else observed
    stack = []

    def read(doc, filename, search_path, options):
        search_path = mx.FileSearchPath(search_path.asString())
        if stack:
            search_path.prepend(mx.FilePath(str(stack[-1].parent)))
        # A newly created file earlier on the search path can shadow the old
        # include. Observe those candidates as well as the selected file.
        if not filename.isAbsolute():
            for directory in search_path.asString('\0').split('\0'):
                candidate = Path(directory) / filename.asString()
                dependencies.add(str(candidate.absolute()))
        resolved = search_path.find(filename)
        dependencies.add(os.path.abspath(resolved.asString()))
        for dependency, digest in fingerprint(dependencies):
            observed.setdefault(dependency, digest)
        # Pass the resolved path while preserving the parent's include history.
        # Set its real source URI before importLibrary copies its children, so
        # relative assets in nested or search-path includes keep their origin.
        resolved = mx.FilePath(os.path.abspath(resolved.asString()))
        real_path = Path(resolved.asString())
        if real_path.resolve() in [p.resolve() for p in stack]:
            raise ValueError(f'MaterialX include cycle at {real_path}')
        stack.append(real_path)
        try:
            mx.readFromXmlFile(doc, resolved, search_path, options)
        finally:
            stack.pop()
        doc.setSourceUri(resolved.asString())

    options = mx.XmlReadOptions()
    options.readXIncludeFunction = read
    document = mx.createDocument()
    read(document, mx.FilePath(os.path.abspath(path)), mx.FileSearchPath(), options)
    document.setDataLibrary(library())
    image_dependencies(document, Path(path).absolute().parent, dependencies)
    source_dependencies(document, Path(path).absolute().parent, dependencies, observed)
    for filename, digest in fingerprint(dependencies):
        if filename in observed and observed[filename] != digest:
            raise RuntimeError('MaterialX documents changed while reading; retry the material')
        observed[filename] = digest
    return document


def image_dependencies(document, base_dir, dependencies):
    from .materialx_inputs import filename_path, terminal
    for element in document.traverseTree():
        if hasattr(element, 'getType') and element.getType() == 'filename':
            port = terminal(element)
            if port is not None and port.getValueString():
                dependencies.add(str(filename_path(port, base_dir)))


def source_dependencies(document, base_dir, dependencies, observed):
    """Observe authored GLSL implementations and their recursively included files."""
    import MaterialX as mx
    roots = [Path(p) for p in mx.getDefaultDataSearchPath().asString('\0').split('\0')]
    roots += [Path(base_dir)]
    visited = set()

    def read(filename, local):
        candidates = [Path(filename)] if Path(filename).is_absolute() else [root / filename for root in [local, *roots]]
        for candidate in candidates:
            candidate = candidate.absolute()
            dependencies.add(str(candidate))
            observed.setdefault(str(candidate), _digest(str(candidate), _stamp(candidate)))
            if candidate.is_file():
                if candidate not in visited:
                    visited.add(candidate)
                    includes(candidate.read_text(), candidate.parent)
                break

    def includes(code, local):
        for filename in re.findall(r'^\s*#\s*include\s*["<]([^">]+)[">]', code, flags=re.MULTILINE):
            read(filename, local)

    for element in document.traverseTree():
        if element.getCategory() == 'implementation':
            source = Path(element.getActiveSourceUri())
            local = source.parent if source.is_absolute() else base_dir / source.parent
            code = element.getAttribute('sourcecode')
            if code:
                includes(code, local)
            elif element.getAttribute('file'):
                read(element.getAttribute('file'), local)


@lru_cache(maxsize=1)
def library_fingerprint():
    """Installed definitions and shader sources are immutable within a process."""
    import MaterialX as mx
    # Include generator templates and GLSL includes as well as node definitions.
    root = Path(mx.__file__).parent / 'libraries'
    return fingerprint(p for p in root.rglob('*') if p.is_file())


def _valid_artifacts(directory):
    try:
        artifacts = json.loads((directory / 'manifest.json').read_text())
        if not isinstance(artifacts, dict) or 'baked.mtlx' not in artifacts:
            return False
        # Only files created in this cache entry can be certified by its manifest.
        if any(Path(name).name != name for name in artifacts):
            return False
        return all(digest is not None and _digest(str(directory / name), _stamp(directory / name)) == digest
                   for name, digest in artifacts.items())
    except (OSError, ValueError, TypeError):
        return False


def bake(path, size, script):
    """Publish a bake only after its document and images have been produced.

    The lock serializes writers of the same content. The manifest is published
    last, so a killed baker or a missing/corrupt image never becomes a cache hit.
    """
    import fcntl
    import subprocess
    import sys
    import MaterialX as mx

    dependencies = set()
    observed = {}
    read_document(path, dependencies, observed)
    sources = tuple(sorted(observed.items()))
    key = hashlib.sha256(json.dumps((sources, library_fingerprint(), mx.getVersionString(),
                                    size, script), sort_keys=True).encode()).hexdigest()
    root = Path(tempfile.gettempdir()) / 'scenerender-mtlx' / 'v2'
    root.mkdir(parents=True, exist_ok=True)
    directory = root / key
    with (root / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _valid_artifacts(directory):
            return str(directory / 'baked.mtlx')
        with tempfile.TemporaryDirectory(prefix=key + '-', dir=root) as scratch:
            staging = Path(scratch)
            out = staging / 'baked.mtlx'
            result = subprocess.run([sys.executable, '-c', script, os.path.abspath(path),
                                     scratch, str(out), str(size)],
                                    capture_output=True, text=True, timeout=600)
            if result.returncode or not out.is_file():
                raise RuntimeError((result.stderr or result.stdout).strip().splitlines()[-1:] or 'baker failed')
            if fingerprint(dependencies) != sources:
                raise RuntimeError('MaterialX dependencies changed during baking; retry the material')
            # The baker writes absolute image filenames. Make generated images
            # relative before moving the complete entry out of the staging area.
            document = mx.createDocument()
            mx.readFromXmlFile(document, str(out))
            from .materialx_inputs import library
            document.setDataLibrary(library())
            valid, message = document.validate()
            if not valid:
                raise RuntimeError(f'MaterialX baker produced an invalid document: {message}')
            for element in document.traverseTree():
                if hasattr(element, 'getType') and element.getType() == 'filename':
                    filename = Path(element.getValueString())
                    if filename.is_absolute() and filename.is_relative_to(staging):
                        if not filename.is_file():
                            raise RuntimeError(f'MaterialX baker did not produce {filename.name}')
                        element.setValueString(str(filename.relative_to(staging)))
                    elif element.getValueString() and not filename.is_absolute() and not (staging / filename).is_file():
                        raise RuntimeError(f'MaterialX baker did not produce {filename}')
            mx.writeToXmlFile(document, str(out))
            files = sorted(p for p in staging.iterdir() if p.is_file())
            artifacts = {p.name: _digest(str(p), _stamp(p)) for p in files}
            directory.mkdir(exist_ok=True)
            for source in files:
                os.replace(source, directory / source.name)
            manifest = staging / 'manifest.json'
            manifest.write_text(json.dumps(artifacts, sort_keys=True))
            os.replace(manifest, directory / manifest.name)
        return str(directory / 'baked.mtlx')
