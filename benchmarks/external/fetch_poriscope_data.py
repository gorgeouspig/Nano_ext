"""Download Poriscope's sample recordings into ``benchmarks/_external/data/poriscope/``.

Source (not redistributed here; the files stay out of git):

    Multi-channel DNA translocations through a solid-state nanopore: dataset
    to verify Poriscope functionality. Federated Research Data Repository
    (FRDR). Version 1: DOI 10.20383/103.01599; version 2: DOI
    10.20383/103.01695.
    Licence: Creative Commons Attribution 4.0 International (CC BY 4.0),
    https://creativecommons.org/licenses/by/4.0/ — see the dataset page and
    its CITATION.txt for the authors and the citation to use.

The dataset (version 2) holds four Chimera channels (``*_HS<n>_*.log`` raw
data with ``.json`` metadata; 3.3 GB per channel), Poriscope tutorial
databases and README/LICENSE/CITATION files. By default only the text files,
the metadata and one channel (HS2) are fetched; ``--include`` selects others.

The script

1. resolves the DOI, follows the newest version linked from the dataset page
   (``--url`` pins a specific version page) and reports the versions found;
2. lists the files through FRDR's file-size cache (``/repo/filesizecache``);
3. downloads each file through ``/repo/files/...``, which redirects to the
   repository's Globus HTTPS endpoint, and prints the host that served it;
4. verifies each file against FRDR's ``frdr-dfdr-checksums.txt`` when present
   and against ``poriscope_data.sha256`` (``<sha256>  <file name>`` per line).
   Files not yet listed are reported with their hash; ``--record`` appends
   them so that later downloads are verified.

Every network failure names the host that could not be reached, so that it can
be allowed in the environment's network settings.

    python benchmarks/external/fetch_poriscope_data.py --list      # versions and files
    python benchmarks/external/fetch_poriscope_data.py --record    # first download
    python benchmarks/external/fetch_poriscope_data.py             # verify
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import html
import re
import sys
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

HERE = Path(__file__).resolve().parent
DEST = HERE.parent / "_external" / "data" / "poriscope"
MANIFEST = HERE / "poriscope_data.sha256"

DOI = "10.20383/103.01599"  # version 1; the page links to newer versions
FRDR = "https://www.frdr-dfdr.ca"
DEFAULT_INCLUDE = "*.txt,*_HS2_*.json,*_HS2_*.log"


def _session():
    import requests

    s = requests.Session()
    s.headers["User-Agent"] = "Nano_ext-benchmarks/1 (+https://github.com/gorgeouspig/Nano_ext)"
    return s


def _get(session, url, **kw):
    """GET that turns connection errors into a message naming the host."""
    import requests

    try:
        r = session.get(url, timeout=60, **kw)
    except requests.RequestException as exc:
        host = urlparse(getattr(exc.request, "url", None) or url).hostname
        raise SystemExit(f"cannot reach {host} ({type(exc).__name__}): {url}")
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code} from {urlparse(r.url).hostname}: {r.url}")
    return r


def versions(page_url: str, text: str) -> dict[int, str]:
    """{version number: dataset page URL} linked from a dataset page."""
    out = {}
    for href, label in re.findall(r'href="([^"]+)"[^>]*>\s*Version(?:\s|&nbsp;)*(\d+)\s*<', text):
        out[int(label)] = urljoin(page_url, html.unescape(href))
    return out


def resolve_page(session, url: str | None) -> tuple[str, str]:
    """(URL, HTML) of the dataset page: *url*, or the newest version of DOI."""
    if url:
        r = _get(session, url)
        return r.url, r.text
    r = _get(session, f"https://doi.org/{DOI}")
    found = versions(r.url, r.text)
    for v, u in sorted(found.items()):
        print(f"  version {v}: {u}")
    if found and found[max(found)].rstrip("/") != r.url.rstrip("/"):
        r = _get(session, found[max(found)])
    return r.url, r.text


def list_files(session, text: str) -> list[dict]:
    """Files of the dataset: dicts with ``name``, ``path`` and ``size``."""
    m = re.search(r"name='item_id' value='(\d+)'", text) or re.search(r"setItem\('(\d+)'\)", text)
    if not m:
        raise SystemExit("item id not found on the dataset page")
    tree = _get(session, f"{FRDR}/repo/filesizecache", params={"item_id": m.group(1)}).json()
    out = []

    def walk(node):
        for c in node.get("contents", []):
            if c["type"] == "dir":
                walk(c)
            else:
                out.append({"name": c["name"], "path": c["path"], "size": int(c["size"])})

    walk(tree)
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_manifest() -> dict[str, str]:
    if not MANIFEST.exists():
        return {}
    out = {}
    for line in MANIFEST.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            digest, name = line.split(maxsplit=1)
            out[name.strip()] = digest
    return out


def frdr_checksums() -> dict[str, str]:
    """{file name: hex digest} from FRDR's checksum file, if downloaded."""
    p = DEST / "frdr-dfdr-checksums.txt"
    out = {}
    if p.exists():
        for line in p.read_text(errors="replace").splitlines():
            m = re.search(r"\b([0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\b", line)
            if m:
                name = Path(line.replace(m.group(1), "").strip(" \t*,:;|")).name
                if name:
                    out[name] = m.group(1).lower()
    return out


