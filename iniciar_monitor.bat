@echo off
rem Alerta Carros: vigia os 6 sites sem parar e envia os bons negocios para o Telegram.
rem Se o monitor parar (falta de internet, erro), volta a arrancar sozinho ao fim de 30 s.
cd /d "%~dp0"
set PYTHON=python
if exist ".venv\Scripts\python.exe" set PYTHON=.venv\Scripts\python.exe
set PYTHONIOENCODING=utf-8
:ciclo
"%PYTHON%" monitor.py
echo O monitor parou. A reiniciar daqui a 30 segundos (Ctrl+C para sair)...
timeout /t 30 /nobreak >nul
goto ciclo
