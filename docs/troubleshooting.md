# Troubleshooting

## Installing dlib and face_recognition

dlib is a C++ library. `pip install dlib` compiles it from source, which needs CMake and a C++ compiler. **Install dlib first**, so failures are easy to see:

```bash
pip install dlib==19.24.6 -v
```

Verified on this project's development machine: Ubuntu 22.04, Python 3.10.12, CMake 3.22.1, GCC 11. Build time 4 min 2 s, after which `pip check` reported no broken requirements.

| Symptom | Cause | Fix |
|---|---|---|
| `CMake must be installed to build dlib` / `cmake: command not found` | CMake missing or not on PATH | Linux: `sudo apt install cmake build-essential`; macOS: `brew install cmake` + `xcode-select --install`; Windows: `winget install Kitware.CMake`, then open a **new** terminal |
| Windows: `error: Microsoft Visual C++ 14.0 or greater is required` / `No CMAKE_CXX_COMPILER could be found` | No MSVC toolchain | Install **Visual Studio 2022 Build Tools** → workload **Desktop development with C++** (MSVC v143, Windows 10/11 SDK). Reboot, then retry from a new PowerShell |
| Windows: build picks 32-bit tools or fails linking | 32-bit or Microsoft Store Python | Install 64-bit Python from python.org; recreate the venv |
| `Python.h: No such file or directory` | Python headers missing | `sudo apt install python3.10-dev` (match your Python version) |
| Compiler killed / `c++: fatal error: Killed signal` | Out of memory during the parallel build (dlib compiles with many parallel jobs) | Close other apps and add swap (at least 4 GB total memory), then retry; or use the conda-forge prebuilt dlib below |
| Build very slow | Normal on laptops | 10–20 minutes is common; it only happens once per venv |
| `ModuleNotFoundError: No module named 'pkg_resources'` when importing face_recognition | `face_recognition_models` uses `pkg_resources`, removed in recent setuptools | `pip install "setuptools==75.6.0"` (pinned in requirements.txt) |
| `Please install face_recognition_models with this command…` | Models package missing | `pip install face_recognition_models==0.3.0` |
| `pip install -r requirements.txt` rebuilds dlib | dlib not yet installed in this venv | Expected once; afterwards pip reuses it |
| `RuntimeError: Unsupported image type, must be 8bit gray or RGB image` | Non-uint8 or RGBA array passed to dlib | The app always converts to 8-bit RGB; if you call dlib directly, use `cv2.cvtColor(..., cv2.COLOR_BGR2RGB)` |

**Fallback that keeps the same library:** if compiling is impossible (e.g. locked-down Windows PCs), use Miniforge/conda: `conda create -n wfa python=3.10 && conda activate wfa && conda install -c conda-forge dlib=19.24`, then `pip install -r requirements.txt` (pip sees dlib as already installed). This is still dlib with `face_recognition`; the app has **not** been switched to another recognition library.

Check the install: `flask --app wsgi face check-engine` should print `face engine OK`.

## Camera problems (browser)

| Message in the app | What to do |
|---|---|
| "The browser blocks the camera on insecure pages…" | Open `http://localhost:5000` or `http://127.0.0.1:5000` **on the machine running the browser**, or serve over HTTPS. A LAN address like `http://192.168.1.10:5000` is *not* a secure context |
| "Camera permission was denied…" | Click the camera/lock icon in the address bar → Allow → press Start camera. Chrome: `chrome://settings/content/camera`. Windows: Settings → Privacy & security → Camera → allow desktop apps/browsers. macOS: System Settings → Privacy & Security → Camera |
| "No camera was found…" | Plug in the webcam; check Device Manager / `ls /dev/video*` |
| "The camera could not be started… in use" | Close Zoom, Teams, other tabs or the OS Camera app; unplug and replug USB webcams |
| Preview is mirrored | Intentional, for the worker's comfort. Submitted frames are not mirrored |

To test a webcam attached to the server itself (not the browser path): `flask --app wsgi face check-camera --index 0`. It uses OpenCV `VideoCapture` and prints brightness and face-detection results for 5 frames without saving them.

## Recognition problems

| Symptom | Likely cause | Fix |
|---|---|---|
| "Face not recognised" for an enrolled worker | Lighting or pose differs from enrollment; cap or mask; threshold too strict | Improve kiosk lighting; add enrollment samples under kiosk conditions; check the worker is active and samples aren't *pending review*; only then consider raising `FACE_MATCH_THRESHOLD` slightly, with evaluation |
| "Could not identify reliably" (ambiguous) | Two enrolled workers look similar (e.g. siblings) | Re-enroll both with more varied samples; supervisor manual entry meanwhile |
| "The requested action was not detected" | Blink too fast, head turned too little or too far (face lost), wrong direction | Blink slowly; turn about 20–30° and come back; "YOUR left" means the worker's own left |
| "Your face was not clearly visible in enough frames" | Moved out of frame, too dark, blurry | Stand 0.5–1 m away, centred; hold still |
| Enrollment saved as *pending review* | Samples resemble another worker | Admin checks IDs under Enrollment reviews; approve if they are different people, reject if it's a duplicate registration |
| Slow verification | CPU-bound HOG on many frames | Lower `LIVENESS_FRAME_COUNT` (min 6) or `PROCESSING_MAX_WIDTH`; a faster CPU helps more than RAM |

## Database problems

| Symptom | Fix |
|---|---|
| `Can't connect to MySQL server on '127.0.0.1'` | `docker compose ps` should show `healthy`; check `MYSQL_PORT` and the port in `DATABASE_URL` match; first start takes ~30 s |
| `Access denied for user 'wfa_app'` | `MYSQL_PASSWORD` and the password in `DATABASE_URL` differ, or the volume was created with older passwords: `docker compose down -v` (**destroys data**) and start again |
| `cryptography package is required for sha256_password or caching_sha2_password` | `pip install cryptography==43.0.3` |
| `permission denied while trying to connect to the docker API` (Linux) | Your user is not in the `docker` group: `sudo usermod -aG docker $USER`, then log out and back in (or `newgrp docker`); alternatively run `sudo docker compose …` |
| `Target database is not up to date` | `flask --app wsgi db upgrade` |
| Port 3306 already used by a local MySQL | Set `MYSQL_PORT=3307` in `.env` and use `:3307` in `DATABASE_URL` |
| Test DB missing (`Unknown database 'worker_attendance_test'`) | The init script only runs on the first container start. Create it manually: `docker exec -it wfa-mysql mysql -uroot -p -e "CREATE DATABASE worker_attendance_test; GRANT ALL ON worker_attendance_test.* TO 'wfa_app'@'%';"` |

## App startup problems

| Message | Fix |
|---|---|
| `SECRET_KEY is missing, a placeholder, or shorter than 32 characters` | Generate one: `python -c "import secrets; print(secrets.token_hex(32))"` and put it in `.env` |
| `DATABASE_URL is not set` | Copy `.env.example` to `.env` and edit |
| `BUSINESS_TIMEZONE '…' is not a valid IANA zone` | Use names like `Asia/Kolkata`; on Windows make sure `tzdata` is installed (it is in requirements.txt) |
| Windows: `Activate.ps1 cannot be loaded because running scripts is disabled` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| "Your form session expired" | The CSRF token no longer matches the session (e.g. after signing in in another tab). Reload the page |
