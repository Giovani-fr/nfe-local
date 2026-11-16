@echo off
setlocal EnableExtensions

cd /d "%~dp0"
set "VENV_PYTHON=%USERPROFILE%\venvs\nfe-local\Scripts\python.exe"
if not defined PORTA set "PORTA=8765"

if not exist "%VENV_PYTHON%" (
    echo [ERRO] Ambiente virtual nao encontrado em "%VENV_PYTHON%".
    echo        Execute primeiro o arquivo instalar.bat.
    if not defined INICIAR_SEM_PAUSA pause
    exit /b 1
)

echo ============================================================================
echo   nfe-local - Servidor Local (http://127.0.0.1:%PORTA%)
echo   Pressione CTRL+C nesta janela para encerrar o sistema.
echo ============================================================================

if not defined INICIAR_SEM_NAVEGADOR (
    start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:%PORTA%"
)

"%VENV_PYTHON%" -m uvicorn api.app:app --host 127.0.0.1 --port %PORTA%
exit /b %ERRORLEVEL%
