@echo off
setlocal
where conda >nul 2>nul || (echo ERROR: Conda was not found. Install Miniconda/Anaconda and reopen the terminal.& exit /b 1)
call conda env update -f environment.yml --prune
if errorlevel 1 exit /b 1
if not exist .env copy .env.example .env >nul
call conda run -n guardianlens-collector python scripts\init_db.py
if errorlevel 1 exit /b 1
call conda run -n guardianlens-collector python scripts\self_test.py
if errorlevel 1 exit /b 1
echo.
echo Setup complete. Edit .env, then run run.bat
