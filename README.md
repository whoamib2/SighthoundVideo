# Sighthound Video

Sighthound Video is the open-source legacy release of Sighthound's intelligent video management system (VMS). It combines camera management, recording, search, rule-based alerts, and computer-vision-assisted object detection in a desktop application for Windows and macOS.

## Project status

Sighthound Video is a legacy product and is no longer under active development as a packaged commercial application. The latest packaged legacy release referenced by the public Sighthound support knowledgebase is Sighthound Video 7.0.16.

This repository is best understood as a source release for preservation, reference, experimentation, and community maintenance. It may require additional work to build or run on modern systems.

Useful public resources:

* Sighthound Video knowledgebase: https://support.sighthound.com/kb/section/39/
* Release notes: https://support.sighthound.com/kb/article/148-release-notes/
* System requirements: https://support.sighthound.com/kb/article/71-system-requirements/
* Getting started: https://support.sighthound.com/kb/article/18-getting-started/
* Camera compatibility: https://support.sighthound.com/kb/article/47-what-cameras-can-i-use/
* macOS installation notes: https://support.sighthound.com/kb/article/163-macos-install-sighthound-video/
* Apple Silicon / M1 note: https://support.sighthound.com/kb/article/150-does-sighthound-video-support-the-new-m1-processor-macs/
* Remote access: https://support.sighthound.com/kb/article/34-how-to-enable-remote-access/

## Important technical notes

Phase 1 ported the Python tree to 3.11+ (also exercised on 3.12). Phase 2 replaces the closed `videoLib2`, `svsentry`, and `SIOWrapper` binaries with open backends so a Python 3.11 process can capture, track, and classify without those extensions. Windows stays a first-class target. Linux and macOS use the same sources.

What works in the open backend:

* Capture and decode of files, RTSP, and HTTP through PyAV (FFmpeg is inside the PyAV wheels). USB cameras use OpenCV when `opencv-python-headless` is installed, including DirectShow on Windows.
* Clip files registered with `ClipManager.addClip`, plus `getMsList`, `getDuration`, `ClipReader`, and remux helpers.
* The 32-byte live-view mmap header the monitor reads, JPEG snapshots, and a best-effort single-variant HLS playlist (MPEG-TS segments).
* A motion gate and a ByteTrack-style tracker (high-score association, then low-score recovery, with a constant-velocity prediction) that calls `QueuedDataManagerCloud`.
* An ONNX Runtime YOLO detector on the existing local HTTP contract: raw RGB `POST` with `X-Width` and `X-Height`, JSON `objects` reply. COCO classes map to `person`, `vehicle`, and `animal` for `TargetTrigger`. Providers are DirectML, CUDA, and CPU on Windows, and CUDA and CPU on Linux. CPU is the fallback when those providers are absent.
* `plate_text`, `face_identity`, and `delivery_carrier` still travel on the detection JSON when a model output provides them.

### Windows installer

