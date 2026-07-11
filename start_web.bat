@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv (
  py -3.11 -m venv .venv
)
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .
start "" http://127.0.0.1:8787
posting-navigator-web
pause
