import os
os.environ['GUARDIANLENS_MODE']='soft_test'
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from guardianlens.app import run
run()