End users run `dist\SighthoundVideo-Setup.exe`. A developer produces it on a Windows PC. From a checkout of this branch:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Package
```

`scripts\bootstrap.ps1` updates the checkout (or clones the repository when you point it at an empty directory) and runs `scripts\build_windows.ps1`. Pass `-Branch cursor/python3-port-phase2-0735` when the script is not already inside a checkout.

`scripts\build_windows.ps1` is safe to re-run. It reuses `.venv`, downloads YOLOX-Nano when `models\yolox_nano.onnx` is missing, runs ruff, compileall, and pytest, and builds the CMake projects when MSVC 2022 is installed. With `-Package` it installs PyInstaller and, through winget, Inno Setup when that compiler is missing. If winget itself is missing, the script says so and stops before the installer step. MSVC 2022 is optional: when it is absent the script prints a message and continues. The native projects are `optsearches`, `launch`, and `xnat`.

The package is a PyInstaller one-folder bundle. Python, wxPython, PyAV/FFmpeg, and ONNX Runtime DirectML are inside the folder, so the installed app does not need Python. Inno Setup adds Start menu and desktop shortcuts, an uninstaller, and an optional "start with Windows" task. The finished file is `dist\SighthoundVideo-Setup.exe`, and the script prints that path.

The Windows build installs `onnxruntime-directml`. The analytics process uses the GPU when DirectML is available and the CPU otherwise. YOLOX-Nano is fetched at build time and copied into the app folder, so the installed program works offline. That file is about 3.5 MB.

The default weights are Megvii YOLOX-Nano (Apache-2.0). They are not in git:

```bash
python scripts/fetch_detector_model.py
```

Ultralytics YOLOv8 and YOLOv10 are AGPL-3.0. The fetch script does not download them. A raw YOLOv8 ONNX file can still be selected with `SIGHTHOUND_DETECTOR_MODEL` if you accept that license. See `models/README.md`.

Set `SIGHTHOUND_USE_CLOSED_CAPTURE=1` or `SIGHTHOUND_USE_CLOSED_SENTRY=1` only if you still have those Python 2.7 binaries and want the adapters to try them.

What is left:

* The wxPython UI and a PyInstaller bundle have not been exercised against these backends on Windows or macOS.
* Plate, face, and delivery-carrier recognizers are not implemented.
* Packet capture returns "driver not available" (`-2`). There is no pcap backend.
* Clip playback does not draw the old on-frame box and zone overlays.
* HLS is a single-variant playlist, not the historical multi-bitrate ladder.

### Windows setup

* Python 3.11 or 3.12, 64-bit.
* `python -m pip install -e ".[dev]"` from the repo root. That installs Pillow, requests, wxPython, numpy, PyAV, ONNX Runtime, pytest, ruff, and the `onnx` package used to build the tiny test detector.
* wxPython wheels need the Visual C++ runtime that the wheel documents. Capture itself does not need Visual Studio once PyAV is installed.
* CPU inference needs no GPU. `onnxruntime-directml` uses the GPU when DirectML is available and the CPU otherwise.
* A developer who wants CUDA instead of the packaged DirectML runtime can install `onnxruntime-gpu` built for their CUDA version. `CUDAExecutionProvider` is used on Windows and Linux when it is present.
* USB cameras: `python -m pip install opencv-python-headless`.
* Download the detector before starting the analytics process: `python scripts/fetch_detector_model.py`.

Phase 1 still covers the Python 3 syntax port, wxPython 4 / Pillow 10 call sites, CMake 3.20 projects for `launch`, `xnat`, `optsearches`, and Apple `volumeUtils`, and PyInstaller packaging via `scripts/package_app.py`. On Windows, `scripts\build_windows.ps1` runs those checks and can write the installer.

Historical build notes (Python 2.7, VS2017, `cx_Freeze`, `py2exe`) remain below for the original source release. Several assets are still Git LFS objects.

Before building, install Git LFS and fetch LFS artifacts:

```bash
git lfs install
git lfs pull
```

The repository includes Git LFS pointers for large files such as platform dependency archives, images, icons, sounds, and Windows DLLs.

## Original project goals and capabilities

Sighthound Video was designed to:

* Run on x64 Windows and macOS.
* Support common IP cameras and USB webcams.
* Use computer vision to improve video search and reduce noise from irrelevant motion.
* Record and manage video from multiple cameras.
* Provide rule-based alerts and actions.
* Support camera discovery and connectivity workflows such as ONVIF, RTSP, UPnP, and NAT traversal.
* Provide a native desktop interface for monitoring, searching, and managing video.

Some future-facing integrations mentioned in older project materials may not be complete or actively maintained in this source release.

## Source package

The Sighthound Video source package is organized around three major components:

* `source.zip` / repository source - Sighthound Video source code.
* `libs.zip` / `libs.tgz` - packaged binary dependencies.
* `tools.zip` / `tools.tgz` - Python 2.7.18 runtime and supporting Python packages.

The binary dependency archives are required for practical builds. In this repository they may be represented as Git LFS objects.

## Build environment - macOS

Source-build requirements:

* Intel Mac.
* macOS 12.3 or newer, based on the original source release notes.
* Xcode 13.4 with command line tools.
* CMake.
* Git LFS.
* Python 3.11+ (the packaged tools bundle was Python 2.7 and is historical).

Apple Silicon source builds are not guaranteed. The public support knowledgebase notes that the packaged legacy app can run on M1 Macs using Rosetta, but that does not necessarily mean the source build is natively supported on Apple Silicon.

## Build environment - Windows

Source-build requirements:

* Microsoft Visual Studio 2022 (MSVC) or current clang-cl.
* CMake 3.20 or newer.
* Git for Windows: https://gitforwindows.org/
* Git LFS.
* `make`.
  * The original build used `make` from MSYS2 in a standalone fashion.
  * The `make` build from https://sourceforge.net/projects/ezwinports/ did not work in the original build notes.
* Ninja: https://ninja-build.org/
* Inno Setup: https://jrsoftware.org/isinfo.php
* Python 3.11+ (the packaged tools bundle was Python 2.7 and is historical).

Build preparation:

* Open an MSVC shell, such as `x64 Native Tools Command Prompt for VS2017`.
* Add required tools to `PATH`.
* Set compiler variables:

```bat
set PATH=/path/to/cmake/bin;/path/to/git/bin;/path/to/git/usr/bin;%PATH%;/path/to/llvm10/bin;/path/to/ninja;/path/to/make
set CC=clang-cl
set CXX=clang-cl
```

Create an installer:

```bash
bash ./buildSV.sh
```

## Project structure

* `appCommon` - modules shared across application components.
* `backEnd` - backend processes, camera management, recording, search, rules, and responses.
  * `responses` - actions triggered by real-time events.
  * `triggers` - rule/event definitions used by real-time search and alerts.
* `config` - runtime configuration, including video pipeline configuration.
* `fonts` - bundled font resources.
* `frontEnd` - native desktop front end.
  * `bmps` - UI images.
  * `constructionComponents` - query/rule construction UI elements.
  * `docs` - license text for bundled code and dependent libraries.
  * `resources` - platform resources.
  * `sounds` - bundled sounds used by alert responses.
  * `updater` - macOS updater application resources.
* `icons` - application and installer icons.
* `launch` - service/daemon launcher components.
* `Microsoft.VC90.CRT.x86_64` - Microsoft runtime files used by older Windows packaging.
* `scripts` - build, signing, notarization, and packaging scripts.
* `vitaToolbox` - internal utility library used throughout the app.
* `xnat` - NAT traversal library.
* `FrontEndLaunchpad.py` - application launch entry point.
* `Makefile` - top-level build entry point.
* `PackageSources.py` - packaging helper used during build.

## Licensing

Sighthound Video is licensed under the GNU GPLv3 license as described in `COPYING`.

Alternative licensing may be available from Sighthound, Inc. by contacting opensource@sighthound.com.

Some legacy source files may contain older copyright or proprietary headers. If you plan to redistribute modified versions, review the license headers and bundled dependency licenses carefully.

## Support

For packaged legacy application usage, consult the public Sighthound support knowledgebase:

https://support.sighthound.com/kb/section/39/

For source builds, this repository is the primary reference. Because the project is legacy software, modern build environments may require troubleshooting beyond the original documentation.
