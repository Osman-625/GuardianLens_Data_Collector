from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from guardianlens.db import init_db
from guardianlens.security import ensure_local_token
from guardianlens.config import settings
init_db(); token=ensure_local_token(); print(f'Database initialized: {settings.db_path}'); print('Local collector token created/verified.')
