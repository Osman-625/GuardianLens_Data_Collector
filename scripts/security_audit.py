from pathlib import Path
import json, sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from guardianlens.audit import security_report
from guardianlens.config import settings
r=security_report(); out=settings.data_dir/'reports'/'security_audit.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2));raise SystemExit(0 if r['pass'] else 1)
