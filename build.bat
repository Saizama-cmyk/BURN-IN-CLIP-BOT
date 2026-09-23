@echo off
rem Build dist\Ashvane\Ashvane.exe (PyInstaller one-folder, windowed).
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if not exist "%PY%" (
  echo .venv not found. Create it: py -3.12 -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
  exit /b 1
)

echo [1/3] Generating release metadata and app icon...
"%PY%" installer\make_assets.py || goto :fail
"%PY%" -m clipbot.icon assets || goto :fail

rem Whisper on the GPU needs NVIDIA's cuBLAS/cuDNN DLLs (pip: nvidia-cublas-cu12 nvidia-cudnn-cu12).
rem Bundle them when installed; clipbot.transcribe adds <app>\nvidia\*\bin to the DLL path.
set NV=
set SP=%CD%\.venv\Lib\site-packages\nvidia
for %%L in (cublas cudnn cuda_nvrtc) do if exist "%SP%\%%L\bin" call set NV=%%NV%% --add-binary "%SP%\%%L\bin;nvidia\%%L\bin"
set VERSIONFILE=
if exist "%CD%\installer\version_info.txt" set VERSIONFILE=--version-file "%CD%\installer\version_info.txt"

echo [2/3] Running PyInstaller...
"%PY%" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name Ashvane --icon "%CD%\assets\clipbot.ico" %VERSIONFILE% ^
  --distpath dist --workpath build --specpath build ^
  --paths "%CD%" ^
  --add-data "%CD%\clipbot\dashboard\static;clipbot\dashboard\static" ^
  --add-data "%CD%\assets;assets" ^
  --collect-all streamlink --collect-all streamlink_cli ^
  --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime ^
  --collect-all webview --collect-submodules uvicorn --collect-submodules clipbot ^
  --hidden-import pystray._win32 %NV% ^
  clipbot\__main__.py || goto :fail

echo [3/3] Checking output...
if not exist dist\Ashvane\Ashvane.exe goto :fail
echo BUILD OK: dist\Ashvane\Ashvane.exe
exit /b 0

:fail
echo BUILD FAILED
exit /b 1
