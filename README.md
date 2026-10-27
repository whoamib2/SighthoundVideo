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

Phase 1 of the source port targets Python 3.11+ (also exercised on 3.12). Windows stays a first-class native target, and the same Python sources are what Linux and macOS import.

What this port covers:

* Python syntax and the standard-library renames (`pickle`, `http.client`, `xmlrpc`, `queue`, `urllib`).
* Bytes versus text for live-view mmap headers, pickle files, SQLite blobs, HTTP bodies, and XML-RPC.
* wxPython 4.x (Phoenix) call sites and Pillow 10 image helpers.
* Dependencies in `pyproject.toml`. The desktop UI needs `wxPython`; tests and lint need the `dev` extra (`pytest`, `ruff`) plus Pillow and requests.
* CMake 3.20 projects for `launch`, `xnat`, `backEnd/triggers` (`optsearches`), and `vitaToolbox/path` (`volumeUtils`, Apple only), aimed at MSVC 2022 and current clang/gcc.
* Packaging through `scripts/package_app.py` (PyInstaller). `frontEnd/setup-Win.py` and `frontEnd/setup-Mac.py` delegate to it.
* GitHub Actions on `ubuntu-latest` and `windows-latest`.

What is still blocked by closed binaries that are not in this tree (`videoLib2`, `svsentry`, `SIOWrapper`, sometimes inside the LFS `libs` archive):

* Camera capture and clip decode (`backEnd.native.capture`).
* The tracking pipeline (`backEnd.native.tracking`).
* The in-process object detector (`backEnd.native.detector.SIOObjectDetector`).

Those names are the only import sites for the closed extensions. Calling them without the binary raises `NativeDependencyError`. The local detector HTTP contract is unchanged: raw RGB `POST` with `X-Width` and `X-Height`, JSON `objects` reply. Detections can already carry `plate_text`, `face_identity`, and `delivery_carrier` (USPS, FedEx, UPS, Amazon). This phase does not run those recognizers.

Recommended next steps:

* Replace capture with PyAV or OpenCV behind `backEnd.native.capture`.
* Replace `svsentry` with an open tracker behind `pipeline_base` / `object_collector_base`.
* Serve the existing RGB HTTP contract from an ONNX Runtime detector and fill the reserved attributes.
* Smoke-test the wxPython UI and a PyInstaller bundle on Windows and macOS once those backends exist.

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
