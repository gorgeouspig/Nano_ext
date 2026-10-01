"""Download Poriscope's sample recordings into ``benchmarks/_external/data/poriscope/``.

Source (not redistributed here; the files stay out of git):

    Poriscope sample data. Federated Research Data Repository (FRDR).
    DOI 10.20383/103.01599
    https://www.frdr-dfdr.ca/repo/dataset/48fb491a-d5d0-442a-9f1f-6e79e882d999
    Licence: Creative Commons Attribution 4.0 International (CC BY 4.0),
    https://creativecommons.org/licenses/by/4.0/ — see the dataset page for
    the authors and the citation to use.

The script

1. reads the dataset page (DOI resolved through doi.org) and reports any newer
   version it links to; ``--url`` points it at a specific version's page;
2. collects the file links on that page (or takes them from ``--file-url``);
3. downloads each file, prints the host it came from, and checks its SHA256
   against ``poriscope_data.sha256`` (``<sha256>  <file name>`` per line).
   Files not yet listed are reported with their hash; ``--record`` appends
   them so that later downloads are verified;
4. unpacks ``.zip`` / ``.tar*`` archives next to the download.

Every network failure names the host that could not be reached, so that it can
be allowed in the environment's network settings.

    python benchmarks/external/fetch_poriscope_data.py --list      # links only
    python benchmarks/external/fetch_poriscope_data.py --record    # first download
    python benchmarks/external/fetch_poriscope_data.py             # verify
"""

from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
import tarfile
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

HERE = Path(__file__).resolve().parent
DEST = HERE.parent / "_external" / "data" / "poriscope"
MANIFEST = HERE / "poriscope_data.sha256"

DOI = "10.20383/103.01599"
DATASET_PAGE = "https://www.frdr-dfdr.ca/repo/dataset/48fb491a-d5d0-442a-9f1f-6e79e882d999"
DATA_SUFFIXES = (".abf", ".dat", ".bin", ".h5", ".hdf5", ".npy", ".log", ".edh", ".tdms",
                 ".zip", ".tar", ".tar.gz", ".tgz", ".7z", ".csv", ".json")


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
        raise SystemExit(f"cannot reach {urlparse(url).hostname} ({type(exc).__name__}): {url}")
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code} from {urlparse(r.url).hostname}: {r.url}")
    for hop in [*r.history, r]:
        print(f"  via {urlparse(hop.url).hostname}", file=sys.stderr)
    return r


def resolve_page(session, url: str | None) -> tuple[str, str]:
    """(final URL, HTML) of the dataset page."""
    r = _get(session, url or f"https://doi.org/{DOI}")
    return r.url, r.text


def newer_versions(page_url: str, text: str) -> list[str]:
    """Links on the page that look like other versions of the dataset."""
    out = set()
    for m in re.finditer(r'href="([^"]+)"[^>]*>([^<]*[Vv]ersion[^<]*)<', text):
        out.add(f"{html.unescape(m.group(2)).strip()}: {urljoin(page_url, html.unescape(m.group(1)))}")
    for m in re.finditer(r"10\.20383/10[0-9]\.\d+", text):
        if m.group(0) != DOI:
            out.add(f"other DOI on the page: {m.group(0)}")
    return sorted(out)


def file_links(page_url: str, text: str) -> list[str]:
    links = []
    for href in re.findall(r'href="([^"]+)"', text):
        u = urljoin(page_url, html.unescape(href))
        path = urlparse(u).path.lower()
        if path.endswith(DATA_SUFFIXES) or "globus" in urlparse(u).netloc or "/download" in path:
            links.append(u)
    return list(dict.fromkeys(links))


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


def download(session, url: str) -> Path:
    import requests

    name = Path(urlparse(url).path).name or "download"
    target = DEST / name
    if target.exists():
        print(f"{name}: already present")
        return target
    print(f"{name}: downloading from {urlparse(url).hostname}")
    try:
        with session.get(url, stream=True, timeout=60) as r:
            if r.status_code >= 400:
                raise SystemExit(f"HTTP {r.status_code} from {urlparse(r.url).hostname}: {r.url}")
            hosts = {urlparse(h.url).hostname for h in [*r.history, r]}
            print(f"  served by {', '.join(sorted(hosts))}")
            cd = r.headers.get("content-disposition", "")
            m = re.search(r'filename="?([^";]+)"?', cd)
            if m:
                target = DEST / Path(m.group(1)).name
            tmp = target.with_suffix(target.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
    except requests.RequestException as exc:
        raise SystemExit(f"cannot reach {urlparse(url).hostname} ({type(exc).__name__}): {url}")
    tmp.rename(target)
    return target


def unpack(path: Path) -> None:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            z.extractall(DEST)
    elif tarfile.is_tarfile(path):
        with tarfile.open(path) as t:
            try:
                t.extractall(DEST, filter="data")
            except TypeError:  # Python without extraction filters
                t.extractall(DEST)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", help="dataset page of a specific version (default: resolve the DOI)")
    ap.add_argument("--file-url", action="append", default=[], help="download this file URL (repeatable)")
    ap.add_argument("--list", action="store_true", help="only list versions and file links")
    ap.add_argument("--record", action="store_true", help="append hashes of unlisted files to the manifest")
    args = ap.parse_args()

    session = _session()
    urls = args.file_url
    if not urls:
        page_url, text = resolve_page(session, args.url)
        print(f"dataset page: {page_url}")
        for v in newer_versions(page_url, text):
            print(f"  version link: {v}")
        urls = file_links(page_url, text)
        for u in urls:
            print(f"  file: {u}")
        if not urls:
            print("no file links found on the page; the files may be served by Globus or a script. "
                  "Pass them with --file-url.")
    if args.list or not urls:
        return

    DEST.mkdir(parents=True, exist_ok=True)
    known = read_manifest()
    new, bad = [], []
    for u in urls:
        path = download(session, u)
        digest = sha256(path)
        if path.name not in known:
            print(f"  {path.name}: sha256 {digest} (not in manifest)")
            new.append(f"{digest}  {path.name}")
        elif known[path.name] != digest:
            print(f"  {path.name}: SHA256 MISMATCH (expected {known[path.name]}, got {digest})")
            bad.append(path.name)
            continue
        else:
            print(f"  {path.name}: sha256 OK")
        unpack(path)
    if new and args.record:
        with open(MANIFEST, "a") as f:
            if not MANIFEST.stat().st_size:
                f.write("# sha256  file name — Poriscope sample data, DOI 10.20383/103.01599 (CC BY 4.0)\n")
            f.write("\n".join(new) + "\n")
        print(f"recorded {len(new)} hash(es) in {MANIFEST.name}")
    if bad:
        raise SystemExit(f"checksum mismatch: {', '.join(bad)}")


if __name__ == "__main__":
    main()
