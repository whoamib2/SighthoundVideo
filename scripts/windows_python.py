"""Which Python the Windows build uses, and why.

PyPI was checked on 2026-10-27 for Windows amd64 wheels.

Python 3.14 is preferred. These current releases install on stock CPython 3.14:

* wxPython 4.3.2, numpy 2.5.4, Pillow 13.0.0, and onnxruntime-directml 1.24.4
  publish ``cp314-cp314-win_amd64`` wheels.
* PyAV 19.0.2 and onnx 1.23.2 publish ``cp312-abi3-win_amd64``. pip installs
  that stable-ABI wheel on 3.14. Their ``cp314t`` wheels are free-threaded
  only and are not used.
* opencv-python-headless 5.0.0.93 publishes ``cp37-abi3-win_amd64``.
* PyInstaller 6.22.3 publishes ``py3-none-win_amd64``.

Python 3.15 and newer are refused. onnxruntime-directml 1.24.4 has no
Windows amd64 cp315 wheel, and that is the package that sets the ceiling.
Python 3.11, 3.12, and 3.13 remain usable when 3.14 is not installed.
``requires-python`` stays ``>=3.11``.
"""

from __future__ import annotations

PREFERRED = (3, 14)
MINIMUM = (3, 11)
MAXIMUM = (3, 14)
# Newest interpreter the Windows dependency set does not support, and why.
CEILING_PACKAGE = "onnxruntime-directml"
CEILING_REASON = (
    "onnxruntime-directml 1.24.4 has no Windows amd64 cp315 wheel, "
    "so the Windows build does not use Python 3.15 or newer"
)

# Order the build script tries. Newest first, none above MAXIMUM.
CANDIDATES = tuple(range(MAXIMUM[1], MINIMUM[1] - 1, -1))

# Passed to python.exe -c by scripts/build_windows.ps1. Windows PowerShell 5.1
# deletes " characters inside native arguments, so these strings contain neither
# quote character. chr(46) is "." and chr(10) is a newline.
VERSION_PROBE = (
    "import sys; sys.stdout.write(str(sys.version_info[0])+chr(46)+str(sys.version_info[1])+chr(10))"
)
EXECUTABLE_PROBE = "import sys; sys.stdout.write(sys.executable+chr(10))"


def version_label(version: tuple[int, int] = PREFERRED) -> str:
    return "%d.%d" % version
