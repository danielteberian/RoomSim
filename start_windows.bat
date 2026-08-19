@echo off
REM Quick-start for running the simulation on this same Windows machine,
REM using the Ollama instance also running here (http://localhost:11434).
REM
REM If you later move the app to a Raspberry Pi and keep Ollama here on the
REM desktop, change SIM_OLLAMA_HOST below to this PC's LAN IP (e.g.
REM http://192.168.1.50:11434) and run this same set of commands there instead.

REM See docs/model-choice.md for why this model was picked and for
REM AMD RX 590 GPU-acceleration notes/caveats.
set SIM_BACKEND=ollama
set SIM_OLLAMA_HOST=http://localhost:11434
set SIM_MODEL=hermes3
set SIM_ADJUDICATOR_MODEL=hermes3

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo Could not find venv\Scripts\activate.bat -- run setup first:
    echo   python -m venv venv
    echo   venv\Scripts\activate
    echo   pip install -r requirements.txt
    pause
    exit /b 1
)

echo Starting the room simulation at http://localhost:8000
echo Reading page at http://localhost:8000/read
echo Press Ctrl+C to stop.
uvicorn main:app --host 0.0.0.0 --port 8000

pause