def hexdigest(path: Path, n_hex: int) -> str:
    h = {32: hashlib.md5, 40: hashlib.sha1, 64: hashlib.sha256}[n_hex]()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(session, f: dict) -> Path:
    import requests

    target = DEST / f["name"]
    if target.exists() and target.stat().st_size == f["size"]:
        print(f"{f['name']}: already present")
        return target
    url = f"{FRDR}/repo/files{quote(f['path'])}"
    print(f"{f['name']}: {f['size'] / 1e6:.1f} MB")
    tmp = target.with_name(target.name + ".part")
    try:
        with session.get(url, stream=True, timeout=60) as r:
            hosts = [urlparse(h.url).hostname for h in [*r.history, r]]
            print(f"  via {' -> '.join(hosts)}")
            if r.status_code >= 400:
                raise SystemExit(f"HTTP {r.status_code} from {hosts[-1]}: {r.url}")
            with open(tmp, "wb") as out:
                for chunk in r.iter_content(1 << 20):
                    out.write(chunk)
    except requests.RequestException as exc:
        host = urlparse(getattr(exc.request, "url", None) or url).hostname
        raise SystemExit(f"cannot reach {host} ({type(exc).__name__}) while downloading {f['name']}")
    tmp.rename(target)
    return target


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="dataset page of a specific version (default: newest version of the DOI)")
    ap.add_argument("--include", default=DEFAULT_INCLUDE,
                    help=f"comma-separated file name patterns (default: {DEFAULT_INCLUDE}; '*' for all)")
    ap.add_argument("--list", action="store_true", help="only list versions and files")
    ap.add_argument("--record", action="store_true", help="append hashes of unlisted files to the manifest")
    args = ap.parse_args()

    session = _session()
    page_url, text = resolve_page(session, args.url)
    doi = re.search(r'"@id":\s*"https://doi.org/([^"]+)"', text)
    print(f"dataset page: {page_url}" + (f" (DOI {doi.group(1)})" if doi else ""))
    files = list_files(session, text)
    patterns = [p.strip() for p in args.include.split(",") if p.strip()]
    chosen = [f for f in files if any(fnmatch.fnmatch(f["name"], p) for p in patterns)]
    for f in files:
        print(f"  {'*' if f in chosen else ' '} {f['size'] / 1e6:10.1f} MB  {f['name']}")
    print(f"selected {len(chosen)} file(s), {sum(f['size'] for f in chosen) / 1e9:.2f} GB")
    if args.list:
        return

    DEST.mkdir(parents=True, exist_ok=True)
    # FRDR's checksum file first, so that the others can be checked against it
    chosen.sort(key=lambda f: f["name"] != "frdr-dfdr-checksums.txt")
    known, new, bad = read_manifest(), [], []
    for f in chosen:
        path = download(session, f)
        ref = frdr_checksums().get(path.name)
        if ref:
            ok = hexdigest(path, len(ref)) == ref
            print(f"  FRDR checksum {'OK' if ok else 'MISMATCH'}")
            if not ok:
                bad.append(path.name)
                continue
        digest = sha256(path)
        if path.name not in known:
            print(f"  sha256 {digest} (not in manifest)")
            new.append(f"{digest}  {path.name}")
        elif known[path.name] != digest:
            print(f"  SHA256 MISMATCH (expected {known[path.name]})")
            bad.append(path.name)
        else:
            print("  sha256 OK")
    if new and args.record:
        with open(MANIFEST, "a") as out:
            out.write("\n".join(new) + "\n")
        print(f"recorded {len(new)} hash(es) in {MANIFEST.name}")
    if bad:
        raise SystemExit(f"checksum mismatch: {', '.join(bad)}")


if __name__ == "__main__":
    main()
