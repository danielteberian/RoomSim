@echo off
REM Quick-start for running the simulation against a rented RunPod GPU
REM (48GB card) running Ollama, instead of the local RX 590 (too weak for
REM anything past an 8B model — see docs/model-choice.md).
REM
REM The pod must actually be RUNNING for this to work — RunPod bills per
REM second while it's up, so stop it from the dashboard when you're not
REM actively simulating. If you spin up a new pod, its proxy URL changes —
REM update SIM_OLLAMA_HOST below to match.
set SIM_BACKEND=ollama
set SIM_OLLAMA_HOST=https://zn0hslixtop3ix-11434.proxy.runpod.net
set SIM_MODEL=qwen2.5:32b-instruct-q8_0
set SIM_ADJUDICATOR_MODEL=qwen2.5:32b-instruct-q8_0

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
