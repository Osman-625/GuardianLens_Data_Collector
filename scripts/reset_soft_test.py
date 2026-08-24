from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'data/soft_test/collector_soft_test.sqlite3'
for x in [p,Path(str(p)+'-wal'),Path(str(p)+'-shm')]: x.unlink(missing_ok=True)
runtime=ROOT/'data/soft_test/runtime'
if runtime.exists(): shutil.rmtree(runtime)
(runtime/'private/screenshots').mkdir(parents=True,exist_ok=True)
(runtime/'private/staging').mkdir(parents=True,exist_ok=True)
(runtime/'images/carousell').mkdir(parents=True,exist_ok=True)
(runtime/'images/mudah').mkdir(parents=True,exist_ok=True)
(runtime/'temporary').mkdir(parents=True,exist_ok=True)
(runtime/'exports').mkdir(parents=True,exist_ok=True)
(runtime/'reports').mkdir(parents=True,exist_ok=True)
print('Soft-test database and runtime assets reset.')
