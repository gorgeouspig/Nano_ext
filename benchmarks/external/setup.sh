#!/usr/bin/env bash
# Fetch and install the external tools compared in the benchmark, each in its
# own environment (they pin incompatible dependency versions). Everything goes
# to benchmarks/_external/ (not tracked). Needs git and uv
# (https://docs.astral.sh/uv/); uv downloads the Python versions it needs.
#
#   bash benchmarks/external/setup.sh [poriscope] [mosaic] [autonanopore]
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$HERE/_external"
mkdir -p "$EXT"

PORISCOPE_REPO=https://github.com/TCossaLab/poriscope
PORISCOPE_COMMIT=9d2b9e8a61f2714666a71df17f4456ea0cac0e4a   # MIT; provides the NanoTrees fitter
AUTONANOPORE_REPO=https://github.com/bellstwohearted/AutoNanopore
AUTONANOPORE_COMMIT=a44cb802dbd67668c933c944e3b0a23b03633973  # no licence file: used unmodified, not redistributed
MOSAIC_VERSION=2.4                                            # PyPI mosaic-nist (NIST, US Gov. open source)

checkout() {  # repo commit dir
  if [ ! -d "$3/.git" ]; then git clone -q "$1" "$3"; fi
  git -C "$3" fetch -q origin "$2" 2>/dev/null || git -C "$3" fetch -q origin
  git -C "$3" checkout -q "$2"
}

targets=("$@")
sel() { [ ${#targets[@]} -eq 0 ] && return 0; for t in "${targets[@]}"; do [ "$t" = "$1" ] && return 0; done; return 1; }

if sel poriscope; then
  checkout "$PORISCOPE_REPO" "$PORISCOPE_COMMIT" "$EXT/poriscope"
  uv venv -q --python 3.12 "$EXT/venv-poriscope"
  VIRTUAL_ENV="$EXT/venv-poriscope" uv pip install -q "$EXT/poriscope"
  echo "poriscope: $("$EXT/venv-poriscope/bin/python" -c 'import poriscope, sys; print(sys.version.split()[0])')"
fi
if sel mosaic; then
  uv venv -q --python 3.10 "$EXT/venv-mosaic"
  # its metadata also pins test/packaging tools (codecov 2.1.12 is no longer on
  # PyPI), so install it without them and add the runtime pins it declares
  VIRTUAL_ENV="$EXT/venv-mosaic" uv pip install -q --no-deps "mosaic-nist==$MOSAIC_VERSION"
  VIRTUAL_ENV="$EXT/venv-mosaic" uv pip install -q numpy==1.23.5 scipy==1.9.3 pandas==1.5.2 \
      lmfit==1.1.0 uncertainties==3.1.7 PyWavelets==1.4.1 pyabf==2.3.7 cython==0.29.32
  echo "mosaic: $("$EXT/venv-mosaic/bin/python" -c 'import mosaic; print(mosaic.__version__)')"
fi
if sel autonanopore; then
  checkout "$AUTONANOPORE_REPO" "$AUTONANOPORE_COMMIT" "$EXT/autonanopore"
  echo "autonanopore: $EXT/autonanopore/AutoNanopore.py"
fi
