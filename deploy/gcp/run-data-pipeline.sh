#!/usr/bin/env bash
set -euo pipefail
LOG=/data/pipeline.log
exec > >(tee -a "$LOG") 2>&1

echo "=== Pipeline start $(date -Is) ==="

chmod +x ~/parallel-upload.sh

echo "--- Step 1-3: Upload 1B dataset ---"
bash ~/parallel-upload.sh /data/graph_csv gs://unified-fraud-demo-data/1B

echo "--- Step 4: Remove local 1B data ---"
rm -rf /data/graph_csv
df -h /data

echo "--- Step 5: Generate 10B (2 txns/user) ---"
python3 ~/generate-ags-csv.py \
  --users 10000000000 \
  --txns-per-user 2 \
  --out_dir /data/graph_csv \
  --workers 176 \
  --seed 42 | tee /data/generate-10b.log

echo "--- Step 6-7: Upload 10B dataset ---"
bash ~/parallel-upload.sh /data/graph_csv gs://unified-fraud-demo-data/10B

echo "=== Pipeline complete $(date -Is) ==="
df -h /data
gcloud storage du -s gs://unified-fraud-demo-data/1B/ gs://unified-fraud-demo-data/10B/ || true
