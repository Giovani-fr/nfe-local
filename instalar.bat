@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "PROJETO_DIR=%CD%"
set "LOG_FILE=%PROJETO_DIR%\instalar.log"
set "VENV_DIR=%USERPROFILE%\venvs\nfe-local"
set "PYTHON_CMD="

echo ============================================================================
echo   INSTALADOR - Visualizador e Validador Fiscal Local (nfe-local)
echo ============================================================================
echo.
echo [INFO] Log completo sera gravado em: "%LOG_FILE%"
echo [INFO] Inicio da instalacao em %DATE% %TIME% > "%LOG_FILE%"

echo [1/4] Verificando instalacao do Python 3.12...
py -3.12 --version >> "%LOG_FILE%" 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=py -3.12"
    goto :PYTHON_OK
)

python --version >> "%LOG_FILE%" 2>&1
if %ERRORLEVEL% EQU 0 (
    set "PYTHON_CMD=python"
    goto :PYTHON_OK
)

if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :PYTHON_OK
)

echo [AVISO] Python 3.12 nao foi encontrado no sistema.
echo [INFO] Tentando instalar Python.Python.3.12 via winget...
winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements >> "%LOG_FILE%" 2>&1
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    set "PYTHON_CMD=%LocalAppData%\Programs\Python\Python312\python.exe"
    goto :PYTHON_OK
)

echo [ERRO] Nao foi possivel localizar ou instalar o Python 3.12 automaticamente.
echo        Instale o Python 3.12 em https://www.python.org/downloads/ marcando "Add Python to PATH".
if not defined INSTALAR_SEM_PAUSA pause
exit /b 1

:PYTHON_OK
echo [OK] Python localizado: %PYTHON_CMD%

echo [2/4] Criando ambiente virtual isolado em "%VENV_DIR%" (fora do OneDrive)...
if not exist "%USERPROFILE%\venvs" mkdir "%USERPROFILE%\venvs"
if not exist "%VENV_DIR%\Scripts\python.exe" (
    %PYTHON_CMD% -m venv "%VENV_DIR%" >> "%LOG_FILE%" 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERRO] Falha ao criar o ambiente virtual em "%VENV_DIR%". Veja "%LOG_FILE%".
        if not defined INSTALAR_SEM_PAUSA pause
        exit /b 1
    )
)

echo [3/4] Atualizando pip e instalando dependencias de requirements.txt...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG_FILE%" 2>&1
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%PROJETO_DIR%\requirements.txt" >> "%LOG_FILE%" 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERRO] Falha ao instalar pacotes de requirements.txt. Consulte "%LOG_FILE%".
    if not defined INSTALAR_SEM_PAUSA pause
    exit /b 1
)

echo [4/4] Criando atalho "Visualizador NF-e Local.lnk"...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$pasta = if ($env:INSTALAR_ATALHO_PASTA) { $env:INSTALAR_ATALHO_PASTA } else { [Environment]::GetFolderPath('Desktop') };" ^
    "$caminhoAtalho = Join-Path $pasta 'Visualizador NF-e Local.lnk';" ^
    "$ws = New-Object -ComObject WScript.Shell;" ^
    "$sc = $ws.CreateShortcut($caminhoAtalho);" ^
    "$sc.TargetPath = Join-Path $env:PROJETO_DIR 'iniciar.bat';" ^
    "$sc.WorkingDirectory = $env:PROJETO_DIR;" ^
    "$sc.Description = 'Visualizador, Validador e Analisador Local de NF-e, NFC-e e NFS-e';" ^
    "$sc.Save();" >> "%LOG_FILE%" 2>&1

echo.
echo ============================================================================
echo   [SUCESSO] Instalacao concluida!
echo   Para abrir o sistema, use o atalho "Visualizador NF-e Local" na Area de
echo   Trabalho ou execute iniciar.bat.
echo ============================================================================
if not defined INSTALAR_SEM_PAUSA pause
exit /b 0
