from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from guardianlens.exports import export_approved
for p in export_approved(): print(p)
