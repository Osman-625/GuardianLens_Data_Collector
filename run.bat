@echo off
setlocal
where conda >nul 2>nul || (echo ERROR: Conda was not found.& exit /b 1)
call conda run -n guardianlens-collector python main.py
